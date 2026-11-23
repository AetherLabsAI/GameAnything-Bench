"""key proxy: the provider's own charge per request (gateway / OpenRouter <base>/generation?id=)"""
import json, threading
from http.server import BaseHTTPRequestHandler, HTTPServer

from gab.host.keyproxy import KeyProxy, request_via_socket


def fake_upstream(costs, seen_auth):
    class H(BaseHTTPRequestHandler):
        def log_message(self, *a): pass
        def do_POST(self):
            n = int(self.headers.get("Content-Length") or 0); self.rfile.read(n)
            rid = f"br-{len(costs)}"; costs[rid] = 0.001 * (len(costs) + 1)
            b = json.dumps({"id": f"chatcmpl-{len(costs)}", "usage": {"prompt_tokens": 7}}).encode()
            self.send_response(200); self.send_header("x-request-id", rid); self.send_header("Content-Length", str(len(b))); self.end_headers(); self.wfile.write(b)
        def do_GET(self):
            seen_auth.append(self.headers.get("Authorization"))
            i = self.path.split("id=")[-1]
            if i in costs: b = json.dumps({"data": {"id": i, "total_cost": costs[i], "tokens_prompt": 7, "tokens_completion": 3, "model": "m"}}).encode()
            else: b = json.dumps({"data": None}).encode()
            self.send_response(200); self.send_header("Content-Length", str(len(b))); self.end_headers(); self.wfile.write(b)
    s = HTTPServer(("127.0.0.1", 0), H); threading.Thread(target=s.serve_forever, daemon=True).start(); return s


def test_actual_costs(tmp_path):
    costs, auth = {}, []
    up = fake_upstream(costs, auth)
    p = KeyProxy(tmp_path / "s.sock", f"http://127.0.0.1:{up.server_port}/v1", key="SECRET", log=tmp_path / "log").start()
    try:
        for _ in range(3):
            st, _b = request_via_socket(str(tmp_path / "s.sock"), "POST", "/v1/chat/completions", body=b'{"model":"m"}',
                                        headers={"Content-Type": "application/json", "Content-Length": "13"})
            assert st == 200
        r = p.actual_costs(out=tmp_path / "costs.jsonl")
    finally: p.stop(); up.shutdown()
    assert r["n"] == 3 and r["found"] == 3 and abs(r["usd"] - 0.006) < 1e-9
    assert auth and all(a == "Bearer SECRET" for a in auth)                  # looked up with the proxy's own credential
    lines = [json.loads(l) for l in (tmp_path / "costs.jsonl").read_text().splitlines()]
    assert [x["id"] for x in lines] == ["br-0", "br-1", "br-2"] and "SECRET" not in (tmp_path / "costs.jsonl").read_text()
    assert "id=br-0" in (tmp_path / "log").read_text() and "SECRET" not in (tmp_path / "log").read_text()


def test_no_lookup_for_subscription(tmp_path):
    p = KeyProxy(tmp_path / "s.sock", "https://api.anthropic.com", key="T", api="anthropic", auth="oauth")
    p.ids.append(("req_1", "", "/v1/messages", 200))
    assert p.actual_costs() is None                     # Claude subscription: nothing is charged per request
