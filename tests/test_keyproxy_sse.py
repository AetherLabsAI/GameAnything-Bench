"""key proxy: streamed chat completions lose empty tool-call deltas (Kimi K3 via a gateway sends {"index": 0, "type":
"function"} with no 'function'; OpenCode's AI SDK rejects the chunk and the harness dies -- 2026-11-05)"""
import json, threading, time
from http.server import BaseHTTPRequestHandler, HTTPServer

from gab.run import infra
from gab.host.keyproxy import KeyProxy, clean_sse_line, request_via_socket

BAD = {"choices": [{"delta": {"tool_calls": [{"index": 0, "type": "function"}]}, "finish_reason": None, "index": 0}], "id": "c", "object": "chat.completion.chunk"}
GOOD = {"choices": [{"delta": {"tool_calls": [{"index": 0, "id": "call_1", "type": "function", "function": {"name": "bash", "arguments": ""}}]}, "index": 0}]}


def test_clean_sse_line():
    out = clean_sse_line(b"data: " + json.dumps(BAD).encode() + b"\n")
    d = json.loads(out[5:]); assert d["choices"][0]["delta"] == {} and d["id"] == "c" and out.endswith(b"\n")
    for keep in (b"data: " + json.dumps(GOOD).encode() + b"\n", b"data: [DONE]\n", b": keep-alive\n", b"\n", b"data: {broken\n",
                 b'data: {"choices":[{"delta":{"content":"hi"}}]}\r\n'):
        assert clean_sse_line(keep) == keep
    mixed = {"choices": [{"delta": {"tool_calls": [{"index": 0, "type": "function"}, GOOD["choices"][0]["delta"]["tool_calls"][0]]}}]}
    d = json.loads(clean_sse_line(b"data: " + json.dumps(mixed).encode() + b"\n")[5:])
    assert d["choices"][0]["delta"]["tool_calls"] == GOOD["choices"][0]["delta"]["tool_calls"]


def test_classify_stream_error():
    msg = 'Type validation failed: Value: {"choices":[{"delta":{"tool_calls":[{"index":0,"type":"function"}]}}]}.'
    assert infra.classify(msg)[0]["code"] == "api_stream_invalid"


def test_proxy_cleans_a_stream_split_across_reads(tmp_path):
    lines = [b"data: " + json.dumps(GOOD).encode() + b"\n\n", b"data: " + json.dumps(BAD).encode() + b"\n\n", b"data: [DONE]\n\n"]
    class H(BaseHTTPRequestHandler):
        protocol_version = "HTTP/1.0"
        def log_message(self, *a): pass
        def do_POST(self):
            self.rfile.read(int(self.headers.get("Content-Length") or 0))
            self.send_response(200); self.send_header("Content-Type", "text/event-stream"); self.end_headers()
            blob = b"".join(lines); cut = len(lines[0]) + 20            # the bad line arrives in two pieces
            self.wfile.write(blob[:cut]); self.wfile.flush(); time.sleep(0.2); self.wfile.write(blob[cut:]); self.wfile.flush()
    up = HTTPServer(("127.0.0.1", 0), H); threading.Thread(target=up.serve_forever, daemon=True).start()
    p = KeyProxy(tmp_path / "s.sock", f"http://127.0.0.1:{up.server_port}/v1", key="K", log=tmp_path / "log").start()
    try:
        st, body = request_via_socket(str(tmp_path / "s.sock"), "POST", "/v1/chat/completions", body=b"{}",
                                      headers={"Content-Type": "application/json", "Content-Length": "2"})
    finally: p.stop(); up.shutdown()
    assert st == 200
    chunks = [json.loads(l[5:]) for l in body.split(b"\n") if l.startswith(b"data: {")]
    assert chunks[0] == GOOD and chunks[1]["choices"][0]["delta"] == {} and body.rstrip().endswith(b"data: [DONE]")


def test_repair_tool_history():
    from gab.host.keyproxy import repair_tool_history, INTERRUPTED
    call = lambda i: {"id": i, "type": "function", "function": {"name": "bash", "arguments": "{}"}}
    ok = {"model": "gemini-3.8-flash", "messages": [{"role": "user", "content": "go"},
          {"role": "assistant", "content": None, "tool_calls": [call("a"), call("b")]},
          {"role": "tool", "tool_call_id": "a", "content": "1"}, {"role": "tool", "tool_call_id": "b", "content": "2"},
          {"role": "assistant", "content": ""}, {"role": "user", "content": "continue"}]}
    b = json.dumps(ok).encode()
    assert repair_tool_history(b) == (b, (0, 0))                                   # consistent: byte for byte
    bad = json.loads(b); bad["messages"].pop(3)                                     # result of "b" missing
    bad["messages"].insert(1, {"role": "tool", "tool_call_id": "zz", "content": "orphan"})
    out, n = repair_tool_history(json.dumps(bad).encode())
    m = json.loads(out)["messages"]
    assert n == (1, 1) and [x["role"] for x in m] == ["user", "assistant", "tool", "tool", "assistant", "user"]
    assert m[3] == {"role": "tool", "tool_call_id": "b", "content": INTERRUPTED}
    assert infra.classify("Please ensure that the number of function response parts is equal to the number of function call parts of the function call turn.")[0]["code"] == "api_history_rejected"


def test_proxy_keeps_rejected_requests(tmp_path):
    class H(BaseHTTPRequestHandler):
        def log_message(self, *a): pass
        def do_POST(self):
            self.rfile.read(int(self.headers.get("Content-Length") or 0)); b = b'{"error":{"code":400,"message":"bad history"}}'
            self.send_response(400); self.send_header("Content-Length", str(len(b))); self.end_headers(); self.wfile.write(b)
    up = HTTPServer(("127.0.0.1", 0), H); threading.Thread(target=up.serve_forever, daemon=True).start()
    (tmp_path / "agent").mkdir()
    p = KeyProxy(tmp_path / "s.sock", f"http://127.0.0.1:{up.server_port}/v1", key="SECRETKEY", log=tmp_path / "agent" / "llm_proxy.log").start()
    try:
        st, _ = request_via_socket(str(tmp_path / "s.sock"), "POST", "/v1/chat/completions", body=b'{"messages":[]}',
                                   headers={"Content-Type": "application/json", "Content-Length": "15"})
    finally: p.stop(); up.shutdown()
    d = tmp_path / "agent" / "llm_4xx"
    assert st == 400 and sorted(f.name for f in d.iterdir()) == ["001_400.request.json", "001_400.response.txt"]
    assert (d / "001_400.request.json").read_bytes() == b'{"messages":[]}' and b"bad history" in (d / "001_400.response.txt").read_bytes()
    assert all(b"SECRETKEY" not in f.read_bytes() for f in d.iterdir())


def test_shrink_images_only_above_the_limit():
    import base64, io
    from PIL import Image
    from gab.host import keyproxy as K
    buf = io.BytesIO(); Image.effect_noise((2400, 1350), 80).convert("RGB").save(buf, "PNG")
    url = "data:image/png;base64," + base64.b64encode(buf.getvalue()).decode()
    body = json.dumps({"model": "m", "messages": [{"role": "user", "content": [{"type": "text", "text": "look"},
            {"type": "image_url", "image_url": {"url": url}}]}]}).encode()
    assert K.shrink_images(body, above=len(body) + 1) == (body, (0, len(body), len(body)))        # below the limit: untouched
    out, (n, a, b) = K.shrink_images(body, above=1000)
    assert n == 1 and b < a / 3 and a == len(body)
    u = json.loads(out)["messages"][0]["content"][1]["image_url"]["url"]
    im = Image.open(io.BytesIO(base64.b64decode(u.split(",", 1)[1])))
    assert u.startswith("data:image/jpeg;base64,") and max(im.size) == K.SHRINK_SIDE and json.loads(out)["messages"][0]["content"][0]["text"] == "look"


def test_new_tool_call_without_id_gets_one():
    """qwen3.8-max via a gateway: the first delta of a tool call had a function but no id (AI SDK: Expected 'id' to be a string)"""
    seen = set()
    first = {"choices": [{"index": 0, "delta": {"tool_calls": [{"index": 0, "type": "function", "function": {"name": "bash", "arguments": ""}}]}}]}
    more = {"choices": [{"index": 0, "delta": {"tool_calls": [{"index": 0, "function": {"arguments": '{"c": 1}'}}]}}]}
    d1 = json.loads(clean_sse_line(b"data: " + json.dumps(first).encode() + b"\n", seen)[5:])
    t = d1["choices"][0]["delta"]["tool_calls"][0]
    assert isinstance(t["id"], str) and t["id"].startswith("call_gab_") and t["function"]["name"] == "bash"
    line = b"data: " + json.dumps(more).encode() + b"\n"
    assert clean_sse_line(line, seen) == line                                     # continuation deltas need no id
    ok = {"choices": [{"index": 0, "delta": {"tool_calls": [{"index": 1, "id": "call_x", "function": {"name": "read"}}]}}]}
    line = b"data: " + json.dumps(ok).encode() + b"\n"
    assert clean_sse_line(line, seen) == line                                     # an id already there: untouched
    assert infra.classify("UnknownError: Expected 'id' to be a string.")[0]["code"] == "api_stream_invalid"
    assert infra.BY["api_stream_invalid"].kind == "transient"


def test_chunk_without_choices_gets_an_empty_list():
    """kimi-k3 via a gateway streamed {"model": "kimi-k3", "usage": {}} (no choices): AI SDK 'expected array, received undefined'"""
    out = clean_sse_line(b'data: {"model":"kimi-k3","usage":{}}\n')
    assert json.loads(out[5:]) == {"model": "kimi-k3", "usage": {}, "choices": []}
    err = b'data: {"error":{"message":"x"}}\n'
    assert clean_sse_line(err) == err                                              # an error chunk stays as it is
    ok = b'data: {"choices":[{"delta":{"content":"hi"}}]}\n'
    assert clean_sse_line(ok) == ok


def test_reasoning_delta_object_becomes_a_string():
    """gpt-6-astra via a gateway: delta.reasoning is an encrypted-reasoning item (object), not the plain string the AI SDK
    expects -- 2026-11-07, "Type validation failed ... delta.reasoning: expected string, received object" """
    chunk = {"choices": [{"delta": {"reasoning": {"content": [], "encrypted_content": "gAAA...", "id": "rs_1",
                                                    "summary": [], "type": "reasoning"}, "role": "assistant"}, "index": 0}]}
    out = clean_sse_line(b"data: " + json.dumps(chunk).encode() + b"\n")
    d = json.loads(out[5:])
    assert d["choices"][0]["delta"]["reasoning"] == ""
    with_text = {"choices": [{"delta": {"reasoning": {"content": [{"type": "text", "text": "thinking"}], "type": "reasoning"}}}]}
    out = clean_sse_line(b"data: " + json.dumps(with_text).encode() + b"\n")
    assert json.loads(out[5:])["choices"][0]["delta"]["reasoning"] == "thinking"
    plain = b'data: {"choices":[{"delta":{"reasoning":"already a string"}}]}\n'
    assert clean_sse_line(plain) == plain                                         # already a string: untouched
    assert infra.classify('Type validation failed: Value: {"choices":[{"delta":{"reasoning":{"content":[]}}}]}.'
                           )[0]["code"] == "api_stream_invalid"


def test_proxy_retries_backend_dependent_400(tmp_path, monkeypatch):
    from gab.host import keyproxy as K
    monkeypatch.setattr(K, "RETRY_400_WAIT", 0.01)
    calls = []
    class H(BaseHTTPRequestHandler):
        def log_message(self, *a): pass
        def do_POST(self):
            self.rfile.read(int(self.headers.get("Content-Length") or 0)); calls.append(1)
            if (self.path.endswith("/flaky") and len(calls) < 2) or (self.path.endswith("/flaky4") and len(calls) < 5):
                b = b'{"error":{"code":"400","message":"Please ensure that the number of function response parts is equal to the number of function call parts of the function call turn."}}'
                self.send_response(400)
            elif self.path.endswith("/bad"):
                b = b'{"error":{"code":"400","message":"invalid model"}}'; self.send_response(400)
            else:
                b = b'{"ok":true}'; self.send_response(200)
            self.send_header("Content-Length", str(len(b))); self.end_headers(); self.wfile.write(b)
    up = HTTPServer(("127.0.0.1", 0), H); threading.Thread(target=up.serve_forever, daemon=True).start()
    p = K.KeyProxy(tmp_path / "s.sock", f"http://127.0.0.1:{up.server_port}/v1", key="K", log=tmp_path / "log").start()
    p.allow = __import__("re").compile(r"^/v1/.*$")
    try:
        st, b = request_via_socket(str(tmp_path / "s.sock"), "POST", "/v1/chat/completions/flaky", body=b"{}", headers={"Content-Length": "2"})
        assert st == 200 and b == b'{"ok":true}' and len(calls) == 2                      # retried once, then fine
        calls.clear()
        st, b = request_via_socket(str(tmp_path / "s.sock"), "POST", "/v1/chat/completions/flaky4", body=b"{}", headers={"Content-Length": "2"})
        assert st == 200 and len(calls) == 5                                               # 4 rejections are still retried
        calls.clear()
        st, b = request_via_socket(str(tmp_path / "s.sock"), "POST", "/v1/chat/completions/bad", body=b"{}", headers={"Content-Length": "2"})
        assert st == 400 and b"invalid model" in b and len(calls) == 1                     # other 400s pass through at once
    finally: p.stop(); up.shutdown()
    assert "400 retried (1)" in (tmp_path / "log").read_text()


def test_drop_old_images_keeps_the_latest():
    from gab.host.keyproxy import drop_old_images, OMITTED
    img = lambda n: {"type": "image_url", "image_url": {"url": f"data:image/png;base64,{n}"}}
    body = json.dumps({"messages": [{"role": "user", "content": [img(i), {"type": "text", "text": "t"}]} for i in range(5)]}).encode()
    out, k = drop_old_images(body, keep=2)
    o = json.loads(out); flat = [p for m in o["messages"] for p in m["content"]]
    assert k == 3 and [p.get("text") for p in flat if p["type"] == "text"].count(OMITTED) == 3
    assert [p["image_url"]["url"][-1] for p in flat if p["type"] == "image_url"] == ["3", "4"]
    assert drop_old_images(json.dumps({"messages": []}).encode(), keep=2)[1] == 0


def test_proxy_drops_old_images_on_media_budget_400(tmp_path, monkeypatch):
    from gab.host import keyproxy as K
    monkeypatch.setattr(K, "RETRY_400_WAIT", 0.01); monkeypatch.setattr(K, "MEDIA_KEEP", 1)
    seen = []
    class H(BaseHTTPRequestHandler):
        def log_message(self, *a): pass
        def do_POST(self):
            o = json.loads(self.rfile.read(int(self.headers.get("Content-Length") or 0)))
            n = sum(1 for m in o["messages"] for p in m["content"] if p["type"] == "image_url"); seen.append(n)
            if n > 1: b = b'{"error":{"code":"validation_error","message":"media inputs exceed the encoded media budget after processing"}}'; self.send_response(400)
            else: b = b'{"ok":true}'; self.send_response(200)
            self.send_header("Content-Length", str(len(b))); self.end_headers(); self.wfile.write(b)
    up = HTTPServer(("127.0.0.1", 0), H); threading.Thread(target=up.serve_forever, daemon=True).start()
    p = K.KeyProxy(tmp_path / "s.sock", f"http://127.0.0.1:{up.server_port}/v1", key="K", log=tmp_path / "log").start()
    img = {"type": "image_url", "image_url": {"url": "data:image/png;base64,AAAA"}}
    body = json.dumps({"messages": [{"role": "user", "content": [img, img, img]}]}).encode()
    try:
        st, b = request_via_socket(str(tmp_path / "s.sock"), "POST", "/v1/chat/completions", body=body, headers={"Content-Length": str(len(body))})
    finally: p.stop(); up.shutdown()
    assert st == 200 and seen == [3, 1] and "media budget: 2 older images omitted" in (tmp_path / "log").read_text()


def test_skip_signatures_only_where_missing():
    from gab.host.keyproxy import add_skip_signatures, SKIP_SIG
    body = json.dumps({"messages": [{"role": "assistant", "tool_calls": [
        {"id": "a", "function": {"name": "bash"}},
        {"id": "b", "function": {"name": "bash"}, "extra_content": {"google": {"thought_signature": "real"}}}]}]}).encode()
    out, n = add_skip_signatures(body)
    tc = json.loads(out)["messages"][0]["tool_calls"]
    assert n == 1 and tc[0]["extra_content"]["google"]["thought_signature"] == SKIP_SIG
    assert tc[1]["extra_content"]["google"]["thought_signature"] == "real"


def test_proxy_adds_placeholder_signature_after_plain_retry(tmp_path, monkeypatch):
    from gab.host import keyproxy as K
    monkeypatch.setattr(K, "RETRY_400_WAIT", 0.01)
    seen = []
    class H(BaseHTTPRequestHandler):
        def log_message(self, *a): pass
        def do_POST(self):
            o = json.loads(self.rfile.read(int(self.headers.get("Content-Length") or 0)))
            sig = (o["messages"][0]["tool_calls"][0].get("extra_content") or {}).get("google", {}).get("thought_signature")
            seen.append(sig)
            if not sig: b = b'{"error":{"code":"400","message":"Function call is missing a thought_signature in functionCall parts."}}'; self.send_response(400)
            else: b = b'{"ok":true}'; self.send_response(200)
            self.send_header("Content-Length", str(len(b))); self.end_headers(); self.wfile.write(b)
    up = HTTPServer(("127.0.0.1", 0), H); threading.Thread(target=up.serve_forever, daemon=True).start()
    p = K.KeyProxy(tmp_path / "s.sock", f"http://127.0.0.1:{up.server_port}/v1", key="K", log=tmp_path / "log").start()
    body = json.dumps({"messages": [{"role": "assistant", "tool_calls": [{"id": "a", "function": {"name": "bash"}}]}]}).encode()
    try:
        st, b = request_via_socket(str(tmp_path / "s.sock"), "POST", "/v1/chat/completions", body=body, headers={"Content-Length": str(len(body))})
    finally: p.stop(); up.shutdown()
    assert st == 200 and seen[:2] == [None, None] and seen[2] == K.SKIP_SIG       # first request and one plain retry unchanged
