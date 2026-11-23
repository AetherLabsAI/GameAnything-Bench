"""Gate probes: the regression checks are a GATE (not a scored column, not picked per task).
After the build + install + load check (gab/eval/run.py gate_check) the modded game must also
  - run stably: the game's STABLE pool item (probe `log_errors`); a real crash / hang fails, more non-fatal error lines than vanilla
    do not (stable()), and
  - still contain the host game: one or two basic stock-content items (a stock level / map / menu loads, the player can move or act)
    must still find their stock content (present()). Partial changes (a changed opening, other movement numbers, a repurposed
    weapon, added content) pass: the probe only has to reach the stock content, not reproduce vanilla's numbers. Only a mod that
    deletes or replaces the host game wholesale fails.
The items are the `gate:` list of benchmark/pools/<game>/pool.yaml (the log_errors item = the stability check, the others = host-present).
Vanilla baseline cached per game version, site and probe set; every probe run on a fresh instance; a check that does not pass is
re-run once on a fresh modded instance and fails only if it fails twice. A check whose VANILLA baseline is broken could not run
because of the harness: the gate is then undecided (pass None, fail "infra") and the trial becomes infra_error, not a fail.
-> evaluate(): (gate block, game minutes); the block goes into result.json `gate` (checks = the raw evidence, for diagnostics)."""
import hashlib, socket, re, time
from pathlib import Path

import yaml

from .. import config
from ..util import log, rjson, wjson

FAILS = ("build", "load", "unstable", "host_removed")       # result.json gate.fail
# error kinds that are fatal even when the process survived (a new one fails the stability check); other new error lines do not
FATAL = re.compile(r"\bfatal\b|segmentation fault|SIGSEGV|SIGABRT|SIGBUS|core dumped|stack overflow|out of memory|OutOfMemory"
                   r"|\bpanic(?:ked)?\b|access violation", re.I)


def pool(game): return yaml.safe_load((config.REPO / "benchmark" / "pools" / game / "pool.yaml").read_text())


def gate_items(game):
    """-> (stable item, [host-present items], verified): the pool's `gate:` list; verified = every gate item is `ready`"""
    p = pool(game); allit = {i["id"]: i for i in (p.get("always") or []) + (p.get("pool") or [])}
    ids = p.get("gate") or []
    miss = [i for i in ids if i not in allit]
    if miss: raise ValueError(f"benchmark/pools/{game}/pool.yaml: gate items not in the pool: {miss}")
    its = [allit[i] for i in ids]
    st = [i for i in its if i["probe"] == "log_errors"]
    if len(st) != 1: raise ValueError(f"benchmark/pools/{game}/pool.yaml: the gate needs exactly one log_errors (STABLE) item, got {[i['id'] for i in st]}")
    host = [i for i in its if i["probe"] != "log_errors"]
    if not host: raise ValueError(f"benchmark/pools/{game}/pool.yaml: the gate has no host-present item")
    return st[0], host, all(i.get("status") == "ready" for i in its)


def site(ad):
    """where the probes run: the seat's sandbox, else this machine (trials of one machine share its speed). Baselines are kept
    per site (probes can run several times slower on one machine than another, so a baseline from a fast machine against a
    modded run on a slow one fails the perf items)"""
    sb = getattr(getattr(ad, "seat", None), "sandbox", None)
    return sb if config.get("host") == "pool" and sb else f"host-{socket.gethostname()}"


def baseline(ad, names, refresh=False):
    ver = ad.version()["build"]
    rev = getattr(ad, "probe_rev", None)     # an adapter whose probe outputs changed bumps it: older cached baselines no longer match
    key = hashlib.sha256((",".join(sorted(names)) + (f"@{rev}" if rev else "")).encode()).hexdigest()[:12]
    f = config.path("games_dir") / ad.name / "baseline" / re.sub(r"[^\w.-]+", "_", ver) / re.sub(r"[^\w.-]+", "_", site(ad)) / f"{key}.json"
    if f.exists() and not refresh: return rjson(f), str(f)
    log(f"{ad.name}: vanilla baseline for {sorted(names)} (version {ver})")
    b = ad.run_probes(names, None, label="vanilla")
    b["_meta"] = {"version": ver, "site": site(ad), "names": sorted(names), "at": time.strftime("%Y-%m-%dT%H:%M:%S")}
    if not all(isinstance(v, dict) and "error" in v for k, v in b.items() if not k.startswith("_") and k != "log_errors"): wjson(f, b)
    return b, str(f)


def probe_names(spec):
    """pool `probe:` spec -> the probe runner names it needs (`a.f1+f2,b` -> a, b; `render.menu` -> render)"""
    return {p.split(".")[0] for p in spec.split(",")}


# ---- stability
def _new(got, base):
    """entries of got that are not in base (lists of names / tags)"""
    b = set(map(str, base or [])); return [x for x in (got or []) if str(x) not in b]


def crash_signals(base, got):
    """crashes / hangs the probe runners report, new against vanilla -> [descriptions], or None when the session carries none of
    the known crash fields (then the STABLE item's own verdict stands). Known fields, by game: alive_at_end (Celeste 64, Terraria,
    Valheim, Cities, Xonotic), _crashed_at (Xonotic, Minecraft), _finished/_exit (Godot TPS), _runs[].crashed (SuperTuxKart),
    per-probe _crashed + _error_kinds (Tanks of Freedom II), log_errors.{crashed, crash, alive, not_finished} (Flare, Colobot,
    Widelands, Tuxemon, Stardew, Minecraft)"""
    base = base if isinstance(base, dict) else {}
    out, known = [], False
    if "alive_at_end" in got:
        known = True
        if got["alive_at_end"] is False and base.get("alive_at_end") is not False: out.append("the game was not alive at the end of the probe run")
    if got.get("_crashed_at"): known = True; out.append(f"the game died during {got['_crashed_at']}")
    if "_finished" in got:
        known = True
        if got["_finished"] is False and base.get("_finished") is not False: out.append(f"the game did not finish the probe run (exit {got.get('_exit')})")
    if isinstance(got.get("_runs"), list) and any(isinstance(r, dict) and "crashed" in r for r in got["_runs"]):
        known = True
        bc = {r.get("tag") for r in base.get("_runs") or [] if isinstance(r, dict) and r.get("crashed")}
        cr = [r.get("tag") for r in got["_runs"] if isinstance(r, dict) and r.get("crashed") and r.get("tag") not in bc]
        if cr: out.append(f"the game crashed in {cr[:5]}")
    pc = {k: v for k, v in got.items() if isinstance(v, dict) and ("_error_kinds" in v or "_crashed" in v)}
    if pc:
        known = True
        cr = {k: v["_crashed"] for k, v in pc.items() if v.get("_crashed") and not (base.get(k) or {}).get("_crashed")}
        if cr: out.append(f"crash / timeout in {cr}")
    le, bl = got.get("log_errors"), base.get("log_errors") if isinstance(base.get("log_errors"), dict) else {}
    if isinstance(le, dict):
        if "crashed" in le:
            known = True
            c = le["crashed"]; c = _new(c, bl.get("crashed")) if isinstance(c, list) else c
            if c: out.append(f"crashed / hung in {c}")
        if "crash" in le:
            known = True
            if le["crash"] and not bl.get("crash"): out.append("the game crashed: " + " | ".join(le.get("crash_report") or [])[:300])
        if "alive" in le:
            known = True
            if le["alive"] is False and bl.get("alive") is not False: out.append("the game exited")
        if "not_finished" in le:
            known = True
            nf = _new(le["not_finished"], bl.get("not_finished"))
            if nf: out.append(f"runs that did not finish (crashed or hung): {nf}")
    return out if known else None


def error_kinds(d):
    """the error lines / kinds a session reports, digits blanked (one kind per message)"""
    if not isinstance(d, dict): return set()
    xs = list(d.get("_error_lines") or [])
    le = d.get("log_errors")
    if isinstance(le, dict):
        for k in ("lines", "_lines", "signatures"): xs += list(le.get(k) or [])
        if isinstance(le.get("errors"), dict): xs += list(le["errors"])
    for v in d.values():
        if isinstance(v, dict): xs += list(v.get("_error_kinds") or [])
    return {re.sub(r"\d+", "#", str(x)) for x in xs}


def stable(ad, spec, base, got):
    """stability check -> (ok | None, detail). Starts from the STABLE item's verdict (ad.compare); a failure stands when the game
    crashed or hung or logged a new FATAL error kind, or when the session reports none of the known crash fields; a failure that
    is only more / new non-fatal error lines than vanilla passes ("no real process crash or hang, no new fatal error kinds"). None = the vanilla baseline itself is broken (harness)."""
    if not isinstance(base, dict) or (isinstance(base.get("log_errors"), dict) and set(base["log_errors"]) == {"error"}):
        return None, f"vanilla baseline: {(base or {}).get('log_errors')}"
    ok, det = ad.compare(spec, base, got)
    if ok is not False: return ok, det
    cr = crash_signals(base, got)
    if cr is None: return False, det
    if cr: return False, {"crash": cr, "item": det}
    same = crash_signals({}, got)
    if same: return None, f"the game crashed / hung on vanilla too ({crash_signals({}, base)}): {same}"
    fatal = sorted(k for k in error_kinds(got) - error_kinds(base) if FATAL.search(k))
    if fatal: return False, {"new_fatal_errors": fatal[:5], "item": det}
    return True, {"stable": "no crash or hang, no new fatal error kind", "tolerated": det}


# ---- host present
def _part(d, p):
    """session dict -> the output of probe p (`render.menu` -> d["render"]["menu"])"""
    if "." in p and p.split(".")[0] == "render":
        r = d.get("render"); return r.get(p.split(".")[1]) if isinstance(r, dict) else None
    return d.get(p.split(".")[0])


def _err(x):
    if isinstance(x, dict):
        if x.get("error"): return str(x["error"])
        if x.get("_crashed") and len([k for k in x if not str(k).startswith("_")]) == 0: return f"crashed: {x['_crashed']}"
        if not x: return "empty probe output"
    return None


def _loads(x, path=""):
    """per-map / per-level load flags in a probe output -> {path: loaded}: `loaded: bool` leaves (Celeste 64 maps, Colobot
    stock_levels, Xonotic maps_load) and "ok" / other strings in a `tracks` dict (SuperTuxKart)"""
    out = {}
    if isinstance(x, dict):
        for k, v in x.items():
            if k == "loaded" and isinstance(v, bool): out[path] = v
            elif path.endswith("tracks") and isinstance(v, str): out[f"{path}.{k}"] = v == "ok"
            elif isinstance(v, dict): out.update(_loads(v, f"{path}.{k}"))
    return out


def present(spec, base, got):
    """host-present check -> (ok | None, detail): every probe of the spec reached its stock content on the modded game (its output
    exists and carries no error, and the spec's top-level fields that vanilla reports are there). The numbers are NOT compared
    with vanilla: a changed opening, movement or content passes. None = the vanilla baseline is broken (harness)."""
    seen = []
    for part in spec.split(","):
        p = part if part.startswith("render.") else part.split(".")[0]
        fields = part.split(".", 1)[1].split("+") if "." in part and not part.startswith("render.") else []
        b, g = _part(base or {}, p), _part(got or {}, p)
        if b is None or _err(b): return None, f"{p}: vanilla baseline broken: {_err(b) or 'not in the baseline'}"
        if g is None:
            why = (got or {}).get("error") or ((got or {}).get("render") or {}).get("error") if isinstance(got, dict) else None
            return False, f"{p}: not reached on the modded game (the probe run ended before it" + (f": {why})" if why else ")")
        if isinstance(g, dict) and g.get("not_tested") and not _err(g): return None, f"{p}: could not test: {g['not_tested']}"
        if _err(g): return False, f"{p}: {_err(g)}"
        if isinstance(b, dict) and isinstance(g, dict):
            lost = [f for f in fields if b.get(f) is not None and g.get(f) is None]
            if lost: return False, f"{p}: stock content not found: {lost} missing"
        bl, gl = _loads(b), _loads(g)                 # per-map / per-level load flags: at least one stock one must still load
        okb = {k for k, v in bl.items() if v}
        if okb and not any(gl.get(k) for k in okb): return False, f"{p}: none of the {len(okb)} stock maps / levels that load in vanilla loads"
        seen.append(p + (f" ({sum(1 for k in okb if gl.get(k))}/{len(okb)} stock maps / levels load)" if okb else ""))
    return True, f"stock content reached: {', '.join(seen)}"


def na(r):
    """a check result (ok, detail) the adapter calls not applicable: detail {"not_applicable": why} (e.g. a
    Tuxemon mod that changes the opening and every map of the walk routes). Excluded from the gate: neither a pass nor a fail."""
    return r[0] is None and isinstance(r[1], dict) and "not_applicable" in r[1]


def applicable(ad, spec, base, got):
    """host-present item -> None, or (None, {"not_applicable": why}) when the probe output says so or the adapter's own compare
    does (Tuxemon decides it there, e.g. a stock-start warp with no leg on maps the mod leaves alone)"""
    for part in spec.split(","):
        g = _part(got or {}, part if part.startswith("render.") else part.split(".")[0])
        if isinstance(g, dict) and "not_applicable" in g: return None, {"not_applicable": g["not_applicable"]}
    try: r = ad.compare(spec, base, got) if getattr(ad, "compare", None) else None
    except Exception: r = None
    return r if r and na(r) else None


# ---- the gate
def host_gone(spec, got):
    """the probe runner says the mod removed the host game itself (godot-tps gt_probe.gd `_host_gone`: "host main scene removed by
    the mod") for a part of this spec"""
    for part in spec.split(","):
        g = _part(got or {}, part if part.startswith("render.") else part.split(".")[0])
        if isinstance(g, dict) and g.get("host_removed"): return True
    return False


def decide(checks):
    """[{kind: stable | host, ok, excluded?, host_gone?}] -> (pass, fail): excluded (not applicable) checks do not count; a host
    check whose runner reported the host game removed -> host_removed (even when the run also looks unstable: an honest
    "host removed" error); a broken vanilla baseline -> (None, "infra"); else unstable before host_removed"""
    checks = [c for c in checks if not c.get("excluded")]
    vb = lambda c: c["ok"] is None and (c["kind"] == "stable" or c.get("baseline_broken", True))
    if any(c["kind"] == "host" and c["ok"] is False and c.get("host_gone") for c in checks): return False, "host_removed"
    if any(vb(c) for c in checks): return None, "infra"
    if any(c["kind"] == "stable" and c["ok"] is False for c in checks): return False, "unstable"
    # host present = ANY host check reaches stock content (only a wholesale removal fails). A host check that
    # could not be tested on the MODDED game while vanilla is fine (baseline_broken False) is not infra: it is not a pass either
    hosts = [c for c in checks if c["kind"] == "host"]
    if hosts and not any(c["ok"] is True for c in hosts): return False, "host_removed"
    return True, None


def evaluate(task, ad, mod_dir, out_dir):
    """-> (gate block {pass, fail, verified, checks, baseline, reason}, game_minutes)"""
    t0 = time.time(); out = Path(out_dir); out.mkdir(parents=True, exist_ok=True)
    st, host, verified = gate_items(task.game)
    its = [("stable", st), *[("host", h) for h in host]]
    names = sorted(set().union(*(probe_names(i["probe"]) for _, i in its)) | {"log_errors"})
    B = {}; B["base"], bpath = baseline(ad, names)
    check = lambda kind, i, got: stable(ad, i["probe"], B["base"], got) if kind == "stable" else \
        (applicable(ad, i["probe"], B["base"], got) or present(i["probe"], B["base"], got))
    # a regate replays saved sessions: leave them as they are. NOT getattr(ad, "replay"): every game adapter has a replay() method
    # (trace replay), which made this False on real evaluations (probes_mod.json never written, no baseline refresh)
    keep = getattr(ad, "replay_sessions", False) is not True
    got = ad.run_probes(names, mod_dir, label="mod")
    if keep: wjson(out / "probes_mod.json", got)
    res = {i["id"]: check(k, i, got) for k, i in its}
    if any(r[0] is None and not na(r) for r in res.values()) and getattr(ad, "run_probes", None) and keep:
        log(f"gate probes: vanilla baseline broken for {[i for i, r in res.items() if r[0] is None and not na(r)]}: running the baseline again")
        B["base"], bpath = baseline(ad, names, refresh=True)                # a cached baseline with one failed probe: once more
        res = {i["id"]: check(k, i, got) for k, i in its}
    first = dict(res)
    # a check that did not pass is re-run once on a fresh modded instance; it fails only if it fails twice.
    # Not the probes the adapter marks `_deterministic` (failure certain to repeat: godot-tps, the mod removed the host main scene)
    det = set(got.get("_deterministic") or []) if isinstance(got, dict) else set()
    modcnt = lambda r: r[0] is None and not na(r) and "vanilla" not in str(r[1])     # could not test on the MODDED game only
    redo = [(k, i) for k, i in its if (res[i["id"]][0] is False or modcnt(res[i["id"]])) and not (k == "host" and probe_names(i["probe"]) <= det)]
    if redo:
        rn = sorted(set(names) - det | {"log_errors"})
        log(f"gate probes: re-running {rn}" + (f" (not the deterministic {sorted(det)})" if det else ""))
        got2 = ad.run_probes(rn, mod_dir, label="mod-rerun")
        if keep: wjson(out / "probes_mod_rerun.json", got2)
        if det: got2 = {**{n: got[n] for n in det if n in got}, **got2}     # the deterministic outputs stand
        for k, i in redo: res[i["id"]] = check(k, i, got2)
    lastgot = got2 if redo else got
    checks = [{"id": i["id"], "kind": k, "probe": i["probe"], "ok": res[i["id"]][0], "detail": res[i["id"]][1],
               **({"excluded": True} if na(res[i["id"]]) else {}),
               **({"baseline_broken": "vanilla" in str(res[i["id"]][1])} if res[i["id"]][0] is None and not na(res[i["id"]]) else {}),
               **({"host_gone": True} if k == "host" and host_gone(i["probe"], lastgot if (k, i) in redo else got) else {}),
               **({"first_run": {"ok": first[i["id"]][0], "detail": first[i["id"]][1]}} if first[i["id"]] is not res[i["id"]] else {})}
              for k, i in its]
    ok, fail = decide(checks)
    bad = [c for c in checks if c["ok"] is not True and not c.get("excluded")]
    g = {"pass": ok, "fail": fail, "verified": verified, "checks": checks, "baseline": bpath,
         "reason": None if ok else "; ".join(f"{c['id']}: {str(c['detail'])[:300]}" for c in bad)}
    wjson(out / "gate_probes.json", g)
    return g, round((time.time() - t0) / 60, 1)
