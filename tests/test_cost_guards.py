"""cost guards (2026-11-09): codex auto-compaction, the key proxy's per-request token cap and its per-round USD cap"""
import base64, json, threading
from http.server import BaseHTTPRequestHandler, HTTPServer

from gab.run import infra
from gab.agent.base import AgentRun, Budget
from gab.host import keyproxy as K
from gab.host.keyproxy import KeyProxy, request_via_socket


def fake_upstream(seen, usage=None, costs=None, sse=False):
    """a model API: every POST answers with `usage` (and an x-request-id); GET <base>/generation?id= answers costs[id]"""
    class H(BaseHTTPRequestHandler):
        def log_message(self, *a): pass
        def do_POST(self):
            n = int(self.headers.get("Content-Length") or 0); seen.append((self.path, self.rfile.read(n)))
            rid = f"br-{len(seen)}"
            if sse:
                b = (b'event: response.created\ndata: {"type":"response.created","response":{"usage":null}}\n\n'
                     + b'event: response.completed\ndata: ' + json.dumps({"type": "response.completed", "response": {"id": "resp_1", "usage": usage}}).encode() + b"\n\n")
                self.send_response(200); self.send_header("Content-Type", "text/event-stream"); self.send_header("x-request-id", rid)
                self.end_headers(); self.wfile.write(b); return
            b = json.dumps({"id": "chatcmpl-1", "usage": usage or {}}).encode()
            self.send_response(200); self.send_header("x-request-id", rid); self.send_header("Content-Length", str(len(b)))
            self.end_headers(); self.wfile.write(b)
        def do_GET(self):
            i = self.path.split("id=")[-1]
            c = (costs or {}).get(i)
            b = json.dumps({"data": {"id": i, "total_cost": c, "model": "m"} if c is not None else None}).encode()
            self.send_response(200); self.send_header("Content-Length", str(len(b))); self.end_headers(); self.wfile.write(b)
    s = HTTPServer(("127.0.0.1", 0), H); threading.Thread(target=s.serve_forever, daemon=True).start(); return s


def post(sock, path, obj):
    b = json.dumps(obj).encode()
    return request_via_socket(str(sock), "POST", path, body=b, headers={"Content-Type": "application/json", "Content-Length": str(len(b))})


# ---- token estimate ---------------------------------------------------------------------------------------------------
def test_estimate_tokens_text_vs_images():
    png = "data:image/png;base64," + base64.b64encode(b"x" * 300_000).decode()
    text = {"model": "m", "messages": [{"role": "user", "content": "a" * 40_000}]}
    n, chars, imgs = K.estimate_tokens(json.dumps(text).encode())
    assert imgs == 0 and 10_000 <= n <= 10_100
    with_img = {"model": "m", "input": [{"role": "user", "content": [{"type": "input_text", "text": "a" * 40_000},
                                                                     {"type": "input_image", "image_url": png}]}]}
    n2, chars2, imgs2 = K.estimate_tokens(json.dumps(with_img).encode())
    assert imgs2 == 1 and n2 - n < K.IMAGE_TOKENS + 100              # the 400 KB of base64 counts as one image, not as text
    anth = {"messages": [{"role": "user", "content": [{"type": "image", "source": {"type": "base64", "media_type": "image/png",
                                                                                     "data": "A" * 500_000}}]}]}
    n3, _, imgs3 = K.estimate_tokens(json.dumps(anth).encode())
    assert imgs3 == 1 and n3 < K.IMAGE_TOKENS + 50
    assert K.estimate_tokens(b"not json " * 100)[0] == 225             # plain bytes / 4


def test_usage_and_price():
    u = K.usage_of(b'data: {"type":"response.completed","response":{"usage":{"input_tokens":1000000,"input_tokens_details":'
                   b'{"cached_tokens":500000},"output_tokens":1000}}}\n\n')
    assert u["in"] == 1_000_000 and u["cached"] == 500_000 and u["out"] == 1000
    usd = K.estimate_usd("gpt-5.6-sol", "openai", u, 0)                # 0.5M x 4 + 0.5M x 0.4 + 1k x 20 per M
    assert abs(usd - (2.0 + 0.2 + 0.02)) < 1e-9
    a = K.usage_of(b'data: {"type":"message_start","message":{"usage":{"input_tokens":100,"cache_read_input_tokens":900}}}\n',
                   b'data: {"type":"message_delta","usage":{"output_tokens":50}}\n')
    assert a["in"] == 100 and a["cached"] == 900 and a["out"] == 50
    assert K.estimate_usd("unknown-model", "openai", None, 1_000_000) == K.DEFAULT_RATES["input"]   # no usage: the request estimate


# ---- per-request cap --------------------------------------------------------------------------------------------------
def test_oversize_request_refused_without_forwarding(tmp_path):
    seen = []; up = fake_upstream(seen, usage={"prompt_tokens": 7})
    log = tmp_path / "llm_proxy.log"
    p = KeyProxy(tmp_path / "s.sock", f"http://127.0.0.1:{up.server_port}/v1", key="SECRET", log=log, max_request_tokens=2000).start()
    try:
        st, b = post(tmp_path / "s.sock", "/v1/responses", {"model": "m", "input": "x" * 12000})      # ~3000 tokens
        assert st == 400 and not seen
        e = json.loads(b)["error"]
        assert e["code"] == "request_too_large" and "gab proxy: request too large (~3,0" in e["message"] and "head/tail/grep" in e["message"]
        st, _ = post(tmp_path / "s.sock", "/v1/responses", {"model": "m", "input": "small",
                                                              "img": "data:image/png;base64," + "A" * 100_000})   # one image: small
        assert st == 200 and len(seen) == 1
        assert request_via_socket(str(tmp_path / "s.sock"), "GET", "/v1/models")[0] == 200            # not a model request
    finally: p.stop(); up.shutdown()
    assert p.refused["request_too_large"] == 1
    assert "400 refused request_too_large" in log.read_text() and "SECRET" not in log.read_text()
    # the transcript line of the harness is classified as its own guard code: not model-api (never resumed), not systematic
    m = infra.classify("unexpected status 400 Bad Request: " + b.decode())
    assert [x["code"] for x in m] == ["request_too_large"] and m[0]["kind"] == infra.G
    assert infra.decide([{**m[0], "source": "t"}], "crash")[0] is None


def test_oversize_anthropic_error_format(tmp_path):
    seen = []; up = fake_upstream(seen)
    p = KeyProxy(tmp_path / "s.sock", f"http://127.0.0.1:{up.server_port}", key="K", api="anthropic", auth="x-api-key",
                 max_request_tokens=10).start()
    try: st, b = post(tmp_path / "s.sock", "/v1/messages", {"model": "claude", "messages": [{"role": "user", "content": "y" * 400}]})
    finally: p.stop(); up.shutdown()
    o = json.loads(b)
    assert st == 400 and not seen and o["type"] == "error" and o["error"]["type"] == "invalid_request_error"
    assert o["error"]["message"].startswith("gab proxy: request too large")


# ---- per-round USD cap ------------------------------------------------------------------------------------------------
def test_spend_cap_trips_after_priced_requests(tmp_path):
    """gpt-5.6-sol at $4 / M input: 1M-token answers cost ~$4 each; with a $10 cap the 4th request is refused"""
    seen = []; up = fake_upstream(seen, usage={"input_tokens": 1_000_000, "output_tokens": 0}, sse=True)
    log = tmp_path / "llm_proxy.log"
    p = KeyProxy(tmp_path / "s.sock", f"http://127.0.0.1:{up.server_port}/v1", key="SECRET", log=log, max_usd=10,
                 model="gpt-5.6-sol").start()
    try:
        sts = [post(tmp_path / "s.sock", "/v1/responses", {"model": "gpt-5.6-sol", "input": "hi", "stream": True}) for _ in range(5)]
    finally: p.stop(); up.shutdown()
    assert [s for s, _ in sts] == [200, 200, 200, 400, 400] and len(seen) == 3
    e = json.loads(sts[3][1])["error"]
    assert e["code"] == "budget_exceeded" and e["message"].startswith("gab proxy: spend cap $10.00 reached for this round")
    assert abs(p.spent() - 12.0) < 1e-6 and p.budget_hit["max_usd"] == 10 and p.refused["budget_exceeded"] == 2
    assert "400 refused budget_exceeded" in log.read_text()
    # what InContainer records -> the infra block of the trial / round (gab/run/trial.py guard_incidents)
    from gab.run.trial import guard_incidents
    g = guard_incidents({"budget_exceeded": p.budget_hit, "refused": dict(p.refused)})
    blk = infra.assess(g, "crash")
    assert blk["status"] == "budget_exceeded" and blk["primary"] == "budget_exceeded"
    assert not infra.resumable(blk) and not infra.transient_only(blk)
    assert infra.classify(e["message"])[0]["code"] == "budget_exceeded"
    from gab.agent.until_done import transient_api_failure
    assert transient_api_failure(AgentRun(exit_reason="crash", extra={"error": "unexpected status 400: " + e["message"]})) is None


def test_spend_cap_counts_earlier_spend_and_provider_charge(tmp_path):
    """spent_usd of earlier runs counts; with live_pricing the provider's own charge replaces the estimate"""
    seen = []; costs = {f"br-{i}": 3.0 for i in range(1, 9)}
    up = fake_upstream(seen, usage={"prompt_tokens": 10}, costs=costs)    # the estimate alone would be ~$0
    p = KeyProxy(tmp_path / "s.sock", f"http://127.0.0.1:{up.server_port}/v1", key="K", max_usd=5, spent_usd=1.0, live_pricing=True).start()
    try:
        assert post(tmp_path / "s.sock", "/v1/chat/completions", {"model": "m", "messages": []})[0] == 200
        import time
        for _ in range(50):
            if p.spent() >= 4.0: break
            time.sleep(0.1)
        assert abs(p.spent() - 4.0) < 1e-6                              # 1.0 before + the provider's 3.0
        assert post(tmp_path / "s.sock", "/v1/chat/completions", {"model": "m", "messages": []})[0] == 200   # 4 < 5: forwarded
        for _ in range(50):
            if p.spent() >= 7.0: break
            time.sleep(0.1)
        assert post(tmp_path / "s.sock", "/v1/chat/completions", {"model": "m", "messages": []})[0] == 400
        r = p.actual_costs()
    finally: p.stop(); up.shutdown()
    assert len(seen) == 2 and r["found"] == 2 and abs(r["usd"] - 6.0) < 1e-9


def test_until_done_stops_at_the_cap(tmp_path):
    """a segment stopped by the cap is not continued (no CONTINUE, no infra resume)"""
    from gab.agent.until_done import UntilDone

    class F:
        can_resume = True
        def __init__(self): self.model, self.opts, self.n = "m", {}, 0
        def session_of(self, p): return "S"
        def version(self): return "v"
        def run(self, workdir, instruction, budget, tools):
            self.n += 1
            return AgentRun(exit_reason="crash", extra={"budget_exceeded": {"max_usd": 10, "spent_usd": 10.5},
                                                        "error": "gab proxy: spend cap $10.00 reached for this round"})
    w = tmp_path / "work"; (w / "deliverable").mkdir(parents=True); f = F()
    r = UntilDone(f).run(w, "BRIEF", Budget(minutes=60), {})
    assert f.n == 1 and r.extra["budget_exceeded"]["max_usd"] == 10 and r.exit_reason == "crash"


def test_agent_infra_records_budget_exceeded(tmp_path):
    from gab.run.trial import agent_infra
    d = tmp_path / "run" / "T-x" / "fine"; (d / "agent").mkdir(parents=True); (d / "work").mkdir()
    run = AgentRun(exit_reason="crash", tokens_in=10, extra={"budget_exceeded": {"max_usd": 20.0, "spent_usd": 20.3}})
    blk = agent_infra(d, run)
    assert blk["status"] == "budget_exceeded" and blk["primary"] == "budget_exceeded"
    assert blk["incidents"][0]["component"] == "cost-guard"


# ---- codex auto-compaction --------------------------------------------------------------------------------------------
def test_codex_command_has_auto_compaction(tmp_path, monkeypatch):
    from gab.agent import base
    w = tmp_path / "work"; w.mkdir(); home = tmp_path / "agent_home"
    a = base.get("codex", model="gpt-5.6-sol", base_url="http://x/v1", api_key_env="K")
    cmd, _ = a.command(w, "BRIEF", home)                                # host / container: the same command()
    assert "model_auto_compact_token_limit=300000" in cmd and cmd[cmd.index("model_auto_compact_token_limit=300000") - 1] == "-c"
    assert cmd.index("model_auto_compact_token_limit=300000") < cmd.index("--")
    a.opts["resume"] = "abc"
    cmd, _ = a.command(w, "GO ON", home)                                # exec resume
    assert cmd[1:3] == ["exec", "resume"] and "model_auto_compact_token_limit=300000" in cmd and cmd[-3:] == ["--", "abc", "GO ON"]
    monkeypatch.setenv("GAB_CODEX_AUTO_COMPACT_TOKENS", "120000")
    assert "model_auto_compact_token_limit=120000" in base.get("codex", model="m").command(w, "B", home)[0]
    monkeypatch.setenv("GAB_CODEX_AUTO_COMPACT_TOKENS", "0")
    assert not any("auto_compact" in c for c in base.get("codex", model="m").command(w, "B", home)[0])


def test_refine_skips_budget_exceeded_trials():
    from gab.cli import _over_budget
    assert _over_budget({"infra": {"status": "budget_exceeded"}})
    assert _over_budget({"infra": None, "refine_rounds": [{"infra": {"status": "budget_exceeded"}}]})
    assert not _over_budget({"infra": {"status": None}, "refine_rounds": [{"infra": None}]})
    assert not _over_budget({})
