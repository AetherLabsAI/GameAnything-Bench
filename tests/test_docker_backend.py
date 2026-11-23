"""host `docker` backend, the host key proxy, the tool bridge and the agent-in-container wrapper -- with a fake docker CLI
(tests/fake_docker.py), a local fake model API and no Docker Engine."""
import http.server, json, os, sys, threading
from pathlib import Path

import pytest
import yaml

from gab.agent.base import Budget
from gab.host import docker as D
from gab.host.base import Seat

FAKE = Path(__file__).resolve().parent / "fake_docker.py"
SECRET = "sk-or-v1-" + "x" * 40


@pytest.fixture
def fake(tmp_path, monkeypatch):
    st = tmp_path / "fakedocker"
    monkeypatch.setenv("FAKE_DOCKER_DIR", str(st))
    monkeypatch.setenv("GAB_DOCKER", f"{sys.executable} {FAKE}")
    man = tmp_path / "images.yaml"
    man.write_text(yaml.safe_dump({"registry": "ghcr.io/example", "tag": "20261030", "games": {
        "flare": {"repo": "gameanything-bench-flare", "digest": "sha256:" + "1" * 64, "gpu": False},
        "voxelibre": {"repo": "gameanything-bench-voxelibre", "gpu": True},
        "minecraft": {"repo": "gameanything-bench-minecraft", "tag": "local", "gpu": True}}}))
    monkeypatch.setenv("GAB_DOCKER_IMAGES", str(man))
    for k in ("GAB_MINECRAFT_DIR", "GAB_DOCKER_IMAGE", "GAB_DOCKER_TAG", "GAB_DOCKER_PULL", "GAB_DOCKER_GPUS", "FAKE_NVIDIA", "FAKE_PULL_DENIED"):
        monkeypatch.delenv(k, raising=False)
    class F:
        dir = st
        def calls(self): return [json.loads(l) for l in (st / "calls.jsonl").read_text().splitlines()] if (st / "calls.jsonl").exists() else []
        def run_args(self): return next(c for c in self.calls() if c[0] == "run")
    return F()


def test_image_ref(fake, monkeypatch):
    assert D.image_ref("flare") == "ghcr.io/example/gameanything-bench-flare@sha256:" + "1" * 64
    assert D.image_ref("voxelibre") == "ghcr.io/example/gameanything-bench-voxelibre:20261030"     # no digest yet -> tag
    monkeypatch.setenv("GAB_DOCKER_TAG", "dev")
    assert D.image_ref("flare") == "ghcr.io/example/gameanything-bench-flare:dev"
    monkeypatch.setenv("GAB_DOCKER_IMAGE", "mine:1")
    assert D.image_ref("anything") == "mine:1"
    monkeypatch.delenv("GAB_DOCKER_IMAGE"); monkeypatch.delenv("GAB_DOCKER_TAG")
    with pytest.raises(RuntimeError): D.image_ref("no_such_game")


def test_acquire_exec_put_copy_release(fake, tmp_path):
    b = D.Backend()
    seat = b.acquire({"game": "flare", "mounts": [("/h/work", "/h/work", "rw")]}, "gab-t-x-f")
    a = fake.run_args()
    assert ["pull", D.image_ref("flare")] in fake.calls()                       # missing -> pulled
    assert a[a.index("--network") + 1] == "none" and "--init" in a and "no-new-privileges" in a
    assert "--gpus" not in a and "GAB_GPU=0" in a                               # flare: no GPU wanted
    assert "/h/work:/h/work:rw" in a and a[-1] == D.image_ref("flare")
    assert seat.display == ":101" and seat.home == "/home/player" and seat.sandbox.startswith("gab-gab-t-x-f-")
    # exec / put (docker cp fast path) / copy_in / copy_out through the fake container
    r = seat.exec("echo hello $M2W_LEASE-x", env={"A": "1"}); assert r.stdout.strip() == "hello -x"
    p = tmp_path / "inside" / "a.txt"; seat.put(str(p), "data 1"); assert p.read_text() == "data 1"
    assert any(c[0] == "cp" for c in fake.calls())
    src = tmp_path / "mod"; (src / "sub").mkdir(parents=True); (src / "sub" / "f.lua").write_text("x")
    seat.home = str(tmp_path)        # the fake runs on this host: no /home/player here (copy_in stages its tgz in the home)
    seat.copy_in(src, str(tmp_path / "inst" / "mod")); assert (tmp_path / "inst" / "mod" / "sub" / "f.lua").read_text() == "x"
    assert seat.copy_out(str(p), tmp_path / "out" / "b.txt") and (tmp_path / "out" / "b.txt").read_text() == "data 1"
    seat.release()
    assert ["rm", "-f", "-v", seat.sandbox] in fake.calls()


def test_commercial_game_files_mounted_read_only(fake, tmp_path, monkeypatch):
    """minecraft: the user's own game folder on the host is mounted read only at /mc/main (never in the image); the adapter
    on a docker seat reads the game there, whatever GAB_MINECRAFT_DIR says on the host"""
    from gab.game.minecraft import adapter as MA
    b = D.Backend()
    assert D.image_ref("minecraft") == "ghcr.io/example/gameanything-bench-minecraft:local"     # local build, no digest
    monkeypatch.setenv("GAB_MINECRAFT_DIR", "")
    with pytest.raises(RuntimeError, match="fetch_game.py"): b.acquire({"game": "minecraft"}, "p")
    mc = tmp_path / "mc"; monkeypatch.setenv("GAB_MINECRAFT_DIR", str(mc))
    with pytest.raises(RuntimeError, match="no versions/"): b.acquire({"game": "minecraft"}, "p")
    v = mc / "versions" / MA.VERSION_ID; v.mkdir(parents=True); (v / f"{MA.VERSION_ID}.json").write_text("{}")
    seat = b.acquire({"game": "minecraft", "mounts": [("/h/work", "/h/work", "rw")]}, "p")
    a = fake.run_args()
    assert f"{mc}:/mc/main:ro" in a and "/h/work:/h/work:rw" in a and "GAB_SCREEN=1280x720x24" in a
    assert ["/h/work", "/h/work", "rw"] in seat.info["mounts"] and [str(mc), "/mc/main", "ro"] in seat.info["mounts"]
    assert MA.Adapter(seat).main() == "/mc/main"
    assert "docker" in MA.Adapter(seat).requirements()["hosts"]
    seat.release()


def test_dotnet_workstation_gc_in_every_container(fake):
    """2026-11-07: Roslyn's compiler server (server GC: one spinning GC thread per host core) burnt 5-7 cores each in idle
    containers; every game container gets DOTNET_gcServer=0, and so do the Terraria bridges (Steam hub processes)"""
    seat = D.Backend().acquire({"game": "flare"}, "p")
    a = fake.run_args(); assert a[a.index("DOTNET_gcServer=0") - 1] == "-e"
    seat.release()
    for b in ("gtr.sh", "trp.sh"):
        t = (Path(__file__).resolve().parent.parent / "gab" / "game" / "terraria" / "bridge" / b).read_text()
        assert "DOTNET_gcServer=0" in t, b


def test_gpu_and_pull_modes(fake, monkeypatch):
    b = D.Backend()
    monkeypatch.setattr(b, "gpu_count", lambda: 1)                              # one GPU: --gpus all (spreading: own test)
    assert b.gpu_args("voxelibre") == []                                        # wants a GPU, no nvidia runtime -> software GL
    monkeypatch.setenv("FAKE_NVIDIA", "1"); assert b.gpu_args("voxelibre") == ["--gpus", "all"]
    assert b.gpu_args("flare") == []
    monkeypatch.setenv("GAB_DOCKER_GPUS", "none"); assert b.gpu_args("voxelibre") == []
    monkeypatch.setenv("GAB_DOCKER_GPUS", "device=1"); assert b.gpu_args("flare") == ["--gpus", "device=1"]
    monkeypatch.setenv("GAB_DOCKER_PULL", "never")
    with pytest.raises(RuntimeError, match="docker_pull is never"): b.ensure_image("x:1")
    monkeypatch.setenv("GAB_DOCKER_PULL", "missing"); monkeypatch.setenv("FAKE_PULL_DENIED", "1")
    with pytest.raises(RuntimeError, match="docker login ghcr.io"): b.ensure_image("ghcr.io/example/y:1")


def test_doctor(fake):
    lines = D.Backend().doctor(["flare"])
    assert lines[0][0] == "docker" and lines[0][1]
    assert lines[1][0] == "image flare" and lines[1][1] is False


# ---- key proxy --------------------------------------------------------------------------------------------------------
class _Upstream(http.server.BaseHTTPRequestHandler):
    seen = []
    def log_message(self, *a): pass
    def do_POST(self):
        n = int(self.headers.get("Content-Length") or 0); body = self.rfile.read(n)
        _Upstream.seen.append({"path": self.path, "auth": self.headers.get("Authorization"), "xkey": self.headers.get("x-api-key"), "body": body})
        self.send_response(200); self.send_header("Content-Type", "text/event-stream"); self.end_headers()   # close-delimited stream
        for i in range(3): self.wfile.write(f"event: response.output_text.delta\ndata: {{\"i\": {i}}}\n\n".encode()); self.wfile.flush()
        self.wfile.write(b"event: response.completed\ndata: {}\n\n")
    do_GET = do_POST


@pytest.fixture
def upstream():
    s = http.server.ThreadingHTTPServer(("127.0.0.1", 0), _Upstream); _Upstream.seen = []
    threading.Thread(target=s.serve_forever, daemon=True).start()
    yield f"http://127.0.0.1:{s.server_address[1]}/api/v1"
    s.shutdown()


def test_keyproxy_adds_key_and_filters_paths(tmp_path, upstream, monkeypatch):
    from gab.host.keyproxy import KeyProxy, request_via_socket
    monkeypatch.setenv("FAKE_KEY_VAR", SECRET)
    sock = str(tmp_path / "llm.sock"); log = tmp_path / "proxy.log"
    p = KeyProxy(sock, upstream, key_env="FAKE_KEY_VAR", log=str(log)).start()
    try:
        st, body = request_via_socket(sock, "POST", "/api/v1/responses", b'{"model":"m"}',
                                      {"Content-Type": "application/json", "Authorization": "Bearer gab-proxy-no-key", "x-api-key": "zzz"})
        assert st == 200 and b"response.completed" in body and body.count(b"delta") == 3
        assert _Upstream.seen[-1]["auth"] == f"Bearer {SECRET}" and _Upstream.seen[-1]["xkey"] is None
        assert _Upstream.seen[-1]["body"] == b'{"model":"m"}' and _Upstream.seen[-1]["path"] == "/api/v1/responses"
        assert request_via_socket(sock, "GET", "/api/v1/models")[0] == 200
        for bad in ("/api/v1/credits", "/api/v1/keys", "/other/responses", "/api/v1/../v2/responses", "/"):
            assert request_via_socket(sock, "GET", bad)[0] == 403, bad
        assert p.container_base_url(18080) == "http://127.0.0.1:18080/api/v1"
    finally: p.stop()
    assert SECRET not in log.read_text() and "403 denied" in log.read_text()
    assert not Path(sock).exists()


def test_keyproxy_needs_key(tmp_path, monkeypatch):
    from gab.host.keyproxy import KeyProxy
    monkeypatch.delenv("NOPE_KEY", raising=False)
    with pytest.raises(RuntimeError, match="NOPE_KEY"): KeyProxy(str(tmp_path / "s"), "https://x.org/v1", key_env="NOPE_KEY")


# ---- tool bridge + agent in container ---------------------------------------------------------------------------------
def test_tool_bridge(tmp_path):
    import time
    from gab.agent.incontainer import ToolBridge, call_bridge
    w = tmp_path / "work"; (w / "sub").mkdir(parents=True)
    tool = tmp_path / "game"; tool.write_text("#!/bin/bash\necho \"args=$* cwd=$PWD\"; echo err >&2; exit 3\n"); tool.chmod(0o755)
    tb = ToolBridge(tmp_path / "t.sock", {"game": str(tool)}, [w], time.time() + 60, log=tmp_path / "tb.jsonl").start()
    try:
        r = call_bridge(tmp_path / "t.sock", "game", ["shot", "a b.png"], cwd=str(w / "sub"))
        assert r["rc"] == 3 and r["stdout"].strip() == f"args=shot a b.png cwd={w / 'sub'}" and r["stderr"].strip() == "err"
        assert call_bridge(tmp_path / "t.sock", "game", [], cwd="/etc")["stdout"].strip().endswith(f"cwd={w}")   # outside -> work dir
        assert call_bridge(tmp_path / "t.sock", "bash", ["-c", "id"])["rc"] == 127                               # only given tools
    finally: tb.stop()
    assert tb.calls == 2 and len((tmp_path / "tb.jsonl").read_text().splitlines()) == 2


def _stage(tmp_path):
    d = tmp_path / "runs" / "n" / "T" / "fine"
    w = d / "work"; (w / "task").mkdir(parents=True); (w / "reference").mkdir()
    (w / "task" / "task.yaml").write_text("target_game: flare\n"); (w / "task" / "instruction_fine.md").write_text("x")
    (w / ".gab" / "bin").mkdir(parents=True)
    g = w / ".gab" / "bin" / "game"; g.write_text("#!/bin/bash\necho game\n"); g.chmod(0o755)
    return d, w, {"game": str(g), "ffmpeg": "/usr/bin/ffmpeg"}


def test_codex_in_container(fake, tmp_path, monkeypatch, upstream):
    from gab.agent import base as abase
    from gab.agent.incontainer import mounts, wrap
    monkeypatch.setenv("FAKE_KEY_VAR", SECRET)
    exe = tmp_path / "codex-bin"; exe.write_text("#!/bin/sh\n"); exe.chmod(0o755)
    helper = tmp_path / "codex-code-mode-host"; helper.write_text("#!/bin/sh\n"); helper.chmod(0o755)
    link = tmp_path / "codex-link"; link.symlink_to(exe)              # an npm-style symlink: the real file must be copied
    monkeypatch.setenv("GAB_CODEX_CONTAINER_BINARY", str(link))
    monkeypatch.setenv("GAB_HIDDEN_DIR", str(tmp_path / "hidden"))
    d, w, tools = _stage(tmp_path)
    m = mounts(d)
    from gab.agent.incontainer import bridge_dir
    bd = bridge_dir(d)
    assert (str(d / "work"), str(d / "work"), "rw") in m and (str(bd), "/run/gab", "rw") in m and len(str(bd / "tools.sock")) < 100
    b = D.Backend(); seat = b.acquire({"game": "flare", "mounts": m}, "p")
    A = wrap(abase.get("codex", model="openai/gpt-6-astra", base_url=upstream, api_key_env="FAKE_KEY_VAR", reasoning_effort="high"), seat, d)
    run = A.run(w, "do the task", Budget(minutes=1), tools)
    assert run.exit_reason == "done" and run.tokens_in == 600 and run.tokens_cached == 400 and run.tokens_out == 50
    assert run.extra["in_container"]["container"] == seat.sandbox and run.extra["in_container"]["uid"] == 2000
    ex = [c for c in fake.calls() if c[0] == "exec" and "/run/gab/agent_entry.sh" in c][0]
    assert ex[ex.index("-u") + 1] == "2000:2000" and ex[ex.index("-w") + 1] == str(w)
    i = ex.index("/run/gab/agent_entry.sh"); cmd = ex[i + 1:]
    assert cmd[0] == "/opt/gab-agent/bin/codex" and cmd[cmd.index("-s") + 1] == "danger-full-access"
    prov = next(c for c in cmd if c.startswith("model_providers.gab="))
    assert 'base_url="http://127.0.0.1:18080/api/v1"' in prov and 'env_key="FAKE_KEY_VAR"' in prov
    assert "model_auto_compact_token_limit=300000" in cmd                       # codex compacts its context (cost guard)
    assert "model_auto_compact_token_limit=300000" in (d / "agent" / "command.txt").read_text()
    assert run.extra["spend"]["max_request_tokens"] == 1_000_000
    assert "FAKE_KEY_VAR=gab-proxy-no-key" in ex and "GAB_LLM_PORT=18080" in ex
    assert not any(SECRET in json.dumps(c) for c in fake.calls())               # the key never reaches the docker CLI
    assert not any(SECRET in p.read_text(errors="replace") for p in d.rglob("*") if p.is_file())
    assert (fake.dir / "rootfs" / "opt/gab-agent/bin/codex").exists()          # harness copied into the container
    assert not (fake.dir / "rootfs" / "opt/gab-agent/bin/codex").is_symlink()
    assert (fake.dir / "rootfs" / "opt/gab-agent/bin/codex-code-mode-host").exists()   # and the helper it spawns
    assert (bd / "bin" / "game").is_symlink() and not (bd / "bin" / "ffmpeg").exists() and not (bd / "llm.sock").exists()
    assert any(c[0] == "exec" and "pkill -KILL -u 2000" in c[-1] for c in fake.calls())
    assert any(c[0] == "exec" and c[-1].startswith("chown -R") for c in fake.calls())
    # a second run (iterative round) starts again from the user's options, not the rewritten ones
    A.run(w, "again", Budget(minutes=1), tools)
    ex2 = [c for c in fake.calls() if c[0] == "exec" and "/run/gab/agent_entry.sh" in c][-1]
    assert any('base_url="http://127.0.0.1:18080/api/v1"' in c for c in ex2)
    seat.release()


def test_in_container_refuses_without_proxy_or_docker(fake, tmp_path, monkeypatch):
    from gab.agent import base as abase
    from gab.agent.incontainer import wrap
    d, w, tools = _stage(tmp_path)
    seat = D.Backend().acquire({"game": "flare"}, "p")
    A = wrap(abase.get("codex", model="m"), seat, d)                            # no --base-url: would need a login file inside
    with pytest.raises(RuntimeError, match="host proxy"): A.run(w, "x", Budget(minutes=1), tools)
    class Pool: name = "pool"
    with pytest.raises(RuntimeError, match="docker host"): wrap(abase.get("codex"), Seat(Pool(), "sb"), d)


def test_cmd_agent_in_container(fake, tmp_path, monkeypatch):
    from gab.agent import base as abase
    from gab.agent.incontainer import wrap
    d, w, tools = _stage(tmp_path)
    seat = D.Backend().acquire({"game": "flare"}, "p")
    A = wrap(abase.get("cmd", agent_cmd="game info > {out}/x.txt"), seat, d)
    run = A.run(w, "x", Budget(minutes=1), tools)
    ex = [c for c in fake.calls() if c[0] == "exec" and "/run/gab/agent_entry.sh" in c][0]
    assert ex[-3:] == ["bash", "-c", f"game info > {w}/deliverable/x.txt"] and run.exit_reason == "done"
    assert "GAB_LLM_PORT=18080" not in ex


def test_gpu_games_are_spread_over_the_gpus(monkeypatch):
    """docker_gpus=auto: a GPU game gets ONE device, the least used over all running gab containers (2026-11-05: with
    --gpus all every trial rendered on GPU 0 while 7 L40S were idle)"""
    import subprocess as sp
    from gab.host import docker as D
    b = D.Backend.__new__(D.Backend)
    running = ["0", "0", "1", "2", "2", "2"]
    def run(args, timeout=None):
        out = ""
        if args[0] == "info": out = '{"nvidia":{}} []'
        if args[0] == "ps": out = "\n".join(running)
        return sp.CompletedProcess(args, 0, out, "")
    monkeypatch.setattr(b, "_run", run, raising=False)
    monkeypatch.setattr(b, "gpu_count", lambda: 4)
    monkeypatch.setattr(D, "manifest", lambda: {"games": {"g3d": {"gpu": True}, "g2d": {"gpu": False}}})
    monkeypatch.setattr(D.config, "get", lambda k: "auto" if k == "docker_gpus" else None)
    assert b.gpu_args("g3d") == ["--gpus", "device=3", "--label", f"{D.Backend.GPU_LABEL}=3"]     # 3 is unused
    running[:] = ["0", "1", "2", "3", "3", "1"]
    assert b.gpu_args("g3d")[1] == "device=0" and b.gpu_args("g2d") == []
    running[:] = ["", "", "1", "2", "3"]                                                          # 2 old --gpus all containers
    assert b.gpu_args("g3d")[1] == "device=1"                                                     # GPU 0 counts them
    monkeypatch.setattr(b, "gpu_count", lambda: 1)
    assert b.gpu_args("g3d") == ["--gpus", "all"]                                                   # one GPU: as before
