"""key proxy pricing from the answer's reported usage (2026-11-12): Claude Code asks for compressed answers (gzip, deflate,
br, zstd); api.anthropic.com answered compressed, the proxy found no usage and priced every request at the full input rate of
its own input estimate (cache reads included) -- a trial Claude Code put at $2.66 was stopped at "~$20.35" by the $20 cap.
The proxy now asks for identity and decodes gzip / deflate from an upstream that compresses anyway. No network, no model."""
import gzip, json, threading, zlib
from http.server import BaseHTTPRequestHandler, HTTPServer

import pytest

from gab.host import keyproxy as K
from gab.host.keyproxy import KeyProxy, request_via_socket

SONNET = "claude-sonnet-5-5"                     # prices.yaml: input 2.0, cached_input 0.10, output 10.0 per M


def anthropic_sse(inp=2, read=200_000, write=1_000, out=100):
    """a recorded-style Anthropic stream: usage in message_start (input / cache read / cache write) and message_delta (output)"""
    ev = [("message_start", {"type": "message_start", "message": {
              "model": SONNET, "id": "msg_01", "type": "message", "role": "assistant", "content": [], "stop_reason": None,
              "usage": {"input_tokens": inp, "cache_creation_input_tokens": write, "cache_read_input_tokens": read,
                        "cache_creation": {"ephemeral_5m_input_tokens": 0, "ephemeral_1h_input_tokens": write},
                        "output_tokens": 1, "service_tier": "standard"}}}),
          ("content_block_start", {"type": "content_block_start", "index": 0, "content_block": {"type": "thinking", "thinking": "", "signature": ""}}),
          ("content_block_delta", {"type": "content_block_delta", "index": 0, "delta": {"type": "signature_delta", "signature": "Q" * 30_000}}),
          ("content_block_stop", {"type": "content_block_stop", "index": 0}),
          ("content_block_start", {"type": "content_block_start", "index": 1, "content_block": {"type": "tool_use", "id": "toolu_1", "name": "Bash", "input": {}}}),
          ("content_block_delta", {"type": "content_block_delta", "index": 1, "delta": {"type": "input_json_delta", "partial_json": '{"command": "echo \\"usage\\": {}"}'}}),
          ("content_block_stop", {"type": "content_block_stop", "index": 1}),
          ("message_delta", {"type": "message_delta", "delta": {"stop_reason": "tool_use", "stop_sequence": None},
                             "usage": {"output_tokens": out}}),
          ("message_stop", {"type": "message_stop"})]
    return b"".join(f"event: {e}\ndata: {json.dumps(d, separators=(',', ':'))}\n\n".encode() for e, d in ev)


def chat_sse(prompt=100_000, cached=90_000, out=200):
    """an OpenAI chat-completions stream with the usage chunk last (stream_options.include_usage)"""
    ch = [{"id": "c1", "object": "chat.completion.chunk", "choices": [{"index": 0, "delta": {"content": "hi"}}]},
          {"id": "c1", "object": "chat.completion.chunk", "choices": [{"index": 0, "delta": {}, "finish_reason": "stop"}]},
          {"id": "c1", "object": "chat.completion.chunk", "choices": [], "usage": {"prompt_tokens": prompt, "completion_tokens": out,
                                                                                "prompt_tokens_details": {"cached_tokens": cached}}}]
    return b"".join(b"data: " + json.dumps(c).encode() + b"\n\n" for c in ch) + b"data: [DONE]\n\n"


def responses_sse(inp=100_000, cached=90_000, out=200):
    return (b'event: response.created\ndata: {"type":"response.created","response":{"id":"resp_1","usage":null}}\n\n'
            + b'event: response.output_text.delta\ndata: {"type":"response.output_text.delta","delta":"' + b"x" * 2_000_000 + b'"}\n\n'
            + b"event: response.completed\ndata: " + json.dumps({"type": "response.completed", "response": {"id": "resp_1", "usage": {
                "input_tokens": inp, "input_tokens_details": {"cached_tokens": cached}, "output_tokens": out}}}).encode() + b"\n\n")


def compress(body, enc):
    if enc in ("gzip", "x-gzip"): return gzip.compress(body, mtime=0)
    if enc == "deflate": return zlib.compress(body)                     # zlib-wrapped, as RFC 9110 means it
    if enc == "raw-deflate": c = zlib.compressobj(wbits=-15); return c.compress(body) + c.flush()
    if enc == "br": return b"\x8b\x02\x80" + bytes(range(200))            # bytes the proxy cannot decode
    return body


def upstream(body, seen, enc=None, honor=False, ctype="text/event-stream"):
    """a model API answering `body` chunked; enc: the Content-Encoding it uses (honor: only when the client asks for it)"""
    class H(BaseHTTPRequestHandler):
        protocol_version = "HTTP/1.1"
        def log_message(self, *a): pass
        def do_POST(self):
            self.rfile.read(int(self.headers.get("Content-Length") or 0)); ae = self.headers.get("Accept-Encoding") or ""
            seen.append(ae)
            e = enc if enc and (not honor or enc in ae) else None
            b = compress(body, e)
            self.send_response(200); self.send_header("Content-Type", ctype); self.send_header("Transfer-Encoding", "chunked")
            if e: self.send_header("Content-Encoding", "deflate" if e == "raw-deflate" else e)
            self.end_headers()
            for i in range(0, len(b), 777): c = b[i:i + 777]; self.wfile.write(b"%x\r\n%s\r\n" % (len(c), c))
            self.wfile.write(b"0\r\n\r\n")
    s = HTTPServer(("127.0.0.1", 0), H); threading.Thread(target=s.serve_forever, daemon=True).start(); return s


def run(tmp_path, body, path, api, enc=None, honor=False, model=SONNET, req=None):
    seen = []; up = upstream(body, seen, enc, honor)
    base = f"http://127.0.0.1:{up.server_port}" + ("" if api == "anthropic" else "/v1")
    log = tmp_path / "llm_proxy.log"
    p = KeyProxy(tmp_path / "s.sock", base, key="SECRET", log=log, api=api, auth="oauth" if api == "anthropic" else "bearer",
                 max_usd=20, model=model).start()
    req = req or {"model": model, "stream": True, "messages": [{"role": "user", "content": "x" * 800_000}]}   # ~200k tokens
    b = json.dumps(req).encode()
    try: st, got = request_via_socket(str(tmp_path / "s.sock"), "POST", path, body=b, headers={
        "Content-Type": "application/json", "Content-Length": str(len(b)), "Accept-Encoding": "gzip, deflate, br, zstd"})
    finally: p.stop(); up.shutdown()
    return st, got, seen, p, log.read_text()


ANTH_USD = (2 * 2.0 + 1_000 * 2.0 + 200_000 * 0.10 + 100 * 10.0) / 1e6          # $0.023004, not ~200k x $2 = $0.40


def test_usage_of_anthropic_stream_and_price():
    u = K.usage_of(anthropic_sse())
    assert u == {"in": 2, "cached": 200_000, "out": 100, "write": 1_000, "cost": None}   # an escaped "usage" in a tool input is not read
    assert abs(K.estimate_usd(SONNET, "anthropic", u, 0) - ANTH_USD) < 1e-12


def test_cache_write_rate_when_listed(monkeypatch):
    monkeypatch.setitem(K._RATES, "m-cw", {"input": 2.0, "output": 10.0, "cached_input": 0.1, "cache_write": 2.5})
    u = {"in": 0, "cached": 0, "out": 0, "write": 1_000_000, "cost": None}
    assert K.estimate_usd("m-cw", "anthropic", u, 0) == 2.5
    assert K.estimate_usd(SONNET, "anthropic", u, 0) == 2.0               # prices.yaml has no cache_write: the input rate


@pytest.mark.parametrize("enc,honor", [(None, False), ("br", True), ("gzip", False), ("deflate", False), ("raw-deflate", False)])
def test_anthropic_stream_priced_from_usage(tmp_path, enc, honor):
    """Claude Code's request (Accept-Encoding: gzip, deflate, br, zstd): the upstream is asked for identity; one that
    compresses anyway (gzip / deflate) is decoded for the usage, and the client still gets the bytes as sent"""
    body = anthropic_sse()
    st, got, seen, p, log = run(tmp_path, body, "/v1/messages?beta=true", "anthropic", enc, honor)
    assert st == 200 and seen == ["identity"]
    assert got == (body if enc in (None, "br") else compress(body, enc))  # forwarded untouched (the client decodes)
    assert abs(p.spent() - ANTH_USD) < 1e-6 and p.costs[0]["usage"] is True
    assert "usage=none" not in log and "est=$0.0230" in log and "SECRET" not in log


def test_undecodable_encoding_falls_back_and_says_so(tmp_path):
    st, got, seen, p, log = run(tmp_path, anthropic_sse(), "/v1/messages", "anthropic", "br")    # ignores identity, sends br
    assert st == 200 and p.costs[0]["usage"] is False
    assert p.spent() > 0.3 and "usage=none (content-encoding br)" in log  # the request's own estimate, at the input rate


@pytest.mark.parametrize("enc", [None, "gzip"])
def test_chat_completions_stream_priced_from_usage(tmp_path, enc):
    body = chat_sse()
    st, got, seen, p, log = run(tmp_path, body, "/v1/chat/completions", "openai", enc, model="gpt-5.6-sol")
    assert st == 200 and seen == ["identity"]
    assert got == (body if enc is None else compress(body, "gzip"))          # a compressed stream is not "cleaned" line by line
    assert abs(p.spent() - (10_000 * 4.0 + 90_000 * 0.40 + 200 * 20.0) / 1e6) < 1e-9 and p.costs[0]["usage"] is True


@pytest.mark.parametrize("enc", [None, "gzip"])
def test_responses_stream_usage_after_a_long_answer(tmp_path, enc):
    """the Responses API reports usage in response.completed, after > 2 MB of output: read from the (decoded) tail"""
    st, got, seen, p, log = run(tmp_path, responses_sse(), "/v1/responses", "openai", enc, model="gpt-5.6-sol",
                                req={"model": "gpt-5.6-sol", "stream": True, "input": "x" * 800_000})
    assert st == 200 and p.costs[0]["usage"] is True
    assert abs(p.spent() - (10_000 * 4.0 + 90_000 * 0.40 + 200 * 20.0) / 1e6) < 1e-9


def test_inflate_fed_in_pieces():
    body = anthropic_sse()
    for enc in ("gzip", "deflate", "raw-deflate"):
        z = compress(body, enc); f = K._Inflate()
        assert b"".join(f.feed(z[i:i + 100]) for i in range(0, len(z), 100)) == body
    f = K._Inflate(); assert f.feed(b"\x07\x00\x00\x00 not deflate") == b"" and f.feed(b"more") == b""
