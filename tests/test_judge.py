"""Judge health (gab/eval/judge.py): every LLM judge call is checked, retried once, recorded; a judge that still fails gives
no scores and makes the trial infra_error judge_failed. Fake claude() results, no model, no game."""
import json
from pathlib import Path

import pytest

from gab.run import infra
from gab.eval import functions, judge, visual

ITEMS = [{"id": i, "item": "x", "anchors": {"1": "a", "0.5": "b", "0": "c"}} for i in ("CR1", "CR2", "FP1")]
IDS = [f["id"] for f in ITEMS]


def rec(answer="", **kw):
    return {"name": "j", "model": "m", "wall_s": 1.0, "is_error": False, "timeout": False, "usd": 0.01, "tokens_in": 1, "tokens_out": 1,
            "permission_denials": [], "answer": answer, **kw}


def verdict(ids=IDS, **kw):
    return {"items": [{"id": i, "score": 1, "could_not_test": False, "reason": "r", **kw} for i in ids]}


def ans(ids=IDS, **kw): return json.dumps(verdict(ids, **kw))


# ---- the check of one call: each failure type
@pytest.mark.parametrize("r,txt,want", [
    (rec(), ans(), None),
    (rec("timeout", is_error=True, timeout=True), "timeout", "timeout"),
    (rec("boom", is_error=True, subtype="error_during_execution"), "", "is_error (error_during_execution): boom"),
    (rec(permission_denials=[{"tool": "Bash", "input": "cd x && play phase2"}]), "I stopped.", "permission denial: Bash: cd x && play phase2"),
    (rec(), "The permission system denied my `play phase2` call, so I've stopped", "permission denial: answer:"),
    (rec(), "no json here", "no parseable JSON"),
    (rec(), ans(["CR1"]), "items missing: CR2, FP1"),
    (rec(), json.dumps({"items": [{"id": "CR1", "score": "high"}, {"id": "CR2", "score": 0}, {"id": "FP1", "score": 0.5}]}), "items missing: CR1"),
])
def test_failure_types(r, txt, want):
    _, reason = judge.failure(r, txt, functions.parsed(txt), IDS)
    assert reason == want if want is None else reason.startswith(want), reason


def test_complete_verdict_stands_unless_a_denial_left_items_untested():
    den = rec(permission_denials=[{"tool": "Bash", "input": "play state | head"}])
    got, reason = judge.failure(den, "", verdict(), IDS); assert reason is None and set(got) == set(IDS)   # retried bare, all scored
    v = verdict(); v["items"][2].update(score=None, could_not_test=True)
    _, reason = judge.failure(den, "", v, IDS); assert reason.startswith("permission denial with items could_not_test")
    _, reason = judge.failure(rec(), "", v, IDS); assert reason is None              # a real could_not_test, no denial: stands
    _, reason = judge.failure(rec("x", is_error=True, subtype="error_max_turns"), "", verdict(), IDS); assert reason is None
    _, reason = judge.failure(rec(), "", v, IDS, need_score=True); assert reason == "items missing: FP1"     # arbiter / visual


# ---- run(): retry once, record health
def test_run_retries_once_then_ok():
    cost, answers = {}, [(("not json"), rec()), (ans(), rec())]
    got, bad = judge.run(cost, "replay_verifier", lambda n: answers[n - 1], functions.json_judge(IDS))
    h = judge.summary(cost)
    assert bad is None and set(got) == set(IDS)
    assert (h["calls"], h["retries"], h["failed_calls"], h["failed"]) == (2, 1, 1, [])
    assert [a["ok"] for a in h["stages"]["replay_verifier"]["attempts"]] == [False, True] and cost["usd"] == 0.02
    assert judge.incidents(cost) == []


def test_run_fails_for_good_gives_judge_failed_incident():
    cost = {}
    got, bad = judge.run(cost, "arbiter", lambda n: ("", rec("You've hit your weekly limit", is_error=True)), functions.json_judge(IDS))
    assert bad.startswith("is_error") and got == {}
    h = judge.summary(cost)
    assert (h["calls"], h["retries"], h["failed_calls"]) == (2, 1, 2) and h["failed"][0]["stage"] == "arbiter"
    assert "_incidents" not in h["failed"][0] and h["failed"][0]["causes"] == ["subscription_limit"]
    inc = judge.incidents(cost); codes = [i["code"] for i in inc]
    assert codes == ["judge_failed", "subscription_limit"] and inc[0]["stage"] == "arbiter" and inc[0]["kind"] == "transient"
    assert infra.decide(inc) == ("infra_error", "subscription_limit")          # systematic cause first: the run stops


def test_judge_failed_alone_is_infra_error_and_retried():
    blk = infra.assess([infra.make("judge_failed", "play_verifier: no verdict.json", source="eval play_verifier")])
    assert blk["status"] == "infra_error" and blk["primary"] == "judge_failed"
    assert infra.transient_only(blk) and not infra.resumable(blk)              # gab run re-evaluates; the agent run is not touched


# ---- replay verifier
def test_replay_verifier_failure_is_flagged(tmp_path, monkeypatch):
    monkeypatch.setattr(functions, "replay_prompt", lambda *a: "prompt")
    calls = []
    monkeypatch.setattr(functions, "claude", lambda p, out, name, **kw: calls.append(name) or ("garbage", rec()))
    cost = {}
    v = functions.replay_verifier("g", ITEMS, [{"trace": "a.yaml", "ok": True}], tmp_path, cost)
    assert calls == ["replay_verifier", "replay_verifier.try2"]
    assert all(x["judge_failed"] == "replay_verifier: no parseable JSON" and x["score"] is None for x in v.values())
    assert judge.summary(cost)["failed"][0]["stage"] == "replay_verifier"


# ---- play verifier: the 2026-11-06 case (denied `play phase2`, no verdict.json), retried from a fresh instance
class FakeAd:
    name = "tuxemon"
    def __init__(self): self.log = []
    def stop(self, inst): self.log.append("stop")
    def provision(self, inst): self.log.append("provision")
    def install_mod(self, inst, mod): self.log.append(f"install {Path(mod).name}")


class FakeTask:
    game = "tuxemon"; hidden = {"play_suggestions": {}}
    def items(self, col): return [f for f in ITEMS if f["id"].startswith({"content_realization": "CR", "functions_playability": "FP"}[col])]
    def instruction(self, level): return "build it"


def play_setup(tmp_path, monkeypatch, answers):
    import gab.run.trial as trial
    ctxs = []
    monkeypatch.setattr(trial, "write_ctx", lambda path, *a, **kw: ctxs.append(kw["play"]) or Path(path).parent.mkdir(parents=True, exist_ok=True))
    monkeypatch.setattr(trial, "tools", lambda w, ctx, mode="game": {"play": str(tmp_path / "bin" / "play")})
    monkeypatch.setattr(functions, "items_text", lambda F, task=None, out=None: "items")
    prompts = []
    def fake(prompt, out, name, **kw):
        prompts.append(prompt); n = len(prompts)
        (out / "shots").mkdir(exist_ok=True); (out / "shots" / f"a00{n}.png").write_bytes(b"")
        txt, r, v = answers[n - 1]
        if v is not None: (out / "verdict.json").write_text(v)
        return txt, r
    monkeypatch.setattr(functions, "claude", fake)
    dv = tmp_path / "dv"; (dv / "traces").mkdir(parents=True); (dv / "mod").mkdir()
    out = tmp_path / "eval" / "functions" / "play"
    return dv, out, ctxs, prompts


def test_play_verifier_denied_then_fresh_retry(tmp_path, monkeypatch):
    denied = rec("The permission system denied my `play phase2` call, so I've stopped",
                 permission_denials=[{"tool": "Bash", "input": "cd /x && play phase2"}])
    dv, out, ctxs, prompts = play_setup(tmp_path, monkeypatch, [("stopped", denied, None), ("done", rec(), ans())])
    ad, cost = FakeAd(), {}
    v, _ = functions.play_verifier(FakeTask(), "fine", ad, None, "inst", dv, out, cost)
    assert all(x["score"] == 1 for x in v.values())
    assert ad.log == ["stop", "provision", "install mod", "stop"]                       # a fresh instance with the mod
    assert [c["used"] for c in ctxs] == [0, 0] and not any(c["phase2"] for c in ctxs)  # the whole budget again, phase 1 again
    assert (out.parent / "play.try1" / "shots" / "a001.png").exists() and not (out / "shots" / "a001.png").exists()
    assert (out / "verdict.json").exists() and "no `cd`, no `&&`" in prompts[0] and "a denied command means it broke" in prompts[0]
    st = judge.summary(cost)["stages"]["play_verifier"]
    assert st["ok"] and st["attempts"][0]["reason"].startswith("permission denial: Bash: cd /x && play phase2")
    assert st["attempts"][0]["permission_denials"] == 1


def test_play_verifier_fails_twice(tmp_path, monkeypatch):
    dv, out, _, _ = play_setup(tmp_path, monkeypatch, [("x", rec(), None), ("y", rec(), "{not json")])
    cost = {}
    v, _ = functions.play_verifier(FakeTask(), "fine", FakeAd(), None, "inst", dv, out, cost)
    assert all(x["judge_failed"] == "play_verifier: verdict.json is not parseable JSON" for x in v.values())
    h = judge.summary(cost); assert h["stages"]["play_verifier"]["attempts"][0]["reason"] == "no verdict.json"
    assert [f["stage"] for f in h["failed"]] == ["play_verifier"]


# ---- arbitration
def test_arbitrate_never_one_verifier_only_after_a_judge_failure(tmp_path, monkeypatch):
    monkeypatch.setattr(functions, "claude", lambda *a, **kw: pytest.fail("no arbiter call expected"))
    rv = {i: {"score": 1, "could_not_test": False} for i in IDS}
    pv = {i: judge.placeholder("play_verifier", "no verdict.json") for i in IDS}
    fin = functions.arbitrate(ITEMS, rv, pv, tmp_path, {})
    assert all(s is None and d == "judge failed: play_verifier: no verdict.json" for s, d in fin.values())


def test_arbitrate_real_could_not_test_and_arbiter_failure(tmp_path, monkeypatch):
    calls = []
    monkeypatch.setattr(functions, "claude", lambda p, out, name, **kw: calls.append(name) or (ans(["CR2"]), rec()))
    rv = {"CR1": {"score": None, "could_not_test": True, "reason": "replay broke"}, "CR2": {"score": 1}, "FP1": {"score": 1}}
    pv = {"CR1": {"score": None, "could_not_test": True, "reason": "budget"}, "CR2": {"score": 0}, "FP1": {"score": 0}}
    cost = {}
    fin = functions.arbitrate(ITEMS, rv, pv, tmp_path, cost)
    assert fin["CR1"] == (None, "both verifiers could not test")                       # real verdicts: unchanged
    assert fin["CR2"][0] == 1.0 and fin["FP1"] == (None, "arbitration failed: items missing: FP1")
    assert calls == ["arbiter", "arbiter.followup", "arbiter.try2"] and judge.summary(cost)["failed"][0]["stage"] == "arbiter"   # missing-item follow-up first


# ---- visual judge
def test_visual_judge_failure(tmp_path, monkeypatch):
    class Task:
        def items(self, col): return [{"id": "VP1", "item": "x", "anchors": {}}]
    import subprocess
    monkeypatch.setattr(visual.subprocess, "run", lambda *a, **kw: subprocess.CompletedProcess(a, 0))
    monkeypatch.setattr(visual, "keyframes", lambda *a: []); monkeypatch.setattr(visual, "resolve", lambda *a, **kw: (None, []))
    (tmp_path / "dv" / "shots").mkdir(parents=True); (tmp_path / "dv" / "shots" / "s.png").write_bytes(b"")
    answers = [("", rec("timeout", is_error=True, timeout=True)), (json.dumps({"items": [{"id": "VP1", "score": 0.5}]}), rec())]
    monkeypatch.setattr(visual, "claude", lambda p, out, name, **kw: answers.pop(0))
    cost = {}
    res = visual.evaluate(Task(), "g", tmp_path / "dv", [], None, tmp_path / "v", cost)
    assert res[0]["score"] == 0.5 and judge.summary(cost)["stages"]["visual_judge"]["attempts"][0]["reason"] == "timeout"
    answers[:] = [("nope", rec()), ("nope", rec())]
    cost = {}
    res = visual.evaluate(Task(), "g", tmp_path / "dv", [], None, tmp_path / "v", cost)
    assert res[0]["could_not_test"] and res[0]["evidence"]["judge_failed"] == "visual_judge: no parseable JSON"
    assert judge.incidents(cost)[0]["code"] == "judge_failed"


# ---- eval_trial: a judge that failed for good -> result.json infra_error judge_failed with the judge health, not scored
def test_eval_trial_judge_failed_is_infra_error(tmp_path, monkeypatch):
    from gab.eval import run
    from gab.host import base as hbase
    from gab.game import base as gbase
    class Seat:
        def release(self): pass
    class Ad:
        name = "g"
        def version(self): return {}
        def provision(self, inst): pass
        def install_mod(self, inst, mod): pass
    class Task:
        id = "T-VO-x"; game = "g"; meta = {"version": 1}; hidden = {"instructions_meta": {"explicit_requirements": []}}
        def items(self, col): return []
    monkeypatch.setattr(hbase, "get", lambda *a: type("HB", (), {"acquire": lambda self, *a: Seat()})())
    monkeypatch.setattr(gbase, "get", lambda *a: Ad())
    monkeypatch.setattr(run, "gate_check", lambda *a: {"pass": True, "build": True, "load": True, "log": None})
    monkeypatch.setattr(run.regression, "evaluate", lambda *a: ({"pass": True, "fail": None, "checks": [], "reason": None}, 0.0))   # the gate block
    def fev(task, level, ad, seat, inst, dv, out, cost):
        judge.run(cost, "play_verifier", lambda n: ("", rec()), functions.json_judge(["CR1"])); return [], [], 0.0
    monkeypatch.setattr(run.functions, "evaluate", fev)
    monkeypatch.setattr(run.visual, "evaluate", lambda *a: [])
    d = tmp_path / "runs" / "r" / "T-VO-x" / "fine"; (d / "agent").mkdir(parents=True); (d / "deliverable" / "mod").mkdir(parents=True)
    (d / "trial.json").write_text(json.dumps({"task": "T-VO-x", "level": "fine", "name": "r"}))
    r = run.eval_trial(d, Task(), "fine", force=True)
    assert r["status"] == "infra_error" and r["infra"]["primary"] == "judge_failed" and r["scores"]["C"]["content_realization"] is None
    assert r["judges"]["calls"] == 2 and r["judges"]["retries"] == 1 and r["judges"]["failed"][0]["reason"] == "no parseable JSON"
    assert "play_verifier: no parseable JSON" in r["infra"]["incidents"][0]["message"]
    led = (tmp_path / "runs" / "_infra" / "ledger.jsonl").read_text()
    assert "judge_failed" in led


def test_loose_json_recovers_items_after_a_stray_brace():
    from gab.util import loose_json
    bad = '{"items":[{"id":"CR1","score":1,"reason":"ok"},{"id":"FP1","score":0.5,"evidence":["x"],"reason":"y"}}]}'
    d = loose_json(bad)
    assert [i["id"] for i in d["items"]] == ["CR1", "FP1"] and d["items"][1]["score"] == 0.5
