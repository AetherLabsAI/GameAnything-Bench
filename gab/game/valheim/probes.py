"""Valheim regression probes for gab: from the original probes_valheim.py (pool verified in game 2026-10-30, Valheim 1.0.21 Linux,
BepInExPack_Valheim 5.4.2351, ValheimBridge 0.2.0) ported to a gab Seat. The measurements (bridge/driver.py = probes_valheim_driver.py,
unchanged), the runner (bridge/vhp.sh: root / Steam folder / BepInEx source are parameters; the template step takes BepInEx from an
unpacked BepInExPack_Valheim when the Steam folder has none) and compare() / item_result() are the pool's; what changed is how commands
reach the sandbox (seat.exec) and the instance folder (<root>/gab/probe-<label>).

A session = 2 game starts on a private Xvfb (:151) through VirtualGL: run1 (fresh world "gaprobe" seed gaprobe01, character "gaprobe",
the probe groups, a marker chest + items, save) -> run2 (the same world: read them back). ~6.5-7 min. Results: `VHPROBE {json}` lines.
"""
import json, re, time

VERSION = "Valheim 1.0.21 (Steam 892970, Linux) + BepInExPack_Valheim 5.4.2351 + ValheimBridge 0.2.0"
GROUPS = ["inventory", "content", "worldgen", "perf", "move", "swim", "combat", "creatures", "buildcraft", "sail", "portal", "weather", "skills", "save"]
# BepInEx log entries "[Error  :   Source] ..." / "[Fatal ...]"; noise present in every vanilla run is listed in ERR_IGNORE
ERR = re.compile(r"^\[(Error|Fatal)\s*:")
ERR_IGNORE = re.compile(r"$^")



def shq(s): return "'" + str(s).replace("'", "'\\''") + "'"


def groups_of(names):
    """probe names as the framework passes them (the part of a pool item's `probe:` before the first '.', e.g. "move,swim",
    "craft,content", "saveload", "build", "log_errors") -> the session's groups ("build" / "craft" come from the group buildcraft,
    "saveload" from save)"""
    alias = {"build": "buildcraft", "craft": "buildcraft", "saveload": "save"}
    out = []
    for n in names:
        for part in str(n).split(","):
            g = alias.get(part.strip().split(".")[0], part.strip().split(".")[0])
            if g in GROUPS and g not in out: out.append(g)
    return [g for g in GROUPS if g in out]


class VHProbes:
    """the pool's session() on a gab Seat. root = <home>/pool-probes/valheim (game-tmpl/, ValheimBridge.dll, bin/vhp.sh, bin/driver.py)"""

    def __init__(self, seat, root, steam, bepinex, display=":151"):
        self.seat, self.root, self.steam, self.bep, self.disp = seat, root, steam, bepinex, display

    def sh(self, cmd, timeout=110, check=True):
        r = self.seat.exec(f"export VHP_ROOT={self.root} VHP_STEAM={shq(self.steam)} VHP_BEPINEX={shq(self.bep)} VHP_DISPLAY={self.disp}; {cmd}", timeout=timeout)
        if check and r.returncode != 0:
            raise RuntimeError(f"sandbox command failed ({r.returncode}): {cmd[:200]}\n{r.stdout[-2000:]}\n{r.stderr[-2000:]}")
        return r.stdout

    def _read(self, path):
        got = {}
        for line in self.sh(f"cat {path} 2>/dev/null", check=False).splitlines():
            if line.startswith("VHPROBE "):
                try: d = json.loads(line[8:]); got[d.pop("probe")] = d
                except ValueError: pass
        return got

    def display(self):
        d = self.disp.lstrip(":")
        self.sh(f"[ -e /tmp/.X11-unix/X{d} ] || {{ rm -f /tmp/.X{d}-lock; setsid nohup Xvfb :{d} -screen 0 1280x720x24 -nolisten tcp > {self.root}/xvfb{d}.log 2>&1 < /dev/null & sleep 2; }}", check=False)

    def session(self, name, mods=(), only=None):
        """mods = plugin .dll files or folders inside the sandbox (copied into the instance's BepInEx/plugins/)"""
        R = self.root
        groups = [g for g in GROUPS if only is None or g in only]
        inst = f"{R}/gab/probe-{name}"
        res = {"version": VERSION, "mods": [str(m) for m in mods], "started": time.strftime("%Y-%m-%d %H:%M:%S")}
        self.sh(f"rm -rf {inst} && mkdir -p {inst}/save && cp -a {R}/game-tmpl {inst}/game && cp {R}/ValheimBridge.dll {inst}/game/BepInEx/plugins/")
        self.display()
        for m in mods: self.sh(f"cp -r {shq(m)} {inst}/game/BepInEx/plugins/")
        runs, t0 = [], time.time()
        for mode, g in (("run1", groups), ("run2", ["saveload"] if "save" in groups else [])):
            if not g: continue
            self.sh(f"setsid nohup bash {R}/bin/vhp.sh run {inst} {mode} {','.join(g)} > {inst}/{mode}.runner.out 2>&1 < /dev/null &")
            t1, shot = time.time(), False
            while time.time() - t1 < 1800:
                time.sleep(5)
                if f"EXIT={mode}" in self.sh(f"tail -c 200 {inst}/{mode}.driver.out 2>/dev/null", check=False): break
                if mode == "run1" and not shot and '"probe": "move"' in self.sh(f"cat {inst}/run1.jsonl 2>/dev/null", check=False):
                    shot = True; self.sh(f"bash {R}/bin/vhp.sh shot {inst}/shot_run1.png", check=False)
            got = self._read(f"{inst}/{mode}.jsonl")
            for k in (["build", "craft"] if "buildcraft" in g else []) + [x for x in g if x != "buildcraft"]:
                res[k] = got.get(k, {"error": "no result" + (f": {got['_fatal']['error'][:300]}" if "_fatal" in got else "")})
            if mode == "run1": res["_entered"] = got.get("_entered")
            runs.append({"step": mode, "done": "_done" in got, "fatal": got.get("_fatal", {}).get("error"),
                         "boots": self.sh(f"grep -c '=== start try' {inst}/game.out", check=False).strip()})
        res["_runs"] = runs; res["_wall_s"] = round(time.time() - t0, 1)
        res["alive_at_end"] = all(r["done"] for r in runs)
        lg = self.sh(f"cat {inst}/bepinex-run1.log {inst}/bepinex-run2.log 2>/dev/null", check=False)
        errs = [l[:300] for l in lg.splitlines() if ERR.search(l) and not ERR_IGNORE.search(l)]
        res["log_errors"] = len(errs); res["_error_lines"] = errs[:20]; res["_instance"] = inst
        return res


# ------------------------------------------------------------------ compare ------------------------------------------------------------------
def _close(a, b, rel, ab):
    if a is None or b is None: return a == b
    if isinstance(a, bool) or isinstance(b, bool) or not isinstance(a, (int, float)) or not isinstance(b, (int, float)): return a == b
    return abs(a - b) <= max(ab, rel * abs(a))


# (relative, absolute) tolerances from the A/A runs 2026-10-30 (real-time game: frame-rate dependent numbers get ranges)
TOL = {
    "move": {"walk_speed": (0.25, 0.3), "walk_max": (0.15, 0.5), "run_speed": (0.08, 0.3), "run_max": (0.15, 0.8), "run_stamina_used_3s": (0.15, 2),
             "jump_height": (0.2, 0.25), "jump_stamina": (0.0, 1.0), "fall_damage_20m": (0.25, 5)},
    "swim": {"swim_speed": (0.15, 0.3), "swim_stamina_used_3s": (0.25, 3)},
    # club_first_hit: (0.35, 2) -> (0.6, 2) gab 2026-10-31: Valheim rolls a random skill factor per hit at
    # Clubs level 0; vanilla first hits seen 13.1-19.7 (10-30 + 10-31) and 9.7 in a NegRun run (run speed only) on the test sandbox
    "combat": {"club_first_hit": (0.6, 2), "blunt20_taken": (0.05, 0.5), "blocked_blunt20_taken": (0.2, 1), "block_stamina": (0.3, 3)},
    "build": {"pole_top_height": (0.0, 2.5), "poles_standing": (0.0, 1)},
    "sail": {"half_moved_m": (0.35, 5), "full_moved_m": (0.35, 5), "half_speed": (0.35, 0.5), "full_speed": (0.35, 0.5)},
    "weather": {"world_time_rate": (0.1, 0.2)},
    "skills": {},
}
IGNORE = {"club_hits", "club_hit_mean", "club_hits_landed", "spawn_ground", "envs_time", "run_speed", "spots_tried", "block_stamina", "blocked_blunt20_taken"}


def _diff(b, g, tol, path=""):
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


def _short(d): return dict(list(d.items())[:10], **({"more": len(d) - 10} if len(d) > 10 else {}))


def compare(name, base, got):
    if not isinstance(got, dict) or not got: return None, "not probed"
    if not isinstance(base, dict) or not base: return None, "no baseline"
    if "error" in base: return None, f"baseline error: {str(base['error'])[:200]}"
    if "error" in got: return False, str(got["error"])[:300]
    if name == "content":   # vanilla prefabs / recipes / hammer pieces must stay (additions allowed); sampled items + creatures equal
        d = {}
        for k in ("prefabs", "recipes", "hammer_pieces"):
            miss = sorted(set(base.get(k) or []) - set(got.get(k) or []))
            if miss: d[k] = f"{len(miss)} vanilla entries missing/changed: {[m[:80] for m in miss[:3]]}"
        for k in ("items", "creatures"):
            bad = [i for i, v in (base.get(k) or {}).items() if (got.get(k) or {}).get(i) != v]
            if bad: d[k] = f"changed: {bad[:8]}"
        return (not d), (d or "stock content as vanilla")
    if name == "creatures":
        d = {}
        miss = sorted(set(base.get("meadows_spawners") or []) - set(got.get("meadows_spawners") or []))
        if miss: d["meadows_spawners"] = f"{len(miss)} vanilla Meadows spawners missing/changed: {miss[:2]}"
        miss = sorted(set(base.get("all_spawn_prefabs") or []) - set(got.get("all_spawn_prefabs") or []))
        if miss: d["all_spawn_prefabs"] = f"missing {miss[:6]}"
        # greyling_approaches / deer_flees are recorded but not compared: the AI's reaction in 8 s was not stable in A/A
        return (not d), (d or "spawn tables as vanilla")
    if name == "combat":   # the first club hit (the target staggers away after it), damage taken unblocked / blocked
        base = dict(base, club_first_hit=(base.get("club_hits") or [None])[0]); got = dict(got, club_first_hit=(got.get("club_hits") or [None])[0])
    if name == "skills":   # player-driven skills only: Clubs / Blocking XP depend on the Greydwarf's AI (hits landed, swings at the shield)
        strip = lambda x: dict(x, after_probes={k: v for k, v in (x.get("after_probes") or {}).items() if k in ("Run", "Jump", "Swim")})
        base, got = strip(base), strip(got)
    if name == "perf":
        b, g = base.get("fps_median"), got.get("fps_median")
        if not b or g is None: return None, "no reading"
        return g >= 0.7 * b, f"{g} fps (vanilla {b})"
    if name == "weather":
        b, g = dict(base), dict(got)
        d = _diff({k: v for k, v in b.items() if k != "envs"}, {k: v for k, v in g.items() if k != "envs"}, TOL["weather"])
        be, ge = [e[2:] for e in b.get("envs") or []], [e[2:] for e in g.get("envs") or []]   # is_day + environment per fixed skip
        if be != ge: d["envs"] = f"{be} -> {ge}"
        return (not d), (d or "day/night and weather as vanilla")
    d = _diff(base, got, TOL.get(name, {}))
    if not d: return True, "same as vanilla"
    return False, _short(d)


def item_result(spec, base, got):
    if "," in spec:
        parts = [item_result(s.strip(), base, got) for s in spec.split(",")]
        if any(p[0] is False for p in parts): return False, {s.strip(): p[1] for s, p in zip(spec.split(","), parts) if p[0] is False}
        if any(p[0] is None for p in parts): return None, {s.strip(): p[1] for s, p in zip(spec.split(","), parts) if p[0] is None}
        return True, "; ".join(str(p[1]) for p in parts)[:200]
    if spec == "log_errors":
        if not got.get("alive_at_end", False): return False, f"game crashed or hung: {[r for r in got.get('_runs', []) if not r.get('done')]}"
        b, g = base.get("log_errors"), got.get("log_errors")
        return (g is not None and b is not None and g <= b), f"{g} error lines vs vanilla {b}"
    p = spec.split(".")[0]
    b, g = base.get(p), got.get(p)
    if "." in spec and isinstance(b, dict) and isinstance(g, dict):
        fields = spec.split(".", 1)[1].split("+")
        b = {k: v for k, v in b.items() if k in fields}; g = {k: v for k, v in g.items() if k in fields or k == "error"}
    return compare(p, b, g)


