"""Evaluation knobs for the reliability experiments (2026-11-06): play budget from config / env, `gab eval --tag`
outputs, the missing-item follow-up call, the evidence ablation modes and the re-judge of a saved evaluation, `--from-file`.
Fake claude() results, no model, no game."""
import json
from pathlib import Path
from types import SimpleNamespace

import pytest

from gab import cli
from gab.eval import functions, judge, run
from gab.report import aggregate

ITEMS = [{"id": i, "item": "x", "anchors": {"1": "a", "0.5": "b", "0": "c"}} for i in ("CR1", "CR2", "FP1")]
IDS = [f["id"] for f in ITEMS]


def rec(answer="", **kw):
    return {"name": "j", "model": "m", "wall_s": 1.0, "is_error": False, "timeout": False, "usd": 0.01, "tokens_in": 1, "tokens_out": 1,
            "permission_denials": [], "answer": answer, **kw}


def ans(ids=IDS, score=1): return json.dumps({"items": [{"id": i, "score": score, "could_not_test": False, "reason": "r"} for i in ids]})


class Task:
    id = "T-VO-x"; game = "g"; meta = {"version": 1}; hidden = {"instructions_meta": {"explicit_requirements": []}}
    def items(self, col):
        return {"content_realization": ITEMS[:2], "functions_playability": ITEMS[2:]}.get(col, [])


@pytest.fixture(autouse=True)
def no_refs(monkeypatch): monkeypatch.setattr(functions, "resolve", lambda *a, **kw: (None, []))


# ---- (a) play budget
def test_play_budget_config_and_env(monkeypatch):
    monkeypatch.delenv("GAB_PLAY_ACTIONS", raising=False); monkeypatch.delenv("GAB_PLAY_MINUTES", raising=False)
    monkeypatch.setattr(functions.config, "_toml", lambda: {})
    assert functions.play_budget() == (40, 30.0)
    monkeypatch.setattr(functions.config, "_toml", lambda: {"play_actions": 80, "play_minutes": 45})
    assert functions.play_budget() == (80, 45.0)
    monkeypatch.setenv("GAB_PLAY_ACTIONS", "20"); monkeypatch.setenv("GAB_PLAY_MINUTES", "15")
    assert functions.play_budget() == (20, 15.0)


# ---- (c) missing-item follow-up
def test_followup_asks_only_for_missing_items():
    calls, fcalls = [], []
    def call(n): calls.append(n); return ans(["CR1"]), rec()
    def fu(miss, n): fcalls.append(miss); return ans(miss, 0.5), rec(), *functions.json_judge(miss)(ans(miss, 0.5), rec())
    cost = {}
    got, bad = judge.run(cost, "replay_verifier", call, functions.json_judge(IDS), followup=fu)
    assert bad is None and calls == [1] and fcalls == [["CR2", "FP1"]]
    assert got["CR1"]["score"] == 1 and got["FP1"]["score"] == 0.5
    s = judge.summary(cost)
    assert s["followups"] == 1 and s["retries"] == 0 and s["calls"] == 2 and s["stages"]["replay_verifier"]["attempts"][1]["attempt"] == "1.followup"


def test_followup_failure_falls_back_to_full_retry():
    answers = [ans(["CR1"]), ans()]; resets = []
    def call(n): return answers.pop(0), rec()
    def fu(miss, n): return "nope", rec(), *functions.json_judge(miss)("nope", rec())
    cost = {}
    got, bad = judge.run(cost, "play_verifier", call, functions.json_judge(IDS), before_retry=resets.append, followup=fu)
    assert bad is None and resets == [2] and set(got) == set(IDS)
    s = judge.summary(cost); assert s["followups"] == 1 and s["retries"] == 1 and s["failed_calls"] == 2


def test_no_followup_for_other_failures():
    fcalls = []
    def fu(miss, n): fcalls.append(miss)
    got, bad = judge.run({}, "x", lambda n: ("", rec("timeout", is_error=True, timeout=True)), functions.json_judge(IDS), followup=fu)
    assert bad == "timeout" and fcalls == []


def test_replay_verifier_followup_prompt_has_only_missing(monkeypatch, tmp_path):
    prompts = []
    def fake(p, out, name, **kw):
        prompts.append((name, p)); return (ans(["CR1", "CR2"]) if len(prompts) == 1 else ans(["FP1"])), rec()
    monkeypatch.setattr(functions, "claude", fake)
    R = [{"trace": "t.yaml", "ok": True, "claims": [], "shots": [], "timeline": []}]
    v = functions.replay_verifier("g", ITEMS, R, tmp_path / "r", {})
    assert {k: x["score"] for k, x in v.items()} == {"CR1": 1, "CR2": 1, "FP1": 1}
    assert prompts[1][0] == "replay_verifier.followup" and "FP1 (functional)" in prompts[1][1] and "CR1 (content)" not in prompts[1][1]


# ---- (d) evidence modes
def test_evidence_mode(monkeypatch):
    monkeypatch.delenv("GAB_EVIDENCE", raising=False); monkeypatch.setattr(functions.config, "_toml", lambda: {})
    assert functions.evidence_mode() == "full"
    monkeypatch.setenv("GAB_EVIDENCE", "shots+state")
    assert functions.evidence_mode() == "shots+state"
    with pytest.raises(ValueError): functions.evidence_mode("everything")


def dv_with_shots(tmp_path):
    dv = tmp_path / "dv"; (dv / "shots").mkdir(parents=True); (dv / "traces").mkdir()
    (dv / "shots" / "01.png").write_bytes(b""); (dv / "traces" / "a.yaml").write_text("claims: [the door opens]\nsteps: []\n")
    return dv


@pytest.mark.parametrize("mode,stages,state", [("shots", ["shots_verifier"], False), ("shots+state", ["shots_verifier"], True),
                                               ("shots+state+replay", ["replay_verifier"], None)])
def test_judge_items_modes(monkeypatch, tmp_path, mode, stages, state):
    names, prompts = [], []
    def fake(p, out, name, **kw): names.append(name); prompts.append(p); return ans(), rec()
    monkeypatch.setattr(functions, "claude", fake)
    dv = dv_with_shots(tmp_path)
    R = [{"trace": "a.yaml", "ok": True, "claims": ["the door opens"], "shots": ["/x/001.png"], "state": {"door": "open"},
          "timeline": [{"step": 1, "screenshot": "/x/001.png", "actions": [{"do": "key"}], "diff": {"door": ["closed", "open"]}}]}]
    def noplay(): raise AssertionError("no play verifier in an ablation")
    items, pm = functions.judge_items(mode, Task(), ITEMS, R, dv, tmp_path / "f", {}, "g", play=noplay)
    assert names == stages and pm == 0.0 and all(i["score"] == 1.0 for i in items)
    assert all(mode in i["evidence"]["decision"] or "only" in i["evidence"]["decision"] for i in items)
    if state is not None:
        assert "01.png" in prompts[0] and "the door opens" in prompts[0]
        assert ("STATE TIMELINES" in prompts[0]) == state and "/x/001.png" not in prompts[0]      # never the replay screenshots


def saved_eval(tmp_path):
    """a trial dir with a saved evaluation (eval/ + result.json) as eval_trial leaves it"""
    d = tmp_path / "runs" / "r" / "T-VO-x" / "fine"; dv_src = dv_with_shots(tmp_path)
    (d / "eval" / "functions" / "replay" / "a").mkdir(parents=True)
    import shutil; shutil.copytree(dv_src, d / "deliverable")
    (d / "eval" / "functions" / "replay" / "a" / "replay.json").write_text(json.dumps({"state": {"door": "open"}, "timeline": [], "log": []}))
    (d / "eval" / "functions" / "functions.json").write_text(json.dumps({"replays": [{"trace": "a.yaml", "ok": True, "claims": ["c"],
        "shots": [str(d / "eval/functions/replay/a/001.png")], "timeline": [{"step": 1, "screenshot": "001.png", "diff": {"door": ["c", "o"]}}]}]}))
    items = [{"id": i, "column": functions.COLUMN[i[:2]], "score": 0.0, "could_not_test": False} for i in IDS] + \
            [{"id": "VP1", "column": "visual_presentation", "score": 0.5, "could_not_test": False}]
    r = {"trial": {"task": "T-VO-x", "level": "fine"}, "status": "completed", "gate": {"pass": True, "fail": None}, "items": items,
         "scores": {}, "cost": {"agent": {}, "eval": {"usd": 1.0}}, "versions": {}, "timestamps": {}}
    (d / "result.json").write_text(json.dumps(r))
    return d


# ---- (b) tagged eval output, aggregate ignores it unless asked
def test_eval_trial_tag_writes_tagged_outputs(monkeypatch, tmp_path):
    from gab.host import base as hbase
    from gab.game import base as gbase
    class Seat:
        def release(self): pass
    class Ad:
        name = "g"
        def version(self): return {}
        def provision(self, inst): pass
        def install_mod(self, inst, mod): pass
    monkeypatch.setattr(hbase, "get", lambda *a: type("HB", (), {"acquire": lambda self, *a: Seat()})())
    monkeypatch.setattr(gbase, "get", lambda *a: Ad())
    monkeypatch.setattr(run, "gate_check", lambda ad, inst, mod, e: {"pass": True, "build": True, "load": True, "log": str(e)})
    monkeypatch.setattr(run.regression, "evaluate", lambda *a: ({"pass": True, "fail": None, "checks": [], "reason": None}, 0.0))
    seen = {}
    def fev(task, level, ad, seat, inst, dv, out, cost):
        seen["out"] = out; seen["budget"] = functions.play_budget()
        return [{"id": "CR1", "column": "content_realization", "score": 1.0, "could_not_test": False}], [], 0.0
    monkeypatch.setattr(run.functions, "evaluate", fev)
    monkeypatch.setattr(run.visual, "evaluate", lambda *a: [])
    monkeypatch.setenv("GAB_PLAY_ACTIONS", "20")
    d = tmp_path / "runs" / "r" / "T-VO-x" / "fine"; (d / "agent").mkdir(parents=True); (d / "deliverable" / "mod").mkdir(parents=True)
    (d / "trial.json").write_text(json.dumps({"task": "T-VO-x", "level": "fine", "name": "r"}))
    (d / "result.json").write_text(json.dumps({"keep": 1})); (d / "eval").mkdir(); (d / "eval" / "keep").write_text("x")
    r = run.eval_trial(d, Task(), "fine", tag="rep2")
    assert seen["out"] == d / "eval@rep2" / "functions" and seen["budget"] == (20, 30.0)
    assert json.loads((d / "result.json").read_text()) == {"keep": 1} and (d / "eval" / "keep").exists()
    assert (d / "result@rep2.json").exists() and r["eval_settings"] == {"tag": "rep2", "play_actions": 20, "play_minutes": 30.0, "evidence": "full"}
    monkeypatch.setenv("GAB_EVAL_TAG", "envtag")
    run.eval_trial(d, Task(), "fine", force=True)
    assert (d / "result@envtag.json").exists() and json.loads((d / "result.json").read_text()) == {"keep": 1}
    with pytest.raises(ValueError): run.eval_tag("a/b")


def test_aggregate_reads_tagged_only_when_asked(monkeypatch, tmp_path):
    monkeypatch.setenv("GAB_RUNS_DIR", str(tmp_path))
    d = tmp_path / "r" / "T-VO-x" / "fine"; d.mkdir(parents=True)
    (d / "result.json").write_text(json.dumps({"main": 1})); (d / "result@rep2.json").write_text(json.dumps({"tagged": 1}))
    assert aggregate.results("r") == [{"main": 1}] and aggregate.results("r@rep2") == [{"tagged": 1}]
    assert aggregate.split_tag("r@abl-shots") == ("r", "abl-shots")


def test_eval_from_file(tmp_path, monkeypatch):
    class T:
        def __init__(self, i): self.id = i; self.game = "g"
    monkeypatch.setattr("gab.run.task.Task", T)
    f = tmp_path / "pairs.txt"; f.write_text("# E0\nrunA/T-VO-x\n\nrunB/T-IO-y/fine  # comment\n")
    jobs = cli._eval_jobs(SimpleNamespace(from_file=str(f), name=None))
    assert [(n, t.id, lv) for n, t, lv in jobs] == [("runA", "T-VO-x", "fine"), ("runB", "T-IO-y", "fine")]


def test_cmd_eval_passes_tag_and_knobs(tmp_path, monkeypatch):
    import gab.run.trial
    class T:
        def __init__(self, i): self.id = i; self.game = "g"
    monkeypatch.setattr("gab.run.task.Task", T); monkeypatch.setenv("GAB_RUNS_DIR", str(tmp_path))
    for k in ("GAB_EVAL_TAG", "GAB_PLAY_ACTIONS", "GAB_PLAY_MINUTES", "GAB_EVIDENCE"):   # setenv first: restored after (cmd_eval sets them)
        monkeypatch.setenv(k, ""); monkeypatch.delenv(k)
    (tmp_path / "runA" / "T-VO-x" / "fine" / "deliverable").mkdir(parents=True)
    f = tmp_path / "pairs.txt"; f.write_text("runA/T-VO-x\n")
    seen = []
    monkeypatch.setattr(gab.run.trial, "eval_trial", lambda d, t, lv, force=False, tag=None: seen.append((d.name, t.id, force, tag, functions.play_budget())))
    a = SimpleNamespace(from_file=str(f), name=None, deliverables=None, regate=False, rejudge_from=None, force=True, workers=1,
                        tag="b80", play_actions=80, play_minutes=None, evidence=None)
    cli.cmd_eval(a)
    assert seen == [("fine", "T-VO-x", True, "b80", (80, 30.0))]


@pytest.mark.parametrize("mode,state,rshots", [("shots+replay", False, True), ("shots+replay+state", True, True)])
def test_judge_items_replay_screenshot_modes(monkeypatch, tmp_path, mode, state, rshots):
    """the 2x2 with the developer's evidence: replay screenshots on, replay state on / off; the replay folder is readable"""
    calls = []
    def fake(p, out, name, **kw): calls.append((name, p, kw)); return ans(), rec()
    monkeypatch.setattr(functions, "claude", fake)
    dv = dv_with_shots(tmp_path)
    R = [{"trace": "a.yaml", "ok": True, "claims": ["the door opens"], "shots": ["/x/001.png"], "state": {"door": "open"},
          "timeline": [{"step": 1, "screenshot": "001.png", "actions": [{"do": "key"}], "diff": {"changed": {"door": ["closed", "open"]}}}]}]
    items, pm = functions.judge_items(mode, Task(), ITEMS, R, dv, tmp_path / "f", {}, "g", play=None, extra_dirs=(tmp_path / "rep",))
    (name, p, kw), = calls
    assert name == "shots_verifier" and all(i["score"] == 1.0 for i in items)
    assert "01.png" in p and "the door opens" in p and "/x/001.png" in p and "001.png after step 1" in p
    assert ("closed" in p) == state and ("final state" in p) == state
    assert tmp_path / "rep" in kw["add_dirs"]


@pytest.mark.parametrize("mode", ["replay-shots", "replay-state"])
def test_judge_items_replay_part_modes(monkeypatch, tmp_path, mode):
    """our replays only, one capture kind: no developer screenshots, and the judge can read only copies of that kind"""
    calls = []
    def fake(p, out, name, **kw): calls.append((name, p, kw)); return ans(), rec()
    monkeypatch.setattr(functions, "claude", fake)
    dv = dv_with_shots(tmp_path); rep = tmp_path / "rep" / "a"; (rep / "state").mkdir(parents=True)
    (rep / "001.png").write_bytes(b"x"); (rep / "state" / "001.json").write_text("{}")
    R = [{"trace": "a.yaml", "ok": True, "claims": ["the door opens"], "shots": [str(rep / "001.png")], "state": {"door": "open"},
          "timeline": [{"step": 1, "screenshot": "001.png", "actions": [{"do": "key"}], "diff": {"changed": {"door": ["closed", "open"]}}}]}]
    items, _ = functions.judge_items(mode, Task(), ITEMS, R, dv, tmp_path / "f", {}, "g", play=None, extra_dirs=(tmp_path / "rep",))
    (name, p, kw), = calls
    cap = tmp_path / "f" / "replay" / "captures" / "a"
    assert name == "replay_verifier" and all(i["score"] == 1.0 for i in items)
    assert "01.png" not in p.replace("001.png", "") and "the door opens" in p           # no developer screenshot
    assert kw["add_dirs"] == (tmp_path / "f" / "replay",) and str(rep) not in p           # never the replay folder itself
    if mode == "replay-shots":
        assert (cap / "001.png").exists() and not (cap / "state").exists() and "closed" not in p and "final state" not in p
    else:
        assert not (cap / "001.png").exists() and (cap / "state" / "001.json").exists() and "closed" in p and "001.png" not in p


# ---- alternate deliverable (a deliverable dir named in gab eval --from-file)
def _fake_eval_env(monkeypatch, seen):
    from gab.host import base as hbase
    from gab.game import base as gbase
    class Seat:
        def release(self): pass
    class Ad:
        name = "g"
        def version(self): return {}
        def provision(self, inst): pass
        def install_mod(self, inst, mod): pass
    monkeypatch.setattr(hbase, "get", lambda *a: type("HB", (), {"acquire": lambda self, *a: Seat()})())
    monkeypatch.setattr(gbase, "get", lambda *a: Ad())
    def gate(ad, inst, mod, e): seen["gate_mod"] = mod; return {"pass": True, "build": True, "load": True, "log": str(e)}
    monkeypatch.setattr(run, "gate_check", gate)
    monkeypatch.setattr(run.regression, "evaluate", lambda task, ad, mod, out: (seen.__setitem__("reg_mod", mod) or {"pass": True, "fail": None, "checks": [], "reason": None}, 0.0))
    def fev(task, level, ad, seat, inst, dv, out, cost):
        seen["dv"] = dv; seen["mode"] = functions.evidence_mode()
        return [{"id": "CR1", "column": "content_realization", "score": 1.0, "could_not_test": False}], [], 0.0
    monkeypatch.setattr(run.functions, "evaluate", fev)
    monkeypatch.setattr(run.visual, "evaluate", lambda task, game, dv, R, play, out, cost: seen.__setitem__("vis_dv", dv) or [])


def test_eval_trial_alternate_deliverable(monkeypatch, tmp_path):
    seen = {}; _fake_eval_env(monkeypatch, seen)
    d = tmp_path / "runs" / "r" / "T-VO-x" / "fine"; (d / "agent").mkdir(parents=True)
    (d / "deliverable" / "mod").mkdir(parents=True); (d / "deliverable" / "mod" / "new.txt").write_text("iter1")
    old = d / "attempts" / "2" / "deliverable"; (old / "mod").mkdir(parents=True); (old / "mod" / "old.txt").write_text("base")
    (d / "trial.json").write_text(json.dumps({"task": "T-VO-x", "level": "fine", "name": "r"}))
    (d / "result.json").write_text(json.dumps({"keep": 1})); (d / "result@other.json").write_text(json.dumps({"keep": 2}))
    r = run.eval_trial(d, Task(), "fine", tag="lite-base", deliverable="attempts/2/deliverable")
    assert seen["dv"] == old.resolve() and seen["gate_mod"] == old.resolve() / "mod" and seen["reg_mod"] == old.resolve() / "mod"
    assert seen["vis_dv"] == old.resolve()
    assert r["deliverable"]["path"] == "attempts/2/deliverable" and r["deliverable"]["sha256"] == tree_hash_of(old)
    assert (d / "result@lite-base.json").exists() and (d / "eval@lite-base").is_dir()
    assert json.loads((d / "result.json").read_text()) == {"keep": 1} and json.loads((d / "result@other.json").read_text()) == {"keep": 2}
    r2 = run.eval_trial(d, Task(), "fine", tag="lite-iter1")
    assert seen["dv"] == d / "deliverable" and r2["deliverable"]["path"] == "deliverable" and r2["deliverable"]["sha256"] != r["deliverable"]["sha256"]
    with pytest.raises(ValueError): run.eval_trial(d, Task(), "fine", deliverable="attempts/2/deliverable")          # no tag
    with pytest.raises(ValueError): run.eval_trial(d, Task(), "fine", tag="x", deliverable="../../other/deliverable")   # outside the trial
    with pytest.raises(FileNotFoundError): run.eval_trial(d, Task(), "fine", tag="y", deliverable="attempts/9/deliverable")


def tree_hash_of(p):
    from gab.util import tree_hash
    return tree_hash(p)


def test_eval_from_file_deliverable_column(tmp_path, monkeypatch):
    class T:
        def __init__(self, i): self.id = i; self.game = "g"
    monkeypatch.setattr("gab.run.task.Task", T)
    f = tmp_path / "pairs.txt"; f.write_text("runA/T-VO-x attempts/2/deliverable\nrunB/T-IO-y/fine\n")
    alt = {}
    jobs = cli._eval_jobs(SimpleNamespace(from_file=str(f), name=None), alt)
    assert [(n, t.id, lv) for n, t, lv in jobs] == [("runA", "T-VO-x", "fine"), ("runB", "T-IO-y", "fine")]
    assert alt == {("runA", "T-VO-x", "fine"): "attempts/2/deliverable"}
    f.write_text("runA/T-VO-x a b\n")
    with pytest.raises(SystemExit): cli._eval_jobs(SimpleNamespace(from_file=str(f), name=None), {})


def test_cmd_eval_alternate_deliverable(tmp_path, monkeypatch):
    """every trial of --from-file evaluated with its own deliverable dir; refused without a tag"""
    import gab.run.trial
    class T:
        def __init__(self, i): self.id = i; self.game = "g"
    monkeypatch.setattr("gab.run.task.Task", T); monkeypatch.setenv("GAB_RUNS_DIR", str(tmp_path))
    for k in ("GAB_EVAL_TAG", "GAB_PLAY_ACTIONS", "GAB_PLAY_MINUTES", "GAB_EVIDENCE"):
        monkeypatch.setenv(k, ""); monkeypatch.delenv(k)
    for t, n in (("T-VO-x", 2), ("T-IO-y", 3)):
        (tmp_path / "runA" / t / "fine" / "attempts" / str(n) / "deliverable").mkdir(parents=True)
    (tmp_path / "runA" / "T-IO-z" / "fine" / "deliverable").mkdir(parents=True)        # no attempts/4: skipped
    f = tmp_path / "pairs.txt"; f.write_text("runA/T-VO-x attempts/2/deliverable\nrunA/T-IO-y attempts/3/deliverable\nrunA/T-IO-z attempts/4/deliverable\n")
    seen = []
    monkeypatch.setattr(gab.run.trial, "eval_trial", lambda d, t, lv, force=False, tag=None, deliverable=None:
                        seen.append((t.id, tag, deliverable, functions.evidence_mode())))
    a = SimpleNamespace(from_file=str(f), name=None, deliverables=None, regate=False, rejudge_from=None, force=False, workers=1,
                        tag="lite-base", play_actions=None, play_minutes=None, evidence="shots+state+replay", deliverable_from=None)
    cli.cmd_eval(a)
    assert seen == [("T-VO-x", "lite-base", "attempts/2/deliverable", "shots+state+replay"),
                    ("T-IO-y", "lite-base", "attempts/3/deliverable", "shots+state+replay")]
    monkeypatch.delenv("GAB_EVAL_TAG")                                     # cmd_eval exported it
    with pytest.raises(SystemExit): cli.cmd_eval(SimpleNamespace(**{**a.__dict__, "tag": None}))
