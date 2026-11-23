#!/usr/bin/env python3
"""A fake Anthropic Messages API for tests (no real model, no credential): POST /v1/messages answers a canned reply (SSE stream
when the request asks for one, JSON otherwise; with tool_command set, the first answer of a conversation is a Bash tool call
running that command, the answer after its tool_result the text, which then quotes the tool output), /v1/messages/count_tokens a token count, anything else 404. Every request is
appended to LOG as one JSON line {method, path, authorization, x_api_key, anthropic_beta, anthropic_version, stream, model}.

    python3 fake_anthropic.py HOST PORT LOG [REPLY_TEXT]      (or import serve() in a test)
"""
import json, sys, threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

REPLY = "GAB_FAKE_REPLY ok"
USAGE = {"input_tokens": 1234, "output_tokens": 56, "cache_creation_input_tokens": 100, "cache_read_input_tokens": 200}


def _sse(model, text, tool=None):
    if tool: block, delta, stop = ({"type": "tool_use", "id": "toolu_fake01", "name": "Bash", "input": {}},
                                   {"type": "input_json_delta", "partial_json": json.dumps({"command": tool})}, "tool_use")
    else: block, delta, stop = {"type": "text", "text": ""}, {"type": "text_delta", "text": text}, "end_turn"
    msg = {"id": "msg_fake01", "type": "message", "role": "assistant", "model": model, "content": [], "stop_reason": None,
           "stop_sequence": None, "usage": {**USAGE, "output_tokens": 1}}
    ev = [("message_start", {"type": "message_start", "message": msg}),
          ("content_block_start", {"type": "content_block_start", "index": 0, "content_block": block}),
          ("content_block_delta", {"type": "content_block_delta", "index": 0, "delta": delta}),
          ("content_block_stop", {"type": "content_block_stop", "index": 0}),
          ("message_delta", {"type": "message_delta", "delta": {"stop_reason": stop, "stop_sequence": None},
                             "usage": {"output_tokens": USAGE["output_tokens"]}}),
          ("message_stop", {"type": "message_stop"})]
    return "".join(f"event: {e}\ndata: {json.dumps(d)}\n\n" for e, d in ev).encode()


def _tool_result(j):
    """the text of the last tool_result in the request's messages (None: no tool call answered yet)"""
    for m in reversed(j.get("messages") or []):
        for c in (m.get("content") if isinstance(m.get("content"), list) else []):
            if isinstance(c, dict) and c.get("type") == "tool_result":
                x = c.get("content")
                return x if isinstance(x, str) else " ".join(i.get("text", "") for i in x or [] if isinstance(i, dict))
    return None


def handler(log, text=REPLY, tool_command=None):
    lock = threading.Lock()

    class H(BaseHTTPRequestHandler):
        protocol_version = "HTTP/1.1"
        def log_message(self, *a): pass

        def _rec(self, body):
            try: j = json.loads(body or b"{}")
            except ValueError: j = {}
            h = self.headers
            rec = {"method": self.command, "path": self.path, "authorization": h.get("Authorization"), "x_api_key": h.get("x-api-key"),
                   "anthropic_beta": h.get("anthropic-beta"), "anthropic_version": h.get("anthropic-version"),
                   "stream": j.get("stream"), "model": j.get("model")}
            with lock, open(log, "a") as f: f.write(json.dumps(rec) + "\n")
            return j

        def _send(self, code, body, ctype="application/json"):
            self.send_response(code); self.send_header("Content-Type", ctype); self.send_header("Content-Length", str(len(body)))
            self.send_header("request-id", "req_fake01"); self.end_headers(); self.wfile.write(body)

        def do_POST(self):
            n = int(self.headers.get("Content-Length") or 0); j = self._rec(self.rfile.read(n) if n else b"")
            p = self.path.split("?")[0]; model = j.get("model") or "claude-fake"
            if p == "/v1/messages/count_tokens": return self._send(200, json.dumps({"input_tokens": 42}).encode())
            if p != "/v1/messages": return self._send(404, b'{"type":"error","error":{"type":"not_found_error","message":"fake"}}')
            if j.get("stream"):
                if tool_command:
                    res = _tool_result(j)
                    if res is None: return self._send(200, _sse(model, text, tool=tool_command), "text/event-stream")
                    return self._send(200, _sse(model, f"{text} [tool said: {res.strip()[:200]}]"), "text/event-stream")
                return self._send(200, _sse(model, text), "text/event-stream")
            return self._send(200, json.dumps({"id": "msg_fake02", "type": "message", "role": "assistant", "model": model,
                                               "content": [{"type": "text", "text": text}], "stop_reason": "end_turn",
                                               "stop_sequence": None, "usage": USAGE}).encode())

        def do_GET(self):
            self._rec(b""); self._send(404, b'{"type":"error","error":{"type":"not_found_error","message":"fake"}}')

    return H


def serve(host, port, log, text=REPLY, tool_command=None):
    """start in a thread -> the server (server_address[1] = the port)"""
    s = ThreadingHTTPServer((host, port), handler(log, text, tool_command))
    threading.Thread(target=s.serve_forever, daemon=True).start()
    return s


if __name__ == "__main__":
    s = ThreadingHTTPServer((sys.argv[1], int(sys.argv[2])), handler(sys.argv[3], sys.argv[4] if len(sys.argv) > 4 else REPLY))
    s.serve_forever()
