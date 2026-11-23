"""Evaluate a frozen deliverable (docs/evaluation.md: rubric v4 + the gate) on a fresh instance and write result.json.
Order: gate = build + install + load on a clean instance (gate_check), then the gate probes (gab/eval/regression.py: runs stably,
the host game is still there) -> content realization + functions (replays, replay verifier, play verifier, arbitration) -> visual.
A gate failure sets every column to 0; result.json gate.fail = build | load | unstable | host_removed. A gate probe that could not
run because of the harness (vanilla baseline broken) makes the trial infra_error, not a fail.
Then (config host_fidelity_in_eval, default on) the host-fidelity judge (gab/eval/host_fidelity.py) rejudges
the saved evidence -> fine/host_fidelity[@tag].json, and result.json gets scores.P (Preservation 0 / 0.5 / 1, None when
insufficient / not judged), scores.P_status and Overall = 0.3 CR + 0.3 FP + 0.3 VP + 0.1 P (gab/eval/scoring.py; scores.overall_cfv
keeps the 3-column mean).
status infra_error (gab/run/infra.py): the agent run or the evaluation hit an infrastructure failure; the trial is not scored
(scores None) and is run again by the next `gab run`."""
import re, shutil, time, traceback
from pathlib import Path

from .. import __version__, config
from ..run import infra
from ..game import base as gbase
from ..host import base as hbase
from .scoring import column_scores, explicit, with_preservation
from ..run.task import COLS_C
from ..run.trial import check_deliverable, clean_name
from ..util import log, rjson, tree_hash, wjson
from . import functions, judge, regression, visual


def gate_check(ad, inst, mod, e):
    """build + install + load on a clean instance -> {pass, build, load, log, reason, errors}; e/gate.log keeps the whole text"""
    ok, blog = ad.build(inst, mod) if Path(mod).exists() else (False, "no mod/ in the deliverable")
    lok, llog = False, ""
    if ok:
        ad.provision(inst); ad.install_mod(inst, mod)
        lok, llog = ad.load_check(inst); ad.stop(inst)
    Path(e).mkdir(parents=True, exist_ok=True)
    (Path(e) / "gate.log").write_text(f"BUILD {'ok' if ok else 'FAILED'}\n{blog}\n\nLOAD {'ok' if lok else 'FAILED'}\n{llog}\n")
    g = gbase.read_gate_log(llog if ok else blog)                 # reason + first error lines (adapters put them first)
    return {"pass": bool(ok and lok), "fail": None if ok and lok else ("build" if not ok else "load"), "build": ok, "load": lok, "log": str(Path(e) / "gate.log"),
            "reason": g["reason"] or (None if ok else "build / structure check failed"),
            "errors": g["errors"] or ([] if ok else [l for l in g["detail"].splitlines() if l.strip()][:gbase.ERR_N])}


def eval_tag(tag=None):
    """the evaluation's output tag: --tag, else config eval_tag (env GAB_EVAL_TAG); None = the main result"""
    t = tag or config.get("eval_tag") or None
    if t and not re.fullmatch(r"[\w.+-]+", t): raise ValueError(f"eval tag {t!r}: letters, digits, . + - _ only")
    return t


def out_paths(d, tag=None):
    """-> (eval dir, result file): eval/ + result.json, or with a tag eval@<tag>/ + result@<tag>.json (repeats, budget variants and
    ablations never overwrite the main result; aggregate / table read them only when asked: `gab table --names run@tag`)"""
    d = Path(d); return (d / f"eval@{tag}", d / f"result@{tag}.json") if tag else (d / "eval", d / "result.json")


def settings(tag=None):
    """result.json `eval_settings`: the knobs this evaluation ran with"""
    a, m = functions.play_budget()
    return {"tag": tag, "play_actions": a, "play_minutes": m, "evidence": functions.evidence_mode()}


def deliverable_dir(d, rel=None, tag=None):
    """the deliverable an evaluation reads: <trial>/deliverable, or with rel (`gab eval --deliverable-from`) an alternate one
    inside the trial dir, e.g. attempts/2/deliverable (an archived earlier version). An alternate deliverable needs a tag: its
    result never goes to result.json. -> (path, rel or None)"""
    d = Path(d)
    if not rel or str(rel).strip("/") == "deliverable": return d / "deliverable", None
    if not tag: raise ValueError(f"--deliverable-from {rel}: needs --tag (never overwrites the main result)")
    p = (d / rel).resolve()
    if d.resolve() not in p.parents: raise ValueError(f"--deliverable-from {rel}: must be inside the trial dir {d}")
    return p, str(rel).strip("/")


def infra_result(d, task, level, block, err=None, judges=None, tag=None, deliverable=None, status="infra_error"):
    """result.json of a trial that is not scored: an infrastructure failure, not the agent's (status infra_error), or an agent
    run stopped by its USD cap (status budget_exceeded, gab/host/keyproxy.py)"""
    d = Path(d); tm = rjson(d / "trial.json", {}) or {}; ag = rjson(d / "agent" / "agent.json", {}) or {}
    dv, rel = deliverable_dir(d, deliverable, tag)
    res = {"trial": {k: tm.get(k) for k in ("task", "level", "agent", "model", "framework", "seed", "name")}, "attempt": tm.get("attempt"),
           "versions": {"task": task.meta.get("version"), "gab": __version__},
           "status": status, "error": err or (f"infrastructure: {block.get('primary')}" if status == "infra_error" else
                                               f"agent stopped by gab's cost guard: {block.get('primary')}"), "infra": block,
           "eval_settings": settings(tag), "judges": judges, "gate": {"pass": None, "log": None}, "scores": {"C": {c: None for c in COLS_C}, "overall": None},
           "items": [], "explicit_requirements": [],
           "deliverable": {"sha256": tree_hash(dv) if dv.exists() else None, "check": None, "path": rel or "deliverable"}, "leaks": ag.get("leaks") or [],
           "cost": {"agent": {k: ag.get(k) for k in ("wall_s", "tokens_in", "tokens_out", "tokens_cached", "usd", "exit_reason")},
                    "eval": {"wall_s": 0, "tokens_in": 0, "tokens_out": 0, "usd": 0, "game_minutes": 0}},
           "timestamps": {"agent_start": tm.get("started"), "eval_end": time.strftime("%Y-%m-%dT%H:%M:%S")}}
    rf = out_paths(d, tag)[1]; wjson(rf, res)
    log(f"{task.id}/{level}: {rf.name} written ({status}: {block.get('primary')}); not scored")
    return res


def eval_trial(d, task, level, seat=None, force=False, play=True, attempt=None, tag=None, deliverable=None):
    """deliverable: an alternate deliverable dir inside the trial (`gab eval --deliverable-from`, needs a tag), e.g.
    attempts/2/deliverable; default <trial>/deliverable"""
    tag = eval_tag(tag); d = Path(d); dv, rel = deliverable_dir(d, deliverable, tag); e, rf = out_paths(d, tag)
    if rf.exists() and not force: return rjson(rf)
    if rel and not dv.is_dir(): raise FileNotFoundError(f"{d}: no deliverable at {rel}")
    ag = rjson(d / "agent" / "agent.json", {}) or {}
    if (ag.get("infra") or {}).get("status") == "infra_error":   # the agent never had a working environment: nothing to score
        return infra_result(d, task, level, ag["infra"], tag=tag, deliverable=rel)
    if (ag.get("infra") or {}).get("status") == infra.BUDGET:    # stopped by its USD cap: recorded, not scored
        return infra_result(d, task, level, ag["infra"], tag=tag, deliverable=rel, status=infra.BUDGET)
    if force: shutil.rmtree(e, ignore_errors=True)
    e.mkdir(parents=True, exist_ok=True)
    tm = rjson(d / "trial.json", {})
    T0 = time.time(); ts = {"eval_start": time.strftime("%Y-%m-%dT%H:%M:%S"), "agent_start": tm.get("started")}
    cost = {}; gm = 0.0; items = []; status = "completed"; gate = {"pass": None, "log": None}; ver = {}; err = None; ginfra = None
    own = seat is None
    try:
        # ---- gate
        if own: seat = hbase.get().acquire({"game": task.game}, f"gab-eval-{clean_name(tm.get('name'))}-{task.id[-14:]}-{level[0]}")
        ad = gbase.get(task.game, seat); ver = ad.version()
        inst = f"eval-{clean_name(tm.get('name'))}-{level}"[:48] + "-" + __import__("hashlib").sha1(str(Path(d).resolve()).encode()).hexdigest()[:8]
        t0 = time.time(); mod = dv / "mod"
        gate = gate_check(ad, inst, mod, e)
        gm += (time.time() - t0) / 60
        CRs, F, Vs = task.items("content_realization"), task.items("functions_playability"), task.items("visual_presentation")
        if gate["pass"]:
            gp, m = regression.evaluate(task, ad, mod, e / "regression"); gm += m
            gate = {**gate, "pass": gp["pass"], "fail": gp["fail"], "probes": gp}
            if gp["pass"] is False: gate["reason"] = gp["reason"]
        if not gate["build"]: status = "build_failed"
        elif not gate["load"]: status = "load_failed"
        elif gate["fail"] in ("unstable", "host_removed"): status = gate["fail"]
        log(f"{task.id}/{level}: gate {'pass' if gate['pass'] else gate['fail']}" + (f" ({gate.get('reason')})" if not gate["pass"] else ""))
        if gate["pass"] is None and gate.get("fail") == "infra":
            ginfra = infra.make("gate_baseline_broken", (gate.get("probes") or {}).get("reason") or "")
        elif gate["pass"]:
            ad.provision(inst); ad.install_mod(inst, mod)
            fi, R, m = functions.evaluate(task, level, ad, seat, inst, dv, e / "functions", cost); items += fi; gm += m
            log(f"{task.id}/{level}: content + functions done")
            items += visual.evaluate(task, ad.name, dv, R, e / "functions" / "play", e / "visual", cost)
            log(f"{task.id}/{level}: visual done")
        else:
            items += [{"id": x["id"], "column": c, "score": 0.0, "could_not_test": False, "evidence": f"gate failed ({gate['fail']})", "judge": None}
                      for c, xs in (("content_realization", CRs), ("functions_playability", F), ("visual_presentation", Vs)) for x in xs]
    except Exception as x:
        status, err = "error", f"{type(x).__name__}: {x}"; (e / "error.txt").write_text(traceback.format_exc()); log(f"{task.id}/{level}: ERROR {err}")
    finally:
        if own and seat: seat.release()
    # infrastructure during the evaluation: the play verifier's tool calls, an evaluation crash, a gate that failed on the
    # environment (offline build, GL) rather than on the mod
    inc, n_calls = infra.from_calls(infra.calls(sorted(e.rglob("calls.jsonl"))))
    inc += judge.incidents(cost)                                   # a judge that failed after its retry: judge_failed, not scored
    if status == "error": inc += [{**i, "source": "eval exception", "evidence": str(e / "error.txt")}
                                  for i in infra.classify(f"{err}\n{(e / 'error.txt').read_text(errors='replace')}", exclude=("model-api",))]
    if gate.get("fail") in ("build", "load") and gate.get("log") and Path(gate["log"]).exists():
        inc += [{**i, "source": "eval gate", "evidence": gate["log"]} for i in infra.classify(Path(gate["log"]).read_text(errors="replace")[-200000:], exclude=("model-api",))]
    gpf = str(e / "regression" / "gate_probes.json")
    if ginfra: inc.append({**ginfra, "source": "eval gate probes", "evidence": gpf})
    if gate.get("fail") in ("unstable", "host_removed"):           # a probe run that died on the environment, not on the mod
        inc += [{**i, "source": "eval gate probes", "evidence": gpf} for i in infra.classify(str(gate.get("reason") or ""), exclude=("model-api",))]
    blk = infra.assess([{**i, "source": i.get("source") or "eval tool call"} for i in inc], infra_calls=n_calls) if inc else None
    if blk:
        for i in blk["incidents"]: infra.record(d, {**i, "sources": [f"eval@{tag}" if tag else "eval", *i.get("sources", [])]}, attempt=attempt)
        if blk["status"]: return infra_result(d, task, level, blk, err, judges=judge.summary(cost), tag=tag, deliverable=rel)
    if ag.get("exit_reason") == "timeout" and status == "completed": status = "timeout"
    for i in items: i["scored"] = True; i.setdefault("could_not_test", False)
    sc = column_scores(items, gate["pass"] if status != "error" else None)
    ts["eval_end"] = time.strftime("%Y-%m-%dT%H:%M:%S")
    res = {"trial": {k: tm.get(k) for k in ("task", "level", "agent", "model", "framework", "seed", "name")},
           "versions": {"task": task.meta.get("version"), "rubric": (task.hidden.get("provenance") or {}).get("source_sha256"),
                        "game": ver.get("build"), "image": ver.get("image"), "mod_loader": ver.get("mod_loader"), "gab": __version__,
                        "judges": config.get("judge_model")},
           "eval_settings": settings(tag), "status": status, "error": err, "judges": judge.summary(cost), "gate": gate, "scores": sc, "items": items,
           "explicit_requirements": explicit(task.hidden["instructions_meta"]["explicit_requirements"], items),
           "deliverable": {"sha256": tree_hash(dv) if dv.exists() else None, "check": check_deliverable(dv) if dv.exists() else None,
                           "path": rel or "deliverable"},
           "leaks": ag.get("leaks") or [], "infra": blk or ag.get("infra"),
           "cost": {"agent": {"wall_s": ag.get("wall_s"), "tokens_in": ag.get("tokens_in"), "tokens_out": ag.get("tokens_out"),
                              "tokens_cached": ag.get("tokens_cached"), "usd": ag.get("usd"), "exit_reason": ag.get("exit_reason")},
                    "eval": {"wall_s": round(time.time() - T0), "tokens_in": cost.get("tokens_in", 0), "tokens_out": cost.get("tokens_out", 0),
                             "usd": cost.get("usd", 0), "game_minutes": round(gm, 1)}},
           "timestamps": ts}
    wjson(rf, res)
    log(f"{task.id}/{level}: {rf.name} written ({status}) C={sc['C']}")
    if host_fidelity_on() and status != "error":
        res = add_preservation(d, task, level, tag, res, rf)
    return res


def host_fidelity_on():
    """config host_fidelity_in_eval (env GAB_HOST_FIDELITY_IN_EVAL: 0 / false / no / off = off)"""
    v = config.get("host_fidelity_in_eval")
    return v if isinstance(v, bool) else str(v).strip().lower() not in ("0", "false", "no", "off", "")


def add_preservation(d, task, level, tag, res, rf, judge_fn=None):
    """after the CR / FP / VP judging: the host-fidelity judge on the evaluation's saved evidence (fresh, force) ->
    fine/host_fidelity[@tag].json; P into res["scores"] and Overall by the WEIGHTS rule; res rewritten to rf. A host-fidelity
    failure never loses the result: P stays missing (P_status error) and Overall is the 3-column mean. judge_fn: tests"""
    from . import host_fidelity
    try:
        hf = (judge_fn or host_fidelity.run_trial)(d, task.id, task.game, force=True, modality=task.modality, tag=tag)
    except Exception as x:
        log(f"{task.id}/{level}: host fidelity failed: {type(x).__name__}: {x}")
        hf = {"status": "error", "score": None, "reason": f"{type(x).__name__}: {x}"}
    sc = with_preservation(res["scores"], hf, (res.get("gate") or {}).get("pass"))
    res = {**res, "scores": sc, "host_fidelity": {"status": hf.get("status"), "score": hf.get("score"), "reason": hf.get("reason"),
                                                  "file": host_fidelity.paths(d, tag)[2].name}}
    wjson(rf, res)
    log(f"{task.id}/{level}: P={sc['P']} ({sc['P_status']}) -> overall {sc['overall']} (3-column {sc['overall_cfv']})")
    return res


class _Replay:
    """a game adapter stand-in that replays the probe sessions an earlier evaluation saved (regate)"""
    replay_sessions = True                                              # no fresh vanilla baseline: the saved one decides
    def __init__(self, ad, sessions): self.ad, self.name, self.sessions = ad, ad.name, sessions
    def compare(self, spec, base, got): return self.ad.compare(spec, base, got)
    def run_probes(self, names, mod_dir=None, label="mod"): return self.sessions[label]


