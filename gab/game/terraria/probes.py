"""Terraria (tModLoader) regression probes for gab: from the original probes_terraria.py (pool verified in game 2026-10-30,
tModLoader v2026.8.3.0 stable on Terraria 1.4.4.9) ported to a gab Seat. The measurements, the bridge mod (bridge/GAProbe = the
pool's GAProbe, unchanged), the sandbox runner (bridge/trp.sh: only its root is a parameter, TRP_ROOT) and compare() / item_result()
are the pool's; what changed is how commands reach the sandbox (seat.exec instead of a fixed `docker exec`) and the
instance folder (<root>/gab/probe-<label> instead of <root>/inst/<name>).

A session = 3 game starts (see the pool module): headless server (world generated WITH the mods, seed "gaprobe1", small; content /
worldgen / newplayer), client into the probe world on a private Xvfb (:151) through VirtualGL (inventory, perf, move, accessory,
dig, combat, time, npcs, craft, enemies, boss, save), client again (saveload). ~3.5 min. Results: `GAPROBE {json}` lines.
"""
import json, re, time
from pathlib import Path

VERSION = "tModLoader v2026.8.3.0 (stable) / Terraria 1.4.4.9, Steam app 1281930 (private copy of the Steam install)"
SERVER_GROUPS = ["content", "worldgen", "newplayer"]
CLIENT_GROUPS = ["inventory", "perf", "move", "accessory", "dig", "combat", "time", "npcs", "craft", "enemies", "boss", "save"]
ALL = SERVER_GROUPS + CLIENT_GROUPS + ["saveload"]
TIMEOUT = 420
ENTRY = re.compile(r"^\[\d\d:\d\d:\d\d\.\d+\] \[([^\]]*)/(\w+)\]")
# noise seen in vanilla runs (A/A 2026-10-30), independent of mods: tML's process monitor racing exiting processes
ERR_IGNORE = re.compile(r"GAPROBE|/proc/\d+/stat", re.I)


def log_error_entries(text):
    """tModLoader log entries (a header line + its continuation lines) at level ERROR / FATAL, or WARN entries that carry an
    exception (tML logs caught mod exceptions as 'Silently Caught Exception'); minus ERR_IGNORE noise."""
    out, cur = [], None
    for line in text.splitlines() + ["[00:00:00.000] [end/INFO]"]:
        m = ENTRY.match(line)
        if m:
            if cur and (cur[0] in ("ERROR", "FATAL") or (cur[0] == "WARN" and "Exception" in cur[1])) and not ERR_IGNORE.search(cur[1]):
                out.append(cur[1].replace("\n", " | ")[:400])
            cur = [m.group(2), line]
        elif cur:
            cur[1] += "\n" + line
    return out

def shq(s): return "'" + str(s).replace("'", "'\\''") + "'"


def groups_of(names):
    """probe names as the framework passes them (the part of a pool item's `probe:` before the first '.', e.g. "save,saveload",
    "craft,content", "newplayer,inventory,accessory", "log_errors") -> the session's groups"""
    out = []
    for n in names:
        for part in str(n).split(","):
            g = part.strip().split(".")[0]
            if g in ALL and g not in out: out.append(g)
    return out


class TRProbes:
    """the pool's session() on a gab Seat. root = the private tModLoader install (<root>/steamapps/common/tModLoader, bin/trp.sh,
    mods/GAProbe.tmod)"""

    def __init__(self, seat, root, log=lambda s: None):
        self.seat, self.root, self.log = seat, root, log

    def sh(self, cmd, timeout=110, check=True):
        r = self.seat.exec(f"export TRP_ROOT={self.root}; {cmd}", timeout=timeout)
        if check and r.returncode != 0:
            raise RuntimeError(f"sandbox command failed ({r.returncode}): {cmd[:200]}\n{r.stdout[-2000:]}\n{r.stderr[-2000:]}")
        return r.stdout

    def _read(self, path):
        got = {}
        for line in self.sh(f"cat {path} 2>/dev/null", check=False).splitlines():
            if line.startswith("GAPROBE "):
                try:
                    d = json.loads(line[8:]); got[d.pop("probe")] = d
                except ValueError:
                    pass
        return got

    def _detached(self, cmd, marker_file, marker, timeout):
        """start cmd detached in the sandbox and poll until marker appears in marker_file (long docker exec calls get cut)"""
        self.sh(f"setsid nohup bash -c {shq(cmd)} > /dev/null 2>&1 < /dev/null &")
        t0 = time.time()
        while time.time() - t0 < timeout:
            time.sleep(4)
            if marker in self.sh(f"tail -c 300 {marker_file} 2>/dev/null", check=False): return True
        return False

    def session(self, name, mods=(), only=None, inst=None):
        """fresh instance -> server step, client step, reload step -> parsed results (the pool's session(), same steps).
        mods = .tmod paths inside the sandbox (built by the adapter)."""
        R = self.root
        names = [n for n in ALL if only is None or n in only or (n == "saveload" and "save" in only)]
        inst = inst or f"{R}/gab/probe-{name}"
        res = {"version": VERSION, "mods": [str(m) for m in mods], "started": time.strftime("%Y-%m-%d %H:%M:%S")}
        self.sh(f"rm -rf {inst} && mkdir -p {inst}/save/Mods {inst}/shots && cp {R}/mods/GAProbe.tmod {inst}/save/Mods/ && bash {R}/bin/trp.sh display >/dev/null")
        enabled = ["GAProbe"]
        for m in mods:
            self.sh(f"cp {shq(m)} {inst}/save/Mods/")
            enabled.append(Path(m).stem)
        self.sh(f"echo {shq(json.dumps(enabled))} > {inst}/save/Mods/enabled.json")
        runs, t0 = [], time.time()
        srv = [g for g in SERVER_GROUPS if g in names] or ["newplayer"]   # the player file is always needed
        ok = self._detached(f"bash {R}/bin/trp.sh server {inst} {','.join(srv)}", f"{inst}/server.out", "EXIT=server", 300)
        got = self._read(f"{inst}/server.jsonl")
        runs.append({"step": "server", "groups": srv, "done": "_done" in got, "finished": ok})
        for g in srv:
            if g in names: res[g] = got.get(g, {"error": "no result (server step " + ("crashed" if "_done" not in got else "missing") + ")"})
        cli = [g for g in CLIENT_GROUPS if g in names]
        for tag, groups in (("run1", cli), ("run2", ["saveload"] if "saveload" in names else [])):
            if not groups: continue
            cmd = f"bash {R}/bin/trp.sh client {inst} {','.join(groups)} {tag}"
            self.sh(f"setsid nohup bash -c {shq(cmd)} > /dev/null 2>&1 < /dev/null &")
            t1, shot = time.time(), set()
            while time.time() - t1 < TIMEOUT:
                time.sleep(4)
                if "EXIT=" in self.sh(f"tail -c 200 {inst}/{tag}.out 2>/dev/null", check=False): break
                if tag == "run1":   # screenshots: the arena (perf) and the boss fight (after enemies)
                    cur = self.sh(f"grep -o '\"probe\":\"[a-z_]*\"' {inst}/{tag}.jsonl 2>/dev/null | tail -1", check=False)
                    for key in ("perf", "enemies"):
                        if f'"{key}"' in cur and key not in shot:
                            shot.add(key); self.sh(f"bash {R}/bin/trp.sh shot {inst}/shots/{key}.png", check=False)
            lg = self.sh(f"tail -c 300 {inst}/{tag}.out", check=False)
            got = self._read(f"{inst}/{tag}.jsonl")
            for g in groups:
                res[g] = got.get(g, {"error": f"no result ({'crashed' if '_done' not in got else 'missing'})" + (f": {got['_fatal']['error'][:300]}" if "_fatal" in got else "")})
            runs.append({"step": tag, "groups": groups, "done": "_done" in got, "exit": (re.findall(r"EXIT=(\d+)", lg) or ["?"])[-1],
                         "fatal": got.get("_fatal", {}).get("error")})
        res["_runs"] = runs
        res["_wall_s"] = round(time.time() - t0, 1)
        res["alive_at_end"] = all(r.get("done") for r in runs)
        log_all = self.sh(f"cat {inst}/server.log {inst}/run1.client.log {inst}/run2.client.log 2>/dev/null", check=False)
        err_lines = log_error_entries(log_all)
        res["log_errors"] = len(err_lines); res["_error_lines"] = err_lines[:20]
        res["_instance"] = inst
        return res


# ------------------------------------------------------------------ compare ------------------------------------------------------------------
def _close(a, b, rel, ab):
    if a is None or b is None: return a == b
    if isinstance(a, bool) or isinstance(b, bool) or not isinstance(a, (int, float)) or not isinstance(b, (int, float)): return a == b
    return abs(a - b) <= max(ab, rel * abs(a))


# (relative, absolute) tolerances from the A/A runs 2026-10-30; fields not listed must be equal
TOL = {
    "move": {"run_top_speed": (0.0, 0.05), "run_ticks_to_95pct": (0.0, 2), "stop_ticks": (0.0, 2), "stop_dist": (0.0, 0.3), "run_dist_2s_tiles": (0.02, 0.5),
             "jump_height_tiles": (0.02, 0.2), "jump_air_ticks": (0.0, 2), "hop_height_tiles": (0.05, 0.2), "fall_max_speed": (0.0, 0.05),
             "fall_ticks_30": (0.0, 2), "fall_damage_30": (0.0, 2), "grapple_latched_tick": (0.0, 3), "grapple_rise_tiles": (0.1, 1.0)},
    "accessory": {"boots_top_speed": (0.0, 1.6), "double_jump_height_tiles": (0.05, 0.5)},
    "dig": {k + "_ticks": (0.0, 2) for k in ("dirt", "stone", "copper", "wood", "hammer_wall")},
    "combat": {"hit_damage_mean": (0.2, 1.0), "slime_kb_speed": (0.3, 0.5), "player_kb_speed": (0.1, 0.3), "contact_damage": (0.2, 2), "contact_life_lost": (0.2, 2),
               "player_immune_ticks": (0.0, 2), "hits": (0.0, 1)},
    "time": {"ticks_per_300": (0.0, 1), "dusk_time": (0.0, 2)},
    "npcs": {"room_score": (0.0, 0)},
    "boss": {},
}
IGNORE = {"cavern_spot", "path", "boots_ticks_to_5"}


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


def _short(d):
    return dict(list(d.items())[:10], **({"more": len(d) - 10} if len(d) > 10 else {}))


def _content(base, got, fields):
    """Stock content: per-id stat hashes of vanilla items / NPCs / projectiles / tiles must match; every vanilla recipe must
    still exist (mods may add content and recipes)."""
    d = {}
    for k in ("items", "npcs", "projectiles", "tiles"):
        if fields and k not in fields: continue
        bm, gm = base.get(k) or {}, got.get(k) or {}
        bad = [i for i in bm if bm[i] != "random" and gm.get(i) != "random" and not (k == "projectiles" and i in RANDOM_PROJ) and gm.get(i) != bm[i]]
        if bad:
            nm = base.get("item_names") or {}
            d[k] = f"{len(bad)} changed: " + ", ".join((f"{i} {nm.get(i, '')}".strip() if k == "items" else i) for i in bad[:8])
    if not fields or "recipes" in fields:
        miss = sorted(set(base.get("recipes") or []) - set(got.get("recipes") or []))
        if miss: d["recipes"] = f"{len(miss)} vanilla recipes missing/changed: {miss[:3]}"
    return (not d), (d or "stock content as vanilla")


FAMILIES = [("Zombie", "Zombie"), ("Eye", "Eye"), ("Slime", "Slime"), ("Bat", "Bat"), ("Skeleton", "Skeleton"), ("Worm", "Worm"),
            ("Critter", "Bird|Bunny|Squirrel|Butterfly|Owl|Firefly|Mouse|Snail|Raven|Frog|Duck|Grasshopper|Stinkbug|Ladybug|Turtle|Rat")]


def _families(counts):
    out = {}
    for k, v in counts.items():
        fam = next((f for f, pat in FAMILIES if re.search(pat, k)), "other:" + k)
        out[fam] = out.get(fam, 0) + v
    return out


RANDOM_PROJ = {"221", "729", "976", "977", "1001"}   # SetDefaults rolls random scale/timeLeft per process (A/A 2026-10-30)


def _enemies(base, got):
    """Spawn tables (the game's spawner, NPC.SpawnNPC called 30000x per condition, Main.rand reseeded): grouped into families
    (zombie / eye / slime / bat / skeleton / worm / critter variants count together); the total per condition is within
    0.4x..2.5x of vanilla (A/A + negative-run spread up to 1.6x), every family that spawned >= 8 times in vanilla still
    spawns (A/A spread: see the pool header)."""
    d = {}
    for cond in ("surface_day", "surface_night", "cavern"):
        b, g = base.get(cond), got.get(cond)
        if not isinstance(b, dict): continue
        if not isinstance(g, dict): d[cond] = f"{g}"; continue
        tb, tg = sum(b.values()), sum(g.values())
        if not (0.4 * tb <= tg <= 2.5 * tb): d[cond + ".total"] = f"{tb} -> {tg}"
        fb, fg = _families(b), _families(g)
        lost = [k for k, v in fb.items() if v >= 8 and fg.get(k, 0) == 0]
        if lost: d[cond + ".lost"] = lost
        if base.get(cond + "_zones") != got.get(cond + "_zones"): d[cond + ".zones"] = f"{base.get(cond + '_zones')} -> {got.get(cond + '_zones')}"
    return (not d), (d or "spawn tables as vanilla")


def _npcs(base, got):
    """Town NPCs: the Guide talks; every vanilla shop entry is still sold at the same price (additions allowed); housing and move-in."""
    d = {}
    bs, gs = base.get("shops") or {}, got.get("shops") or {}
    for npc, items in bs.items():
        if not isinstance(items, list): continue
        g = gs.get(npc)
        if not isinstance(g, list): d["shop." + npc] = f"{g}"; continue
        miss = [x for x in items if x not in g]
        if miss: d["shop." + npc] = f"missing/repriced {miss[:6]}"
    rest = _diff({k: v for k, v in base.items() if k != "shops"}, {k: v for k, v in got.items() if k != "shops"}, TOL["npcs"])
    d.update(rest)
    return (not d), (d or "town NPCs as vanilla")


def compare(name, base, got):
    """-> (ok | None, detail)."""
    if not isinstance(got, dict) or not got: return None, "not probed"
    if not isinstance(base, dict) or not base: return None, "no baseline"
    if "error" in (base or {}): return None, f"baseline error: {str(base['error'])[:200]}"
    if "error" in got: return False, str(got["error"])[:300]
    if name == "content": return _content(base, got, got.get("_fields"))
    if name == "enemies": return _enemies(base, got)
    if name == "npcs": return _npcs(base, got)
    if name == "perf":
        b, g = base.get("update_ms_median"), got.get("update_ms_median")
        if None in (b, g): return None, "no reading"
        ok = g <= max(2 * b, b + 1.0) and (got.get("ups") or 0) >= 0.85 * (base.get("ups") or 60)
        return ok, f"update {g} ms (vanilla {b}), {got.get('ups')} updates/s (vanilla {base.get('ups')})"
    if name == "dig":   # drops: the tile's main drop must still drop (copper ore sometimes adds a random extra item)
        bb, gg = dict(base), dict(got); d = {}
        for k in [k for k in bb if k.endswith("_drops")]:
            x, y = set(bb.pop(k) or []), set(gg.pop(k, None) or [])
            if (x or y) and not (x & y): d[k] = f"{sorted(x)} -> {sorted(y)}"
        d.update(_diff(bb, gg, TOL["dig"]))
        return (not d), (d or "same as vanilla")
    if name == "craft":   # vanilla recipes stay available (a mod may add some)
        d = {}
        for k in ("hand_recipes", "bench_recipes"):
            miss = sorted(set(base.get(k) or []) - set(got.get(k) or []))
            if miss: d[k] = f"missing {miss}"
        d.update(_diff({k: v for k, v in base.items() if not k.endswith("_recipes")}, {k: v for k, v in got.items() if not k.endswith("_recipes")}, {}))
        return (not d), (d or "crafting as vanilla")
    if name == "boss":   # loot: the vanilla drop types (minus rare ones) must still drop
        d = _diff({k: v for k, v in base.items() if k != "loot_types"}, {k: v for k, v in got.items() if k != "loot_types"}, TOL["boss"])
        miss = sorted(set(base.get("loot_types") or []) - set(got.get("loot_types") or []) - BOSS_RARE)
        if miss: d["loot_types"] = f"missing {miss}"
        return (not d), (d or "Eye of Cthulhu as vanilla")
    if name == "combat":
        b, g = dict(base), dict(got)
        bl, gl = set(b.pop("slime_loot", []) or []), set(g.pop("slime_loot", []) or [])
        # gab 2026-10-31: how many of the 4 swings land and the slime's top speed after a hit depend on its
        # random hop (an airborne slime flies further, out of reach): the test sandbox NegFall run 4 -> 2 hits, 5.5 -> 7.7 px/tick with an
        # unchanged sword / slime. Compared instead: at least one hit lands; the hit's own knockback (hit_knockback) stays exact.
        bh, gh = b.pop("hits", None), g.pop("hits", None); b.pop("slime_kb_speed", None); g.pop("slime_kb_speed", None)
        d = _diff(b, g, TOL["combat"])
        if (bh or 0) > 0 and not gh: d["hits"] = f"{bh} -> {gh} (no hit landed)"
        if not (bl - SLIME_RARE) <= gl: d["slime_loot"] = f"{sorted(bl)} -> {sorted(gl)}"
        return (not d), (d or "same as vanilla")
    d = _diff(base, got, TOL.get(name, {}))
    if not d: return True, "same as vanilla"
    return False, _short(d)


BOSS_RARE = {1299, 1734, 2112, 2104, 3763, 3262}   # Binoculars, (A/A-varying) rare drops: see the pool header
SLIME_RARE = {29, 1309, 3176}                      # life crystal-like / slime staff (1/10000) style rare drops


def item_result(spec, base, got):
    """Pool item `probe:` spec on two session JSONs -> (ok | None, detail). `p.f1+f2` = fields of probe p; `a,b` = all of a, b."""
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
    if p == "content" and "." in spec and isinstance(g, dict) and "error" not in g:
        return compare(p, b, dict(g, _fields=spec.split(".", 1)[1].split("+")))
    if "." in spec and isinstance(b, dict) and isinstance(g, dict):
        fields = spec.split(".", 1)[1].split("+")
        b = {k: v for k, v in b.items() if k in fields}; g = {k: v for k, v in g.items() if k in fields or k == "error"}
    return compare(p, b, g)


