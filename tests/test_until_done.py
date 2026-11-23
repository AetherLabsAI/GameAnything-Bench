"""agent/until_done.py: continue the same session until deliverable/DONE or the budget is used up"""
from gab.agent.base import AgentRun, Budget
from gab.agent.until_done import CONTINUE, UntilDone


class Fake:
    """segment 1 ends early without DONE (like a tool call returned as text); segment 2 (resumed) writes DONE"""
    can_resume = True

    def __init__(self, plan):
        self.model, self.opts, self.plan, self.calls = "m", {}, plan, []

    def session_of(self, p): return "S1"
    def version(self): return "v"

    def run(self, workdir, instruction, budget, tools):
        k = len(self.calls); self.calls.append((instruction, dict(self.opts)))
        tr = workdir.parent / "transcript.jsonl"; tr.write_text(f'{{"seg": {k}}}\n')
        tools_used, done = self.plan[k]
        if done: (workdir / "deliverable" / "DONE").write_text("")
        return AgentRun(transcript=str(tr), exit_reason="done", tokens_in=10, extra={"tool_calls": tools_used})


def make_work(tmp_path):
    w = tmp_path / "work"; (w / "deliverable").mkdir(parents=True); return w


def test_resumes_until_done(tmp_path):
    w = make_work(tmp_path); f = Fake([(3, False), (5, True)])
    r = UntilDone(f).run(w, "BRIEF", Budget(minutes=60), {})
    assert [c[0] for c in f.calls] == ["BRIEF", CONTINUE] and f.calls[1][1]["resume"] == "S1"
    assert r.extra["done_marked"] and r.extra["continuations"] == 1 and r.extra["tool_calls"] == 8 and r.tokens_in == 20
    assert (tmp_path / "transcript.jsonl").read_text() == '{"seg": 0}\n{"seg": 1}\n'          # parts joined


def test_stops_after_idle_continuations(tmp_path, monkeypatch):
    monkeypatch.setenv("GAB_UNTIL_DONE_MAX_IDLE", "2")
    w = make_work(tmp_path); f = Fake([(0, False), (0, False), (0, False), (0, False)])
    r = UntilDone(f).run(w, "BRIEF", Budget(minutes=60), {})
    assert len(f.calls) == 2 and r.extra["early_stop"] and not r.extra["done_marked"]


class Crashy(Fake):
    """segments per plan: (tool_calls, done, error text or None) -- an error makes the segment a crash with that error"""
    def run(self, workdir, instruction, budget, tools):
        k = len(self.calls); self.calls.append((instruction, dict(self.opts)))
        tr = workdir.parent / "transcript.jsonl"; tr.write_text(f'{{"seg": {k}}}\n')
        tools_used, done, err = self.plan[k]
        if done: (workdir / "deliverable" / "DONE").write_text("")
        return AgentRun(transcript=str(tr), exit_reason="crash" if err else "done", tokens_in=10,
                        extra={"tool_calls": tools_used, **({"errors": [err]} if err else {})})


def test_transient_api_crash_resumes_same_session(tmp_path, monkeypatch):
    from gab.agent import until_done as U
    monkeypatch.setattr(U.time, "sleep", lambda s: None)
    w = make_work(tmp_path)
    f = Crashy([(4, False, '{"type":"error","error":{"message":"Download multimodal file timed out","statusCode":400}}'), (6, True, None)])
    r = UntilDone(f).run(w, "BRIEF", Budget(minutes=60), {})
    assert [c[0] for c in f.calls] == ["BRIEF", U.INFRA_CONTINUE] and f.calls[1][1]["resume"] == "S1"
    assert r.exit_reason == "done" and r.extra["done_marked"] and r.extra["infra_resumes"] == 1 and r.extra["session_id"] == "S1"
    assert r.extra["segments"][0]["infra_resume"]["code"] == "api_media_timeout" and r.extra["tool_calls"] == 10


def test_systematic_api_crash_stops(tmp_path, monkeypatch):
    from gab.agent import until_done as U
    monkeypatch.setattr(U.time, "sleep", lambda s: None)
    w = make_work(tmp_path)
    f = Crashy([(4, False, 'Invalid JSON payload received. Unknown name "$schema" at \'tools[0].function_declarations[0].parameters\''),
                (6, True, None)])
    r = UntilDone(f).run(w, "BRIEF", Budget(minutes=60), {})
    assert len(f.calls) == 1 and r.exit_reason == "crash" and r.extra["infra_resumes"] == 0 and r.extra["session_id"] == "S1"


def test_resume_limit(tmp_path, monkeypatch):
    from gab.agent import until_done as U
    monkeypatch.setattr(U.time, "sleep", lambda s: None); monkeypatch.setenv("GAB_INFRA_RESUME_MAX", "2")
    w = make_work(tmp_path); e = '{"status": 503, "message": "Service Unavailable"}'
    f = Crashy([(1, False, e)] * 5)
    r = UntilDone(f).run(w, "BRIEF", Budget(minutes=60), {})
    assert len(f.calls) == 3 and r.exit_reason == "crash" and r.extra["infra_resumes"] == 2


def test_crash_after_done_is_done(tmp_path, monkeypatch):
    from gab.agent import until_done as U
    monkeypatch.setattr(U.time, "sleep", lambda s: None)
    w = make_work(tmp_path); f = Crashy([(4, True, "Download multimodal file timed out"), (1, True, None)])
    r = UntilDone(f).run(w, "BRIEF", Budget(minutes=60), {})
    assert len(f.calls) == 1 and r.exit_reason == "done" and r.extra["crash_after_done"]


def test_done_before_min_minutes_is_not_accepted(tmp_path):
    """refine rounds: DONE right away is removed and the agent continues (a fake clock: each segment takes 5 minutes)"""
    import time as _t
    from gab.agent import until_done as ud
    w = make_work(tmp_path); f = Fake([(3, True), (2, True), (4, True), (1, True)])
    clock = [1000.0]; real = ud.time.time
    def fake_run(*a, _orig=Fake.run, **k):
        clock[0] += 300; return _orig(f, *a, **k)
    f.run = fake_run
    ud.time.time = lambda: clock[0]
    try: r = UntilDone(f, min_minutes=12).run(w, "BRIEF", Budget(minutes=40), {})
    finally: ud.time.time = real
    assert len(f.calls) == 3 and f.calls[1][0].startswith("You marked the hand-in as done after 5 minutes")
    assert r.extra["done_too_early"] == 2 and r.extra["done_marked"]


def test_unknown_session_gets_fresh_prompt(tmp_path):
    """a session id the harness no longer has (codex run before sessions were kept): a fresh session with the full prompt"""
    w = make_work(tmp_path); f = Fake([(3, True)]); f.has_session = lambda sid, wd: False
    UntilDone(f).run(w, "CONTINUE ONLY", Budget(minutes=60), {}, resume="OLD", fresh="FULL BRIEF")
    assert f.calls[0][0] == "FULL BRIEF" and "resume" not in f.calls[0][1]


def test_codex_resume_command(tmp_path):
    """2026-11-08: codex continuations resume the same session (`codex exec resume <id>`: no -s / -C, sandbox as config)"""
    from gab.agent import base
    a = base.get("codex", model="m", base_url="http://x/v1", api_key_env="K", sandbox="danger-full-access")
    w = make_work(tmp_path); home = tmp_path / "agent_home"
    cmd, _ = a.command(w, "BRIEF", home)
    assert cmd[1:3] == ["exec", "--json"] and "--ephemeral" not in cmd and cmd[cmd.index("-s") + 1] == "danger-full-access"
    a.opts["resume"] = "abc-123"
    cmd, _ = a.command(w, "GO ON", home)
    assert cmd[1:3] == ["exec", "resume"] and "-s" not in cmd and "-C" not in cmd and 'sandbox_mode="danger-full-access"' in cmd
    assert cmd[-3:] == ["--", "abc-123", "GO ON"]
    assert not a.has_session("abc-123", w)
    s = home / ".codex" / "sessions" / "2026" / "11" / "08"; s.mkdir(parents=True)
    (s / "rollout-2026-11-08T20-50-56-abc-123.jsonl").write_text("{}\n")
    assert a.has_session("abc-123", w)


def test_codex_resumed_usage_is_per_segment(tmp_path):
    """codex reports the thread's running total on turn.completed: a resumed segment counts only its own tokens"""
    from gab.agent import base
    from gab.agent.base import AgentRun
    a = base.get("codex", model="m")
    ev = lambda i, c, o: [{"type": "thread.started", "thread_id": "T1"}, {"type": "turn.completed",
                          "usage": {"input_tokens": i, "cached_input_tokens": c, "output_tokens": o}}]
    r1 = AgentRun(); a.usage(ev(1000, 800, 50), r1)
    assert (r1.tokens_in, r1.tokens_cached, r1.tokens_out) == (200, 800, 50)
    a.opts["resume"] = "T1"; r2 = AgentRun(); a.usage(ev(1300, 1050, 60), r2)
    assert (r2.tokens_in, r2.tokens_cached, r2.tokens_out) == (50, 250, 10)
    # another gab process: the baseline comes from the session's rollout file
    b = base.get("codex", model="m", resume="T1"); home = tmp_path / "h"; s = home / ".codex" / "sessions"; s.mkdir(parents=True)
    (s / "rollout-x-T1.jsonl").write_text('{"type":"event_msg","payload":{"type":"token_count","info":{"total_token_usage":'
                                          '{"input_tokens":1000,"cached_input_tokens":800,"output_tokens":50}}}}\n')
    b.command(tmp_path / "work", "GO ON", home); r3 = AgentRun(); b.usage(ev(1300, 1050, 60), r3)
    assert (r3.tokens_in, r3.tokens_cached, r3.tokens_out) == (50, 250, 10)


def _claude_result(sid, total, own=None, cache_read=1000):
    """a Claude Code result event: total_cost_usd is the session's running total; own: model_usage_since_last_result cost
    (None: an older CLI without the field)"""
    e = {"type": "result", "subtype": "success", "session_id": sid, "is_error": False, "num_turns": 2, "result": "ok",
         "total_cost_usd": total, "usage": {"input_tokens": 6, "output_tokens": 50, "cache_read_input_tokens": cache_read,
                                            "cache_creation_input_tokens": 100},
         "modelUsage": {"claude-x": {"costUSD": total}}}
    if own is not None: e["model_usage_since_last_result"] = {"claude-x": {"costUSD": own, "inputTokens": 6}}
    return e


class ClaudeSegments:
    """UntilDone's inner harness: every segment resumes the same Claude Code session; its transcript's result event carries
    the session's CUMULATIVE total_cost_usd (as `claude -p --resume` does), usage parsed by the real claude_code adapter"""
    can_resume = True

    def __init__(self, totals, with_since=True):
        from gab.agent import base
        self.cc = base.get("claude_code", model="m")
        self.model, self.opts, self.totals, self.with_since, self.calls = "m", {}, totals, with_since, 0

    def session_of(self, p): return "S1"
    def version(self): return "v"

    def run(self, workdir, instruction, budget, tools):
        import json
        k = self.calls; self.calls += 1
        prev = self.totals[k - 1] if k else 0.0
        tr = workdir.parent / "transcript.jsonl"
        tr.write_text(json.dumps({"type": "system", "subtype": "init", "session_id": "S1", "model": "m"}) + "\n"
                      + json.dumps(_claude_result("S1", self.totals[k], round(self.totals[k] - prev, 6) if self.with_since else None)) + "\n")
        if k == len(self.totals) - 1: (workdir / "deliverable" / "DONE").write_text("")
        r = AgentRun(transcript=str(tr), exit_reason="done")
        self.cc.usage([json.loads(x) for x in tr.read_text().splitlines()], r)
        r.extra["tool_calls"] = 1                 # not idle
        return r


def test_claude_resumed_cost_is_per_segment(tmp_path):
    """sonnet55 refine 2026-11-09: 74 resumed segments reported a running total (0.40 ... 2.57) and were summed to 108.85;
    each segment now counts only its own cost (model_usage_since_last_result), tokens stay the per-run usage block"""
    import pytest
    totals = [0.40, 0.53, 0.57, 2.53, 2.57]
    for since in (True, False):                   # False: an older CLI, delta to the session's previous total
        w = tmp_path / f"w{since}"; (w / "deliverable").mkdir(parents=True)
        r = UntilDone(ClaudeSegments(totals, with_since=since)).run(w, "BRIEF", Budget(minutes=60), {})
        assert r.extra["continuations"] == 4
        assert r.usd == pytest.approx(2.57)
        assert [s["usd"] for s in r.extra["segments"]] == pytest.approx([0.40, 0.13, 0.04, 1.96, 0.04])
        assert r.tokens_cached == 5 * 1100 and r.tokens_out == 5 * 50


def test_claude_result_cost():
    from gab.agent.claude_code.adapter import usage_events
    import pytest
    # the field is authoritative even when the session's running total was not restored on resume
    assert usage_events([_claude_result("S", 1.1854, 1.1854)], {"S": 0.0925})["usd"] == pytest.approx(1.1854)
    # an error result with no new spend
    assert usage_events([{**_claude_result("S", 2.9, 0.0), "model_usage_since_last_result": {}}])["usd"] == 0
    # fallback without the field: a session first seen counts in full; a lower total than the kept one too
    t = {}
    assert usage_events([_claude_result("S", 0.5)], t)["usd"] == pytest.approx(0.5) and t == {"S": 0.5}
    assert usage_events([_claude_result("S", 0.8)], t)["usd"] == pytest.approx(0.3)
    assert usage_events([_claude_result("S", 0.2)], t)["usd"] == pytest.approx(0.2)


def test_claude_two_results_in_one_process():
    """one `claude -p` emitted two result events (sonnet55 T-IO-38d193ed23: totals 2.329 then 2.379): cost = the run's own
    (not 2.329 + 2.379), tokens = both results' usage (each counts since the previous result)"""
    from gab.agent.claude_code.adapter import usage_events
    import pytest
    u = usage_events([_claude_result("S", 2.329, 2.329, cache_read=8000), _claude_result("S", 2.379, 0.050, cache_read=300)])
    assert u["usd"] == pytest.approx(2.379) and u["cache_read"] == 8300 and u["tokens_out"] == 100
