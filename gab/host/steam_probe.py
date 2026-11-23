#!/usr/bin/env python3
"""Steam client probe, run INSIDE a (trial-like) container as the game user: does a Steamworks game in this container reach a
running Steam client, and is that client logged on and owning the app? Pure stdlib (ctypes); copied in by `gab steam probe`.

  python3 steam_probe.py --appid 892970 [--lib PATH/libsteam_api.so] [--hold S]

Prints one JSON line: is_steam_running, init (ok / error text), logged_on, steam_id (hashed: never the raw id), subscribed (the
app), app_id seen by Steam, then holds the session S seconds (so several probes overlap) and shuts the API down.
libsteam_api.so: --lib, else the first 64-bit copy found in the mounted Steam library (games ship it), else $GAB_STEAM_API_LIB.
"""
import argparse, ctypes, glob, hashlib, json, os, sys, tempfile, time

LIB = os.path.expanduser("~/.steam/debian-installation/steamapps/common")


def find_lib(want):
    if want: return want
    if os.environ.get("GAB_STEAM_API_LIB"): return os.environ["GAB_STEAM_API_LIB"]
    for p in sorted(glob.glob(f"{LIB}/**/libsteam_api.so", recursive=True)):
        try:
            with open(p, "rb") as f: h = f.read(5)
            if h[:4] == b"\x7fELF" and h[4] == 2: return p      # ELFCLASS64
        except OSError: pass
    return None


def fn(lib, name, res, *args):
    f = getattr(lib, name, None)
    if f is None: return None
    f.restype, f.argtypes = res, list(args); return f


def versioned(lib, prefix, versions):
    for v in versions:
        f = fn(lib, f"{prefix}{v:03d}", ctypes.c_void_p)
        if f: return f()
    return None


def main():
    ap = argparse.ArgumentParser(); ap.add_argument("--appid", type=int, required=True); ap.add_argument("--lib"); ap.add_argument("--hold", type=float, default=0)
    a = ap.parse_args()
    out = {"appid": a.appid, "pid": os.getpid(), "uid": os.getuid(), "steam_pid_file": None}
    try: out["steam_pid_file"] = open(os.path.expanduser("~/.steam/steam.pid")).read().strip()
    except OSError: pass
    out["steamclient_so"] = os.path.exists(os.path.expanduser("~/.steam/sdk64/steamclient.so"))
    out["shm"] = sorted(n for n in os.listdir("/dev/shm") if "Valve" in n or "Shm_" in n)[:8] if os.path.isdir("/dev/shm") else []
    p = find_lib(a.lib); out["lib"] = p
    if not p: out["error"] = "no 64-bit libsteam_api.so (pass --lib)"; print(json.dumps(out)); return 2
    os.environ["SteamAppId"] = os.environ["SteamGameId"] = str(a.appid)
    wd = tempfile.mkdtemp(prefix="steamprobe-"); os.chdir(wd)
    open("steam_appid.txt", "w").write(str(a.appid))
    lib = ctypes.CDLL(p)
    running = fn(lib, "SteamAPI_IsSteamRunning", ctypes.c_bool)
    out["is_steam_running"] = bool(running()) if running else None
    initflat = fn(lib, "SteamAPI_InitFlat", ctypes.c_int, ctypes.c_char_p)
    if initflat:
        msg = ctypes.create_string_buffer(1024); r = initflat(msg)
        out["init"] = "ok" if r == 0 else f"error {r}: {msg.value.decode(errors='replace')}"
    else:
        init = fn(lib, "SteamAPI_Init", ctypes.c_bool); out["init"] = "ok" if init() else "SteamAPI_Init returned false"
    if out["init"] == "ok":
        user = versioned(lib, "SteamAPI_SteamUser_v", range(23, 16, -1))
        apps = versioned(lib, "SteamAPI_SteamApps_v", range(9, 5, -1))
        utils = versioned(lib, "SteamAPI_SteamUtils_v", range(10, 6, -1))
        if user:
            lo = fn(lib, "SteamAPI_ISteamUser_BLoggedOn", ctypes.c_bool, ctypes.c_void_p); sid = fn(lib, "SteamAPI_ISteamUser_GetSteamID", ctypes.c_uint64, ctypes.c_void_p)
            out["logged_on"] = bool(lo(user)) if lo else None
            if sid: out["steam_id_sha8"] = hashlib.sha256(str(sid(user)).encode()).hexdigest()[:8]
        if apps:
            sub = fn(lib, "SteamAPI_ISteamApps_BIsSubscribedApp", ctypes.c_bool, ctypes.c_void_p, ctypes.c_uint32)
            if sub: out["subscribed"] = bool(sub(apps, a.appid))
        if utils:
            g = fn(lib, "SteamAPI_ISteamUtils_GetAppID", ctypes.c_uint32, ctypes.c_void_p)
            if g: out["app_id_seen"] = int(g(utils))
        print(json.dumps(out), flush=True)
        rc = fn(lib, "SteamAPI_RunCallbacks", None); t0 = time.time()
        while time.time() - t0 < a.hold:
            if rc: rc()
            time.sleep(0.5)
        if user and lo: print(json.dumps({"pid": os.getpid(), "after_hold_logged_on": bool(lo(user))}), flush=True)
        sd = fn(lib, "SteamAPI_Shutdown", None)
        if sd: sd()
        return 0
    print(json.dumps(out)); return 1


if __name__ == "__main__":
    sys.exit(main())
