"""Claude Code inside the trial container with a Claude subscription (or an Anthropic key) that never enters the container:
the host key proxy in Anthropic mode (both auth styles) against a fake local Messages API, the in-container wrapper with the
fake docker CLI, and (GAB_DOCKER_SMOKE=1) one real game-image container running the real Claude Code binary offline against
the fake upstream. No real token, no real model call."""
import json, os, shutil, subprocess
from pathlib import Path

import pytest

from gab.agent.base import Budget
from gab.host import docker as D
from test_docker_backend import _stage, fake  # noqa: F401  (fixture)
import fake_anthropic

OAUTH = "sk-ant-oat01-" + "q" * 60          # the shape of a `claude setup-token` token (fake)
APIKEY = "sk-ant-api03-" + "k" * 60
PH = "gab-proxy-no-key"


@pytest.fixture
def anthropic(tmp_path):
    log = tmp_path / "upstream.jsonl"
    s = fake_anthropic.serve("127.0.0.1", 0, str(log), tool_command="game info")
    class U:
        url = f"http://127.0.0.1:{s.server_address[1]}"
        def seen(self): return [json.loads(l) for l in log.read_text().splitlines()] if log.exists() else []
    yield U()
    s.shutdown()


# ---- key proxy, Anthropic mode ------------------------------------------------------------------------------------------
def test_keyproxy_anthropic_oauth(tmp_path, anthropic, monkeypatch):
    from gab.host.keyproxy import KeyProxy, request_via_socket
    monkeypatch.setenv("FAKE_OAUTH", OAUTH)
    sock = str(tmp_path / "llm.sock"); log = tmp_path / "proxy.log"
    p = KeyProxy(sock, anthropic.url, key_env="FAKE_OAUTH", log=str(log), api="anthropic", auth="oauth").start()
    beta = "claude-code-20250219,oauth-2025-04-20,interleaved-thinking-2025-05-14"
    try:
        body = json.dumps({"model": "claude-x", "stream": True, "max_tokens": 5, "messages": []}).encode()
        st, out = request_via_socket(sock, "POST", "/v1/messages?beta=true", body,
                                     {"Content-Type": "application/json", "Authorization": f"Bearer {PH}", "x-api-key": PH,
                                      "anthropic-beta": beta, "anthropic-version": "2023-06-01"})
        assert st == 200 and b"message_stop" in out and b'"name": "Bash"' in out
        r = anthropic.seen()[-1]
        assert r["path"] == "/v1/messages?beta=true" and r["authorization"] == f"Bearer {OAUTH}" and r["x_api_key"] is None
        assert r["anthropic_beta"] == beta and r["anthropic_version"] == "2023-06-01"        # the client's own headers kept
        st, _ = request_via_socket(sock, "POST", "/v1/messages", b'{"model":"m"}', {"Authorization": f"Bearer {PH}"})
        r = anthropic.seen()[-1]
        assert st == 200 and r["anthropic_beta"] == "oauth-2025-04-20" and r["anthropic_version"] == "2023-06-01"   # added if missing
        assert request_via_socket(sock, "POST", "/v1/messages/count_tokens?beta=true", b"{}")[0] == 200
        n = len(anthropic.seen())
        for bad in ("/v1/models", "/v1/complete", "/v1/messages/batches", "/v1/messages/../models", "/v1/files", "/api/oauth/profile",
                    "/api/hello", "/api/event_logging/batch", "/v1/responses", "/chat/completions", "/"):
            assert request_via_socket(sock, "POST", bad, b"{}")[0] == 403, bad
        assert len(anthropic.seen()) == n                                                   # none of them went upstream
        assert p.container_base_url(18080) == "http://127.0.0.1:18080"
    finally: p.stop()
    t = log.read_text()
    assert OAUTH not in t and PH not in t and "Bearer" not in t and "beta=true" not in t     # no headers, no query strings
    assert "403 denied" in t and "POST /v1/messages 200" in t and "api=anthropic auth=oauth" in t


def test_keyproxy_anthropic_api_key(tmp_path, anthropic, monkeypatch):
    from gab.host.keyproxy import KeyProxy, request_via_socket
    monkeypatch.setenv("FAKE_ANTH_KEY", APIKEY)
    sock = str(tmp_path / "llm.sock"); log = tmp_path / "proxy.log"
    p = KeyProxy(sock, anthropic.url + "/gw", key_env="FAKE_ANTH_KEY", log=str(log), api="anthropic", auth="x-api-key").start()
    try:
        st, _ = request_via_socket(sock, "POST", "/gw/v1/messages", b'{"model":"m"}',
                                   {"x-api-key": PH, "Authorization": f"Bearer {PH}", "anthropic-version": "2023-06-01"})
        r = anthropic.seen()[-1]
        assert st == 404 or st == 200     # the fake serves /v1/messages only; what matters is what reached it
        assert r["path"] == "/gw/v1/messages" and r["x_api_key"] == APIKEY and r["authorization"] is None
        assert r["anthropic_beta"] is None and r["anthropic_version"] == "2023-06-01"
        assert request_via_socket(sock, "POST", "/v1/messages", b"{}")[0] == 403              # outside the base path
        assert p.container_base_url(18080) == "http://127.0.0.1:18080/gw"
    finally: p.stop()
    assert APIKEY not in log.read_text()


def test_keyproxy_modes_checked(tmp_path, monkeypatch):
    from gab.host.keyproxy import KeyProxy
    monkeypatch.setenv("FAKE_OAUTH", OAUTH)
    with pytest.raises(ValueError, match="api"): KeyProxy(str(tmp_path / "s"), "https://x.org", key_env="FAKE_OAUTH", api="gemini")
    with pytest.raises(ValueError, match="auth"): KeyProxy(str(tmp_path / "s"), "https://x.org", key_env="FAKE_OAUTH", auth="basic")


# ---- claude_code in the container (fake docker CLI) ---------------------------------------------------------------------
def _claude(fake, tmp_path, monkeypatch, **opts):
    from gab.agent import base as abase
    from gab.agent.incontainer import mounts, wrap
    exe = tmp_path / "claude-bin"; exe.write_text("#!/bin/sh\n"); exe.chmod(0o755)
    monkeypatch.setenv("GAB_CLAUDE_CONTAINER_BINARY", str(exe))
    monkeypatch.setenv("GAB_HIDDEN_DIR", str(tmp_path / "hidden"))
    d, w, tools = _stage(tmp_path)
    seat = D.Backend().acquire({"game": "flare", "mounts": mounts(d)}, "p")
    return d, w, tools, seat, wrap(abase.get("claude_code", model="claude-opus-5-5", **opts), seat, d)


def _agent_exec(fake, i=0):
    ex = [c for c in fake.calls() if c[0] == "exec" and "/run/gab/agent_entry.sh" in c][i]
    env = dict(ex[j + 1].split("=", 1) for j in range(len(ex)) if ex[j] == "-e")
    return ex, env, ex[ex.index("/run/gab/agent_entry.sh") + 1:]


def test_claude_in_container_subscription(fake, tmp_path, monkeypatch):
    from gab.agent import base as abase
    monkeypatch.setenv("CLAUDE_CODE_OAUTH_TOKEN", OAUTH)
    d, w, tools, seat, A = _claude(fake, tmp_path, monkeypatch)
    run = A.run(w, "do the task", Budget(minutes=1), tools)
    assert run.exit_reason == "done" and run.tokens_in == 700 and run.tokens_out == 80 and run.tokens_cached == 320
    assert run.usd == 0.0123 and run.model == "claude-fake-model" and run.extra["tool_calls"] == 1
    assert run.harness_version == "2.1.318 (Claude Code)"
    ex, env, cmd = _agent_exec(fake)
    assert ex[ex.index("-u") + 1] == "2000:2000" and ex[ex.index("-w") + 1] == str(w)
    assert env["CLAUDE_CODE_OAUTH_TOKEN"] == PH and "ANTHROPIC_API_KEY" not in env
    assert env["ANTHROPIC_BASE_URL"] == "http://127.0.0.1:18080" and env["GAB_LLM_PORT"] == "18080"
    for k in ("CLAUDE_CODE_DISABLE_NONESSENTIAL_TRAFFIC", "DISABLE_TELEMETRY", "DISABLE_AUTOUPDATER", "DISABLE_ERROR_REPORTING"):
        assert env[k] == "1", k
    assert env["HOME"] == str(d / "agent_home")
    host = abase.get("claude_code", model="claude-opus-5-5").cmd("do the task", Budget(minutes=1), w)
    assert cmd[0] == "/opt/gab-agent/bin/claude"
    strip = lambda c: c[1:c.index("--allowedTools")] + c[c.index("--disallowedTools"):]
    assert strip(cmd) == strip(host)                                               # same flags / tools / deny rules as on the host
    allow = cmd[cmd.index("--allowedTools") + 1:cmd.index("--disallowedTools")]
    assert allow == ["Bash", "Read", "Glob", "Grep", "Write", "Edit"]                # no Bash allowlist in the container
    assert run.extra["in_container"]["route"] == {"api": "anthropic", "auth": "oauth", "upstream": "https://api.anthropic.com",
                                                  "secret_env": "CLAUDE_CODE_OAUTH_TOKEN"}
    assert not any(OAUTH in json.dumps(c) for c in fake.calls())               # the token never reaches the docker CLI
    assert not any(OAUTH in p.read_text(errors="replace") for p in d.rglob("*") if p.is_file())
    assert (fake.dir / "rootfs" / "opt/gab-agent/bin/claude").exists()
    # until_done resumes the session through the wrapper; the binary is not copied again
    assert A.can_resume and A.session_of(run.transcript) == "sess-1"
    A.opts["resume"] = "sess-1"; A.run(w, "continue", Budget(minutes=1), tools)
    _, _, cmd2 = _agent_exec(fake, -1)
    assert cmd2[cmd2.index("--resume") + 1] == "sess-1"
    assert sum(1 for c in fake.calls() if c[0] == "cp" and c[-1].endswith("/opt/gab-agent/bin/claude")) == 1
    seat.release()


def test_claude_in_container_api_key(fake, tmp_path, monkeypatch):
    monkeypatch.setenv("FAKE_ANTH_KEY", APIKEY); monkeypatch.delenv("CLAUDE_CODE_OAUTH_TOKEN", raising=False)
    d, w, tools, seat, A = _claude(fake, tmp_path, monkeypatch, auth="api", api_key_env="FAKE_ANTH_KEY",
                                   base_url="https://gw.example.org/anthropic")
    run = A.run(w, "do the task", Budget(minutes=1), tools)
    _, env, _ = _agent_exec(fake)
    assert run.exit_reason == "done" and env["ANTHROPIC_API_KEY"] == PH and "CLAUDE_CODE_OAUTH_TOKEN" not in env
    assert env["ANTHROPIC_BASE_URL"] == "http://127.0.0.1:18080/anthropic"
    assert run.extra["in_container"]["route"]["auth"] == "x-api-key"
    assert not any(APIKEY in json.dumps(c) for c in fake.calls())
    seat.release()


def test_claude_in_container_refusals(fake, tmp_path, monkeypatch):
    from gab.agent import base as abase
    from gab.agent.incontainer import wrap
    monkeypatch.delenv("CLAUDE_CODE_OAUTH_TOKEN", raising=False)
    d, w, tools, seat, A = _claude(fake, tmp_path, monkeypatch)
    with pytest.raises(RuntimeError, match="CLAUDE_CODE_OAUTH_TOKEN is not set.*setup-token"): A.run(w, "x", Budget(minutes=1), tools)
    assert not any("/run/gab/agent_entry.sh" in c for c in fake.calls())       # refused before anything started
    with pytest.raises(RuntimeError, match="only --agent claude"):
        wrap(abase.get("codex", model="m", auth="subscription"), seat, d).run(w, "x", Budget(minutes=1), tools)
    seat.release()


def test_host_binary_must_be_native(tmp_path, monkeypatch):
    from gab.agent.incontainer import harness_binary
    monkeypatch.setenv("GAB_CLAUDE_CONTAINER_BINARY", "")
    js = tmp_path / "cli.js"; js.write_text("#!/usr/bin/env node\n"); js.chmod(0o755)
    monkeypatch.setenv("GAB_CLAUDE", str(js))
    with pytest.raises(RuntimeError, match="not a native Linux executable"): harness_binary("claude")
    elf = tmp_path / "claude"; elf.write_bytes(b"\x7fELF" + b"\0" * 60); elf.chmod(0o755)
    link = tmp_path / "bin-claude"; link.symlink_to(elf)
    monkeypatch.setenv("GAB_CLAUDE", str(link))
    assert harness_binary("claude") == str(elf)


# ---- real container smoke test (GAB_DOCKER_SMOKE=1) ---------------------------------------------------------------------
SMOKE_GAME = os.environ.get("GAB_SMOKE_GAME", "godot-tps")


@pytest.mark.skipif(os.environ.get("GAB_DOCKER_SMOKE") != "1", reason="starts a real container: GAB_DOCKER_SMOKE=1")
def test_claude_in_real_container_smoke(tmp_path, anthropic, monkeypatch):
    """the real Claude Code binary in a game image (--network none) reaches the host proxy and its request arrives upstream
    with the injected subscription token; the container holds only the placeholder. The fake model makes one Bash call
    (`game info`): the allow rules let it run, the in-container `game` shim reaches the host tool over the tool bridge."""
    from gab.agent import base as abase
    from gab.agent.incontainer import mounts, wrap
    for k in ("GAB_DOCKER", "GAB_DOCKER_IMAGES", "FAKE_DOCKER_DIR"): monkeypatch.delenv(k, raising=False)
    monkeypatch.setenv("GAB_DOCKER_GPUS", "none")
    monkeypatch.setenv("CLAUDE_CODE_OAUTH_TOKEN", OAUTH)
    if not shutil.which("docker") or subprocess.run(["docker", "version"], capture_output=True).returncode: pytest.skip("no docker")
    d, w, tools = _stage(tmp_path)
    Path(tools["game"]).write_text("#!/bin/bash\necho GAME_TOOL_ON_HOST uid=$(id -u) args=$*\n")
    seat = D.Backend().acquire({"game": SMOKE_GAME, "mounts": mounts(d)}, "oauthsmoke")
    try:
        A = wrap(abase.get("claude_code", model="claude-sonnet-5-5", base_url=anthropic.url), seat, d)
        run = A.run(w, "Reply with one word.", Budget(minutes=4), tools)
        net = subprocess.run(["docker", "inspect", "-f", "{{.HostConfig.NetworkMode}}", seat.sandbox], capture_output=True, text=True).stdout.strip()
    finally:
        seat.release()
    tr = Path(run.transcript).read_text(errors="replace")
    proxy_log = (d / "agent" / "llm_proxy.log").read_text()
    print("proxy log:\n" + proxy_log); print("upstream:", json.dumps(anthropic.seen(), indent=1))
    assert net == "none"
    assert run.exit_reason == "done", run.extra.get("stderr_tail")
    assert fake_anthropic.REPLY in tr and run.tokens_in == 2 * fake_anthropic.USAGE["input_tokens"]   # tool call + answer
    assert f"GAME_TOOL_ON_HOST uid={os.getuid()} args=info" in tr and run.extra["in_container"]["tool_calls"] == 1
    assert run.extra["tool_calls"] == 1 and run.extra["is_error"] is False
    assert "Claude Code" in (run.harness_version or "")
    msgs = [r for r in anthropic.seen() if r["path"].startswith("/v1/messages")]
    assert msgs and all(r["authorization"] == f"Bearer {OAUTH}" and r["x_api_key"] is None for r in msgs)
    assert all("oauth-2025-04-20" in (r["anthropic_beta"] or "") for r in msgs)
    assert any("claude-code-" in (r["anthropic_beta"] or "") for r in msgs)               # the CLI's own betas came through
    assert OAUTH not in proxy_log and OAUTH not in tr
    assert not any(OAUTH in p.read_text(errors="replace") for p in d.rglob("*") if p.is_file())
    assert run.extra["in_container"]["model_requests"] >= 2 and "denied" not in proxy_log


def test_container_binary_override_is_resolved_through_symlinks(tmp_path, monkeypatch):
    """2026-11-08: codex_container_binary pointed at ~/.local/bin/codex, an npm symlink; `docker cp` copied the link
    itself, which dangled in the container, and every in-container codex round died in 4 s ("no such file or directory")"""
    from gab import config
    from gab.agent.incontainer import harness_binary
    real = tmp_path / "vendor" / "codex"; real.parent.mkdir(); real.write_bytes(b"\x7fELF" + b"\0" * 16)
    link = tmp_path / "codex"; link.symlink_to(real)
    get = config.get
    monkeypatch.setattr(config, "get", lambda k, *a, **kw: str(link) if k == "codex_container_binary" else get(k, *a, **kw))
    assert harness_binary("codex") == str(real)
