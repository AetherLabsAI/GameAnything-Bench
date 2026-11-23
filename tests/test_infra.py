"""infrastructure errors (gab/run/infra.py): signatures on the pilot's real messages, redaction, the infra_error decision, the ledger
summary, the `game` call log with a fake adapter, the retry / stop policy, the after-run scan and the preflight's OpenRouter check"""
import io, json, time, urllib.error
from pathlib import Path

import pytest

from gab.run import control, infra, preflight, sched, trial
from gab.agent.base import AgentRun

REAL = [   # (message seen 2026-11-05, code)
    ("permission denied while trying to connect to the docker API at unix:///var/run/docker.sock", "docker_socket_denied"),
    ('Get "http://%2Fvar%2Frun%2Fdocker.sock/v1.47/containers/json": dial unix /var/run/docker.sock: connect: operation not permitted', "docker_socket_denied"),
    ("Error: code mode host executable was not found", "agent_binary_missing"),
    ("bwrap: Failed RTM_NEWADDR: Operation not permitted", "bwrap_sandbox"),
    ("bwrap: Failed to make / slave: Permission denied", "bwrap_sandbox"),
    ('{"error":{"message":"Insufficient credits. Add more using https://openrouter.ai/settings/credits","code":402}}', "api_credits"),
    ('{"error":{"message":"The request is prohibited due to a violation of provider Terms Of Service.","code":403}}', "api_tos_block"),
    ("error : Unable to load the service index for source https://api.nuget.org/v3/index.json.", "game_env_offline"),
    ("error :   Resource temporarily unavailable (api.nuget.org:443)", "game_env_offline"),
    ("SDL_Init failed: Couldn't find matching GLX visual", "gl_init_failed"),
    ("Failed to create OpenGL Context", "gl_init_failed"),
    ('API Error: 429 {"type":"error","error":{"type":"rate_limit_error","message":"Rate limited"}}', "api_rate_limit"),
    ('{"type":"error","error":{"type":"overloaded_error","message":"Overloaded"}}', "api_rate_limit"),
    ("API Error: 502 Bad Gateway", "api_upstream"),
    ("Error response from daemon: container gab-r-T-x-f-1a2b3c is not running", "container_gone"),
    ("Error: No such container: gab-r-T-x-f-1a2b3c", "container_gone"),
    ("RuntimeError: seat screen :101 did not come up in gab-x: ", "seat_display"),
    ("cp: error writing 'x': No space left on device", "disk_full"),
    ("Command returned non-zero exit status 137 (OOMKilled)", "oom_killed"),
    ("RuntimeError: docker pull ghcr.io/a/b@sha256:00 failed: manifest unknown", "image_pull_failed"),
    ('{"error": {"code": 401, "message": "No auth credentials found"}}', "api_auth"),
    ("EACCES: permission denied, mkdir '/home/w/runs/r/T-x/fine/agent_home/opencode/data/opencode'", "agent_home_permission"),
    ('{"message": "模型 kimi-k3-fireworks-0.3 已下架 (request id: 762e)", "statusCode": 410, "isRetryable": false}', "api_model_unavailable"),
    ("Function call is missing a thought_signature in functionCall parts. This is required for tools to work correctly", "api_history_rejected"),
    ('{"error":{"code":"invalid_parameter_error","message":"Failed to download multimodal content","type":"invalid_request_error"}}', "api_media_timeout"),
    ('{"name": "UnknownError", "data": {"message": "{\\"message\\":\\"Upstream response stream was interrupted\\",\\"type\\":\\"upstream_error\\",\\"code\\":\\"upstream_stream_read_error\\"}"}}', "api_upstream"),
    ('{"type": "error", "message": "You\\u2019ve hit your usage limit. Visit https://chatgpt.com/codex/settings/usage to purchase more credits or try again at Nov 13th, 2026 6:27 AM."}', "subscription_limit"),
]


@pytest.mark.parametrize("msg,code", REAL)
def test_classify_real_messages(msg, code):
    m = infra.classify(msg)
    assert m and m[0]["code"] == code, m
    assert m[0]["kind"] == infra.BY[code].kind and m[0]["hint"] and len(m[0]["message"]) <= 500


def test_classify_quiet_on_ordinary_text():
    for t in ("the mod loaded fine", "line 401 of player.lua: nil value", "frame took 429 ms", "error: unknown item 'sword'",
              "Traceback (most recent call last):\n  File \"mod.py\", line 3\nNameError: x"):
        assert infra.classify(t, exclude=("model-api",)) == [], t
    assert [m["code"] for m in infra.classify("HTTP 401 from the server", components=("host/docker",))] == []


def test_redaction():
    t = ("OPENROUTER_API_KEY=sk-or-v1-0123456789abcdef0123456789abcdef GITHUB_TOKEN=ghp_abcdefghijklmnopqrstuvwxyz0123 "
         "Authorization: Bearer abc.def-ghi_jkl012345 api_key: 'k123456789' sk-ant-api03-zzzzzzzzzzzzzzzzzzzz input_tokens=5")
    r = infra.redact(t)
    for s in ("sk-or-v1-0123", "ghp_abc", "abc.def-ghi", "k123456789", "sk-ant-api03"): assert s not in r
    assert "OPENROUTER_API_KEY=[REDACTED]" in r and "input_tokens=5" in r
    m = infra.classify("Insufficient credits for key sk-or-v1-0123456789abcdef0123456789abcdef")
    assert "sk-or" not in m[0]["message"]


def inc(code, n=1): return {**infra.make(code, "x"), "n": n}


def test_decide():
    assert infra.decide([]) == (None, None)
    assert infra.decide([inc("api_rate_limit"), inc("docker_socket_denied")]) == ("infra_error", "docker_socket_denied")
    assert infra.decide([inc("api_rate_limit")], exit_reason="done") == (None, "api_rate_limit")     # recovered: a normal run
    assert infra.decide([inc("api_rate_limit")], exit_reason="crash")[0] == "infra_error"            # the API killed the run
    assert infra.decide([inc("container_gone")], exit_reason="done", early=True)[0] == "infra_error"
    assert infra.decide([inc("seat_display")], exit_reason="done", infra_calls=2)[0] is None
    assert infra.decide([inc("seat_display", 3)], exit_reason="done", infra_calls=3)[0] == "infra_error"
    b = infra.assess([inc("api_upstream"), inc("api_upstream")], "crash")
    assert b["incidents"][0]["n"] == 2 and infra.transient_only(b)
    assert not infra.transient_only(infra.assess([inc("api_upstream"), inc("bwrap_sandbox")]))


def test_record_and_ledger(tmp_path):
    runs = tmp_path / "runs"
    for run, task in (("r1", "T-a"), ("r1", "T-b"), ("r2", "T-a")):
        d = runs / run / task / "fine"; d.mkdir(parents=True)
        infra.record(d, {**infra.make("docker_socket_denied", "dial unix /var/run/docker.sock: connect: operation not permitted TOKEN=abc"), "n": 4})
    infra.record(runs / "r2" / "T-a" / "fine", inc("api_rate_limit"))
    assert len((runs / "r1" / "T-a" / "fine" / "infra.jsonl").read_text().splitlines()) == 1
    rec = [json.loads(x) for x in infra.ledger_path(runs).read_text().splitlines()]
    assert [r["code"] for r in rec].count("docker_socket_denied") == 3 and all("abc" not in json.dumps(r) for r in rec)


# ---- gab.run.control: every call logged; an infra failure answered as an ENVIRONMENT error
class FakeSeat:
    def copy_out(self, a, b): return True


class FakeAd:
    screen = (800, 600); extra_help = ""; seat = FakeSeat()
    def __init__(self, start=(True, "ok"), boom=None): self.start_r, self.boom = start, boom
    def attach(self, inst, mod): pass
    def stop(self, inst): pass
    def install_mod(self, inst, mod): pass
    def start(self, inst):
        if self.boom: raise self.boom
        return self.start_r
    def screenshot(self, inst, p): return str(p)
    def state(self, inst): return {"running": True}


def ctl(tmp_path, monkeypatch, ad, *argv):
    t = tmp_path / "trial"; ctx = t / "tools" / "ctx.json"; wd = t / "work"
    (wd / ".gab").mkdir(parents=True, exist_ok=True); ctx.parent.mkdir(parents=True, exist_ok=True)
    c = {"game": "g", "inst": "dev", "workdir": str(wd), "deadline": 9e12, "mode": "dev"}; ctx.write_text(json.dumps(c))
    monkeypatch.setattr(control, "load_ctx", lambda p: (dict(c), ad))
    buf = io.StringIO(); monkeypatch.setattr("sys.stdout", buf)
    control.main(["--ctx", str(ctx), *argv])
    calls = ctx.parent / "calls.jsonl"
    return json.loads(buf.getvalue()), [json.loads(l) for l in calls.read_text().splitlines()] if calls.is_file() else [], t


def test_control_logs_calls_and_marks_infra(tmp_path, monkeypatch):
    a, L, _ = ctl(tmp_path, monkeypatch, FakeAd(), "game", "time")
    assert "environment_error" not in a and L[-1]["cmd"] == "time" and L[-1]["ok"] and "infra" not in L[-1]
    a, L, _ = ctl(tmp_path, monkeypatch, FakeAd(start=(False, "docker exec: dial unix /var/run/docker.sock: connect: operation not permitted")), "game", "start")
    assert a["ok"] is False and a["environment_error"]["code"] == "docker_socket_denied"
    assert "ENVIRONMENT ERROR" in a["environment_error"]["note"] and "fabricate" in a["environment_error"]["note"]
    assert list(a)[0] == "environment_error"                                 # survives the answer's length cut
    r = L[-1]; assert r["infra"] == "docker_socket_denied" and r["kind"] == "systematic" and not r["ok"] and r["s"] >= 0
    a, L, _ = ctl(tmp_path, monkeypatch, FakeAd(start=(False, "lua error: attempt to index nil (mod.lua:3)")), "game", "start")
    assert "environment_error" not in a and not L[-1]["ok"] and "infra" not in L[-1]   # the mod's crash: not infra (yet)


def test_control_exceptions(tmp_path, monkeypatch):
    a, L, _ = ctl(tmp_path, monkeypatch, FakeAd(boom=KeyError("inst")), "game", "start")          # gab's own code raised
    assert a["environment_error"]["code"] == "harness_bug" and L[-1]["infra"] == "harness_bug"
    a, L, _ = ctl(tmp_path, monkeypatch, FakeAd(boom=RuntimeError("Error: No such container: gab-x")), "game", "start")
    assert a["environment_error"]["code"] == "container_gone"
    a, L, _ = ctl(tmp_path, monkeypatch, FakeAd(), "game", "fetch", "only-one-arg")               # the agent's bad arguments
    assert "bad arguments" in a["error"] and "environment_error" not in a and L[-1]["usage_error"] and "infra" not in L[-1]


def test_control_log_falls_back_to_workdir(tmp_path, monkeypatch):
    """codex workspace-write: the agent's `game` process cannot write <trial>/tools -> workdir/.gab/calls.jsonl"""
    real = control.log_call
    def ro(ctx, wd, rec):
        (Path(ctx).parent / "calls.jsonl").mkdir(exist_ok=True)              # a directory there: open(..., "a") fails
        return real(ctx, wd, rec)
    monkeypatch.setattr(control, "log_call", ro)
    _, _, t = ctl(tmp_path, monkeypatch, FakeAd(), "game", "time")
    assert json.loads((t / "work" / ".gab" / "calls.jsonl").read_text())["cmd"] == "time"


# ---- after the agent run
def write_calls(d, recs):
    (d / "tools").mkdir(parents=True, exist_ok=True)
    (d / "tools" / "calls.jsonl").write_text("".join(json.dumps(r) + "\n" for r in recs))


class VanillaAd:
    def __init__(self, ok): self.ok, self.n = ok, 0
    def provision(self, inst): pass
    def attach(self, inst, mod): pass
    def start(self, inst): self.n += 1; return self.ok, "vanilla log"
    def stop(self, inst): pass


def test_agent_infra_vanilla_check(tmp_path):
    d = tmp_path / "runs" / "r" / "T-x" / "fine"; d.mkdir(parents=True)
    write_calls(d, [{"mode": "game", "cmd": "start", "ok": False}, {"mode": "game", "cmd": "build", "ok": True}])
    run = AgentRun(exit_reason="done", extra={"done_marked": True})
    ad = VanillaAd(False); b = trial.agent_infra(d, run, ad)
    assert b["status"] == "infra_error" and b["primary"] == "game_vanilla_start_failed" and ad.n == 1
    trial.agent_infra(d, run, ad); assert ad.n == 1                            # cached per trial
    (d / "tools" / "vanilla_check.json").unlink()
    assert trial.agent_infra(d, run, VanillaAd(True))["status"] is None          # vanilla starts: the mod broke it
    write_calls(d, [{"mode": "game", "cmd": "start", "ok": False}, {"mode": "game", "cmd": "start", "ok": True}])
    (d / "tools" / "vanilla_check.json").unlink(); ad = VanillaAd(False)
    assert trial.agent_infra(d, run, ad)["status"] is None and ad.n == 0         # a start succeeded once: no check


def test_agent_infra_reads_calls_transcript_and_stderr(tmp_path):
    d = tmp_path / "runs" / "r" / "T-x" / "fine"; d.mkdir(parents=True)
    write_calls(d, [{"mode": "game", "cmd": "start", "ok": False, "infra": "container_gone", "error": "No such container"}] * 3)
    b = trial.agent_infra(d, AgentRun(exit_reason="done", extra={"done_marked": True}))
    assert b["status"] == "infra_error" and b["infra_calls"] == 3
    tr = d / "transcript.jsonl"
    tr.write_text(json.dumps({"type": "turn.started"}) + "\n" + json.dumps({"type": "item.completed", "item": {"type": "command_execution",
                  "exit_code": 1, "aggregated_output": "bwrap: Failed to make / slave: Permission denied"}}) + "\n"
                  + json.dumps({"type": "item.completed", "item": {"type": "agent_message", "text": "the 429 rate limit doc says"}}) + "\n")
    (d / "tools" / "calls.jsonl").unlink()
    b = trial.agent_infra(d, AgentRun(transcript=str(tr), exit_reason="done", extra={"done_marked": True}))
    assert [i["code"] for i in b["incidents"]] == ["bwrap_sandbox"] and b["status"] == "infra_error"   # the agent's prose is not scanned
    tr.write_text(""); (d / "transcript.stderr.txt").write_text("ERROR: stream error: 503 Service Unavailable\n")
    b = trial.agent_infra(d, AgentRun(transcript=str(tr), exit_reason="crash"))
    assert b["primary"] == "api_upstream" and b["status"] == "infra_error"


def test_archive_attempt(tmp_path):
    d = tmp_path / "t"; (d / "agent").mkdir(parents=True); (d / "work" / "deliverable" / "shots").mkdir(parents=True)
    for f in ("transcript.jsonl", "transcript.part0.jsonl", "infra.jsonl", "agent/agent.json", "work/deliverable/shots/1.png"): (d / f).write_text("x")
    write_calls(d, [{"cmd": "start"}]); (d / "work" / "brief.md").write_text("keep")
    assert trial.archive_attempt(d) == d / "attempts" / "1"
    assert not (d / "transcript.part0.jsonl").exists() and not (d / "tools" / "calls.jsonl").exists() and (d / "work" / "brief.md").exists()
    assert (d / "attempts" / "1" / "work" / "deliverable" / "shots" / "1.png").exists() and (d / "attempts" / "1" / "tools" / "calls.jsonl").exists()
    (d / "infra.jsonl").write_text("y"); assert trial.archive_attempt(d) == d / "attempts" / "2"
    assert trial.archive_attempt(d) is None


# ---- scheduler policy
def test_policy_retries_transient_then_stops_on_systematic():
    slept = []; p = sched.InfraPolicy(sleep=slept.append)
    tr = {"status": "infra_error", "infra": infra.assess([inc("api_upstream")], "crash")}
    seq = [tr, tr, {"status": "completed"}]
    r = p.run("T-a/fine", lambda n: seq[n - 1])
    assert r["status"] == "completed" and slept == [60, 120] and not p.stop.is_set()
    r = p.run("T-b/fine", lambda n: tr); assert r["status"] == "infra_error" and len(slept) == 4     # 3 tries in all
    sy = {"status": "infra_error", "infra": infra.assess([inc("docker_socket_denied")])}
    n_calls = []
    r = p.run("T-c/fine", lambda n: n_calls.append(n) or sy)
    assert n_calls == [1] and p.stop.is_set() and r is sy
    assert p.run("T-d/fine", lambda n: {"status": "completed"})["status"] == "skipped"
    b = p.banner(); assert "docker_socket_denied" in b and "T-c/fine" in b and "network_access" in b


# ---- eval: an infra_error agent run is not scored; aggregation counts but does not score it
class FakeTask:
    id = "T-VO-x"; game = "g"; meta = {"version": 1}


def test_eval_infra_result_not_scored(tmp_path):
    from gab.eval.run import eval_trial
    from gab.report.aggregate import cell
    d = tmp_path / "runs" / "r" / "T-VO-x" / "fine"; (d / "agent").mkdir(parents=True)
    (d / "trial.json").write_text(json.dumps({"task": "T-VO-x", "level": "fine", "name": "r", "attempt": 2}))
    blk = infra.assess([inc("docker_socket_denied")])
    (d / "agent" / "agent.json").write_text(json.dumps({"exit_reason": "done", "infra": blk}))
    r = eval_trial(d, FakeTask(), "fine", force=True)
    assert r["status"] == "infra_error" and r["infra"]["primary"] == "docker_socket_denied" and r["attempt"] == 2
    ok = {**r, "status": "completed", "scores": {"C": {**r["scores"]["C"], "content_realization": 0.5}}}
    c = cell([r, ok]); assert c["status"] == {"infra_error": 1, "completed": 1} and c["content_realization"]["n"] == 1


# ---- preflight: the OpenRouter key check (no network: urlopen faked)
class Resp(io.BytesIO):
    def __enter__(self): return self
    def __exit__(self, *a): pass


def test_preflight_openrouter(monkeypatch):
    monkeypatch.setenv("FAKE_OR_KEY", "sk-or-v1-test")
    seen = []
    def answer(data):
        def f(req, timeout=0):
            seen.append(req.get_header("Authorization")); path = req.full_url.rsplit("/", 1)[-1]
            if isinstance(data.get(path), int): raise urllib.error.HTTPError(req.full_url, data[path], "x", {}, None)
            return Resp(json.dumps({"data": data[path]}).encode())
        return f
    monkeypatch.setattr(preflight.urllib.request, "urlopen", answer({"key": {"limit": 10, "limit_remaining": 4.5}, "credits": {"total_credits": 20, "total_usage": 5}}))
    assert all(c[1] for c in preflight.openrouter("FAKE_OR_KEY", preflight.OR_API)) and seen[0] == "Bearer sk-or-v1-test"
    monkeypatch.setattr(preflight.urllib.request, "urlopen", answer({"key": {"limit": None, "limit_remaining": None}, "credits": {"total_credits": 5, "total_usage": 5.01}}))
    c = preflight.openrouter("FAKE_OR_KEY", preflight.OR_API); assert c[-1][1] is False and c[-1][3] == "api_credits"
    monkeypatch.setattr(preflight.urllib.request, "urlopen", answer({"key": 401}))
    c = preflight.openrouter("FAKE_OR_KEY", preflight.OR_API); assert c[0][3] == "api_auth" and "sk-or" not in c[0][2]


# ---- run_trial end to end with fakes: infra_error status, agent.json block, ledger, attempts, re-run without --force
class FakeHB:
    name = "fake"
    def acquire(self, req, project):
        from gab.host.base import Seat
        return Seat(self, "sb", "1", ":1", "", "/home/player", {})
    def release(self, seat): pass
    def heartbeat(self, seat): pass


class EnvBrokenAgent:
    """every `game start` hit the docker socket refusal (as logged by gab.run.control); the agent wrote DONE anyway"""
    model = "m"; opts = {}
    def __init__(self, broken=True): self.broken = broken
    def run(self, w, brief, budget, tools):
        rec = {"mode": "game", "cmd": "start", "ok": not self.broken}
        if self.broken: rec.update(infra="docker_socket_denied", error="dial unix /var/run/docker.sock: connect: operation not permitted")
        with open(w.parent / "tools" / "calls.jsonl", "a") as f: f.write(json.dumps(rec) + "\n")
        (w / "deliverable" / "DONE").write_text("")
        return AgentRun(exit_reason="done", extra={"done_marked": True})


def test_run_trial_infra_error(tmp_path, monkeypatch):
    monkeypatch.setenv("GAB_RUNS_DIR", str(tmp_path / "runs"))
    agent = {"a": EnvBrokenAgent()}
    def stage(d, task, level, game, minutes):
        w = d / "work"; (w / "deliverable").mkdir(parents=True, exist_ok=True); return w, "brief"
    monkeypatch.setattr(trial, "stage", stage)
    monkeypatch.setattr(trial.hbase, "get", lambda host=None: FakeHB())
    monkeypatch.setattr(trial.gbase, "get", lambda game, seat: VanillaAd(True))
    monkeypatch.setattr(trial.abase, "get", lambda name, model=None, **o: agent["a"])
    t = FakeTask()
    r = trial.run_trial("r", t, "fine", "fake", opts={"until_done": False}, skip_eval=True)
    d = tmp_path / "runs" / "r" / t.id / "fine"
    assert r["status"] == "infra_error" and r["infra"]["primary"] == "docker_socket_denied"
    assert json.loads((d / "agent" / "agent.json").read_text())["infra"]["status"] == "infra_error"
    led = (tmp_path / "runs" / "_infra" / "ledger.jsonl").read_text().splitlines()
    assert json.loads(led[0])["code"] == "docker_socket_denied" and json.loads(led[0])["run"] == "r"
    r = trial.run_trial("r", t, "fine", "fake", opts={"until_done": False}, skip_eval=False, attempt=2)   # eval: not scored
    assert r["status"] == "infra_error" and json.loads((d / "result.json").read_text())["status"] == "infra_error"
    agent["a"] = EnvBrokenAgent(broken=False)                       # fixed: the next run (no --force) runs the agent again
    r = trial.run_trial("r", t, "fine", "fake", opts={"until_done": False}, skip_eval=True, attempt=3)
    assert r["status"] == "agent_done" and (d / "attempts" / "2" / "tools" / "calls.jsonl").exists()
    tj = json.loads((d / "trial.json").read_text())
    assert [a["status"] for a in tj["attempts"]] == ["infra_error", "infra_error", "agent_done"] and tj["attempt"] == 3


def test_agent_infra_start_crash(tmp_path):
    """a crash before any model call is never a result (2026-11-05: EACCES in agent_home after 5 s -> agent_done)"""
    d = tmp_path / "runs" / "r" / "T-x" / "fine"; (d / "agent").mkdir(parents=True)
    (d / "agent" / "transcript.stderr.txt").write_text("EACCES: permission denied, mkdir '/x/fine/agent_home/opencode/data/opencode'\n")
    b = trial.agent_infra(d, AgentRun(exit_reason="crash", rc=1))
    assert b["status"] == "infra_error" and b["primary"] == "agent_home_permission"
    (d / "agent" / "transcript.stderr.txt").write_text("Bun v1.4.4 (Linux x64)\nsomething unexpected\n")
    b = trial.agent_infra(d, AgentRun(exit_reason="crash", rc=1))
    assert b["status"] == "infra_error" and b["primary"] == "agent_start_crash" and "unexpected" in b["incidents"][0]["message"]
    b = trial.agent_infra(d, AgentRun(exit_reason="crash", rc=1, tokens_in=500, tokens_out=50))
    assert b["status"] is None                                      # a crash after real work: no unexplained-start rule


def test_usage_error_inside_an_adapters_dev_command():
    """argument parsing inside dev_command is the agent's mistake; an exception deeper down stays a harness bug"""
    from gab.run.control import usage_error
    def dev_command(v): return v[v.index("--save-as") + 1]
    def deep(): raise ValueError("internal")
    def dev_command2(v): return deep()
    try: dev_command(["x.lua"])
    except ValueError as e: assert usage_error(e)
    try: dev_command2([])
    except ValueError as e: assert not usage_error(e)


def test_os_error_in_the_agents_workspace_is_not_a_harness_bug(tmp_path):
    """flare build on a mod whose settings.txt is a directory: the agent's mistake (2026-11-05: harness_bug stopped a run)"""
    from gab.run.control import agent_file_error
    w = tmp_path / "work"; (w / "deliverable" / "mod" / "m" / "settings.txt").mkdir(parents=True)
    try: open(w / "deliverable" / "mod" / "m" / "settings.txt").read()
    except IsADirectoryError as e: assert agent_file_error(e, w)
    try: open(tmp_path / "elsewhere" / "x").read()
    except FileNotFoundError as e: assert not agent_file_error(e, w)                 # outside the workspace: a real bug
    assert not agent_file_error(ValueError("x"), w) and not agent_file_error(IsADirectoryError(21, "d", str(w)), None)


def test_vanilla_start_timeout_is_transient(tmp_path):
    """2026-11-05: supertuxkart's vanilla start took > 600 s at load 100 -> game_vanilla_start_failed (systematic) stopped
    the run; a timeout is game_start_slow (transient), a failed start stays systematic"""
    d = tmp_path / "runs" / "r" / "T-x" / "fine"; d.mkdir(parents=True)
    write_calls(d, [{"mode": "game", "cmd": "start", "ok": False}])
    (d / "tools" / "vanilla_check.json").write_text(json.dumps({"ok": False, "log": "vanilla start did not finish in 600 s"}))
    b = trial.agent_infra(d, AgentRun(exit_reason="done", extra={"done_marked": True}), VanillaAd(False))
    assert b["primary"] == "game_start_slow" and infra.transient_only(b)
    (d / "tools" / "vanilla_check.json").write_text(json.dumps({"ok": False, "log": "the game exited"}))
    b = trial.agent_infra(d, AgentRun(exit_reason="done", extra={"done_marked": True}), VanillaAd(False))
    assert b["primary"] == "game_vanilla_start_failed" and not infra.transient_only(b)


def test_tool_call_without_name_is_api_stream_invalid():
    """2026-11-06, qwen3.8-max via a gateway: OpenCode 'Expected 'function.name' to be a string' ended the agent as a crash"""
    i = infra.classify('{"type":"error","error":{"name":"UnknownError","data":{"message":"Expected \'function.name\' to be a string."}}}')
    assert i and i[0]["code"] == "api_stream_invalid" and i[0]["kind"] == "transient"


def test_input_before_game_start_is_not_seat_display():
    """2026-11-06: a resumed kimi agent sent `game hold` before `game start` -> "Can't open display" counted as seat_display"""
    err = "Error: Can't open display: (null)\nFailed creating new xdo instance"
    early = [{"cmd": "hold", "ok": False, "infra": "seat_display", "error": err, "_file": "c"},
             {"cmd": "start", "ok": True, "_file": "c"}]
    assert infra.from_calls(early) == ([], 0)
    late = early + [{"cmd": "hold", "ok": False, "infra": "seat_display", "error": err, "_file": "c"}]
    inc, n = infra.from_calls(late)
    assert n == 1 and inc[0]["code"] == "seat_display"                                # the display went away after the start
    assert "NOT running" in trial.RESUME_MSG


def test_input_after_failed_start_or_stop_is_not_seat_display():
    """2026-11-06, qwen3.8-flash stardew: the agent's mod failed to build, `game start` failed, and its `game hold` calls
    ("Can't open display") made the trial INFRA ERROR seat_display"""
    err = "Error: Can't open display: (null)\nFailed creating new xdo instance"
    hold = {"cmd": "hold", "ok": False, "infra": "seat_display", "error": err, "_file": "c"}
    ok_start, bad_start = {"cmd": "start", "ok": True, "_file": "c"}, {"cmd": "start", "ok": False, "_file": "c"}
    stop = {"cmd": "stop", "ok": True, "_file": "c"}
    assert infra.from_calls([ok_start, stop, bad_start, hold, hold]) == ([], 0)
    assert infra.from_calls([ok_start, stop, hold]) == ([], 0)
    inc, n = infra.from_calls([ok_start, stop, bad_start, hold, ok_start, hold])
    assert n == 1 and inc[0]["code"] == "seat_display"                                # running again: a real display failure


def test_gemini_provider_invalid_argument_is_transient():
    """2026-11-06: gemini via a gateway 400 ERR_PROVIDER_005 'Request contains an invalid argument' (type server_error) ended
    the agent as a crash; 17 such responses over the night, the other trials went on"""
    i = infra.classify('{"error":{"code":"ERR_PROVIDER_005","message":"Request contains an invalid argument","type":"server_error"}}')
    assert i and i[0]["code"] == "api_upstream" and infra.resumable(infra.assess(i, "crash"))
    from gab.host import keyproxy
    assert keyproxy.RETRY_400.search(b'{"error":{"code":"ERR_PROVIDER_005"}}')


def test_opencode_header_timeout_is_transient():
    """2026-11-07, gemini-3.8-flash Minecraft T-VO-vid_6d84fc825226.t1: each Gemini 400 took ~70 s and the key proxy retried
    it 5 times, past OpenCode's 300 s wait for the response headers; OpenCode gave up -> 'agent crash'"""
    line = ('{"type":"error","error":{"name":"APIError","data":{"message":"Provider response headers timed out after 300000ms",'
            '"isRetryable":true,"metadata":{"code":"ProviderHeaderTimeoutError","timeoutMs":"300000"}}}}')
    i = infra.classify(line)
    assert i and i[0]["code"] == "api_upstream" and infra.resumable(infra.assess(i, "crash"))


def test_provider_content_filter_is_transient():
    """2026-11-07, deepseek-v4.1-flash Terraria T-TO-txt_015981ffe10d.e3: OpenCode ended with ContentFilterError after
    57 min (counted as an agent crash); a provider-side event: resume it"""
    line = ('{"type":"error","timestamp":1794091348948,"sessionID":"ses_x","error":{"name":"ContentFilterError",'
            '"data":{"message":"The response was blocked by the provider\'s content filter"}}}')
    i = infra.classify(line)
    assert i and i[0]["code"] == "api_content_filter" and infra.resumable(infra.assess(i, "crash"))


def test_gemini_valid_role_400_is_transient():
    """2026-11-06, gemini-3.8-flash Minecraft T-IO-afedff0bf3: 400 'Please use a valid role: user, model.' on a well-formed
    3.5 MB body (roles system/user/assistant/tool; a same-size request just before got 200) ended the agent as a crash"""
    body = '{"error":{"code":"400","message":"Please use a valid role: user, model.","param":null,"type":"INVALID_ARGUMENT"}}'
    i = infra.classify(body)
    assert i and i[0]["code"] == "api_upstream" and infra.resumable(infra.assess(i, "crash"))
    from gab.host import keyproxy
    assert keyproxy.RETRY_400.search(body.encode())


def test_resumed_trials_get_a_slot_first():
    """2026-11-06: after each restart the fresh trials took the slots and the resumable ones waited"""
    import threading, time as _t
    p = {"workers": 1, "host": "docker", "budget": {"ram_gb": 100, "cores": 100, "gpu_slots": 0},
         "games": [{"game": "g", "max_parallel": 1, "ram_gb": 1, "cores": 1, "gpu": False}], "sandboxes": {}}
    try: S = sched.Scheduler(p)
    except Exception: pytest.skip("scheduler plan shape changed")
    order = []
    def job(name, first):
        with S.slot("g", first=first): order.append(name); _t.sleep(0.05)
    hold = threading.Thread(target=job, args=("hold", False)); hold.start(); _t.sleep(0.02)
    ts = [threading.Thread(target=job, args=(f"new{i}", False)) for i in range(3)]
    for x in ts: x.start()
    _t.sleep(0.01); r = threading.Thread(target=job, args=("resume", True)); r.start()
    for x in [hold, r, *ts]: x.join()
    assert order[0] == "hold" and order[1] == "resume"


def test_input_during_or_after_a_minecraft_build_is_not_seat_display():
    """2026-11-07, qwen3.8-max Minecraft T-HY-vid_6d84fc825226.t1: `game build` load-tests on <inst>-gate and closes the
    agent's game first; the agent's `game hold` during and after the build ("Can't open display") made INFRA ERROR seat_display.
    The log line of the build is written when it ends: the hold sent while it ran comes first in the file"""
    err = "Error: Can't open display: (null)\nFailed creating new xdo instance"
    rec = lambda t, cmd, ok, bad=False: {"t": f"2026-11-07T03:{t}", "cmd": cmd, "ok": ok, "_file": "c",
                                         **({"infra": "seat_display", "error": err} if bad else {})}
    log = [rec("40:00", "start", True), rec("50:10", "hold", False, True), rec("49:49", "build", True),
           rec("50:47", "check", True), rec("51:00", "hold", False, True)]
    assert infra.from_calls(log, build_stops=True) == ([], 0)
    assert "minecraft" in infra.BUILD_STOPS_GAME
    assert infra.from_calls(log)[1] == 2                                  # a game whose build leaves the agent's game running
    inc, n = infra.from_calls(log + [rec("52:00", "start", True), rec("53:00", "hold", False, True)], build_stops=True)
    assert n == 1                                                         # started again: a real display failure


def test_reserved_resume_wins_even_when_fresh_threads_come_first():
    """2026-11-07: restarting 4 Valheim runs (1 worker), 3 of them started a fresh trial: its thread reached the free slot
    before the resumable trial's thread registered. gab run reserves the resumable trials before any thread starts"""
    import threading, time as _t
    p = {"workers": 1, "host": "docker", "budget": {"ram_gb": 100, "cores": 100, "gpu_slots": 0},
         "games": [{"game": "g", "max_parallel": 1, "ram_gb": 1, "cores": 1, "gpu": False}], "sandboxes": {}}
    try: S = sched.Scheduler(p)
    except Exception: pytest.skip("scheduler plan shape changed")
    S.reserve_first("g")
    order = []
    def job(name, first):
        with S.slot("g", first=first): order.append(name); _t.sleep(0.03)
    ts = [threading.Thread(target=job, args=(f"new{i}", False)) for i in range(3)]
    for x in ts: x.start()
    _t.sleep(0.05); assert order == []                                        # the free slot waits for the reserved trial
    r = threading.Thread(target=job, args=("resume", True)); r.start()
    for x in [r, *ts]: x.join(5)
    assert order[0] == "resume" and len(order) == 4
    # a reservation whose trial never asks (the run stopped) is dropped and the others go on
    S.reserve_first("g"); t2 = threading.Thread(target=job, args=("late", False)); t2.start(); _t.sleep(0.05)
    assert "late" not in order
    S.unreserve_first("g"); t2.join(5); assert order[-1] == "late"


def test_fresh_trials_start_once_the_reserved_one_has_its_slot():
    """2026-11-07: qwen-max Stardew, 3 workers, 1 resumable: the resume took its slot, the 2 fresh trials kept waiting (no
    notify after the reservation was gone) until a slot was freed ~1 h later"""
    import threading, time as _t
    p = {"workers": 3, "host": "docker", "budget": {"ram_gb": 100, "cores": 100, "gpu_slots": 0},
         "games": [{"game": "g", "max_parallel": 3, "ram_gb": 1, "cores": 1, "gpu": False}], "sandboxes": {}}
    try: S = sched.Scheduler(p)
    except Exception: pytest.skip("scheduler plan shape changed")
    S.reserve_first("g"); started, release = [], threading.Event()
    def job(name, first):
        with S.slot("g", first=first): started.append(name); release.wait(5)
    ts = [threading.Thread(target=job, args=(f"new{i}", False)) for i in range(2)]
    for x in ts: x.start()
    _t.sleep(0.05); r = threading.Thread(target=job, args=("resume", True)); r.start()
    for _ in range(100):
        if len(started) == 3: break
        _t.sleep(0.02)
    n = len(started); release.set()
    for x in [r, *ts]: x.join(5)
    assert n == 3 and started[0] == "resume"                     # all three run while the resume still holds its slot


def test_media_budget_is_resumable():
    i = infra.classify('{"error":{"code":"validation_error","message":"media inputs exceed the encoded media budget after processing"}}')
    assert i and i[0]["code"] == "api_media_budget" and infra.resumable(infra.assess(i, "crash"))


def test_interrupted_leaves_a_finished_agent_run_alone(tmp_path, monkeypatch):
    """a finished attempt moved back by hand (agent.json exit done) while trial.json still has the start of a discarded one:
    the finished agent.json must not be recorded as interrupted (the run would resume a finished session)"""
    from gab import config
    monkeypatch.setattr(config, "path", lambda k: tmp_path / "runs")
    monkeypatch.setattr(config, "REPO", tmp_path)
    started = time.strftime("%Y-%m-%dT%H:%M:%S", time.localtime(time.time() - 600))
    (tmp_path / "tasks" / "T-s").mkdir(parents=True); (tmp_path / "tasks" / "T-s" / "task.yaml").write_text("target_game: stardew_valley\n")
    d = tmp_path / "runs" / "r" / "T-s" / "fine"; (d / "work" / "deliverable").mkdir(parents=True); (d / "agent").mkdir()
    (d / "trial.json").write_text(json.dumps({"started": started, "attempts": []}))
    (d / "agent" / "transcript.jsonl").write_text('{"sessionID":"s"}\n')
    done = {"exit_reason": "done", "wall_s": 5571.0}
    (d / "agent" / "agent.json").write_text(json.dumps(done))
    assert trial.mark_interrupted(d, json.loads((d / "trial.json").read_text()), 120, apply=True) is None
    assert json.loads((d / "agent" / "agent.json").read_text()) == done


def test_gateway_keyerror_is_api_upstream():
    """2026-11-06: qwen3.8-flash via a gateway error {"message":"'id'","type":"KeyError"} ended a Minecraft trial as a crash"""
    t = '{"type":"error","error":{"name":"UnknownError","data":{"message":"{\\"message\\":\\"\'id\'\\",\\"type\\":\\"KeyError\\",\\"param\\":null,\\"code\\":\\"KeyError\\"}"}}}'
    i = infra.classify(t)
    assert i and i[0]["code"] == "api_upstream"


def test_logged_harness_bug_on_an_agent_path_is_the_agents():
    """2026-11-07: calls.jsonl lines written before the control fix carry infra harness_bug for `game shot /home/player/...`;
    from_calls (and so `gab infra --rescan`, which sets such a trial back) treats them as the agent's mistake"""
    rec = {"cmd": "shot", "ok": False, "infra": "harness_bug", "_file": "c", "args": ["/home/player/gab-minecraft/inst/x/07.png"],
           "error": "PermissionError: [Errno 13] Permission denied: '/home/player'"}
    assert infra.from_calls([rec]) == ([], 0)
    other = {**rec, "args": ["shots/07.png"]}
    assert infra.from_calls([other])[1] == 1                               # not a path the agent gave: still harness_bug


# ---- five crashes that were resumed (2026-11-08)
def test_proxy_unreachable_image_too_small_and_bare_400_are_transient():
    for text, code in [
        ('{"type":"error","error":{"name":"APIError","data":{"message":"Cannot connect to API: ECONNREFUSED: Unable to connect. '
         'Is the computer able to access the url?","metadata":{"url":"http://127.0.0.1:18080/v1/chat/completions"}}}}', "api_proxy_unreachable"),
        ('{"error":{"code":"invalid_parameter_error","message":"The image length and width do not meet the model restrictions. '
         '[height:1 or width:256 must be larger than 10]"}}', "api_image_too_small"),
        ('{"type":"error","error":{"name":"APIError","data":{"message":"{\\"code\\":400,\\"msg\\":\\"bad request\\",\\"request_id\\":'
         '\\"9e4e55f1\\"}","statusCode":400,"isRetryable":false}}}', "api_upstream")]:
        i = infra.classify(text)
        assert i and i[0]["code"] == code and infra.resumable(infra.assess(i, "crash")), code
    from gab.host import keyproxy
    assert keyproxy.RETRY_400.search(b'{"code":400,"msg":"bad request","request_id":"x"}')


def test_agent_pkill_of_the_harness_is_resumable(tmp_path):
    """rc 143 + the agent's own pkill / killall in its transcript -> agent_killed_harness (resume); rc 143 alone is not"""
    d = tmp_path / "runs" / "r" / "T-x" / "fine"; (d / "agent").mkdir(parents=True)
    write_calls(d, [{"mode": "game", "cmd": "start", "ok": True, "t": 1}, {"mode": "game", "cmd": "shot", "ok": True, "t": 2}])
    tr = d / "agent" / "transcript.jsonl"
    tr.write_text(json.dumps({"type": "tool_use", "part": {"type": "tool", "tool": "bash", "state": {"status": "completed",
                  "input": {"command": "pkill -f godot; game start"}}}}) + "\n")
    b = trial.agent_infra(d, AgentRun(exit_reason="crash", rc=143, tokens_in=5000, transcript=str(tr)))
    assert b["status"] == "infra_error" and b["primary"] == "agent_killed_harness" and infra.resumable(b)
    tr.write_text(json.dumps({"type": "tool_use", "part": {"state": {"input": {"command": "ls"}}}}) + "\n")
    assert trial.agent_infra(d, AgentRun(exit_reason="crash", rc=143, tokens_in=5000, transcript=str(tr)))["status"] is None


def test_agent_pkill_still_running_in_opencode_db(tmp_path):
    """the killing command never completes, so it is not in the transcript: read the running bash part from opencode's db"""
    import sqlite3
    d = tmp_path / "runs" / "r" / "T-x" / "fine"; (d / "agent").mkdir(parents=True)
    write_calls(d, [{"mode": "game", "cmd": "start", "ok": True, "t": 1}])
    tr = d / "agent" / "transcript.jsonl"; tr.write_text(json.dumps({"part": {"state": {"input": {"command": "ls"}}}}) + "\n")
    db = d / trial.OPENCODE_DB; db.parent.mkdir(parents=True)
    con = sqlite3.connect(db); con.execute("create table part (data text)")
    part = lambda cmd, st: json.dumps({"type": "tool", "tool": "bash", "state": {"status": st, "input": {"command": cmd}}})
    con.executemany("insert into part values (?)", [(part("ls", "completed"),), (part('python3 x.py; pkill -f "game build"; game build', "running"),)])
    con.commit(); con.close()
    b = trial.agent_infra(d, AgentRun(exit_reason="crash", rc=143, tokens_in=5000, transcript=str(tr)))
    assert b["status"] == "infra_error" and b["primary"] == "agent_killed_harness" and infra.resumable(b)
    con = sqlite3.connect(db); con.execute("delete from part"); con.execute("insert into part values (?)", (part("sleep 5", "running"),))
    con.commit(); con.close()
    assert trial.agent_infra(d, AgentRun(exit_reason="crash", rc=143, tokens_in=5000, transcript=str(tr)))["status"] is None


def test_alibaba_data_inspection_is_a_content_filter():
    """qwen via a gateway: '<400> InternalError.Algo.DataInspectionFailed: Output data may contain inappropriate content.'"""
    msg = ('{"type":"error","error":{"name":"UnknownError","data":{"message":"{\\"message\\":\\"<400> InternalError.Algo.'
           'DataInspectionFailed: Output data may contain inappropriate content.\\",\\"code\\":\\"data_inspection_failed\\"}"}}}')
    assert "api_content_filter" in [i["code"] for i in infra.classify(msg)]


FABLE_QUOTA = [   # real lines of a claude-code stream-json transcript (2026-11-17, fable52-claudecode-edit-s0)
    '{"type":"rate_limit_event","rate_limit_info":{"status":"rejected","resetsAt":1795032000,"rateLimitType":"seven_day_overage_included",'
    '"overageStatus":"rejected","overageDisabledReason":"org_level_disabled","isUsingOverage":false,"unifiedWindows":{"five_hour":'
    '{"utilization":0.01,"resetsAt":1794910200,"status":"allowed"}}}}',
    '{"content":[{"type":"text","text":"You\'ve reached your Fable limit. Switch to another model to continue."}],"is_api_error_message":true,'
    '"api_error_status":429,"api_error":"model_requires_usage_credits","api_error_params":{"rate_limit_info":{"status":"rejected",'
    '"resetsAt":1795032000,"rateLimitType":"seven_day_overage_included","overageStatus":"rejected"}}}',
    '{"subtype":"success","api_error_status":429,"api_error":"model_requires_usage_credits","result":"You\'ve reached your Fable limit. '
    'Switch to another model to continue.","type":"result","duration_ms":554}',
    "You've reached your Fable limit. Switch to another model to continue.",
]
RATE_429 = ('{"type":"error","error":{"type":"rate_limit_error","message":"This request would exceed your account\'s rate limit. '
            'Please try again later."},"request_id":"req_x"}')


@pytest.mark.parametrize("msg", FABLE_QUOTA)
def test_fable_quota_is_systematic(msg):
    m = infra.classify(RATE_429 + "\n" + msg)              # together with the generic 429 body: the quota wins
    assert m[0]["code"] == "api_quota_exhausted" and m[0]["kind"] == infra.S, m
    assert "account quota exhausted; wait for reset or switch account" in m[0]["hint"]
    assert infra.decide([{**x, "n": 1} for x in m], exit_reason="crash") == ("infra_error", "api_quota_exhausted")


def test_fable_quota_not_resumed_nor_retried(tmp_path):
    from gab.agent.until_done import transient_api_failure
    t = tmp_path / "transcript.jsonl"; t.write_text("\n".join(FABLE_QUOTA))
    r = AgentRun(exit_reason="crash", transcript=str(t), extra={"error": RATE_429})
    assert transient_api_failure(r) is None                       # UntilDone: no resume in the same session
    assert not infra.transient_only({"status": "infra_error", "primary": "api_quota_exhausted",
                                     "incidents": [infra.make("api_quota_exhausted", FABLE_QUOTA[-1])]})
    # the plain 429 alone stays transient, a rate_limit_event that was allowed is no quota
    assert infra.classify(RATE_429)[0]["code"] == "api_rate_limit"
    assert not any(x["code"] == "api_quota_exhausted" for x in infra.classify(
        '{"type":"rate_limit_event","rate_limit_info":{"status":"allowed","rateLimitType":"seven_day"}}'))
    assert infra.classify("You've reached your usage limit")[0]["code"] == "subscription_limit"
