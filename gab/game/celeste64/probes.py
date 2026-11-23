"""Celeste 64 regression probes + game-instance helpers. Ported from the original probes_celeste64.py
(verified in game 2026-10-30: Celeste 64 v1.1.1, A/A of 3 vanilla instances + 3 negative checks in pool_celeste64.yaml).
Changes: runs through a gab Seat (any host backend) instead of docker exec on a fixed sandbox; root / display / game dirs are
arguments; the probe runs are started with `setsid -f` (pid file written by the run itself); the instance layout is the
adapter's (prepare_game); C# mods are Source/ overlays built by build() (the pool used whole source trees); no CLI. Probe
logic (the C# bridge, bridge/C64Probe.cs, + the arena map) and the compare rules / tolerances are unchanged.

Runtime: the game's own fixed 60 Hz step, software GL (llvmpipe) on the seat's X display, audio to an ALSA null device. The
bridge (a Foster module, active only with $C64_PROBE or $C64_STATE) presses the game's own keys, teleports the player onto
real actors of the stock map "1" or into a probe arena map added to every probe instance, reads state by reflection and prints
`C64PROBE {json}` lines.
"""
import base64, json, re, shlex, time

VERSION = "1.1.1 (github ExOK/Celeste64 tag v1.1.1, Foster 0.1.18, probe build)"
ALL = ["fps", "boot", "hub", "maps", "move", "dash", "climb", "camera", "interact", "hazards", "npc", "cassette", "save", "perf"]
TIMEOUT = 900
GL_PREFIX = {"mesa": "env LIBGL_ALWAYS_SOFTWARE=1 ", "vgl": "vglrun -d :99 "}
ERR = re.compile(r"Exception|ERROR|\[Error\]|Unhandled|Segmentation|core dumped|Assert", re.I)
ERR_IGNORE = re.compile(r"C64PROBE|ALSA lib|GL \(PEROFRMANCE", re.I)
ASOUND = "pcm.!default {\n  type null\n}\n"


def shq(s): return shlex.quote(s)


# ------------------------------------------------------------------ probe arena map ------------------------------------------------------------------
def _box(x0, y0, z0, x1, y1, z1, tex):
    f = lambda a, b, c: "( %d %d %d ) ( %d %d %d ) ( %d %d %d ) %s 0 0 0 1 1" % (*a, *b, *c, tex)
    return "{\n" + "\n".join([
        f((x0, 0, 0), (x0, 64, 0), (x0, 0, 64)), f((64, y0, 0), (0, y0, 0), (64, y0, 64)), f((64, 0, z0), (64, 64, z0), (0, 0, z0)),
        f((0, 0, z1), (0, 64, z1), (64, 0, z1)), f((64, y1, 64), (0, y1, 64), (64, y1, 0)), f((x1, 0, 64), (x1, 64, 64), (x1, 0, 0))]) + "\n}"


def arena_map():
    """Flat floor (world -600..600, top at z=0), one climbable wall (world face at y=200), player start at world (0,-150,1).
    Map units are 5x world units (the game scales maps by 0.2)."""
    brushes = [_box(-3000, -3000, -64, 3000, 3000, 0, "snow_1"), _box(-3000, 1000, 0, 3000, 1100, 3000, "rock_1")]
    return ("// Game: Generic\n// Format: Standard\n// GameAnything probe arena (probes_celeste64.py)\n{\n\"classname\" \"worldspawn\"\n"
            "\"skybox\" \"city\"\n\"snowAmount\" \"0\"\n" + "\n".join(brushes) +
            "\n}\n{\n\"classname\" \"PlayerSpawn\"\n\"origin\" \"0 -750 8\"\n}\n")


# ------------------------------------------------------------------ instances ------------------------------------------------------------------
def build(seat, bridge, src, out, timeout=900):
    """bridge into a source tree (bridge/apply_bridge.sh) + `dotnet build -c Release -o out` (10-60 s) -> (ok, log tail). Restore
    reads only the image's package cache (~/.nuget/packages): the container has no network and the image's NuGet.Config still
    lists nuget.org, which the restore of a fresh tree otherwise tries to reach and fails on"""
    r = seat.exec(f"bash {bridge}/apply_bridge.sh {src} && cd {src} && DOTNET_CLI_TELEMETRY_OPTOUT=1 DOTNET_NOLOGO=1 dotnet build -c Release -o {out} "
                  f"--source \"$HOME/.nuget/packages\" "
                  f"> {out}.log 2>&1; rc=$?; grep -E ': error [A-Z]+[0-9]+' {out}.log | head -100; tail -25 {out}.log; exit $rc", timeout=timeout)
    return r.returncode == 0, r.stdout[-12000:]


def prepare_game(seat, d, binsrc, content, mods=(), arena=False):
    """d/game = the binaries of binsrc + a fresh Content/ (stock content, then each mod's Content/ overlay in order) and d/home
    (HOME: save.json, controls; ALSA null device)"""
    seat.exec(f"rm -rf {d} && mkdir -p {d}/game {d}/home {d}/shots && cp -r {binsrc}/. {d}/game/ && rm -rf {d}/game/Content && "
              f"cp -r {content} {d}/game/Content", check=True)
    seat.put(f"{d}/home/.asoundrc", ASOUND)
    for m in mods: seat.exec(f"if [ -d {m} ]; then cp -r {m}/. {d}/game/Content/; fi", check=True)
    if arena: seat.put(f"{d}/game/Content/Maps/zz_probe_arena.map", arena_map())


def session(seat, d, names=None, display=":101", gl="mesa", log=lambda s: None):
    """probes in a prepared instance d (prepare_game(..., arena=True)) -> parsed results. The save probe needs a second
    process in the same HOME (saveload)."""
    names = [n for n in ALL if names is None or n in names]
    res = {"version": VERSION, "started": time.strftime("%Y-%m-%d %H:%M:%S")}
    runs = [[n for n in names if n != "save"]] + ([["save"], ["saveload"]] if "save" in names else [])
    log_all, t0 = "", time.time()
    for i, group in enumerate(r for r in runs if r):
        tag = f"run{i}"
        # detached (setsid -f, own process group = the pid it writes): a long exec can be cut by the remote docker transport
        cmd = (f"echo $$ > {d}/{tag}.pid; cd {d}/game && HOME={d}/home XDG_DATA_HOME={d}/home DISPLAY={display} C64_PROBE={','.join(group)} "
               f"C64_PROBE_OUT={d}/{tag}.jsonl C64_PROBE_SHOTS={d}/shots SDL_VIDEO_X11_FORCE_EGL=1 __GL_SYNC_TO_VBLANK=0 vblank_mode=0 timeout -k 10 {TIMEOUT} {GL_PREFIX[gl]}./Celeste64 > {d}/{tag}.log 2>&1; "
               f"echo EXIT=$? >> {d}/{tag}.log")
        seat.exec(f"setsid -f bash -c {shq(cmd)} > /dev/null 2>&1 < /dev/null")
        t1 = time.time()
        while time.time() - t1 < TIMEOUT + 60:
            time.sleep(3)
            if "EXIT=" in seat.exec(f"tail -c 200 {d}/{tag}.log 2>/dev/null").stdout: break
        else: seat.exec(f"p=$(cat {d}/{tag}.pid 2>/dev/null); [ -n \"$p\" ] && kill -9 -- -$p 2>/dev/null; true")
        lg = seat.exec(f"cat {d}/{tag}.log").stdout
        log_all += f"\n=== {tag} {group}\n" + lg
        got = {}
        for line in seat.exec(f"cat {d}/{tag}.jsonl 2>/dev/null").stdout.splitlines():
            if line.startswith("C64PROBE "):
                try: x = json.loads(line[9:]); got[x.pop("probe")] = x
                except ValueError: pass
        for n in group:
            res[n] = got.get(n, {"error": f"no result ({'crashed' if '_done' not in got else 'missing'})"})
        res.setdefault("_runs", []).append({"group": group, "done": "_done" in got, "exit": (re.findall(r"EXIT=(\d+)", lg) or ["?"])[-1],
                                            "fatal": got.get("_fatal", {}).get("error")})
        log(f"celeste64 probes {tag} {group}: done={'_done' in got}")
    if isinstance(res.get("save"), dict) and "saveload" in res:
        res["save"]["reload"] = res.pop("saveload")
    res["_wall_s"] = round(time.time() - t0, 1)
    res["alive_at_end"] = all(r["done"] for r in res["_runs"])
    err_lines = [l for l in log_all.splitlines() if ERR.search(l) and not ERR_IGNORE.search(l)]
    res["log_errors"] = len(err_lines)
    res["_error_log_file"] = seat.exec(f"ls {d}/home/Celeste64/ErrorLog.txt 2>/dev/null").stdout.strip() or None
    elog = seat.exec(f"head -c 4000 {d}/home/Celeste64/ErrorLog.txt 2>/dev/null").stdout       # the game's own crash report
    from ..base import error_lines                                     # for people (gate log): deduplicated, + 2 stack lines each
    res["_error_lines"] = error_lines(log_all + "\n" + elog, ERR, ERR_IGNORE, after=2)
    return res


# ------------------------------------------------------------------ compare ------------------------------------------------------------------
def _close(a, b, rel, ab):
    if a is None or b is None: return a == b
    if isinstance(a, bool) or isinstance(b, bool) or not isinstance(a, (int, float)) or not isinstance(b, (int, float)): return a == b
    return abs(a - b) <= max(ab, rel * abs(a))


# (relative, absolute) tolerances from the A/A runs 2026-10-30; fields not listed must be equal
TOL = {
    "move": {"run_top_speed": (0.0, 0.5), "run_frames_to_95pct": (0.0, 1), "run_dist_1s": (0.02, 1.0), "stop_frames": (0.0, 2), "stop_dist": (0.05, 0.5),
             "jump_height": (0.02, 0.3), "jump_air_frames": (0.0, 2), "hop_height": (0.03, 0.3), "run_jump_dist": (0.03, 1.5),
             "fall_terminal": (0.0, 0.5), "fall_frames_150": (0.0, 2)},
    "dash": {"dash_speed": (0.0, 0.5), "dash_frames": (0.0, 1), "dash_dist": (0.05, 1.0), "refill_frames_after_landing": (0.0, 3)},
    "climb": {"climb_rise_0_5s": (0.05, 1.0), "hang_drift": (0.0, 1.0), "kick_away_speed": (0.0, 1.0), "kick_up_speed": (0.0, 1.0)},
    "camera": {"rest_dist": (0.03, 1.0), "rest_pitch_dz": (0.03, 1.0), "rotate_0_5s_deg": (0.0, 4), "run_max_dist": (0.10, 3.0), "after_run_dist": (0.05, 2.0)},
    "hazards": {"respawn_s": (0.0, 0.2), "respawn_dist_from_spawn": (0.0, 1.0), "respawn_dist_from_checkpoint": (0.0, 1.0)},
    "interact": {"rise": (0.03, 2), "cooldown_s": (0.0, 0.2)},
    "perf": {},
    "fps": {"updates_per_s": (0.1, 3)},
}
IGNORE = {"_frames", "state_before_second", "spawn"}


def _diff(b, g, tol, path=""):
    """Recursive diff of two JSON values with per-field tolerances (tol keys are leaf field names)."""
    d = {}
    if isinstance(b, dict) and isinstance(g, dict):
        for k in sorted(set(b) | set(g)):
            if str(k).startswith("_") or k in IGNORE: continue
            d.update(_diff(b.get(k), g.get(k), tol, f"{path}.{k}" if path else str(k)))
        return d
    leaf = path.split(".")[-1]
    if leaf in tol:
        if not _close(b, g, *tol[leaf]): d[path] = f"{b} -> {g}"
    elif b != g:
        d[path] = f"{b} -> {g}" if not isinstance(b, (list, dict)) else f"{json.dumps(b)[:80]} -> {json.dumps(g)[:80]}"
    return d


def compare(name, base, got):
    """-> (ok | None, detail)."""
    if not isinstance(got, dict) or not got: return None, "not probed"
    if not isinstance(base, dict) or not base: return None, "no baseline"
    if "error" in (base or {}): return None, f"baseline error: {base['error']}"
    if "error" in got: return False, got["error"]
    if name == "boot":   # stock content still there; additions (new levels, maps, assets) are allowed
        d = {}
        gl = {l["id"]: l for l in got.get("levels") or []}
        for l in base.get("levels") or []:
            if gl.get(l["id"]) != l: d["level " + l["id"]] = f"{l} -> {gl.get(l['id'])}"
        miss = sorted(set(base.get("maps") or []) - set(got.get("maps") or []))
        if miss: d["missing maps"] = miss
        for k in ("n_textures", "n_models", "n_subtextures", "n_shaders", "n_fonts"):
            if (got.get(k) or 0) < (base.get(k) or 0): d[k] = f"{base.get(k)} -> {got.get(k)}"
        if not set(base.get("languages") or []) <= set(got.get("languages") or []): d["languages"] = f"{base.get('languages')} -> {got.get('languages')}"
        return not d, d or "stock levels, maps and assets present"
    if name == "maps":   # only the stock maps are compared (a mod may add maps)
        bm, gm = base.get("maps") or {}, got.get("maps") or {}
        fields = got.get("_fields")
        d = {}
        for m, b in bm.items():
            g = gm.get(m)
            if g is None: d[m] = "missing"; continue
            if fields: b, g = {k: v for k, v in b.items() if k in fields}, {k: v for k, v in g.items() if k in fields}
            d.update({f"{m}.{k}": v for k, v in _diff(b, g, {}).items()})
        if not d: return True, f"{len(bm)} stock maps as vanilla"
        return False, dict(list(d.items())[:10], **({"more": len(d) - 10} if len(d) > 10 else {}))
    if name == "perf":
        b, g = base.get("update_ms_median"), got.get("update_ms_median")
        br, gr = base.get("render_ms_median"), got.get("render_ms_median")
        if None in (b, g, br, gr): return None, "no reading"
        # render is software GL (llvmpipe): its CPU time swings 2-3x with the load other games put on the pod (A/A 7.5-20 ms
        # alone/with neighbours, 47-55 ms with 3 sessions at once) -> loose render bound; update (game logic) is stable.
        ok = g <= max(2 * b, b + 1.0) and gr <= max(3 * br, br + 25.0) and (got.get("fps") or 0) >= 0.85 * (base.get("fps") or 60)
        return ok, f"update {g} ms (vanilla {b}), render {gr} ms (vanilla {br}), {got.get('fps')} fps (vanilla {base.get('fps')})"
    if name == "npc":   # one entry per NPC, keyed by its name, so the detail says which one changed
        key = lambda x: {n["npc"]: {k: v for k, v in n.items() if k != "npc"} for n in (x.get("npcs") or [])}
        base, got = {"npcs": key(base)}, {"npcs": key(got)}
    d = _diff(base, got, TOL.get(name, {}))
    if not d: return True, "same as vanilla"
    return False, dict(list(d.items())[:10], **({"more": len(d) - 10} if len(d) > 10 else {}))


def item_result(spec, base, got):
    """Pool item `probe:` spec (probe, or probe.f1+f2) on two session JSONs -> (ok | None, detail)."""
    if spec == "log_errors":
        if not got.get("alive_at_end", False): return False, f"game crashed or hung: {[r for r in got.get('_runs', []) if not r.get('done')]}"
        b, g = base.get("log_errors"), got.get("log_errors")
        return (g is not None and b is not None and g <= b), f"{g} error lines vs vanilla {b}"
    p = spec.split(".")[0]
    b, g = base.get(p), got.get(p)
    if p == "maps" and "." in spec and isinstance(g, dict) and "error" not in g:
        return compare(p, b, dict(g, _fields=spec.split(".", 1)[1].split("+")))
    if "." in spec and isinstance(b, dict) and isinstance(g, dict):
        fields = spec.split(".", 1)[1].split("+")
        b = {k: v for k, v in b.items() if k in fields}; g = {k: v for k, v in g.items() if k in fields or k == "error"}
    return compare(p, b, g)
