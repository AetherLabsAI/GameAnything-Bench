"""Godot TPS demo (Godot 4.7.2) regression probes. Ported from the original probes_godot_tps.py (verified in
game 2026-10-30: A/A + negative checks in games/godot-tps/pool.yaml). Changes: the host side runs through a gab Seat (any host
backend) instead of docker exec on a fixed container; install root / display / GL wrapper are arguments; instance names carry
a timestamp (two evaluations on one sandbox never share a dir); the render run is retried once (a loaded shared GPU hung or
aborted the GL game in 3 of 10 pool runs); no host CLI. compare() / item_result() are unchanged.

Instance (<root>/inst/<name>/): hard-link copy of vanilla/ (+ a real copy of the .godot import cache), the mod laid over it with
`cp --remove-destination` (never writes through to vanilla), `godot --headless --import`, and the bridge gt_probe.gd (next to
this file) as an extra autoload through override.cfg. Headless mode: the demo auto-hosts and loads the level; the bridge drives
the player with the game's own input actions and reads node state. Render mode (render.*): windowed on the seat display, the
Compatibility (OpenGL) renderer (Vulkan loses the device on Xvfb). Mode "gate" (gab load test): every game file loads.
The demo's ENet host binds a fixed port: one probe session at a time per sandbox (GT-ONLINE).
"""
import json, re, shlex, time
from pathlib import Path

HERE = Path(__file__).parent
VERSION = "Godot 4.7.2.stable (ed1daf0bf), tps-demo master (config/features 4.7, imported)"
APPNAME = "Godot Third-Person Shooter Demo"
GODOT = 'G={root}/godot; [ -d "$G" ] && G=$G/godot'          # reference install: <root>/godot is the binary; image: <root>/godot/godot

ERR = re.compile(r"^(ERROR|SCRIPT ERROR|USER ERROR|USER SCRIPT ERROR|Parse Error)|SIGSEGV|Illegal instruction|crash", re.I)
ERR_IGNORE = re.compile(r"resources still in use at exit|Condition \"status < 0\" is true. Returning: ERR_CANT_OPEN|"
                        r"shader type fog not supported|Parameter \"material\" is null|^ERROR: \d+ RID allocations", re.I)
RENDER_PROBES = ("menu", "hud", "frametime")
RENDER_READY = ("menu",)        # GT-VIEW / GT-PERF are blocked in the pool: only the menu runs in a render session
ORDER = ["settings", "resources", "level", "move", "jump", "fall_respawn", "camera", "shoot", "enemy", "audio", "online"]


IMPORT_LINKS = ("(cd {m} && find . -type f | sed 's|^\\./||' | while read -r f; do if [ -e \"{v}/$f.import\" ]; then "
                "cp --remove-destination \"{v}/$f.import\" \"$I/$f.import\"; fi; done; true)")   # gab: a replaced asset's .import link is broken too


def build_sh(root, name, mod_src=None, extra=""):
    """shell: inst/<name> = hard-link copy of vanilla + .godot copy + mod overlay (+ extra) + headless import"""
    I = f"{root}/inst/{name}"
    return f"""set -e; cd {root}; {GODOT.format(root=root)}; I={I}; rm -rf $I $I.user $I.shots; mkdir -p $I
    cp -al vanilla/. $I/; find $I -type d -exec chmod u+w {{}} +
    rm -rf $I/.godot; cp -r vanilla/.godot $I/.godot; chmod -R u+w $I/.godot
    {f'cp -r --remove-destination {mod_src}/. $I/; ' + IMPORT_LINKS.format(m=mod_src, v=root + '/vanilla') if mod_src else ':'}
    mkdir -p $I/gtprobe; cp {root}/probe/gt_probe.gd $I/gtprobe/gt_probe.gd
    if [ -f $I/override.cfg ]; then cp --remove-destination $I/override.cfg $I/override.cfg.tmp; mv $I/override.cfg.tmp $I/override.cfg; fi
    printf '\n[autoload]\n\nGTProbe="*res://gtprobe/gt_probe.gd"\n' >> $I/override.cfg
    {extra}
    U="$I.user/godot/app_userdata/{APPNAME}"; mkdir -p "$U" $I.shots
    printf '[video]\n\ndisplay_mode=0\nvsync=0\n' > "$U/settings.ini"
    XDG_DATA_HOME=$I.user timeout 600 $G --headless --path $I --import > $I.import.log 2>&1 || true"""


class GTProbes:
    """one probe instance (+ mod) through the asked probes: a headless run and, for render.*, a windowed run"""

    def __init__(self, seat, root, display, gl, label="p", log=print):
        self.seat, self.root, self.display, self.gl, self.log = seat, root, display, gl, log
        self.name = f"gab-{re.sub(r'[^A-Za-z0-9_.-]', '_', label)[:30]}-{time.strftime('%m%d%H%M%S')}"

    def sh(self, cmd, timeout=900, check=False): return self.seat.exec(cmd, timeout=timeout, check=check)

    def build(self, mod=None):
        self.seat.put(f"{self.root}/probe/gt_probe.gd", (HERE / "gt_probe.gd").read_text())
        src = None
        if mod:
            src = f"{self.root}/mods/{self.name}"; self.seat.copy_in(mod, src)
        self.sh(build_sh(self.root, self.name, src), timeout=900, check=True)

    def run_game(self, mode, only=None, timeout=600, gl=None):
        R, n = self.root, self.name
        env = (f"XDG_DATA_HOME={R}/inst/{n}.user GT_PROBE_OUT={R}/inst/{n}.{mode}.json GT_PROBE_MODE={mode} "
               f"GT_PROBE_SHOTS={R}/inst/{n}.shots GT_PROBE_ONLY={shlex.quote(','.join(only or []))}")
        if mode != "render": cmd = f"$G --headless --path inst/{n}"
        else: cmd = (f"env DISPLAY={self.display} {self.gl if gl is None else gl} $G --path inst/{n} --rendering-driver opengl3 --display-driver x11 "
                     f"--windowed --resolution 1280x720 --position 0,0")
        t0 = time.time()
        # detached (a long exec gets cut by the sandbox's exec wrapper); its own process group; polled with short execs
        self.sh(f"cd {R}; {GODOT.format(root=R)}; rm -f inst/{n}.{mode}.json inst/{n}.{mode}.rc; export {env} G; "
                f"setsid -f bash -c 'echo $$ > inst/{n}.{mode}.pid; timeout -s KILL {timeout} {cmd} > inst/{n}.{mode}.log 2>&1; echo $? > inst/{n}.{mode}.rc' "
                f"> /dev/null 2>&1 < /dev/null", check=True)
        while time.time() - t0 < timeout + 30:
            time.sleep(5)
            if self.sh(f"test -f {R}/inst/{n}.{mode}.rc && echo y").stdout.strip() == "y": break
        else:
            self.sh(f"p=$(cat {R}/inst/{n}.{mode}.pid 2>/dev/null); [ -n \"$p\" ] && kill -9 -- -$p 2>/dev/null; true")
        rc = re.search(r"(\d+)", self.sh(f"cat {R}/inst/{n}.{mode}.rc 2>/dev/null").stdout)
        j = self.sh(f"cat {R}/inst/{n}.{mode}.json 2>/dev/null").stdout
        lg = self.sh(f"cat {R}/inst/{n}.{mode}.log 2>/dev/null").stdout
        try: res = json.loads(j) if j.strip() else {"error": "no probe output (game crashed or hung)", "_no_output": True}
        except ValueError: res = {"error": "probe output unreadable", "_no_output": True}
        res["_exit"] = int(rc.group(1)) if rc else None
        res["_wall_s"] = round(time.time() - t0, 1)
        res["_finished"] = "GTPROBE done" in lg
        errs = [l for l in lg.splitlines() if ERR.search(l) and not ERR_IGNORE.search(l)]
        res["log_errors"] = len(errs)
        from ..godot_common import error_lines
        res["_error_lines"] = error_lines(lg, ERR, ERR_IGNORE)            # "message @ location", deduplicated, for people
        return res

    def session(self, names, mod=None):
        """names: probe names of ORDER, `render` (the ready render probes) or render.<x>; -> the session dict of probes_godot_tps"""
        res = {"version": VERSION, "mod": str(mod) if mod else None, "started": time.strftime("%Y-%m-%d %H:%M:%S")}
        try:
            self.build(mod)
            honly = [o for o in ORDER if o in names]
            ronly = [o for o in RENDER_PROBES if o in names or f"render.{o}" in names or ("render" in names and o in RENDER_READY)]
            if honly:
                h = spread_error(self.run_game("headless", honly), honly)
                errs = h.pop("_error_lines", []); res.update(h); res["_error_lines"] = errs
            if ronly:
                r = self.run_game("render", ronly)
                if "error" in r:
                    self.log(f"godot-tps render run failed ({r['error']}, exit {r.get('_exit')}): once more")
                    r = self.run_game("render", ronly); r["_retried"] = True
                res["render"] = r
        except Exception as e:
            res["error"] = f"{type(e).__name__}: {str(e)[:300]}"
        return mark_deterministic(res)

    def gate(self):
        """gab load test in the built instance -> {resources, level_loaded, log_errors, _error_lines, ...}"""
        return self.run_game("gate", timeout=300)

    def cleanup(self):
        n = self.name; self.sh(f"cd {self.root} && rm -rf inst/{n} inst/{n}.* mods/{n}")


def spread_error(h, names):
    """A headless run that the probe itself ended early (top-level error written by gt_probe.gd) -> every asked probe gets it:
    - the mod removed the game's main scene (`_host_removed`, a total conversion): {"error": ...} -> compare() fails with it;
    - anything else (the level never came up): {"not_tested": ...} -> compare() says could-not-test, with that evidence instead of
      "not probed". Not a fail: the 2026-11-06 selfcheck hit the same on vanilla (intermittent engine load failure: parse errors
      in level.tscn, sometimes SIGSEGV, exit 0 or 132; the old probe script shows it too).
    No probe output at all (crash / hang) stays "not probed"."""
    if "error" not in h or h.get("_no_output"): return h
    for n in names:
        if not isinstance(h.get(n), dict):
            h[n] = {"error": h["error"], "host_removed": True} if h.get("_host_removed") else {"not_tested": h["error"]}
    return h


def mark_deterministic(res):
    """`_deterministic`: probe names (as the regression picks name them: `render` for render.*) whose failure is certain to
    repeat on a fresh instance, so the evaluation need not re-run them: the mod removed the host game's main scene."""
    det = sorted(n for n in ORDER if isinstance(res.get(n), dict) and res[n].get("host_removed"))
    if any(isinstance(v, dict) and v.get("host_removed") for v in (res.get("render") or {}).values()): det.append("render")
    if det: res["_deterministic"] = sorted(det)
    return res


# ------------------------------------------------------------------ compare ------------------------------------------------------------------
# Tolerances from the A/A runs (2026-10-30, see the pool header): (relative, absolute)
TOL = {
    "move": {"run_speed": (0.05, 0.1), "travel": (0.05, 0.25), "stop_slide": (0.0, 0.25), "heading_err_deg": (0.0, 5.0),
             "right_travel": (0.05, 0.3), "right_side_drift": (0.0, 0.5), "dy": (0.0, 0.2)},
    "jump": {"jump_height": (0.05, 0.05), "air_time": (0.0, 0.07), "gravity": (0.03, 0.2), "end_dy": (0.0, 0.1)},
    "fall_respawn": {"return_s": (0.0, 0.2), "dist_to_start": (0.0, 0.5), "fade_alpha": (0.0, 0.3)},
    "camera": {"far_dist": (0.0, 0.15), "aim_dist": (0.0, 0.15), "released_dist": (0.0, 0.15)},
    "shoot": {"fire_rate": (0.08, 0.0), "bullet_speed": (0.05, 0.5), "parts_max_up": (0.0, 1.5), "robot_respawn_s": (0.0, 1.0)},
    "enemy": {"robot_moved": (0.0, 2.0), "facing_err_deg": (0.0, 15.0), "camera_shake_max": (0.0, 0.3), "far_robot_moved": (0.0, 1.0)},
}
EXACT = {
    "move": [], "jump": ["on_floor_before", "landed", "jump_sound"], "fall_respawn": ["returned"],
    "camera": ["current", "behind", "aiming", "crosshair", "crosshair_after_release", "arm_far", "arm_aim"],
    "shoot": ["killed", "exploded_signal", "hits_to_kill", "health_start"],
    "enemy": ["state_before", "far_robot_state"],
    "online": ["client_started", "joined", "client_walked", "host_players_after_leave"],
}
GRID_MAX_DIFF = 0          # level ray grid cells allowed to differ (A/A: 0 of 1763)


def _close(a, b, rel, ab):
    if a is None or b is None: return a == b
    return abs(a - b) <= max(ab, rel * abs(a))


def _diff_dict(b, g):
    return {k: f"{b.get(k)} -> {g.get(k)}" for k in sorted(set(b) | set(g)) if b.get(k) != g.get(k)}


def compare(name, base, got):
    """-> (ok | None, detail)."""
    norm = lambda x: {k: v for k, v in x.items() if not str(k).startswith("_")} if isinstance(x, dict) else x
    base, got = norm(base), norm(got)
    if not isinstance(got, dict) or not got: return None, "not probed"
    if "not_tested" in got: return None, got["not_tested"]
    if "error" in got: return False, got["error"]
    if not isinstance(base, dict) or "error" in base: return None, f"baseline: {(base or {}).get('error')}"
    if name == "settings":
        d = {}
        for sec in ("project", "tunables"):
            for k, v in _diff_dict(base.get(sec, {}), got.get(sec, {})).items(): d[f"{sec}.{k}"] = v
        for sec in ("autoloads", "audio_buses"):
            if base.get(sec) != got.get(sec): d[sec] = f"{base.get(sec)} -> {got.get(sec)}"
        bi, gi = base.get("input_map", {}), got.get("input_map", {})
        for k in sorted(set(bi) | set(gi)):
            if bi.get(k) != gi.get(k): d[f"input.{k}"] = f"{len(bi.get(k) or [])} events -> {len(gi.get(k) or [])} (changed)"
        return not d, d or "unchanged"
    if name == "resources":
        d = {}
        if got.get("failed"): d["failed"] = got["failed"]
        if got.get("files", 0) < base.get("files", 0): d["files"] = f"{base.get('files')} -> {got.get('files')}"
        return not d, d or f"{got.get('ok')} of {got.get('files')} load"
    if name == "level":
        bg, gg = base.get("grid", {}), got.get("grid", {})
        cells = [k for k in bg if not (bg[k] == gg.get(k) or (bg[k] is not None and gg.get(k) is not None and abs(bg[k] - gg[k]) <= 0.05))]
        d = {}
        if len(cells) > GRID_MAX_DIFF: d["grid"] = f"{len(cells)} of {len(bg)} cells differ, e.g. " + ", ".join(f"{c}: {bg[c]} -> {gg.get(c)}" for c in cells[:4])
        br, gr = base.get("ring_1m", []), got.get("ring_1m", [])
        rd = [i for i, (a, b) in enumerate(zip(br, gr)) if not _close(a, b, 0, 0.05)]
        if rd or len(br) != len(gr): d["ring_1m"] = f"{len(rd)} of {len(br)} rays differ"
        for k in ("player_spawns", "robot_spawns", "robots_at_start", "forklifts", "forklift_anim"):
            if base.get(k) != got.get(k): d[k] = f"{base.get(k)} -> {got.get(k)}"
        bc, gc = base.get("counts", {}), got.get("counts", {})
        cd = {k: f"{bc.get(k)} -> {gc.get(k)}" for k in bc if bc.get(k) != gc.get(k)}
        if cd: d["counts"] = cd
        if (base.get("forklift_moved_2s") or 0) > 1 and (got.get("forklift_moved_2s") or 0) < 1: d["forklift_moved_2s"] = f"{base.get('forklift_moved_2s')} -> {got.get('forklift_moved_2s')}"
        return not d, d or "same as vanilla"
    if name == "audio":
        d = {}
        for k in ("music_stream", "music_playing", "music_bus", "player_sfx_streams", "robot_sfx_streams", "triggered"):
            if base.get(k) != got.get(k): d[k] = f"{base.get(k)} -> {got.get(k)}"
        return not d, d or "same as vanilla"
    if name == "enemy":
        d = {}
        bf, gf = base.get("first_s", {}), got.get("first_s", {})
        for k in sorted(set(bf) | set(gf)):
            if not _close(bf.get(k), gf.get(k), 0, 0.5): d[f"first_s.{k}"] = f"{bf.get(k)} -> {gf.get(k)}"
        if (base.get("laser_shots_20s", 0) > 0) != (got.get("laser_shots_20s", 0) > 0) or abs(base.get("laser_shots_20s", 0) - got.get("laser_shots_20s", 0)) > 1:
            d["laser_shots_20s"] = f"{base.get('laser_shots_20s')} -> {got.get('laser_shots_20s')}"
    else:
        d = {}
    if name == "camera":
        bw, gw = base.get("wall", {}), got.get("wall", {})
        for k in ("cam_behind_wall", "shortened"):
            if bw.get(k) != gw.get(k): d[f"wall.{k}"] = f"{bw.get(k)} -> {gw.get(k)}"
        if not _close(bw.get("cam_dist_at_wall"), gw.get("cam_dist_at_wall"), 0, 0.2): d["wall.cam_dist_at_wall"] = f"{bw.get('cam_dist_at_wall')} -> {gw.get('cam_dist_at_wall')}"
    if name == "online":
        bc, gc = base.get("client") or {}, got.get("client") or {}
        for k in ("connected", "own_player", "players_seen", "robots_seen", "camera_current"):
            if bc.get(k) != gc.get(k): d[f"client.{k}"] = f"{bc.get(k)} -> {gc.get(k)}"
        if base.get("join_s") is not None and (got.get("join_s") is None or got["join_s"] > base["join_s"] + 10): d["join_s"] = f"{base.get('join_s')} -> {got.get('join_s')}"
    if name == "shoot":
        if (base.get("bullets", 0) > 0) != (got.get("bullets", 0) > 0): d["bullets"] = f"{base.get('bullets')} -> {got.get('bullets')}"
        bp, gp = base.get("parts", {}), got.get("parts", {})
        if set(bp) != set(gp): d["parts"] = f"{sorted(bp)} -> {sorted(gp)}"
        for k in bp:
            if k in gp and (bp[k]["fell"] < -0.3) != (gp[k]["fell"] < -0.3): d[f"parts.{k}.fell"] = f"{bp[k]['fell']} -> {gp[k]['fell']}"
    for k, (rel, ab) in TOL.get(name, {}).items():
        if k in base and not _close(base.get(k), got.get(k), rel, ab): d[k] = f"{base.get(k)} -> {got.get(k)}"
    for k in EXACT.get(name, []):
        if k in base and base.get(k) != got.get(k): d[k] = f"{base.get(k)} -> {got.get(k)}"
    if name in TOL or name in EXACT or name == "enemy":
        return not d, d or "same as vanilla"
    d = _diff_dict(base, got)
    return not d, d or "unchanged"


# render-mode probes (session JSON "render" part)
FT_MAX_RATIO, FT_ABS_MS = 1.5, 2.0       # frame time: modded median <= 1.5x vanilla + 2 ms (A/A: see pool header)
IMG_DIFF_MAX = 0.02           # mean |grey| difference at 320x180 (A/A: <= 0.0002)


def image_diff(a, b):
    from PIL import Image
    import numpy as np
    A = np.asarray(Image.open(a).convert("L").resize((320, 180)), dtype=float) / 255.0
    B = np.asarray(Image.open(b).convert("L").resize((320, 180)), dtype=float) / 255.0
    return round(float(abs(A - B).mean()), 4)


def _shots(base, got, keys, d):
    """Image-compare screenshots into d; a shot the game flagged unstable (frames not flowing / two grabs differ) on either
    side is skipped and reported back instead of failing (a loaded shared GPU can stall or garble the GL readback)."""
    unusable = []
    for k, m in keys.items():
        bm, gm = (base.get("shot_meta") or {}).get(m) or {}, (got.get("shot_meta") or {}).get(m) or {}
        if bm.get("stable") is False or gm.get("stable") is False: unusable.append(m); continue
        if k in base and k in got and Path(base[k]).exists() and Path(got[k]).exists():
            v = image_diff(base[k], got[k])
            if v > IMG_DIFF_MAX: d[k] = f"image differs by {v} (max {IMG_DIFF_MAX})"
        else: unusable.append(m)
    return unusable


def compare_render(name, base, got):
    if isinstance(got, dict) and "not_tested" in got: return None, got["not_tested"]
    if not isinstance(got, dict) or "error" in got: return False if isinstance(got, dict) else None, (got or {}).get("error", "not probed")
    if not isinstance(base, dict) or "error" in base: return None, f"baseline: {(base or {}).get('error')}"
    if name == "menu":
        d = {k: f"{base.get(k)} -> {got.get(k)}" for k in ("buttons", "settings_open", "settings_rows", "settings_closed", "play_loads_level") if base.get(k) != got.get(k)}
        # menu screenshots are kept as evidence only: GL readback on the shared seat GPU returned stale / garbled frames
        # in some vanilla runs (2026-10-30), so the verdict uses the menu's own node state
        return not d, d or "same as vanilla"
    if name == "hud":
        d = {}
        if base.get("crosshair_visible") != got.get("crosshair_visible"): d["crosshair_visible"] = f"{base.get('crosshair_visible')} -> {got.get('crosshair_visible')}"
        u = _shots(base, got, {"shot_view": "view_far", "shot_aim": "view_aim"}, d)
        if not d and u: return None, f"screenshots not usable (render stalled / unstable): {u}"
        return not d, d or "same as vanilla"
    if name == "frametime":
        d, bad = {}, []
        for spot in ("spawn", "robot3"):
            bs, gs = base.get(spot) or {}, got.get(spot) or {}
            b, g = bs.get("median_ms"), gs.get("median_ms")
            if b is None or g is None or min(bs.get("frames", 0), gs.get("frames", 0)) < 100 or max(bs.get("warmup_s", 0), gs.get("warmup_s", 0)) >= 89:
                bad.append(spot); continue
            if g > FT_MAX_RATIO * b + FT_ABS_MS: d[spot] = f"{g} ms vs vanilla {b} ms"
        if not d and bad: return None, f"no valid reading (renderer stalled: < 100 frames in 8 s or no warm-up) at {bad}"
        return not d, d or {s: f"{(got.get(s) or {}).get('median_ms')} ms vs {(base.get(s) or {}).get('median_ms')}" for s in ("spawn", "robot3")}


def item_result(spec, base, got):
    """Pool item `probe:` spec (probe, or probe.f1+f2 as in g1_regress.item_result) on two session JSONs -> (ok | None, detail)."""
    if spec == "log_errors":
        if not got.get("_finished"): return False, f"game did not finish the probe run (exit {got.get('_exit')})"
        b, g = base.get("log_errors"), got.get("log_errors")
        return (g is not None and b is not None and g <= b), f"{g} error lines vs vanilla {b}"
    if spec.startswith("render."):
        f = spec.split(".")[1]
        return compare_render(f, (base.get("render") or {}).get(f), (got.get("render") or {}).get(f))
    p = spec.split(".")[0]
    b, g = base.get(p), got.get(p)
    if "." in spec and isinstance(b, dict) and isinstance(g, dict):
        fields = spec.split(".", 1)[1].split("+")
        b = {k: v for k, v in b.items() if k in fields}; g = {k: v for k, v in g.items() if k in fields or k in ("error", "not_tested")}
    return compare(p, b, g)
