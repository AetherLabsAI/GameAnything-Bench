"""stop `gab run` / `gab refine` and continue (2026-11-08, gab/run/interrupt.py): a SIGTERM records each running agent as
interrupted; the same command continues it in the same session with the time left; a killed run is inferred at restart"""
import json, os, signal, subprocess, sys, time
from pathlib import Path

import pytest

from gab.run import interrupt, trial
from gab.refine import refine
from gab.agent import base as abase
from gab.agent.base import AgentRun, Budget, watchdog_kill
from gab.agent.until_done import UntilDone
from gab.util import rjson, wjson


@pytest.fixture(autouse=True)
def clear_stop():
    interrupt.STOP.clear(); yield; interrupt.STOP.clear()


class FakeTask:
    id, game = "T-IO-test", "stardew_valley"
    mdir = Path("/nonexistent")
    def instruction(self, level): return "Make sowing."
    def references(self, kind=None): return []
    def clips(self): return []


class FakeAd:
    name = "stardew_valley"
    def provision(self, inst): pass
    def stop(self, inst): pass


class Agent:
    """a harness: `signal` = send SIGTERM handling mid-run (the real watchdog then stops a real child process)"""
    def __init__(self, sid="SESS-1", can_resume=True, signal_mid=False, done=False):
        self.model, self.opts, self.can_resume, self.sid = "m", {}, can_resume, sid
        self.signal_mid, self.done, self.calls = signal_mid, done, []

    def session_of(self, p): return self.sid if self.can_resume else None
    def version(self): return "v"

    def run(self, workdir, instruction, budget, tools):
        self.calls.append({"prompt": instruction, "resume": self.opts.get("resume"), "minutes": budget.minutes})
        tr = Path(workdir).parent / "agent" / "transcript.jsonl"; tr.parent.mkdir(parents=True, exist_ok=True)
        tr.write_text(json.dumps({"type": "step_start", "sessionID": self.sid}) + "\n")
        if self.signal_mid:
            p = subprocess.Popen([sys.executable, "-c", "import time; time.sleep(60)"], start_new_session=True)
            interrupt._handler(signal.SIGTERM, None)                # what the operator's kill -TERM does in gab
            reason = watchdog_kill(p, time.time() + 600)
            assert p.poll() is not None                              # the harness process was stopped
            return AgentRun(transcript=str(tr), exit_reason=reason, tokens_in=100, usd=0.5, extra={"tool_calls": 3})
        if self.done: (Path(workdir) / "deliverable" / "DONE").write_text("")
        return AgentRun(transcript=str(tr), exit_reason="done", tokens_in=10, usd=0.1, extra={"tool_calls": 1})


def _stub_trial(monkeypatch, tmp_path, agent):
    def stage(d, task, level, game, minutes=120.0, brief="v1"):
        w = d / "work"; (w / "deliverable" / "mod").mkdir(parents=True, exist_ok=True); return w, "BRIEF"
    monkeypatch.setattr(trial, "stage", stage)
    monkeypatch.setattr(trial, "write_ctx", lambda *a, **k: None)
    monkeypatch.setattr(trial, "tools", lambda *a, **k: {})
    monkeypatch.setattr(trial, "agent_infra", lambda *a, **k: {"status": None, "primary": None, "incidents": []})
    monkeypatch.setattr(abase, "get", lambda *a, **k: agent)


def test_watchdog_stops_the_harness_on_a_signal():
    p = subprocess.Popen([sys.executable, "-c", "import time; time.sleep(60)"], start_new_session=True)
    interrupt.STOP.set()
    assert watchdog_kill(p, time.time() + 600) == "interrupted" and p.poll() is not None


def test_until_done_does_not_continue_an_interrupted_segment(tmp_path):
    w = tmp_path / "work"; (w / "deliverable").mkdir(parents=True)
    a = Agent(signal_mid=True)
    r = UntilDone(a).run(w, "BRIEF", Budget(minutes=60), {})
    assert r.exit_reason == "interrupted" and len(a.calls) == 1 and r.extra["session_id"] == "SESS-1"


def test_sigterm_records_and_run_resumes_same_session(tmp_path, monkeypatch):
    d = tmp_path / "run" / "T-IO-test" / "fine"; d.mkdir(parents=True)
    a = Agent(signal_mid=True); _stub_trial(monkeypatch, tmp_path, a)
    run, leaks, inf = trial.run_agent(d, FakeTask(), "fine", "opencode", "m", {}, 30, None, FakeAd(), attempt=1)
    assert run.exit_reason == "interrupted" and inf["primary"] == "harness_interrupted"
    it = rjson(d / "agent" / "interrupted.json")
    assert it["mode"] == "run" and it["session_id"] == "SESS-1" and it["budget_min"] == 30
    ag = rjson(d / "agent" / "agent.json"); assert ag["infra"]["status"] == "infra_error" and ag["extra"]["session_id"] == "SESS-1"
    # 12 minutes had been used (as if the segment ran that long)
    ag["wall_s"] = 720; wjson(d / "agent" / "agent.json", ag)
    interrupt.STOP.clear()
    b = Agent(done=True); _stub_trial(monkeypatch, tmp_path, b)
    run2, _, inf2 = trial.run_agent(d, FakeTask(), "fine", "opencode", "m", {}, 30, None, FakeAd(), attempt=2)
    assert b.calls[0]["resume"] == "SESS-1" and abs(b.calls[0]["minutes"] - 18) < 0.1
    assert b.calls[0]["prompt"].startswith("Your session was interrupted: the benchmark operator")
    assert run2.exit_reason == "done" and run2.wall_s >= 720 and abs(run2.usd - 0.6) < 1e-6 and run2.tokens_in == 110
    assert not (d / "agent" / "interrupted.json").exists() and not (d / "agent" / "resume.json").exists()


def test_fallback_fresh_session_with_remaining_time(tmp_path, monkeypatch):
    d = tmp_path / "run" / "T-IO-test" / "fine"; (d / "work" / "deliverable").mkdir(parents=True); (d / "agent").mkdir()
    blk = {"status": "infra_error", "primary": "harness_interrupted", "incidents": [trial.infra.make("harness_interrupted", "x")]}
    wjson(d / "agent" / "agent.json", {"wall_s": 600, "usd": 1.0, "infra": blk, "extra": {"session_id": None}})
    b = Agent(can_resume=False, done=True); _stub_trial(monkeypatch, tmp_path, b)
    trial.run_agent(d, FakeTask(), "fine", "codex", "m", {}, 30, None, FakeAd(), attempt=2)
    c = b.calls[0]
    assert c["resume"] is None and abs(c["minutes"] - 20) < 0.1
    assert c["prompt"].startswith("BRIEF") and "could not be continued" in c["prompt"] and "About 20 minutes" in c["prompt"]


def test_killed_run_is_inferred_at_restart(tmp_path):
    d = tmp_path / "t"; (d / "work" / "deliverable").mkdir(parents=True); (d / "agent").mkdir()
    started = time.time() - 900
    m = {"started": time.strftime("%Y-%m-%dT%H:%M:%S", time.localtime(started)), "attempt": 1, "attempts": [], "model": "m"}
    wjson(d / "trial.json", m)
    (d / "agent" / "transcript.jsonl").write_text('{"type":"x","sessionID":"ses_K"}\n')
    r = trial.mark_interrupted(d, m, 30, apply=True)
    assert r and r[1] == "ses_K" and 14 < r[0] < 16
    rs = trial.resume_state(d, 30)
    assert rs["sid"] == "ses_K" and 14 < rs["left"] < 16
    assert rjson(d / "agent" / "interrupted.json")["mode"] == "run"
    assert trial.mark_interrupted(d, rjson(d / "trial.json"), 30) is None                          # recorded once


def test_spent_interrupt_is_finished_not_rerun(tmp_path):
    d = tmp_path / "t"; (d / "work" / "deliverable").mkdir(parents=True); (d / "agent").mkdir()
    blk = {"status": "infra_error", "primary": "harness_interrupted", "incidents": [trial.infra.make("harness_interrupted", "x")]}
    wjson(d / "agent" / "agent.json", {"wall_s": 29.7 * 60, "infra": blk, "extra": {}})
    assert trial.resume_state(d, 30) is None and trial.finish_spent_interrupt(d, 30)
    ag = rjson(d / "agent" / "agent.json"); assert ag["exit_reason"] == "timeout" and not ag["infra"]["status"]
    assert (d / "deliverable").is_dir()


def _refine_trial_dir(tmp_path, monkeypatch, agent):
    d = tmp_path / "run" / "T-IO-test" / "fine"
    (d / "work" / "deliverable" / "mod").mkdir(parents=True); (d / "agent").mkdir()
    (d / "deliverable").mkdir()
    wjson(d / "agent" / "agent.json", {"exit_reason": "done", "wall_s": 3600, "extra": {"session_id": "SESS-1"}})
    c = d / "refine" / "r0" / "critic"; c.mkdir(parents=True)
    (c / "feedback.md").write_text("ISSUES"); wjson(c / "scores.json", {"reference_likeness": 1})
    wjson(d / "refine" / "r0" / "replay" / "record.json", {"traces": []})
    monkeypatch.setattr(trial, "write_ctx", lambda *a, **k: None)
    monkeypatch.setattr(trial, "tools", lambda *a, **k: {})
    monkeypatch.setattr(trial, "brief_text", lambda *a, **k: "BRIEF")
    monkeypatch.setattr(refine, "copy_targets", lambda task, w: 0)
    monkeypatch.setattr(refine.gbase, "get", lambda *a, **k: FakeAd())
    monkeypatch.setattr(abase, "get", lambda *a, **k: agent)
    rec = []
    monkeypatch.setattr(refine, "record_replays", lambda d, task, level, seat, out, **k: (rec.append(Path(out)), wjson(Path(out) / "record.json", {}))[1])
    monkeypatch.setattr(refine, "critic", lambda d, task, level, seat, rd, rp, **k: (wjson(Path(rd) / "critic" / "scores.json", {"reference_likeness": 3}), {"reference_likeness": 3})[1])
    return d, rec


def test_refine_round_interrupted_then_continued(tmp_path, monkeypatch):
    a = Agent(signal_mid=True)
    d, rec = _refine_trial_dir(tmp_path, monkeypatch, a)
    run, sb, sa = refine.refine_trial(d, FakeTask(), "fine", "opencode", "m", {}, 40, seat=None)
    assert run.exit_reason == "interrupted" and sa is None and not rec               # no after-review of a cut round
    it = rjson(d / "agent" / "interrupted.json"); assert it["mode"] == "refine" and it["round"] == 1 and it["session_id"] == "SESS-1"
    assert not (rjson(d / "agent" / "agent.json") or {}).get("refine_rounds") and not (d / "refine" / "r1").exists()
    # the agent had worked 10 minutes of the round; it edited review/ (must stay) and the archive count is known
    it["run"]["wall_s"] = 600; it["used_s"] = 600; wjson(d / "agent" / "interrupted.json", it)
    (d / "work" / "review" / "feedback.md").write_text("MY NOTES")
    interrupt.STOP.clear()
    b = Agent(done=True); monkeypatch.setattr(abase, "get", lambda *a, **k: b)
    seen = {}
    real = UntilDone.__init__
    def spy(self, inner, **kw): seen.update(kw); real(self, inner, **kw)
    monkeypatch.setattr(UntilDone, "__init__", spy)
    run2, sb2, sa2 = refine.refine_trial(d, FakeTask(), "fine", "opencode", "m", {}, 40, seat=None)
    c = b.calls[0]
    assert c["resume"] == "SESS-1" and abs(c["minutes"] - 30) < 0.1 and "About 30 minutes of this round are left" in c["prompt"]
    assert seen["min_minutes"] == 30 and abs(seen["elapsed0"] - 10) < 0.01                 # DONE gate over the round's total
    assert (d / "work" / "review" / "feedback.md").read_text() == "MY NOTES"               # review/ not re-copied
    rr = rjson(d / "agent" / "agent.json")["refine_rounds"]
    # DONE at minute 10 of the round is not accepted (gate at 30): the agent was sent on (7 segments, 0.1 each)
    assert len(b.calls) == 7 and b.calls[1]["prompt"].startswith("You marked the hand-in as done after 10 minutes")
    assert len(rr) == 1 and rr[0]["round"] == 1 and rr[0]["wall_s"] >= 600 and abs(rr[0]["usd"] - 1.2) < 1e-6
    assert rr[0]["started"] == it["started"] and sa2 == {"reference_likeness": 3} and rec == [d / "refine" / "r1" / "replay"]
    assert not (d / "agent" / "interrupted.json").exists() and not (d / "agent" / "refine_running.json").exists()


def test_killed_refine_round_is_inferred(tmp_path, monkeypatch):
    b = Agent(done=True)
    d, rec = _refine_trial_dir(tmp_path, monkeypatch, b)
    (d / "work" / "review").mkdir(); (d / "work" / "review" / "feedback.md").write_text("ISSUES")
    (d / "work" / "refine_brief.md").write_text("ROUND BRIEF")
    seg0 = time.time() - 900                                                                    # killed 15 min into the round
    wjson(d / "agent" / "refine_running.json", {"round": 1, "minutes": 40, "phase": "agent", "started": "2026-11-08T10:00:00",
                                                "segment_started": seg0, "prev": None})
    (d / "agent" / "transcript.jsonl").write_text('{"sessionID": "ses_R"}\n')
    rs = refine.interrupted_round(d, 1, 40)
    assert rs["sid"] == "ses_R" and 24.9 < rs["left"] < 25.1
    refine.refine_trial(d, FakeTask(), "fine", "opencode", "m", {}, 40, seat=None)
    assert b.calls[0]["resume"] == "ses_R" and 24.8 < b.calls[0]["minutes"] < 25.1
    assert rjson(d / "agent" / "agent.json")["refine_rounds"][0]["started"] == "2026-11-08T10:00:00"


def test_refine_review_completed_after_a_stop_in_review(tmp_path, monkeypatch):
    b = Agent(done=True)
    d, rec = _refine_trial_dir(tmp_path, monkeypatch, b)
    wjson(d / "agent" / "refine_running.json", {"round": 1, "phase": "review", "started": "2026-11-08T10:00:00",
                                                "run": AgentRun(exit_reason="done", wall_s=1500, usd=2.0).to_dict()})
    run, sb, sa = refine.refine_trial(d, FakeTask(), "fine", "opencode", "m", {}, 40, seat=None)
    assert not b.calls and run.wall_s == 1500 and sa == {"reference_likeness": 3}
    assert rjson(d / "agent" / "agent.json")["refine_rounds"][0]["round"] == 1
