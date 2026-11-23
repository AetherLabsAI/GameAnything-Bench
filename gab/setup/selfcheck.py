"""`gab selfcheck <game>` (docs/adding_a_game.md) and `gab game provision|check|baseline <game>`.
Self-check: provision + start with no mod reaches the game with no dialog (screenshot kept as evidence); A/A of the ready probes on
two fresh vanilla instances (every pool item must agree); each negative mod (gab/game/<game>/negmods/) must fail all its
expected items and nothing else, except items listed as `may_fail` (a real side effect of the change that shows only sometimes). --quick runs only the probes the negative mods, the default picks and the gate items need.
State check (docs/STATE_SPEC.md, gab/game/<game>/statecheck/statecheck.yaml): on a vanilla instance state() has the engine
fields and a vanilla-only `mod`; with the test mod (sets a variable, spawns an entity, shows HUD text) all three appear;
every state() read is timed. --state runs only this part."""
import json, time
from pathlib import Path

import yaml

from .. import config
from ..eval.regression import baseline, pool
from ..game import base as gbase
from ..host import base as hbase
from ..util import log, wjson


def _seat(game, tag):
    return hbase.get().acquire({"game": game}, f"gab-{tag}-{game}")


def ready_items(game):
    """the items the self-check tests: `ready` ones, and `unverified` ones (written, never run in game: the self-check is how they get
    verified; the report lists them under summary.unverified, to flip to ready once they pass)"""
    p = pool(game); return [i for i in p["always"] + p["pool"] if i["status"] in ("ready", "unverified")]


def _find(x, text):
    return str(text).lower() in json.dumps(x, ensure_ascii=False, default=str).lower()


def _get(st, path):
    for k in path.split("."):
        if not isinstance(st, dict) or k not in st: return None
        st = st[k]
    return st


def state_check(ad, game, out):
    """the STATE_SPEC self-check (spec: statecheck.yaml next to the adapter):
      vanilla: {steps: [trace steps from a fresh start into the game], engine: [paths that must be present],
                mod_allow: [texts a vanilla `mod` may hold]}
      mod:     {dir: test mod (a deliverable mod/), steps: [...], expect: ["mod: TEXT", "screen_text: TEXT", ...]}
    -> report dict with ok"""
    f = gbase.gdir(game) / "statecheck" / "statecheck.yaml"
    if not f.exists(): return {"ok": False, "error": f"no {f.relative_to(gbase.gdir(game).parent.parent.parent)}"}
    spec = yaml.safe_load(f.read_text()); rep = {}; inst = "sc-state"
    timed = []
    orig = ad.state
    def timed_state(i):
        t0 = time.time(); r = orig(i); timed.append(round(time.time() - t0, 2)); return r
    ad.state = timed_state
    try:
        for part in ("vanilla", "mod"):
            sp = spec.get(part) or {}
            ad.provision(inst)
            md = (f.parent / sp["dir"]) if part == "mod" else None
            if md: ad.install_mod(inst, md)
            ad.attach(inst, md)
            r = ad.replay(inst, {"steps": (sp.get("steps") or []) + [{"do": "shot", "name": "check"}]}, out / f"state_{part}")
            st = r.get("state") or {}; ad.stop(inst)
            R = {"replay_ok": r.get("ok"), "running": st.get("running"), "keys": sorted(st), "timeline": len(r.get("timeline") or [])}
            if not r.get("ok"): R["log"] = r.get("log")
            miss = [k for k in ("running", "engine", "mod", "screen_text") if k not in st]
            if part == "vanilla":
                R["engine_missing"] = [x for x in sp.get("engine") or [] if _get(st.get("engine") or {}, x) is None]
                allow = [str(a).lower() for a in sp.get("mod_allow") or []]
                R["mod_leaves"] = [k for k, v in gbase._leaves(st.get("mod") or {}, "", {}).items()
                                   if v not in ({}, [], None, 0, "") and not any(a in f"{k}={v}".lower() for a in allow)][:20]
                R["ok"] = bool(r.get("ok") and st.get("running") and not miss and not R["engine_missing"] and not R["mod_leaves"])
            else:
                R["expect"] = {}
                for e in sp.get("expect") or []:
                    sec, _, txt = e.partition(":")
                    R["expect"][e] = _find(st.get(sec.strip()), txt.strip())
                R["ok"] = bool(r.get("ok") and st.get("running") and not miss and all(R["expect"].values()))
            R["sections_missing"] = miss; rep[part] = R
            log(f"selfcheck {game}: state {part}: {'ok' if R['ok'] else 'FAILED'}")
    finally: ad.state = orig
    rep["state_seconds"] = {"n": len(timed), "max": max(timed or [0]), "mean": round(sum(timed) / max(len(timed), 1), 2)}
    rep["ok"] = all(rep.get(p, {}).get("ok") for p in ("vanilla", "mod")) and rep["state_seconds"]["max"] < 3.0
    return rep


def selfcheck(game, quick=False, state_only=False):
    out = config.path("runs_dir") / "_selfcheck" / game / time.strftime("%Y%m%d-%H%M%S"); out.mkdir(parents=True, exist_ok=True)
    if state_only:
        seat = _seat(game, "state")
        try: rep = {"game": game, "seat": f"{seat.sandbox}:{seat.seat}", "state": state_check(gbase.get(game, seat), game, out)}
        finally: seat.release()
        rep["pass"] = rep["state"]["ok"]; wjson(out / "report.json", rep)
        print(json.dumps(rep["state"], indent=1, default=str)[:4000], f"\nreport: {out / 'report.json'}")
        return rep["pass"]
    neg = yaml.safe_load((gbase.gdir(game) / "negmods" / "negmods.yaml").read_text())
    items = ready_items(game)
    if quick:
        want = {"log_errors"} | {pr for v in neg.values() for pr in v.get("quick_probes", [])}
        dp = set(pool(game).get("default_picks", [])) | set(pool(game).get("gate", []))      # + the gate items
        want |= {i["probe"].split(".")[0] for i in items if i["id"] in dp}
        items = [i for i in items if i["probe"].split(".")[0] in want]
    names = sorted({i["probe"].split(".")[0] for i in items})
    seat = _seat(game, "selfcheck"); rep = {"game": game, "quick": quick, "probes": names, "seat": f"{seat.sandbox}:{seat.seat}"}
    try:
        ad = gbase.get(game, seat); rep["version"] = ad.version()
        # 1. provision -> playable, no dialog
        ad.provision("sc-start"); ad.attach("sc-start", None)
        ok, l = ad.start("sc-start"); time.sleep(5)
        rep["start"] = {"ok": ok, "shot": ad.screenshot("sc-start", out / "start.png"), "state": ad.state("sc-start") if ok else l[-500:]}
        ad.stop("sc-start"); log(f"selfcheck {game}: start {'ok' if ok else 'FAILED'}")
        rep["state"] = state_check(ad, game, out)
        # 2. A/A
        A = ad.run_probes(names, None, "aaA"); wjson(out / "A.json", A); log(f"selfcheck {game}: A done")
        B = ad.run_probes(names, None, "aaB"); wjson(out / "B.json", B); log(f"selfcheck {game}: B done")
        rep["aa"] = {i["id"]: ad.compare(i["probe"], A, B) for i in items}
        # 3. negative mods
        rep["negative"] = {}
        for m, spec in neg.items():
            md = gbase.gdir(game) / "negmods" / m
            tmp = out / "negmod" / m
            import shutil; shutil.copytree(md, tmp / m)
            G = ad.run_probes(names, tmp, f"neg-{m[:20]}"); wjson(out / f"neg_{m}.json", G)
            res = {i["id"]: ad.compare(i["probe"], A, G) for i in items}
            fails = sorted(k for k, (o, _) in res.items() if o is False)
            ids = {i["id"] for i in items}
            exp = sorted(x for x in spec["expect_fail"] if x in ids)
            may = sorted(x for x in spec.get("may_fail") or [] if x in ids)   # a real side effect that shows only sometimes
            ok = set(exp) <= set(fails) <= set(exp) | set(may)               # may_fail: never required, never an error
            rep["negative"][m] = {"fails": fails, "expected": exp, "may_fail": may, "ok": ok,
                                  "detail": {k: v[1] for k, v in res.items() if v[0] is not True}}
            log(f"selfcheck {game}: negative {m}: fails {fails} (expected {exp}{', may also fail ' + str(may) if may else ''})")
    finally: seat.release()
    aa_fail = sorted(k for k, (o, _) in rep["aa"].items() if o is False); aa_cnt = sorted(k for k, (o, _) in rep["aa"].items() if o is None)
    unv = sorted(i["id"] for i in items if i["status"] == "unverified")
    rep["summary"] = {"start_ok": rep["start"]["ok"], "aa_fail": aa_fail, "aa_could_not_test": aa_cnt, **({"unverified": unv} if unv else {}),
                      "negative_ok": all(v["ok"] for v in rep["negative"].values()), "state_ok": rep["state"]["ok"]}
    rep["pass"] = rep["start"]["ok"] and rep["state"]["ok"] and not aa_fail and not aa_cnt and rep["summary"]["negative_ok"]
    wjson(out / "report.json", rep)
    print(json.dumps(rep["summary"], indent=1), f"\nreport: {out / 'report.json'}")
    return rep["pass"]


def game_cmd(action, game):
    seat = _seat(game, action)
    try:
        ad = gbase.get(game, seat)
        if action == "provision": print(json.dumps(ad.provision("provision-check"), indent=1)); print(json.dumps(ad.version()))
        elif action == "check":
            ad.provision("check"); ad.attach("check", None)
            ok, l = ad.start("check"); p = ad.screenshot("check", config.path("runs_dir") / "_selfcheck" / game / "check.png")
            print(json.dumps({"start": ok, "shot": p, "state": ad.state("check")}, indent=1, default=str)); ad.stop("check")
        elif action == "baseline":
            names = sorted({i["probe"].split(".")[0] for i in ready_items(game)})
            b, f = baseline(ad, names, refresh=True); print(f)
    finally: seat.release()
