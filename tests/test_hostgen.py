"""gen-image / gen-3d / gen-sfx / gen-music for an agent on the HOST (gab/agent/hostgen.py, e.g. Codex on the host with
your own ChatGPT login): the tools in <trial>/tools/bin, the trial's image router started / stopped around each run,
no credential in the agent's environment, argv or files, and gen-image / gen-sfx end to end against fake upstreams (no paid
call, no model call)."""
import base64, json, os, stat, subprocess, sys, threading
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path

import pytest

from gab import config
from gab.agent import hostgen
from gab.agent._common import CLIAdapter
from gab.agent.base import AgentAdapter, AgentRun, Budget

SECRET = "sk-hostgen-test-" + "q" * 32
def _png():
    import io
    from PIL import Image
    b = io.BytesIO(); Image.new("RGB", (4, 4), (200, 150, 40)).save(b, "PNG"); return base64.b64encode(b.getvalue()).decode()
PNG_B64 = _png()                                                   # a real (tiny) image: the router converts it to PNG
MP3 = b"ID3fake-mp3-bytes"


def _pin(monkeypatch, **pin):
    get = config.get
    monkeypatch.setattr(config, "get", lambda k, *a, **kw: pin[k] if k in pin else get(k, *a, **kw))


def _fake_online(seen):
    """a fake online API: images/generations (JPEG-ish b64) and audio/speech (base64 mp3)"""
    class H(BaseHTTPRequestHandler):
        def log_message(self, *a): pass
        def do_POST(self):
            body = json.loads(self.rfile.read(int(self.headers.get("Content-Length") or 0)) or b"{}")
            seen.append((self.path, self.headers.get("Authorization"), body))
            if self.path.endswith("/images/generations"): out = {"data": [{"b64_json": PNG_B64}]}
            elif self.path.endswith("/audio/speech"): out = {"audio": base64.b64encode(MP3).decode(), "format": "mp3", "duration": 2.0}
            else: out = {"error": {"message": "no"}}
            b = json.dumps(out).encode()
            self.send_response(200 if "error" not in out else 404); self.send_header("Content-Length", str(len(b)))
            self.end_headers(); self.wfile.write(b)
    s = HTTPServer(("127.0.0.1", 0), H); threading.Thread(target=s.serve_forever, daemon=True).start(); return s


@pytest.fixture
def trial(tmp_path, monkeypatch):
    """a trial dir with work/ and tools/bin (as trial.tools() makes it); the router socket under tmp_path"""
    monkeypatch.setenv("GAB_BRIDGE_ROOT", str(tmp_path / "br")); (tmp_path / "br").mkdir()
    d = tmp_path / "runs" / "r" / "T-1" / "fine"; w = d / "work"; (w / "deliverable").mkdir(parents=True)
    (d / "tools" / "bin").mkdir(parents=True)
    return d, w


def _online_route(monkeypatch, up):
    monkeypatch.setenv("FAKE_T360_KEY", SECRET)
    _pin(monkeypatch, image_base_url=f"http://127.0.0.1:{up.server_address[1]}/v1", image_key_env="FAKE_T360_KEY",
         image_model="dola-seedream-5.0-lite", image_api="generations", image_fallback_url="",
         audio_base_url=f"http://127.0.0.1:{up.server_address[1]}/v1", audio_key_env="FAKE_T360_KEY",
         audio_sfx_model="sfx-model", audio_music_model="music-model", meshy_key_env="NO_SUCH_MESHY_KEY")


class Shell(CLIAdapter):
    """a CLI 'harness' that is a shell script: runs like codex on the host (tools_dir, child_env, launch)"""
    name = "shellagent"; binary = "bash"
    def command(self, workdir, instruction, home): return ["bash", "-c", self.opts["script"]], {}
    def usage(self, events, run): pass
    def version(self): return "test"


# ---- installation --------------------------------------------------------------------------------------------------------
def test_install_puts_read_only_tools_outside_the_workdir(trial):
    d, w = trial
    T = hostgen.install(d, None)
    assert set(T) == {"gen-image", "gen-3d", "gen-sfx", "gen-music"}
    for name, p in T.items():
        p = Path(p)
        assert p.parent == d / "tools" / "bin" and not str(p).startswith(str(w))
        assert not (p.stat().st_mode & (stat.S_IWUSR | stat.S_IWGRP | stat.S_IWOTH)) and os.access(p, os.X_OK)
    for f in ("gen_image.py", "gen_media.py"):
        assert not ((d / "tools" / "gen" / f).stat().st_mode & 0o222)
    hostgen.install(d, "/x/images.sock", "/v1")              # again: the read-only files are replaced, not a PermissionError
    assert "GAB_IMAGE_SOCK=/x/images.sock GAB_IMAGE_BASE=/v1" in Path(T["gen-image"]).read_text()


def test_tools_without_a_router_answer_a_clear_error(trial):
    d, w = trial
    T = hostgen.install(d, None)
    env = dict(os.environ, GAB_IMAGE_PORT="1")                # a stray variable of the parent does not leak in
    for name, args in (("gen-image", ["a torch", "o.png"]), ("gen-sfx", ["a click", "c.mp3"]), ("gen-3d", ["m.glb", "--prompt", "x"])):
        r = subprocess.run([T[name], *args], cwd=w, capture_output=True, text=True, env=env)
        assert r.returncode == 1 and json.loads(r.stdout)["ok"] is False and "not available this run" in json.loads(r.stdout)["error"], (name, r.stdout, r.stderr)


def test_sock_dir_is_private_and_short(trial, tmp_path):
    d, _ = trial
    p = hostgen.sock_dir(d)
    assert p.parent == tmp_path / "br" and stat.S_IMODE(p.stat().st_mode) == 0o700 and hostgen.sock_dir(d) == p
    assert len(str(p / "images.sock")) < 107


def test_sock_dir_refuses_a_symlink(trial, tmp_path):
    d, _ = trial
    p = hostgen.sock_dir(d); p.rmdir(); (tmp_path / "elsewhere").mkdir(); p.symlink_to(tmp_path / "elsewhere")
    with pytest.raises(RuntimeError): hostgen.sock_dir(d)


# ---- router lifecycle --------------------------------------------------------------------------------------------------
class Probe(AgentAdapter):
    """records what the agent would see during its run"""
    name = "probe"
    def run(self, workdir, instruction, budget, tools, **kw):
        txt = Path(tools["gen-image"]).read_text()
        sock = txt.split("GAB_IMAGE_SOCK=")[1].split()[0] if "GAB_IMAGE_SOCK=" in txt else None
        self.seen = {"tools": dict(tools), "sock": sock, "alive": bool(sock) and Path(sock).exists(), "kw": kw}
        return AgentRun(exit_reason="done")


def test_router_runs_only_while_the_agent_runs(trial, monkeypatch):
    d, w = trial
    seen = []; up = _fake_online(seen); _online_route(monkeypatch, up)
    try:
        inner = Probe(model="m"); A = hostgen.wrap(inner, d)
        run = A.run(w, "go", Budget(minutes=1), {"game": str(d / "tools" / "bin" / "game")}, resume="sid")
        s = inner.seen
        assert s["alive"] and s["kw"] == {"resume": "sid"} and "game" in s["tools"] and "gen-music" in s["tools"]
        assert not Path(s["sock"]).exists() and not Path(s["sock"]).parent.exists()      # stopped, dir removed
        assert run.extra["host_gen"]["image_requests"] == 0 and run.extra["host_gen"]["image_upstream"].endswith("/v1")
        log = (d / "agent" / "image_proxy.log").read_text()
        assert "router: gen-image generations dola-seedream-5.0-lite" in log and "stop after 0 requests" in log
        A.run(w, "again", Budget(minutes=1), {})                # until_done's next segment: a fresh router, same socket path
        assert inner.seen["alive"] and inner.seen["sock"] == s["sock"]
    finally: up.shutdown()


def test_router_stopped_when_the_agent_crashes(trial, monkeypatch):
    d, w = trial
    seen = []; up = _fake_online(seen); _online_route(monkeypatch, up)
    class Boom(Probe):
        def run(self, *a, **k): super().run(*a, **k); raise RuntimeError("harness died")
    inner = Boom(model="m")
    try:
        with pytest.raises(RuntimeError): hostgen.wrap(inner, d).run(w, "go", Budget(minutes=1), {})
        assert inner.seen["alive"] and not Path(inner.seen["sock"]).exists()
    finally: up.shutdown()


def test_no_route_no_router(trial, monkeypatch):
    d, w = trial
    monkeypatch.delenv("OPENROUTER_API_KEY", raising=False)
    _pin(monkeypatch, image_base_url="https://openrouter.ai/api/v1", image_key_env="OPENROUTER_API_KEY", audio_base_url="",
         meshy_key_env="NO_SUCH_MESHY_KEY")
    inner = Probe(model="m")
    run = hostgen.wrap(inner, d).run(w, "go", Budget(minutes=1), {})
    assert inner.seen["sock"] is None and "gen-image" in inner.seen["tools"]
    assert run.extra["host_gen"] == {"image_requests": 0, "image_upstream": None, "image_backends": None}
    assert not list((d.parents[3] / "br").iterdir())       # no socket dir left behind


def test_wrap_shares_options_with_until_done(trial):
    """until_done sets opts["resume"] on the adapter it wraps: it must reach the harness adapter"""
    from gab.agent.until_done import UntilDone
    d, _ = trial
    inner = Shell(model="m", script="true"); U = UntilDone(hostgen.wrap(inner, d))
    U.inner.opts["resume"] = "abc"
    assert inner.opts["resume"] == "abc" and U.inner.can_resume == inner.can_resume and U.inner.name == "shellagent"


# ---- end to end: a host harness calls gen-image / gen-sfx through the router, against fake upstreams -------------------
def test_gen_tools_end_to_end_with_no_key_in_the_agent(trial, monkeypatch):
    d, w = trial
    seen = []; up = _fake_online(seen); _online_route(monkeypatch, up)
    script = ('gen-image "a hay bale" art/hay.png --aspect-ratio 16:9 > gi.json; gen-sfx "a click" sfx/click > gs.json; '
              'env > env.txt; cat /proc/$$/cmdline > argv.txt')
    (w / "art").mkdir(); (w / "sfx").mkdir()
    A = hostgen.wrap(Shell(model="m", script=script), d)
    try:
        run = A.run(w, "go", Budget(minutes=2), {})
    finally: up.shutdown()
    gi, gs = json.loads((w / "gi.json").read_text()), json.loads((w / "gs.json").read_text())
    assert gi["ok"] and gi["path"] == "art/hay.png" and (w / "art" / "hay.png").exists(), gi
    assert gs["ok"] and gs["path"] == "sfx/click.mp3" and (w / "sfx" / "click.mp3").read_bytes() == MP3, gs
    paths = [p for p, _, _ in seen]
    assert paths == ["/v1/images/generations", "/v1/audio/speech"]
    assert [a for _, a, _ in seen] == [None, f"Bearer {SECRET}"]       # a loopback image server gets no key; audio: the host added it ...
    assert seen[0][2]["size"] == "2560x1440" and seen[1][2]["model"] == "sfx-model"
    env = (w / "env.txt").read_text()
    assert SECRET not in env and "FAKE_T360_KEY" not in env and "GAB_IMAGE_SOCK" not in env   # ... the agent never had it
    for p in d.rglob("*"):                                             # nor any file of the trial (argv, logs, tools, transcript)
        if p.is_file(): assert SECRET not in p.read_text(errors="replace"), p
    hg = run.extra["host_gen"]
    assert hg["image_requests"] == 2 and hg["image_backends"] == {"online": 1, "audio": 1}
    log = (d / "agent" / "image_proxy.log").read_text()
    assert "backend=online n=1" in log and "POST media/audio 200 sfx" in log


def test_gen_image_reaches_the_router_inside_the_codex_sandbox(trial, monkeypatch):
    """Codex's workspace-write sandbox with network on (the codex adapter's setting) can reach the router's unix socket,
    which lives outside every writable root. Skipped where codex (or its sandbox) is not available; runs no model."""
    import shutil
    if not shutil.which("codex"): pytest.skip("no codex on PATH")
    d, w = trial
    xdg = os.environ.get("XDG_RUNTIME_DIR")
    if xdg and os.path.isdir(xdg): monkeypatch.delenv("GAB_BRIDGE_ROOT")      # the socket outside codex's writable roots
    seen = []; up = _fake_online(seen); _online_route(monkeypatch, up)
    T = {}
    class InSandbox(AgentAdapter):
        name = "sbx"
        def run(self, workdir, instruction, budget, tools, **kw):
            T.update(tools)
            return subprocess.run(["codex", "sandbox", "-c", 'sandbox_mode="workspace-write"', "-c", "sandbox_workspace_write.network_access=true",
                                   tools["gen-image"], "a torch", "t.png"], cwd=workdir, capture_output=True, text=True, timeout=120)
    class R(InSandbox):
        def run(self, *a, **k):
            self.r = super().run(*a, **k); return AgentRun(exit_reason="done")
    inner = R(model=None)
    try: hostgen.wrap(inner, d).run(w, "go", Budget(minutes=1), {})
    finally: up.shutdown()
    r = inner.r
    if r.returncode and not r.stdout.strip(): pytest.skip(f"codex sandbox unavailable here: {r.stderr[-200:]}")
    assert json.loads(r.stdout.strip().splitlines()[-1])["ok"], r.stdout + r.stderr
    assert (w / "t.png").exists() and seen and seen[0][0] == "/v1/images/generations"


# ---- wiring in the trial ------------------------------------------------------------------------------------------------
def test_host_mode_trial_wraps_with_hostgen(monkeypatch):
    """run_agent / refine_agent: in_container -> incontainer.wrap, otherwise hostgen.wrap (source check: the
    three call sites, without running a game)"""
    import inspect
    from gab.refine import refine
    from gab.run import trial as tr
    for f in (tr.run_agent, refine.refine_agent):
        src = inspect.getsource(f)
        assert "from ..agent.hostgen import wrap as host_gen" in src and "A = host_gen(A, " in src, f.__name__


def test_host_claude_may_run_the_gen_tools():
    from gab.agent.claude_code.adapter import BASH_OK
    assert {"gen-image", "gen-3d", "gen-sfx", "gen-music"} <= set(BASH_OK)
