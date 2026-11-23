"""Cities: Skylines regression probes for gab: from the original probes_cities_skylines.py (written 2026-10-30) ported to a gab
Seat, + the groups transport and daynight (2026-11-05, for the Cities tasks: bus lines that stop and carry passengers, lighting
over the time of day). UNTESTED IN GAME (the game is not installed in any sandbox yet): `gab selfcheck cities_skylines` is the
first run (A/A + the negative mods). Measurements: the bridge mod CSProbe (bridge/CSProbe), the runner
bridge/csp.sh (root / game folder / display / start script are parameters, a private 1280x800 display, the private game folder's
colossal.cgs copied from the Steam folder instead of written), bridge/cs-start.sh (= the lab's start-game.sh + TMPDIR),
bridge/cgs.py.

Group dependencies (run1 builds one new city, in this order): roads makes the probe area (the main road from the highway end,
its side roads) that zoning / services / growth use; growth needs zoned, serviced roads to grow anything; save writes the autosave
and its marker; run2 (saveload) reads both back. groups_of() adds what a group needs.

The probe-city fixture (a session that picks CS-TRANSPORT must not regrow a city): FIXTURE_GROUPS (transport)
need a populated city but do not measure its growth: when run1 grows a city anyway (growth picked) they run in it; otherwise
they run in their own game start (runF) on a city grown ONCE per game
version and CSProbe build by a vanilla session (make_fixture: roads, zoning, services, growth, then the group "fixture" saves it as
"gafixture" + fixture.cfg with its road layout). The adapter caches the fixture in the sandbox and on the host (games_dir) and hands
the same one to the vanilla baseline and to the modded run; the session records its hash as _fixture and item_result() answers
could-not-test when base and mod ran on different fixtures. The mod under test IS loaded in runF (it changes how buses behave in
the fixture city, not how the city grew: growth items keep measuring growth with the mod in run1). UNTESTED IN GAME, like the rest.
"""
import json, re, time

VERSION = "Cities: Skylines 1.21.x Linux (Steam 255710), map Green Plains"
GROUPS = ["content", "sim", "camera", "infoviews", "daynight", "roads", "zoning", "services", "growth", "fixture", "transport", "save"]   # CSProbe's order
NEEDS = {"zoning": ["roads"], "services": ["roads"], "growth": ["roads", "zoning", "services"], "fixture": ["growth"], "save": ["growth"]}
FIXTURE_GROUPS = ["transport"]                    # run on the loaded probe-city fixture (runF), not in run1
FIXTURE_MAKE = ["roads", "zoning", "services", "growth", "fixture"]
TURBO = True                                      # CSProbe may run the simulation faster than speed 3 if the game allows it (FastSim)
ALIAS = {"saveload": "save", "perf": "sim"}       # result keys / pool probe names made by another group


def groups_of(names):
    """probe names as the framework passes them (the part of a pool item's `probe:` before the first '.', e.g.
    "services,growth", "saveload", "perf", "log_errors") -> the session's groups in CSProbe's order, dependencies included"""
    want = set()
    def add(g):
        if g in want or g not in GROUPS: return
        want.add(g)
        for d in NEEDS.get(g, []): add(d)
    for n in names:
        for part in str(n).split(","):
            g = part.strip().split(".")[0]
            if g != "fixture": add(ALIAS.get(g, g))      # made only by make_fixture
    return [g for g in GROUPS if g in want]
ERR = re.compile(r"Exception|\[Error\]|Error building|error CS\d+", re.I)
ERR_IGNORE = re.compile(r"CSPROBE|UnauthorizedAccessException|Steam|PopsApi|Paradox|EOS|telemetry", re.I)   # see the pool header


def shq(s): return "'" + str(s).replace("'", "'\\''") + "'"


class CSProbes:
    """the pool's session() on a gab Seat. root = <home>/pool-probes/cities (CSProbe.dll, bin/csp.sh, bin/cs-start.sh, bin/cgs.py)"""

    def __init__(self, seat, root, game_dir, display=":158"):
        self.seat, self.root, self.gd, self.disp = seat, root, game_dir, display

    def sh(self, cmd, timeout=110, check=True):
        r = self.seat.exec(f"export CSP_ROOT={self.root} CSP_GAME={shq(self.gd)} CSP_DISPLAY={self.disp}; {cmd}", timeout=timeout)
        if check and r.returncode != 0:
            raise RuntimeError(f"sandbox command failed ({r.returncode}): {cmd[:200]}\n{r.stdout[-2000:]}\n{r.stderr[-2000:]}")
        return r.stdout

    def _read(self, path):
        got = {}
        for line in self.sh(f"cat {path} 2>/dev/null", check=False).splitlines():
            if line.startswith("CSPROBE "):
                try: d = json.loads(line[8:]); got[d.pop("probe")] = d
                except ValueError: pass
        return got

    def session(self, name, mods=(), only=None, fixture=None):
        """one probe session -> results by group. fixture: sandbox folder of the probe-city fixture (gafixture*.crp, fixture.cfg,
        fixture.json; make_fixture) for FIXTURE_GROUPS; without it those groups answer an error"""
        R = self.root; sh = self.sh
        groups = [g for g in GROUPS if only is None or g in only]
        g1 = [g for g in groups if g not in FIXTURE_GROUPS]; gf = [g for g in groups if g in FIXTURE_GROUPS]
        if "growth" in g1: g1, gf = groups, []         # run1 grows a city anyway: transport runs in it (no extra game start; the
        #                                                 baseline is cached per probe set, so base and mod still use the same plan)
        # per-container suffix (2026-11-16): CS_INSTANCE=gaprobe-<dir name> is matched across the shared hub pid namespace, so the
        # fixed "probe-mod" made parallel evals see "already running" and wait out the 1900 s timeout with no result
        tag = (self.sh("hostname", check=False).strip() or "x")[:12]
        inst = f"{R}/gab/probe-{name}-{tag}"
        res = {"version": VERSION, "mods": [str(m) for m in mods], "started": time.strftime("%Y-%m-%d %H:%M:%S")}
        sh(f"rm -rf {inst} && mkdir -p {inst} && bash {R}/bin/csp.sh display > /dev/null")
        runs, t0 = [], time.time()
        fx = None
        if gf and fixture:
            try: fx = json.loads(sh(f"cat {fixture}/fixture.json 2>/dev/null", check=False) or "null")
            except ValueError: fx = None
        if gf and not fx:
            for k in gf: res[k] = {"error": f"no probe-city fixture ({fixture or 'none given'})"}
            gf = []
        if fx: res["_fixture"] = fx.get("sha256")
        plan = [("run1", g1, ""), ("run2", ["saveload"] if "save" in g1 else [], ""),
                ("runF", gf, f"CSP_START=load:gafixture CSP_FIXTURE={shq(fixture)} " if gf else "")]
        for mode, g, env in plan:
            if not g: continue
            sh(f"{env}CSP_TURBO={int(TURBO)} bash {R}/bin/csp.sh prep {inst} {mode} {','.join(g)} " + " ".join(shq(m) for m in mods))
            sh(f"setsid nohup bash {R}/bin/csp.sh run {inst} {mode} > {inst}/{mode}.runner.log 2>&1 < /dev/null &")
            t1, shots = time.time(), set()
            while time.time() - t1 < 1900:
                time.sleep(6)
                if f"EXIT={mode}" in sh(f"cat {inst}/{mode}.runner.out 2>/dev/null", check=False): break
                cur = sh(f"grep -o '\"probe\":\"[a-z_]*\"' {inst}/{mode}.jsonl 2>/dev/null | tail -1", check=False)
                for key in ("_entered", "services", "growth", "transport"):
                    if f'"{key}"' in cur and key not in shots:
                        shots.add(key); sh(f"bash {R}/bin/csp.sh shot {inst}/shot_{mode}_{key.strip('_')}.png", check=False)
                if time.time() - t1 > 300 and "menu" not in shots and not cur:
                    shots.add("menu"); sh(f"bash {R}/bin/csp.sh shot {inst}/shot_{mode}_nocity.png", check=False)
            got = self._read(f"{inst}/{mode}.jsonl")
            for k in g: res[k] = got.get(k, {"error": "no result" + (f": {got['_fatal']['error'][:300]}" if "_fatal" in got else "")})
            if mode == "run1": res["_entered"] = got.get("_entered"); res["_boot"] = got.get("_boot")
            runs.append({"step": mode, "done": "_done" in got, "fatal": got.get("_fatal", {}).get("error")})
            if mode == "runF": res["_fixture_city"] = got.get("_fixture")
            if "_fatal" in got:
                res["_fatal"] = got["_fatal"]; break       # e.g. a legal dialog: never clicked, the session stops here
        res["_runs"] = runs; res["_wall_s"] = round(time.time() - t0, 1)
        res["alive_at_end"] = all(r["done"] for r in runs)
        lg = sh(f"cat {inst}/player-run1.log {inst}/player-run2.log {inst}/player-runF.log 2>/dev/null", check=False)
        errs = [l[:300] for l in lg.splitlines() if ERR.search(l) and not ERR_IGNORE.search(l)]
        res["log_errors"] = len(errs); res["_error_lines"] = errs[:20]; res["_instance"] = inst
        return res

    def make_fixture(self, dest, label="fixture"):
        """grow the probe city once (vanilla: no mods) and keep it in the sandbox folder dest: gafixture*.crp (the game's save),
        fixture.cfg (its road layout for CSProbe), fixture.json {sha256, population, segments, made} -> fixture.json dict or {"error"}"""
        s = self.session(label, (), only=FIXTURE_MAKE)
        f = s.get("fixture") or {}
        if "error" in f or not f.get("saved"): return {"error": f"the fixture session did not save the city: {str(f.get('error') or s.get('_fatal'))[:300]}"}
        inst = s["_instance"]
        out = self.sh(f"rm -rf {dest} && mkdir -p {dest} && cp {inst}/fixture.cfg {dest}/ && "
                      f"find {inst}/data -name 'gafixture*.crp' -exec cp {{}} {dest}/ \\; && ls {dest}/gafixture*.crp >/dev/null && "
                      f"cat {dest}/gafixture*.crp | sha256sum | cut -c1-16", check=False).strip()
        if not re.fullmatch(r"[0-9a-f]{16}", out.splitlines()[-1] if out else ""):
            return {"error": f"no gafixture save / fixture.cfg after the fixture session: {out[-300:]}"}
        info = {"sha256": out.splitlines()[-1], "population": f.get("population"), "segments": f.get("segments"), "made": time.strftime("%Y-%m-%dT%H:%M:%S"),
                "growth": {k: (s.get("growth") or {}).get(k) for k in ("population", "zoned_buildings", "game_days", "wall_s")}}
        self.seat.put(f"{dest}/fixture.json", json.dumps(info))
        return info


# ------------------------------------------------------------------ compare ------------------------------------------------------------------
def _close(a, b, rel, ab):
    if a is None or b is None: return a == b
    if isinstance(a, bool) or isinstance(b, bool) or not isinstance(a, (int, float)) or not isinstance(b, (int, float)): return a == b
    return abs(a - b) <= max(ab, rel * abs(a))


# (relative, absolute) tolerances: FIRST GUESSES (2026-10-30 / 2026-11-05), no A/A run exists yet -- set them from the selfcheck's A/A
# sessions (); fields not listed must be equal
TOL = {
    "sim": {"ratio_3_to_1": (0.35, 0.5), "ratio_2_to_1": (0.35, 0.3), "speed1_game_hours_per_s": (0.3, 0.5), "speed1_game_s_per_frame": (0.02, 0.1),
            "speed2_game_s_per_frame": (0.02, 0.1), "speed3_game_s_per_frame": (0.02, 0.1)},
    "camera": {"pos_err": (0.0, 5), "size_err": (0.0, 5), "angle_err": (0.0, 3), "cam_height_above_ground": (0.15, 20)},
    "roads": {"cost": (0.02, 50), "zone_blocks_created": (0.1, 2)},
    "zoning": {"res_cells": (0.1, 8), "com_cells": (0.1, 8), "ind_cells": (0.1, 8), "demand_res": (0.0, 30), "demand_com": (0.0, 30), "demand_work": (0.0, 30)},
    "daynight": {"hours_per_frame": (0.05, 2e-5), "noon_sun": (0.15, 0.05), "midnight_sun": (0.3, 0.05), "noon_ambient": (0.15, 0.05),
                 "midnight_ambient": (0.3, 0.05)},
    "transport": {"passengers": (0.5, 2), "bus_capacity": (0.5, 60), "buses_peak": (0.5, 2), "line_vehicles": (0.6, 2), "game_days": (0.0, 1.5)},
    "growth": {"population": (0.5, 30), "households": (0.5, 15), "zoned_buildings": (0.4, 6), "res_buildings": (0.5, 5), "com_buildings": (0.6, 4), "ind_buildings": (0.6, 4),
               "no_power": (1.0, 6), "no_water": (1.0, 6), "no_road": (0.0, 2), "abandoned": (0.0, 2), "power_capacity": (0.05, 5), "power_use": (0.6, 50),
               "water_capacity": (0.05, 5), "water_use": (0.6, 50), "garbage_capacity": (0.05, 5), "max_vehicles": (0.6, 15), "max_citizen_instances": (0.6, 30),
               "cash_change": (0.0, 15000), "income_acc": (0.7, 500), "game_days": (0.0, 2)},
}
IGNORE = {"samples", "wall_s", "highway_end_pos"}
REV = "2026-11-15"                                # the adapter's probe_rev (regression.baseline's cache key): older baselines lack bus_capacity*


def _diff(b, g, tol, path=""):
    d = {}
    if isinstance(b, dict) and isinstance(g, dict):
        for k in sorted(set(b) | set(g)):
            if str(k).startswith("_") or k in IGNORE or re.match(r"speed\d_fps$|speed[23]_game_hours_per_s$", str(k)): continue
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
    if name == "content":   # stock prefabs keep their stats (additions allowed); default taxes and start money
        d = {}
        for k in ("buildings", "nets", "vehicles"):
            bad = [i for i, v in (base.get(k) or {}).items() if (got.get(k) or {}).get(i) != v]
            if bad: d[k] = f"{len(bad)} changed/missing: {bad[:6]}"
        for k in ("tax_res_low", "tax_com_low", "tax_ind", "start_cash"):
            if base.get(k) != got.get(k): d[k] = f"{base.get(k)} -> {got.get(k)}"
        return (not d), (d or "stock content as vanilla")
    if name == "perf":
        b, g = base.get("speed3_fps"), got.get("speed3_fps")
        if not b or g is None: return None, "no reading"
        return g >= 0.6 * b, f"{g} simulation frames/s at speed 3 (vanilla {b})"
    if name == "infoviews":
        miss = sorted(set(base.get("opened") or []) - set(got.get("opened") or []))
        return (not miss), (f"no longer opens: {miss}" if miss else f"{len(base.get('opened') or [])} info views open")
    # a probe whose VANILLA run measured nothing cannot tell a broken game from a working one: could not test (2026-11-05, the
    # unverified groups: a bus line with no buses, a day / night cycle whose clock does not move)
    if name == "transport" and not base.get("buses_peak"): return None, f"baseline: no bus ran on the probe line ({base.get('depot')}, {base.get('line')})"
    if name == "transport":
        # 2026-11-15 (selfcheck 20261115-072225: NegBus, 0 seats per bus, passed in a city where nobody boarded): the seats per bus are
        # measured with or without passengers and must be equal; the passenger measures need a baseline in which somebody boarded
        if "bus_capacity_per_bus" in base and base.get("bus_capacity_per_bus") != got.get("bus_capacity_per_bus"):
            return False, {"bus_capacity_per_bus": f"{base.get('bus_capacity_per_bus')} -> {got.get('bus_capacity_per_bus')}"}
        if not base.get("passengers"):
            d = {k: v for k, v in _diff(base, got, TOL["transport"]).items() if k != "passengers"}
            if d: return False, _short(d)
            return None, "baseline: no passenger boarded"
    if name == "daynight" and not base.get("hours_per_frame"): return None, "baseline: the time of day did not advance"
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
    if spec == "perf": return compare("perf", base.get("sim"), got.get("sim"))
    p = spec.split(".")[0]
    if p in FIXTURE_GROUPS and isinstance(got.get(p), dict) and "error" not in got[p] and base.get("_fixture") != got.get("_fixture"):
        return None, f"base and mod ran on different probe-city fixtures ({base.get('_fixture')} / {got.get('_fixture')}): refresh the baseline"
    b, g = base.get(p), got.get(p)
    if "." in spec and isinstance(b, dict) and isinstance(g, dict):
        fields = spec.split(".", 1)[1].split("+")
        b = {k: v for k, v in b.items() if k in fields}; g = {k: v for k, v in g.items() if k in fields or k == "error"}
    return compare(p, b, g)


