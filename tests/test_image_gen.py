"""gen-image (an agent-in-container trial can generate real art, not just recolor / procedurally draw, 2026-11-07): the key
proxy's own `openai_images` API family (only OpenRouter's /images path), the container script that calls it, and the
InContainer wrapper wiring GAB_IMAGE_PORT whenever OPENROUTER_API_KEY is set on the host -- never inside the container."""
import base64, json, os, subprocess, sys, threading
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path

import pytest

from gab.agent.base import Budget
from gab.host import docker as D
from gab.host.keyproxy import KeyProxy, request_via_socket
from test_claude_container import OAUTH, _agent_exec, _claude
from test_docker_backend import _stage, fake  # noqa: F401  (fixture)

GEN_IMAGE = Path(__file__).resolve().parent.parent / "gab" / "agent" / "container_files" / "gen_image.py"
ONE_PIXEL_PNG_B64 = base64.b64encode(bytes.fromhex("89504e470d0a1a0a")).decode()   # not a real PNG; the script never decodes it


def _images_server(answer, status=200):
    class H(BaseHTTPRequestHandler):
        def log_message(self, *a): pass
        def do_POST(self):
            self.rfile.read(int(self.headers.get("Content-Length") or 0))
            b = json.dumps(answer).encode() if not isinstance(answer, bytes) else answer
            self.send_response(status); self.send_header("Content-Length", str(len(b))); self.end_headers(); self.wfile.write(b)
    s = HTTPServer(("127.0.0.1", 0), H); threading.Thread(target=s.serve_forever, daemon=True).start()
    return s


# ---- key proxy: the openai_images API family ---------------------------------------------------------------------------
def test_keyproxy_openai_images_allow(tmp_path, monkeypatch):
    monkeypatch.setenv("FAKE_OR_KEY", "sk-or-fake")
    up = _images_server({"created": 1, "data": [{"b64_json": ONE_PIXEL_PNG_B64, "media_type": "image/png"}], "usage": {"cost": 0.035}})
    sock = str(tmp_path / "images.sock")
    p = KeyProxy(sock, f"http://127.0.0.1:{up.server_address[1]}/api/v1", key_env="FAKE_OR_KEY", api="openai_images", auth="bearer").start()
    try:
        st, body = request_via_socket(sock, "POST", "/api/v1/images", b'{"model":"m","prompt":"x"}',
                                      {"Content-Type": "application/json", "Content-Length": "26"})
        assert st == 200 and b"b64_json" in body
        for bad in ("/api/v1/chat/completions", "/api/v1/completions", "/v1/messages", "/api/v1"):
            assert request_via_socket(sock, "POST", bad, b"{}")[0] == 403, bad
    finally: p.stop(); up.shutdown()


def test_keyproxy_none_auth_adds_no_header(tmp_path, monkeypatch):
    """a local, unauthenticated gen-image server (a loopback image_base_url): no key_env, no key, and the Authorization header the container's placeholder sent is dropped, not forwarded."""
    seen = []
    class H(BaseHTTPRequestHandler):
        def log_message(self, *a): pass
        def do_POST(self):
            self.rfile.read(int(self.headers.get("Content-Length") or 0)); seen.append(self.headers.get("Authorization"))
            b = json.dumps({"data": [{"b64_json": ONE_PIXEL_PNG_B64, "media_type": "image/png"}]}).encode()
            self.send_response(200); self.send_header("Content-Length", str(len(b))); self.end_headers(); self.wfile.write(b)
    up = HTTPServer(("127.0.0.1", 0), H); threading.Thread(target=up.serve_forever, daemon=True).start()
    sock = str(tmp_path / "images.sock")
    p = KeyProxy(sock, f"http://127.0.0.1:{up.server_address[1]}", api="openai_images", auth="none").start()
    try:
        st, body = request_via_socket(sock, "POST", "/images", b'{"model":"m","prompt":"x"}',
                                      {"Content-Type": "application/json", "Content-Length": "26", "Authorization": "Bearer gab-proxy-no-key"})
        assert st == 200 and b"b64_json" in body and seen == [None]
    finally: p.stop(); up.shutdown()


def test_keyproxy_upstream_drop_with_client_gone_is_quiet(tmp_path, capfd):
    """2026-11-07: the local image server was restarted with requests in flight; the agents' gen-image had given up too, so
    the proxy's 502 hit a broken pipe and printed a traceback. Now: one log line ('client gone'), no traceback."""
    import socket, time
    got = threading.Event()
    class H(BaseHTTPRequestHandler):
        def log_message(self, *a): pass
        def do_POST(self):
            self.rfile.read(int(self.headers.get("Content-Length") or 0)); got.set(); time.sleep(0.5)
            self.close_connection = True; self.connection.shutdown(socket.SHUT_RDWR)      # closed without a response
    up = HTTPServer(("127.0.0.1", 0), H); threading.Thread(target=up.serve_forever, daemon=True).start()
    sock, log = str(tmp_path / "images.sock"), tmp_path / "image_proxy.log"
    p = KeyProxy(sock, f"http://127.0.0.1:{up.server_address[1]}", api="openai_images", auth="none", log=log).start()
    try:
        c = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM); c.connect(sock)
        c.setsockopt(socket.SOL_SOCKET, socket.SO_LINGER, b"\x01\x00\x00\x00\x00\x00\x00\x00")   # RST on close
        c.sendall(b"POST /images HTTP/1.1\r\nHost: x\r\nContent-Length: 2\r\n\r\n{}"); assert got.wait(5); c.close()
        for _ in range(50):
            if "client gone" in (log.read_text() if log.exists() else ""): break
            time.sleep(0.1)
        assert "502 client gone" in log.read_text()
        assert "Traceback" not in capfd.readouterr().err
    finally: p.stop(); up.shutdown()


def test_keyproxy_none_auth_needs_no_key(tmp_path):
    """auth="none" must not raise even though no key/key_env is given (every other auth mode requires one)"""
    p = KeyProxy(tmp_path / "s.sock", "http://127.0.0.1:1", api="openai_images", auth="none")
    assert p._key is None


# ---- gen_image.py: the container-side script (run directly, no container) ----------------------------------------------
def _run_gen_image(tmp_path, args, port=None):
    env = dict(os.environ); env.pop("GAB_IMAGE_PORT", None)
    if port is not None: env["GAB_IMAGE_PORT"] = str(port)
    return subprocess.run([sys.executable, str(GEN_IMAGE), *args], cwd=tmp_path, capture_output=True, text=True, env=env)


def test_gen_image_writes_the_file(tmp_path):
    up = _images_server({"created": 1, "data": [{"b64_json": ONE_PIXEL_PNG_B64, "media_type": "image/png"}], "usage": {"cost": 0.035}})
    try:
        r = _run_gen_image(tmp_path, ["a torch sprite", "out.png"], port=up.server_address[1])
    finally: up.shutdown()
    assert r.returncode == 0, r.stderr
    d = json.loads(r.stdout)
    assert d == {"ok": True, "path": "out.png", "extra_paths": [], "cost": 0.035}
    assert (tmp_path / "out.png").read_bytes() == base64.b64decode(ONE_PIXEL_PNG_B64)


def test_gen_image_several_images_get_numbered_siblings(tmp_path):
    two = {"created": 1, "data": [{"b64_json": ONE_PIXEL_PNG_B64, "media_type": "image/png"},
                                   {"b64_json": ONE_PIXEL_PNG_B64, "media_type": "image/png"}], "usage": {"cost": 0.07}}
    up = _images_server(two)
    try:
        r = _run_gen_image(tmp_path, ["two icons", "icon.png", "--n", "2"], port=up.server_address[1])
    finally: up.shutdown()
    d = json.loads(r.stdout)
    assert d["ok"] and d["path"] == "icon.png" and d["extra_paths"] == ["icon_1.png"]
    assert (tmp_path / "icon_1.png").exists()


def test_gen_image_without_proxy_fails_clearly(tmp_path):
    r = _run_gen_image(tmp_path, ["x", "out.png"], port=None)
    assert r.returncode == 1 and json.loads(r.stdout) == {"ok": False, "error": "image generation is not available this run "
                                                           "(no image key configured)"}


def test_gen_image_upstream_error_passed_through(tmp_path):
    up = _images_server({"error": {"code": 402, "message": "insufficient credits"}}, status=402)
    try:
        r = _run_gen_image(tmp_path, ["x", "out.png"], port=up.server_address[1])
    finally: up.shutdown()
    assert r.returncode == 1
    d = json.loads(r.stdout)
    assert d["ok"] is False and "insufficient credits" in d["error"]
    assert not (tmp_path / "out.png").exists()


# ---- InContainer wiring: GAB_IMAGE_PORT only when OPENROUTER_API_KEY is set on the host ---------------------------------
def test_image_proxy_wired_when_openrouter_key_set(fake, tmp_path, monkeypatch):
    from gab import config
    from gab.agent.incontainer import IMAGE_PORT
    get = config.get                                  # the default (OpenRouter) upstream, whatever gab.local.json says
    pin = {"image_base_url": "https://openrouter.ai/api/v1", "image_key_env": "OPENROUTER_API_KEY", "image_api": "images",
           "audio_base_url": "", "meshy_key_env": "NO_SUCH_MESHY_KEY"}
    monkeypatch.setattr(config, "get", lambda k, *a, **kw: pin[k] if k in pin else get(k, *a, **kw))
    monkeypatch.setenv("CLAUDE_CODE_OAUTH_TOKEN", OAUTH)
    monkeypatch.setenv("OPENROUTER_API_KEY", "sk-or-v1-" + "y" * 40)
    d, w, tools, seat, A = _claude(fake, tmp_path, monkeypatch)
    try:
        run = A.run(w, "do the task", Budget(minutes=1), tools)
        _, env, _ = _agent_exec(fake)
        assert env["GAB_IMAGE_PORT"] == str(IMAGE_PORT) and env["GAB_IMAGE_BASE"] == "/api/v1"
        assert run.extra["in_container"]["image_requests"] == 0
        log = (d / "agent" / "image_proxy.log")
        assert log.exists() and "api=openai_images auth=bearer" in log.read_text()
        assert not any("sk-or-v1-" + "y" * 40 in p.read_text(errors="replace") for p in d.rglob("*") if p.is_file())
    finally: seat.release()


def test_image_proxy_prefers_the_local_server_over_openrouter(fake, tmp_path, monkeypatch):
    """a loopback image_base_url (a local server, no credential) needs no key: no Authorization is added"""
    from gab import config
    from gab.agent.incontainer import IMAGE_PORT
    get = config.get
    monkeypatch.setattr(config, "get", lambda k, *a, **kw: "http://127.0.0.1:18090" if k == "image_base_url" else get(k, *a, **kw))
    monkeypatch.setenv("CLAUDE_CODE_OAUTH_TOKEN", OAUTH)
    monkeypatch.setenv("OPENROUTER_API_KEY", "sk-or-v1-" + "y" * 40)
    d, w, tools, seat, A = _claude(fake, tmp_path, monkeypatch)
    try:
        run = A.run(w, "do the task", Budget(minutes=1), tools)
        _, env, _ = _agent_exec(fake)
        assert env["GAB_IMAGE_PORT"] == str(IMAGE_PORT) and env["GAB_IMAGE_BASE"] == ""
        log = (d / "agent" / "image_proxy.log")
        assert log.exists() and "start upstream http://127.0.0.1 api=openai_images auth=none" in log.read_text()
    finally: seat.release()


def test_no_image_proxy_without_openrouter_key(fake, tmp_path, monkeypatch):
    from gab import config
    get = config.get                                  # the default (OpenRouter) upstream, whatever gab.local.json says
    pin = {"image_base_url": "https://openrouter.ai/api/v1", "image_key_env": "OPENROUTER_API_KEY", "image_api": "images",
           "audio_base_url": "", "meshy_key_env": "NO_SUCH_MESHY_KEY"}
    monkeypatch.setattr(config, "get", lambda k, *a, **kw: pin[k] if k in pin else get(k, *a, **kw))
    monkeypatch.setenv("CLAUDE_CODE_OAUTH_TOKEN", OAUTH)
    monkeypatch.delenv("OPENROUTER_API_KEY", raising=False)
    d, w, tools, seat, A = _claude(fake, tmp_path, monkeypatch)
    try:
        run = A.run(w, "do the task", Budget(minutes=1), tools)
        _, env, _ = _agent_exec(fake)
        assert "GAB_IMAGE_PORT" not in env
        assert run.extra["in_container"]["image_requests"] == 0
        assert not (d / "agent" / "image_proxy.log").exists()
    finally: seat.release()


def test_gen_image_through_the_real_proxy_end_to_end(tmp_path, monkeypatch):
    """2026-11-07: every gen-image call of the first rounds got 403 from gab's own proxy: the script asked "/images",
    the proxy forwards only <upstream base>/images (/api/v1/images). The two had only been tested apart. Here: gen_image.py ->
    llm_forward.py (TCP -> unix socket, as in the container) -> KeyProxy -> a fake OpenRouter that checks the path"""
    import socket as _s, time as _t
    seen = []
    class H(BaseHTTPRequestHandler):
        def log_message(self, *a): pass
        def do_POST(self):
            self.rfile.read(int(self.headers.get("Content-Length") or 0)); seen.append((self.path, self.headers.get("Authorization")))
            b = json.dumps({"data": [{"b64_json": ONE_PIXEL_PNG_B64, "media_type": "image/png"}]}).encode()
            self.send_response(200); self.send_header("Content-Length", str(len(b))); self.end_headers(); self.wfile.write(b)
    up = HTTPServer(("127.0.0.1", 0), H); threading.Thread(target=up.serve_forever, daemon=True).start()
    monkeypatch.setenv("FAKE_OR_KEY", "sk-or-fake")
    sock = str(tmp_path / "images.sock")
    p = KeyProxy(sock, f"http://127.0.0.1:{up.server_address[1]}/api/v1", key_env="FAKE_OR_KEY", api="openai_images", auth="bearer").start()
    with _s.socket() as t: t.bind(("127.0.0.1", 0)); port = t.getsockname()[1]
    fwd = subprocess.Popen([sys.executable, str(GEN_IMAGE.parent / "llm_forward.py"), str(port), sock])
    try:
        for _ in range(50):
            try: _s.create_connection(("127.0.0.1", port), 0.2).close(); break
            except OSError: _t.sleep(0.1)
        env = dict(os.environ, GAB_IMAGE_PORT=str(port), GAB_IMAGE_BASE=p.base)
        r = subprocess.run([sys.executable, str(GEN_IMAGE), "a torch", "out.png"], cwd=tmp_path, capture_output=True, text=True, env=env)
        assert r.returncode == 0 and json.loads(r.stdout)["ok"], r.stdout + r.stderr
        assert seen == [("/api/v1/images", "Bearer sk-or-fake")]
        env.pop("GAB_IMAGE_BASE")                                   # the old request path: refused by the proxy
        r = subprocess.run([sys.executable, str(GEN_IMAGE), "a torch", "o2.png"], cwd=tmp_path, capture_output=True, text=True, env=env)
        assert r.returncode == 1 and "only the model API is forwarded" in r.stdout
    finally: fwd.kill(); p.stop(); up.shutdown()


def test_image_route_local_upstream_needs_no_key(monkeypatch):
    """a loopback image_base_url needs no credential (auth="none") and turns gen-image on; a remote one only with its key in
    the environment (auth="bearer"); an empty one turns it off"""
    from gab import config
    from gab.agent.incontainer import InContainer
    vals = {"image_base_url": "http://127.0.0.1:18190/api/v1", "image_key_env": "OPENROUTER_API_KEY"}
    get = config.get
    monkeypatch.setattr(config, "get", lambda k, *a, **kw: vals[k] if k in vals else get(k, *a, **kw))
    monkeypatch.delenv("OPENROUTER_API_KEY", raising=False)
    assert InContainer.image_route() == ("http://127.0.0.1:18190/api/v1", None, None, "none")
    vals["image_base_url"] = "https://openrouter.ai/api/v1"
    assert InContainer.image_route() is None
    monkeypatch.setenv("OPENROUTER_API_KEY", "sk-or-v1-x")
    assert InContainer.image_route() == ("https://openrouter.ai/api/v1", "OPENROUTER_API_KEY", None, "bearer")
    vals["image_base_url"] = ""
    assert InContainer.image_route() is None


def test_gpu_picker_skips_excluded_devices(monkeypatch):
    from gab import config
    from gab.host import docker as Dk
    b = Dk.Backend.__new__(Dk.Backend)
    monkeypatch.setattr(b, "_run", lambda *a, **k: type("R", (), {"stdout": "3\n3\n4\n"})(), raising=False)
    get = config.get
    monkeypatch.setattr(config, "get", lambda k, *a, **kw: [0, 1, 2] if k == "docker_gpu_exclude" else get(k, *a, **kw))
    assert b.pick_gpu(8) == 5                     # 0-2 kept for the image server, 3/4 busy -> the first free other one
    monkeypatch.setattr(config, "get", lambda k, *a, **kw: [] if k == "docker_gpu_exclude" else get(k, *a, **kw))
    assert b.pick_gpu(8) == 0


# ---- the router: images/generations (image_api generations) online first, local fallback -----------------------------------
def _gen_server(status, answer, seen):
    class H(BaseHTTPRequestHandler):
        def log_message(self, *a): pass
        def do_POST(self):
            seen.append((self.path, json.loads(self.rfile.read(int(self.headers.get("Content-Length") or 0))), self.headers.get("Authorization")))
            b = json.dumps(answer).encode()
            self.send_response(status); self.send_header("Content-Length", str(len(b))); self.end_headers(); self.wfile.write(b)
    s = HTTPServer(("127.0.0.1", 0), H); threading.Thread(target=s.serve_forever, daemon=True).start(); return s


def _router(tmp_path, monkeypatch, online, local):
    from gab.host.keyproxy import ImageRouter
    monkeypatch.setenv("FAKE_T360_KEY", "sk-fake")
    sock = str(tmp_path / "images.sock")
    fb = f"http://127.0.0.1:{local.server_address[1]}/api/v1" if local else None
    r = ImageRouter(sock, f"http://127.0.0.1:{online.server_address[1]}/v1", "dola-seedream-5.0-lite", fallback=fb,
                    image_api="generations", key_env="FAKE_T360_KEY", auth="bearer", log=tmp_path / "image_proxy.log").start()
    return r, sock


def _ask(sock, body):
    b = json.dumps(body).encode()
    return request_via_socket(sock, "POST", "/v1/images", b, {"Content-Type": "application/json", "Content-Length": str(len(b))}, timeout=30)


def test_router_online_first_translates_the_request(tmp_path, monkeypatch):
    on, seen = [], []
    online = _gen_server(200, {"data": [{"b64_json": ONE_PIXEL_PNG_B64, "size": "2560x1440"}]}, on)
    local = _gen_server(200, {"data": [{"b64_json": "LOCAL"}]}, seen)
    r, sock = _router(tmp_path, monkeypatch, online, local)
    try:
        st, body = _ask(sock, {"model": "bytedance-seed/seedream-5-0-lite", "prompt": "a hay bale", "n": 2, "aspect_ratio": "16:9"})
        d = json.loads(body)
        assert st == 200 and d["backend"] == "online" and len(d["data"]) == 2 and not seen
        path, req, auth = on[0]
        assert path == "/v1/images/generations" and auth == "Bearer sk-fake" and len(on) == 2       # no n: one call per image
        assert req == {"model": "dola-seedream-5.0-lite", "prompt": "a hay bale", "size": "2560x1440", "response_format": "b64_json", "watermark": False}
        assert r.backends == {"online": 2} and "backend=online" in (tmp_path / "image_proxy.log").read_text()
    finally: r.stop(); online.shutdown(); local.shutdown()


def test_router_falls_back_to_local_on_content_policy(tmp_path, monkeypatch):
    """the online API answers a sensitive subject with 400 content_policy_violation: the local server makes the image"""
    on, seen = [], []
    online = _gen_server(400, {"error": {"code": "content_policy_violation", "message": "may contain sensitive content"}}, on)
    local = _gen_server(200, {"data": [{"b64_json": ONE_PIXEL_PNG_B64, "media_type": "image/png"}]}, seen)
    r, sock = _router(tmp_path, monkeypatch, online, local)
    try:
        st, body = _ask(sock, {"prompt": "a famous plumber", "n": 1})
        assert st == 200 and json.loads(body)["backend"] == "local"
        assert seen[0][0] == "/api/v1/images" and seen[0][1]["prompt"] == "a famous plumber" and seen[0][2] is None   # no key sent locally
        log = (tmp_path / "image_proxy.log").read_text()
        assert "content_policy_violation" in log and "backend=local" in log and r.backends == {"local": 1}
    finally: r.stop(); online.shutdown(); local.shutdown()


def test_router_both_failing_is_a_clear_error_not_a_credit_stop(tmp_path, monkeypatch):
    on, seen = [], []
    online = _gen_server(402, {"error": {"code": "insufficient_quota"}}, on)
    local = _gen_server(500, {"error": {"message": "the generated image is not usable"}}, seen)
    r, sock = _router(tmp_path, monkeypatch, online, local)
    try:
        st, body = _ask(sock, {"prompt": "x"})
        assert st == 502 and b"image generation failed" in body and b"not usable" in body
    finally: r.stop(); online.shutdown(); local.shutdown()


def test_keyproxy_enlarges_images_the_provider_refuses():
    """DashScope refuses an image with a side under 11 px (400, the agent died: qwen3.8-flash on tof2, a 256x1 texture strip)"""
    import base64, io
    from PIL import Image
    from gab.host.keyproxy import fix_tiny_images
    def url(w, h):
        b = io.BytesIO(); Image.new("RGB", (w, h), (200, 10, 10)).save(b, "PNG"); return "data:image/png;base64," + base64.b64encode(b.getvalue()).decode()
    body = json.dumps({"messages": [{"role": "user", "content": [{"type": "image_url", "image_url": {"url": url(256, 1)}},
                                                                  {"type": "image_url", "image_url": {"url": url(64, 64)}}]}]}).encode()
    out, n = fix_tiny_images(body)
    assert n == 1
    c = json.loads(out)["messages"][0]["content"]
    big = Image.open(io.BytesIO(base64.b64decode(c[0]["image_url"]["url"].split(",", 1)[1])))
    assert min(big.size) >= 16 and max(big.size) / min(big.size) <= 100      # ... and the ratio limit (< 200): 4096x16 was refused too
    assert c[1]["image_url"]["url"] == url(64, 64)                                # a normal image is left alone
    assert fix_tiny_images(b'{"messages":[]}') == (b'{"messages":[]}', 0)


def test_router_images_api_passes_the_body_with_the_configured_model(tmp_path, monkeypatch):
    """image_api "images" (OpenRouter's images API): gen-image's body as it is, the router's model, the key added"""
    from gab.host.keyproxy import ImageRouter
    on = []
    online = _gen_server(200, {"data": [{"b64_json": _jpeg_b64(), "media_type": "image/jpeg"}]}, on)
    monkeypatch.setenv("FAKE_OR_KEY", "sk-or-fake")
    sock = str(tmp_path / "images.sock")
    r = ImageRouter(sock, f"http://127.0.0.1:{online.server_address[1]}/v1", "vendor/image-model", image_api="images",
                    key_env="FAKE_OR_KEY", auth="bearer", log=tmp_path / "image_proxy.log").start()
    try:
        st, body = _ask(sock, {"model": None, "prompt": "a hay bale", "n": 1, "aspect_ratio": "16:9"})
        assert st == 200 and json.loads(body)["data"][0]["media_type"] == "image/png"
        path, req, auth = on[0]
        assert path == "/v1/images" and auth == "Bearer sk-or-fake" and req["model"] == "vendor/image-model" and req["aspect_ratio"] == "16:9"
    finally: r.stop(); online.shutdown()


def test_router_without_an_image_key_still_serves_the_other_tools(tmp_path, monkeypatch):
    """no image key but a Meshy key: the router starts, gen-image answers that it is off"""
    from gab import config as C
    from gab.agent import incontainer as IC
    get = C.get
    pin = {"image_base_url": "https://openrouter.ai/api/v1", "image_key_env": "NO_SUCH_IMAGE_KEY", "audio_base_url": "",
           "meshy_key_env": "FAKE_MESHY"}
    monkeypatch.setattr(C, "get", lambda k, *a, **kw: pin[k] if k in pin else get(k, *a, **kw))
    monkeypatch.delenv("NO_SUCH_IMAGE_KEY", raising=False); monkeypatch.setenv("FAKE_MESHY", "m")
    assert IC.gen_tools_on() == {"image": False, "audio": False, "3d": True}
    r = IC.start_image_router(tmp_path / "s.sock", tmp_path / "image_proxy.log")
    try:
        b = json.dumps({"prompt": "x"}).encode()
        st, body = request_via_socket(str(tmp_path / "s.sock"), "POST", f"{r.base}/images", b,
                                      {"Content-Type": "application/json", "Content-Length": str(len(b))}, timeout=30)
        assert st == 502 and b"not available this run" in body
    finally: r.stop()
    monkeypatch.delenv("FAKE_MESHY")
    assert IC.gen_tools_on() == {} and IC.start_image_router(tmp_path / "t.sock", tmp_path / "l.log") is None


def _jpeg_b64():
    import io
    from PIL import Image
    out = io.BytesIO(); Image.new("RGB", (4, 3), (200, 10, 10)).save(out, "JPEG"); return base64.b64encode(out.getvalue()).decode()


def test_images_body_sends_reference_images_as_input_references():
    """OpenRouter's images API ignores an `images` list: references go into input_references [{type: image_url, image_url}]"""
    from gab.host.keyproxy import images_body
    refs = ["data:image/png;base64,AAA", "https://x.org/r.jpg", "not-a-uri"] + ["data:image/jpeg;base64,B"] * 5
    b = images_body({"model": "agent-pick", "prompt": "p", "n": 2, "aspect_ratio": "16:9", "images": refs}, "vendor/model")
    assert b["model"] == "vendor/model" and b["prompt"] == "p" and b["n"] == 2 and b["aspect_ratio"] == "16:9" and "images" not in b
    assert b["input_references"][:2] == [{"type": "image_url", "image_url": {"url": "data:image/png;base64,AAA"}},
                                         {"type": "image_url", "image_url": {"url": "https://x.org/r.jpg"}}]
    assert len(b["input_references"]) == 4
    assert "input_references" not in images_body({"prompt": "p"}, "m")


def test_router_images_api_references_and_png(tmp_path, monkeypatch):
    """through the router: --ref images arrive as input_references, a JPEG answer comes back as PNG"""
    from gab.host.keyproxy import ImageRouter
    on = []
    online = _gen_server(200, {"data": [{"b64_json": _jpeg_b64(), "media_type": "image/jpeg"}]}, on)
    monkeypatch.setenv("FAKE_OR_KEY", "sk-or-fake")
    sock = str(tmp_path / "images.sock")
    r = ImageRouter(sock, f"http://127.0.0.1:{online.server_address[1]}/v1", "vendor/image-model", image_api="images",
                    key_env="FAKE_OR_KEY", auth="bearer", log=tmp_path / "image_proxy.log").start()
    try:
        st, body = _ask(sock, {"prompt": "redraw", "n": 1, "images": ["data:image/png;base64,REF"]})
        d = json.loads(body)
        assert st == 200 and d["data"][0]["media_type"] == "image/png" and base64.b64decode(d["data"][0]["b64_json"])[:4] == b"\x89PNG"
        req = on[0][1]
        assert "images" not in req and req["input_references"] == [{"type": "image_url", "image_url": {"url": "data:image/png;base64,REF"}}]
    finally: r.stop(); online.shutdown()


def test_target_pictures_use_input_references_and_are_png(tmp_path, monkeypatch):
    """refine.make_targets' image call: the same body as gen-image (input_references), the answer written as a real PNG"""
    import urllib.request
    from gab import config
    from gab.refine import refine
    get = config.get
    pin = {"image_base_url": "https://openrouter.ai/api/v1", "image_key_env": "FAKE_OR_KEY", "image_model": "vendor/m", "image_api": "images"}
    monkeypatch.setattr(config, "get", lambda k, *a, **kw: pin[k] if k in pin else get(k, *a, **kw))
    monkeypatch.setenv("FAKE_OR_KEY", "sk-or-fake")
    ref = tmp_path / "ref.png"; ref.write_bytes(base64.b64decode(_jpeg_b64()))
    sent = {}
    class Resp:
        def __enter__(self): return self
        def __exit__(self, *a): pass
        def read(self): return json.dumps({"data": [{"b64_json": _jpeg_b64(), "media_type": "image/jpeg"}]}).encode()
    def fake_open(req, timeout=None):
        sent["url"] = req.full_url; sent["body"] = json.loads(req.data); sent["auth"] = req.headers.get("Authorization"); return Resp()
    monkeypatch.setattr(urllib.request, "urlopen", fake_open)
    png = refine._generate("draw it", [ref], "2560x1440")
    assert png[:4] == b"\x89PNG" and sent["url"] == "https://openrouter.ai/api/v1/images" and sent["auth"] == "Bearer sk-or-fake"
    b = sent["body"]
    assert b["model"] == "vendor/m" and "images" not in b and b["input_references"][0]["image_url"]["url"].startswith("data:image/")
