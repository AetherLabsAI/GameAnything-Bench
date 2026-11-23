"""`gab steam ...`: the machine's Steam hub for host `docker` (gab/host/steam.py). Run by the operator who owns the games.

  gab steam init                 the state dir (config steam_state; mode 700) and its home/ for the container user
  gab steam login                the login in this terminal: account, password (hidden) and Steam Guard code are typed into the hub's
                                 Steam window by xdotool reading stdin (gab/setup/steamsetup.py); skipped when already logged on
  gab steam login --vnc          fallback: hub with VNC on a unix socket in the state dir; prints the ssh -L line; you log in in
                                 Steam's own window
  gab steam up | down | status   start (no VNC) / stop / JSON status (running, logged_on, account, client, installed apps)
  gab steam shot OUT.png         screenshot of the hub screen
  gab steam install GAME         download the game's app ids (terraria, valheim, cities_skylines) without a dialog
  gab steam install APPID        Steam's own install dialog for one app (click through it in VNC)
  gab steam probe --game G --appid A [--n 3] [--hold 60] [--lib-host F]
                                 N trial containers exactly as a trial makes them (Backend.acquire of game G), each runs
                                 steam_probe.py at once (SteamAPI_Init of app A, logged on?, owns A?), all holding the session
                                 together: the multi-instance check of ONE Steam login
"""
import json, sys, time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

from ..host import steam as S
from ..host.docker import Backend


def main(a):
    b = Backend(); h = S.Hub(b)
    if a.action == "init":
        print(f"state dir: {h.init_state()} (it, home/, vnc/, relay/: mode 700, yours)"); return 0
    if a.action == "up": print(h.up()); return 0
    if a.action == "down": print(h.down()); return 0
    if a.action == "status": print(json.dumps(h.status(), indent=None if a.json else 1)); return 0
    if a.action == "login" and not a.vnc:                       # the terminal login (gab/setup/steamsetup.py); --vnc = Steam's window
        from .steamsetup import ensure_hub, terminal_login
        ensure_hub(h); terminal_login(h); return 0
    if a.action == "login":
        if not h.st.is_dir(): h.init_state()
        r = h.up(vnc=True); print(f"hub {h.name}: {r}")
        print(S.vnc_hint(h.st))
        print("In the VNC window: log in to Steam (password + Steam Guard typed by you, or the QR code with the Steam app), tick "
              "'Remember me'. The first start downloads the Steam client (a few minutes). Waiting for the logon (Ctrl-C to stop waiting; "
              "the hub keeps running) ...")
        last = None; t0 = time.time()
        while time.time() - t0 < (a.wait or 1800):
            s = h.status(); k = (s.get("running"), s.get("client"), s.get("logged_on"))
            if k != last: print(time.strftime("%H:%M:%S"), json.dumps({x: s.get(x) for x in ("running", "client", "logged_on", "account", "offline_mode")})); last = k
            if s.get("needs_userns"):
                print("Steam refuses to start: it needs user namespaces in the hub (docker/README.md, Steam games: steam_hub_seccomp + "
                      "steam_hub_apparmor; your decision). `gab steam down`, set them, `gab steam login` again."); return 3
            if s.get("logged_on"):
                print("logged on. Next: gab steam install 1281930 / 105600 / 892970 (if not yet), then `gab steam down && gab steam up` "
                      "(restart without VNC; the login persists)"); return 0
            time.sleep(5)
        print("not logged on yet"); return 2
    if a.action == "shot":
        out = a.arg or "steam-hub.png"
        r = h.helper("shot /tmp/gab-hub-shot.png", timeout=60)
        if r.returncode: print(r.stdout + r.stderr); return 1
        ok = b._run(["cp", f"{h.name}:/tmp/gab-hub-shot.png", out], timeout=60).returncode == 0
        print(out if ok else "docker cp failed"); return 0 if ok else 1
    if a.action == "install":                                   # GAME (its app ids, no dialog) or an APPID (Steam's install dialog)
        if not a.arg: sys.exit("gab steam install GAME | APPID")
        from .steamsetup import install_apps, APPS
        if a.arg in APPS: install_apps(h, a.arg); return 0
        r = h.helper(f"install {int(a.arg)}"); print(r.stdout + r.stderr); return r.returncode
    if a.action == "probe": return probe(b, a)
    sys.exit(f"unknown action {a.action}")


def probe(b, a):
    """N trial containers of game a.game, steam_probe.py in each at the same time -> per-instance results"""
    src = Path(__file__).resolve().parent.parent / "host" / "steam_probe.py"
    seats = []
    try:
        for i in range(a.n):
            s = b.acquire({"game": a.game}, f"gab-steamprobe-{i}"); seats.append(s)
            s.put("/tmp/steam_probe.py", src.read_bytes())
            if a.lib_host: s.put("/tmp/libsteam_api.so", Path(a.lib_host).read_bytes())
            print(f"instance {i}: container {s.sandbox}")
        lib = " --lib /tmp/libsteam_api.so" if a.lib_host else ""
        def one(s):
            r = s.exec(f"python3 /tmp/steam_probe.py --appid {a.appid} --hold {a.hold}{lib}", timeout=a.hold + 120)
            return s.sandbox, r.returncode, r.stdout.strip().splitlines(), r.stderr.strip()[-400:]
        with ThreadPoolExecutor(len(seats)) as ex: res = list(ex.map(one, seats))
        ok = 0
        for name, rc, lines, err in res:
            print(f"--- {name} rc={rc}")
            for l in lines: print("   ", l)
            if err: print("    stderr:", err.replace("\n", " | ")[-400:])
            try: first = json.loads(lines[0]) if lines else {}
            except ValueError: first = {}
            ok += first.get("init") == "ok" and bool(first.get("logged_on")) and first.get("subscribed") is not False
        print(f"{ok}/{len(seats)} instances: SteamAPI_Init ok + logged on + owns app {a.appid}, all at once on one Steam login")
        return 0 if ok == len(seats) else 1
    finally:
        for s in seats:
            try: s.release()
            except Exception: pass


def add_parser(sp):
    s = sp.add_parser("steam", help="the machine's Steam hub for host docker (Terraria, Valheim): init, login, status, probe")
    s.add_argument("action", choices=["init", "login", "up", "down", "status", "shot", "install", "probe"])
    s.add_argument("arg", nargs="?", help="shot: output png; install: app id")
    s.add_argument("--json", action="store_true"); s.add_argument("--vnc", action="store_true", help="login: Steam's own window over VNC (fallback)")
    s.add_argument("--wait", type=int, default=1800, help="login: seconds to wait for the logon")
    s.add_argument("--game", default="valheim"); s.add_argument("--appid", type=int, default=892970)
    s.add_argument("--n", type=int, default=3); s.add_argument("--hold", type=float, default=60)
    s.add_argument("--lib-host", help="probe: a libsteam_api.so on the host to use instead of the game's own")
    s.set_defaults(f=lambda a: sys.exit(main(a)))
