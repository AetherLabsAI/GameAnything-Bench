"""Minecraft regression probes, host side: names, and compare() of a modded run against the vanilla run (vanilla is the
oracle). The probes themselves run inside the sandbox (mcprobes.py, through `mcgab.py probes`), against a fresh instance
in the default world (seed 2024, survival, normal; the deliverable's start.yaml is ignored so vanilla and modded runs
see the same world; its jars, datapacks, resource packs and config are installed).

Pool item spec (games/minecraft/pool.yaml `probe:`): "name" = compare(name) with its tolerance; "name.f1+f2" = those fields
equal to vanilla. Tolerances from the A/A runs of 2026-10-31 (pool.yaml header): terrain 8% of the 97x97 cells may differ
by > 1 block; tick time <= 2x vanilla or + 8 ms; log errors <= vanilla.
"""
import json

ORDER = ["player_start", "terrain_spawn", "difficulty", "game_rules", "time_advances", "weather_changes", "tick_time", "monsters_allowed",
         "move_jump", "place_break", "combat", "inventory_use", "containers", "save_reload", "death_respawn"]
NAMES = set(ORDER) | {"log_errors"}


def _norm(x):
    """JSON round trip (tuples -> lists), "_" debug keys left out"""
    x = json.loads(json.dumps(x))
    return {k: v for k, v in x.items() if not k.startswith("_")} if isinstance(x, dict) else x


def compare(name, base, got):
    """one probe, vanilla base vs got -> (ok | None, detail)"""
    b, g = _norm(base), _norm(got)
    if name == "log_errors":
        if g.get("crash"): return False, "the game crashed: " + " | ".join(g.get("crash_report") or [])[:300]
        be, ge = int(b.get("errors") or 0), int(g.get("errors") or 0)
        return ge <= be, f"{ge} error entries in the game log vs vanilla {be}" + (f": {'; '.join((g.get('lines') or [])[:3])[:300]}" if ge > be else "")
    if name == "tick_time":
        bm, gm = b.get("mspt"), g.get("mspt")
        if bm is None or gm is None: return None, "no reading"
        return gm <= max(2 * bm, bm + 8), f"{gm:.1f} ms per tick vs vanilla {bm:.1f}"
    if name == "terrain_spawn":
        bh, gh = b.get("heights") or [], g.get("heights") or []
        if not bh or len(bh) != len(gh): return None, "no heightmap"
        n = sum(1 for r1, r2 in zip(bh, gh) for x, y in zip(r1, r2) if abs(x - y) > 1); tot = sum(len(r) for r in bh)
        return n / tot < 0.08, f"{n / tot:.1%} of the {len(bh)}x{len(bh)} columns around the world spawn differ in height"
    diff = {k: f"{b.get(k)} -> {g.get(k)}" for k in sorted(set(b) | set(g)) if b.get(k) != g.get(k)}
    return not diff, diff or "same as vanilla"


def item_result(spec, base, got):
    """pool `probe:` spec -> (ok | None, detail); None = could not test"""
    name, _, fields = spec.partition(".")
    b, g = base.get(name), got.get(name)
    if name == "log_errors" and got.get("_crashed_at"): return False, f"the game died during the probe {got['_crashed_at']}"
    if b is None: return None, "not in the vanilla baseline"
    if g is None: return None, (f"not run: the game died during {got['_crashed_at']}" if got.get("_crashed_at") else "not run")
    if isinstance(b, dict) and "error" in b: return None, f"vanilla baseline broken: {b['error']}"
    if isinstance(g, dict) and "error" in g: return None, f"probe error: {g['error']}"
    if not fields: return compare(name, b, g)
    b, g = _norm(b), _norm(g)
    diffs = {f: f"{b.get(f)} -> {g.get(f)}" for f in fields.split("+") if b.get(f) != g.get(f)}
    return not diffs, diffs or "same as vanilla"
