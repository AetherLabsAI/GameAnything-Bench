"""Host-side credential proxy for agents that run INSIDE a trial container (`gab run --agent-in-container`).

The model credential never enters a container. The proxy runs in the gab process on the host, listens on a unix socket in the
trial's bridge directory (bind-mounted into the container at /run/gab), adds the credential and forwards the request to the
real provider. Inside the container a tiny forwarder (gab/agent/container_files/llm_forward.py) exposes the socket as
http://127.0.0.1:<port>, so the container keeps `--network none` (loopback only, no route out).

Three API families (`api=`), each with its own path allowlist; everything else gets 403:
  openai        <base>/responses, /chat/completions, /completions, /models, /embeddings (and sub-paths) (codex, opencode, ...)
  anthropic     <base>/v1/messages, <base>/v1/messages/count_tokens                                   (Claude Code)
  openai_images <base>/images (and sub-paths)                                    (gen-image: OpenRouter image generation)
Gemini models: tool schemas are cleaned of the JSON-Schema keywords Gemini rejects (clean_tool_schemas, below).
Streamed chat completions (openai, text/event-stream): empty tool-call deltas are dropped (clean_sse_line, below).
Chat requests (openai): the tool history is made consistent (repair_tool_history, below); a 4xx answer keeps the request body
and the answer's head in <log dir>/llm_4xx/ (no credentials: the proxy adds those as headers) so a rejection can be diagnosed.
Four ways to add the credential (`auth=`); whatever credential the client sent (Authorization, x-api-key: the placeholder the
harness holds) is dropped first:
  bearer     Authorization: Bearer <secret>          (OpenAI-style keys; Anthropic-compatible gateways that take a bearer key)
  x-api-key  x-api-key: <secret>                     (an Anthropic API key, or a gateway that takes it that way)
  oauth      Authorization: Bearer <secret>          (a Claude subscription token from `claude setup-token`); the client's own
             anthropic-beta / anthropic-version headers pass through (Claude Code sets `oauth-2025-04-20` itself in OAuth mode;
             the proxy adds that beta only if it is missing)
  none       (nothing added)                          a local, unauthenticated upstream (e.g. gen-image's own GPU server):
             no key, no key_env; still only the allowlisted paths are forwarded
The secret is read once from the host environment variable NAMED by key_env (or given in memory as key=). Every request is
logged (method, path without query, status, bytes, seconds) -- never headers, query strings or bodies.

    p = KeyProxy(sock="/x/.gab-bridge/llm.sock", upstream="https://openrouter.ai/api/v1", key_env="OPENROUTER_API_KEY",
                 log="/x/agent/llm_proxy.log"); p.start(); ... ; p.stop()
    p.container_base_url(port)  ->  "http://127.0.0.1:<port>/api/v1"   (what the harness inside the container is given)
    KeyProxy(sock, "https://api.anthropic.com", key_env="CLAUDE_CODE_OAUTH_TOKEN", api="anthropic", auth="oauth")
        .container_base_url(port)  ->  "http://127.0.0.1:<port>"         (ANTHROPIC_BASE_URL inside; the SDK appends /v1/...)

In-process and over a unix socket, so a user needs no extra container or network: the key stays in their own shell
environment on their host.
"""
import http.client, json, os, re, socket, socketserver, threading, time, zlib
from http.server import BaseHTTPRequestHandler
from urllib.parse import urlsplit

ALLOW = {"openai": r"(responses|chat/completions|completions|models|embeddings)(/[^?]*)?",
         "anthropic": r"v1/messages(/count_tokens)?",
         "openai_images": r"images(/[^?]*)?"}      # OpenRouter image generation (gen-image, container_files/gen_image.py): its
                                                     # own narrow allowlist, so the text model's own route never gains it
AUTHS = ("bearer", "x-api-key", "oauth", "none")
OAUTH_BETA = "oauth-2025-04-20"
HOP = {"connection", "keep-alive", "proxy-authenticate", "proxy-authorization", "te", "trailers", "transfer-encoding", "upgrade",
       "host", "authorization", "content-length", "x-api-key", "api-key", "openai-organization", "openai-project"}
EXTRA_HEADERS = {"openrouter.ai": {"HTTP-Referer": "https://github.com/AetherLabsAI/GameAnything-Bench", "X-Title": "GameAnything-Bench"}}


# Gemini behind an OpenAI-compatible gateway rejects JSON-Schema keywords outside its OpenAPI subset in tool
# parameters ("Invalid JSON payload received. Unknown name \"$schema\"", OpenCode: the run died after 31 min;
# the gateway sent earlier requests to a backend that tolerated them). For gemini models the proxy drops those keywords from the
# tool schemas (only schema keywords, never property names); exclusiveMinimum / exclusiveMaximum become minimum / maximum.
GEMINI_DROP = {"$schema", "$id", "$comment", "$anchor", "propertyNames", "patternProperties", "additionalItems", "unevaluatedProperties",
               "unevaluatedItems", "dependentRequired", "dependentSchemas", "contentMediaType", "contentEncoding", "if", "then", "else",
               "examples", "deprecated", "readOnly", "writeOnly", "const"}


def _clean_schema(x):
    if isinstance(x, list): return [_clean_schema(v) for v in x]
    if not isinstance(x, dict): return x
    out = {}
    for k, v in x.items():
        if k in GEMINI_DROP: continue
        if k in ("exclusiveMinimum", "exclusiveMaximum"):
            if isinstance(v, (int, float)) and not isinstance(v, bool): out.setdefault("minimum" if k == "exclusiveMinimum" else "maximum", v)
            continue
        if k in ("properties", "$defs", "definitions") and isinstance(v, dict): out[k] = {n: _clean_schema(s) for n, s in v.items()}
        elif k in ("items", "anyOf", "oneOf", "allOf", "not", "additionalProperties"): out[k] = _clean_schema(v)
        else: out[k] = v
    if x.get("const") is not None and "enum" not in out: out["enum"] = [x["const"]]
    return out


def clean_tool_schemas(body):
    """request body (bytes) -> bytes: for a gemini model, its tool parameter schemas without the keywords Gemini rejects;
    any other body unchanged"""
    try: o = json.loads(body)
    except (ValueError, TypeError): return body
    if not isinstance(o, dict) or "gemini" not in str(o.get("model", "")).lower() or not isinstance(o.get("tools"), list): return body
    for t in o["tools"]:
        if not isinstance(t, dict): continue
        f = t.get("function") if isinstance(t.get("function"), dict) else t          # chat/completions | responses
        if isinstance(f.get("parameters"), dict): f["parameters"] = _clean_schema(f["parameters"])
    return json.dumps(o).encode()


INTERRUPTED = "(no result: this tool call was interrupted before it returned)"

# 400s that depend on the backend the gateway picks, not on the request (seen with Gemini behind a gateway: ~72 s, then the
# same body replayed -> 200 every time): retried here, before anything reaches the harness.
RETRY_400 = re.compile(rb"number of function response parts is equal to the number of function call parts"
                       rb"|missing a thought_signature in functionCall"
                       rb"|Failed to download multimodal content|Download multimodal file timed out"   # the gateway's image fetch
                       rb"|ERR_PROVIDER_005"
                       rb"|Please use a valid role: user, model"
                       rb'|\\?"msg\\?":\\?"bad request\\?"')                   # a gateway's bare 400 on a well-formed body (qwen3.8-max)
# 2 retries were not enough (gemini-3.8-flash: retry 1 ok 25/48, retry 2 ok 5/23, then the trial stopped as
# api_history_rejected and waited for the next launch): 5 retries, 3+6+9+12+15 s apart
RETRY_400_TRIES, RETRY_400_WAIT = 5, 3.0

# Big requests: a long session that reads many full-size screenshots carries them all as base64 in every request; at ~34 MB
# a gateway dropped the connection while receiving (SSLEOFError, qwen3.8-flash), and every resume resent the
# same body. Only when a body is larger than SHRINK_ABOVE, inline images larger than SHRINK_IMAGE are scaled to SHRINK_SIDE px
# (longest side) and re-encoded as JPEG; smaller requests are forwarded byte for byte, so normal trials are not affected.
SHRINK_ABOVE, SHRINK_IMAGE, SHRINK_SIDE, SHRINK_Q = 16_000_000, 500_000, 1600, 88
_DATA_URL = re.compile(r"^data:image/[A-Za-z0-9.+-]+;base64,")


# The critic (gab/refine/refine.py) shrinks EVERY inline image above CRITIC_SHRINK["image"] base64 chars, whatever the request size
# (KeyProxy(shrink=...)): its 1920 px PNG contact sheets of 3D games are 1-4 MB each, the requests reached 10-14 MB within a
# few reads, and gemini-3.8-flash answered empty turn after turn (Valheim). Only the critic's proxy does
# this: the agents' requests are unchanged.
CRITIC_SHRINK = {"above": 0, "image": 200_000, "side": 1568, "q": 85}


_SHRUNK = {}                    # (sha1 of the data URL, side, q) -> the shrunk data URL: each request resends the whole history


def _shrink_data_url(u, side=None, q=None):
    import hashlib
    side, q = side or SHRINK_SIDE, q or SHRINK_Q
    key = (hashlib.sha1(u.encode()).hexdigest(), side, q)
    if key not in _SHRUNK:
        if len(_SHRUNK) >= 256: _SHRUNK.clear()
        _SHRUNK[key] = _shrink_once(u, side, q)
    r = _SHRUNK[key]
    return u if r is None else r


def _shrink_once(u, side, q):
    """-> the shrunk data URL, or None when it would not be smaller"""
    import base64, io
    from PIL import Image
    head = _DATA_URL.match(u).group(0)
    im = Image.open(io.BytesIO(base64.b64decode(u[len(head):])))
    im.thumbnail((side, side)); buf = io.BytesIO()
    im.convert("RGB").save(buf, "JPEG", quality=q)
    out = "data:image/jpeg;base64," + base64.b64encode(buf.getvalue()).decode()
    return out if len(out) < len(u) else None               # never a bigger image (an already small JPEG)


def shrink_images(body, above=None, image=None, side=None, q=None):
    """request body (bytes) -> (bytes, (images shrunk, bytes before, bytes after)); unchanged when not above the limit.
    above: request size (bytes) from which images are shrunk (SHRINK_ABOVE; 0 = always); image: data URLs longer than this
    (SHRINK_IMAGE chars) are scaled to `side` px (longest side) and re-encoded as JPEG quality `q`"""
    above = SHRINK_ABOVE if above is None else above
    image = SHRINK_IMAGE if image is None else image
    if not body or len(body) <= above: return body, (0, len(body or b""), len(body or b""))
    if b"data:image/" not in body: return body, (0, len(body), len(body))
    try: o = json.loads(body)
    except (ValueError, TypeError): return body, (0, len(body), len(body))
    n = 0
    def walk(x):
        nonlocal n
        if isinstance(x, dict):
            for k, v in list(x.items()):
                if isinstance(v, str) and len(v) > image and _DATA_URL.match(v):
                    try:
                        r = _shrink_data_url(v, side, q)
                        if r is not v: x[k] = r; n += 1
                    except Exception: pass
                else: walk(v)
        elif isinstance(x, list):
            for v in x: walk(v)
    walk(o.get("messages") if isinstance(o, dict) else o)
    if not n: return body, (0, len(body), len(body))
    out = json.dumps(o).encode()
    return out, (n, len(body), len(out))


TINY_MIN = 11          # DashScope (qwen via a gateway): "height:1 or width:256 must be larger than 10" -> 400, the agent dies
TINY_RATIO = 100       # ... and "absolute aspect ratio must be smaller than 200" (e.g. 256x1 enlarged to 4096x16)
TINY_SCAN = 20000      # only data URLs shorter than this are decoded (such images are small files)


def fix_tiny_images(body):
    """request body (bytes) -> (bytes, images fixed): an image whose width or height is under TINY_MIN pixels or whose aspect ratio
    is over TINY_RATIO (an agent viewing a 256x1 texture strip) is padded (transparent, centred) to a
    ratio <= TINY_RATIO and scaled up (nearest neighbour) so both sides are >= 16"""
    if not body or b"data:image/" not in body: return body, 0
    try: o = json.loads(body)
    except (ValueError, TypeError): return body, 0
    n = 0
    def fix(u):
        import base64, io
        from PIL import Image
        head = _DATA_URL.match(u).group(0); im = Image.open(io.BytesIO(base64.b64decode(u[len(head):])))
        w, h = im.size
        if min(w, h) >= TINY_MIN and max(w, h) <= TINY_RATIO * min(w, h): return None
        if max(w, h) > TINY_RATIO * min(w, h):              # pad the short side (centred) so the aspect ratio is <= TINY_RATIO
            nw, nh = max(w, -(-h // TINY_RATIO)), max(h, -(-w // TINY_RATIO))
            im = im.convert("RGBA"); pad = Image.new("RGBA", (nw, nh), (0, 0, 0, 0))
            pad.paste(im, ((nw - w) // 2, (nh - h) // 2)); im, (w, h) = pad, (nw, nh)
        f = max(1, -(-16 // max(1, min(w, h))))              # ceil(16 / short side)
        if f > 1: im = im.resize((w * f, h * f), Image.NEAREST)
        b = io.BytesIO(); im.save(b, "PNG")
        return "data:image/png;base64," + base64.b64encode(b.getvalue()).decode()
    def walk(x):
        nonlocal n
        if isinstance(x, dict):
            for k, v in list(x.items()):
                if isinstance(v, str) and len(v) < TINY_SCAN and _DATA_URL.match(v):
                    try:
                        r = fix(v)
                        if r: x[k] = r; n += 1
                    except Exception: pass
                else: walk(v)
        elif isinstance(x, list):
            for v in x: walk(v)
    walk(o.get("messages") if isinstance(o, dict) else o)
    return (json.dumps(o).encode(), n) if n else (body, 0)


SIG_400 = re.compile(rb"missing a thought_signature")
SKIP_SIG = "skip_thought_signature_validator"      # Gemini's documented placeholder for tool calls without a signature


def add_skip_signatures(body):
    """request body -> (body, tool calls given the placeholder signature). Only used on a retry after a thought_signature 400 that
    plain retries did not get past (a resumed gemini session hit it on every request; OpenCode's history
    carries no signatures, the gateway supplies them, and for a resumed session it no longer could)"""
    try: o = json.loads(body)
    except (ValueError, TypeError): return body, 0
    n = 0
    for m in o.get("messages") or []:
        for c in m.get("tool_calls") or []:
            ec = c.get("extra_content")
            if not isinstance(ec, dict): ec = c["extra_content"] = {}
            g = ec.setdefault("google", {})
            if isinstance(g, dict) and not g.get("thought_signature"): g["thought_signature"] = SKIP_SIG; n += 1
    return (json.dumps(o).encode(), n) if n else (body, 0)


MEDIA_BUDGET_400 = re.compile(rb"exceed the encoded media budget")    # kimi-k3 via a gateway: too many images in one request
MEDIA_KEEP = 16                                                       # images kept (the latest) when the provider says so
OMITTED = "[an older screenshot was here; omitted to fit the provider's image limit]"


def drop_old_images(body, keep=MEDIA_KEEP):
    """request body -> (body, images replaced): all but the latest `keep` images of the history become a short text part
    (kimi-k3 400 "media inputs exceed the encoded media budget" with 41 screenshots, 8 MB; the trial ended)"""
    try: o = json.loads(body)
    except (ValueError, TypeError): return body, 0
    parts = [(c, i) for m in (o.get("messages") or []) if isinstance(m.get("content"), list)
             for c in [m["content"]] for i, p in enumerate(c) if isinstance(p, dict) and p.get("type") in ("image_url", "image", "input_image")]
    old = parts[:-keep] if keep else parts
    for c, i in old: c[i] = {"type": "text", "text": OMITTED}
    return (json.dumps(o).encode(), len(old)) if old else (body, 0)


def repair_tool_history(body):
    """chat/completions request (bytes) -> (bytes, (results added, orphans dropped)). Gemini rejects a history whose function
    call and response counts differ ("Please ensure that the number of function response parts is equal to the number of
    function call parts", OpenCode + gemini-3.8-flash after a session resume). Every assistant message with
    tool_calls is followed by exactly one tool message per call id, in call order: a missing result gets a short placeholder, a
    tool message without its call is dropped. A consistent history is returned byte for byte."""
    try: o = json.loads(body)
    except (ValueError, TypeError): return body, (0, 0)
    msgs = o.get("messages") if isinstance(o, dict) else None
    if not isinstance(msgs, list): return body, (0, 0)
    out, added, dropped, i = [], 0, 0, 0
    while i < len(msgs):
        m = msgs[i]; i += 1
        if not isinstance(m, dict) or m.get("role") != "tool": out.append(m)
        if not isinstance(m, dict): continue
        if m.get("role") == "tool": dropped += 1; continue          # a result whose call is not right before it
        ids = [t.get("id") for t in m.get("tool_calls") or [] if isinstance(t, dict) and t.get("id")] if m.get("role") == "assistant" else []
        if not ids: continue
        res = {}
        while i < len(msgs) and isinstance(msgs[i], dict) and msgs[i].get("role") == "tool":
            t = msgs[i]; i += 1
            if t.get("tool_call_id") in ids and t.get("tool_call_id") not in res: res[t["tool_call_id"]] = t
            else: dropped += 1
        for c in ids:
            if c in res: out.append(res[c])
            else: out.append({"role": "tool", "tool_call_id": c, "content": INTERRUPTED}); added += 1
    if not added and not dropped: return body, (0, 0)
    o["messages"] = out
    return json.dumps(o).encode(), (added, dropped)


BODY_ID = re.compile(r'"id"\s*:\s*"([A-Za-z0-9_.:\-]{6,120})"')   # the response's own id (OpenRouter: the generation id "gen-...")


# ---- cost guards (one codex request of a refine round once carried ~3.6M input tokens, about $19)
# A model request whose input is estimated above max_request_tokens is not forwarded; once the requests of this proxy (a trial's
# agent run / a refine round / a critic review) have cost max_usd, every further model request is refused. Both answer 400 in
# the route's own error format, so the harness ends its turn with the message (gab/run/infra.py: request_too_large /
# budget_exceeded; never a transient API error that is resumed).
MODEL_POST = re.compile(r"/(responses|chat/completions|completions|v1/messages)/?(\?.*)?$")   # not count_tokens / models / images
IMAGE_TOKENS = 1500                 # a flat estimate per inline image (its base64 is not text the model reads)
CHARS_PER_TOKEN = 4
TOO_LARGE = ("gab proxy: request too large (~{n} tokens > limit {limit}). Read large files only partially (head/tail/grep), "
             "avoid dumping huge outputs.")
OVER_BUDGET = "gab proxy: spend cap ${cap:.2f} reached for this round (spent ~${spent:.2f}); no further model requests are forwarded."
TAIL = 1 << 20                      # bytes of each answer's end kept to read its usage
DEFAULT_RATES = {"input": 5.0, "output": 25.0, "cached_input": 0.5}   # USD / M tokens for a model not in prices.yaml (cap only)
_RATES = {}


def estimate_tokens(body):
    """request body (bytes) -> (estimated input tokens, text chars, images): text chars / 4 over every string of the JSON body
    except inline base64 payloads (data: URLs, Anthropic {"type": "base64"} sources), plus IMAGE_TOKENS per image"""
    if not body: return 0, 0, 0
    try: o = json.loads(body)
    except (ValueError, TypeError, UnicodeDecodeError): return len(body) // CHARS_PER_TOKEN, len(body), 0
    chars = images = 0
    stack = [o]
    while stack:
        x = stack.pop()
        if isinstance(x, dict):
            if x.get("type") == "base64" and isinstance(x.get("data"), str):          # anthropic image / document source
                images += 1; continue
            for k, v in x.items(): chars += len(k); stack.append(v)
        elif isinstance(x, list): stack.extend(x)
        elif isinstance(x, str):
            if x.startswith("data:") and ";base64," in x[:100]: images += 1
            else: chars += len(x)
    return chars // CHARS_PER_TOKEN + images * IMAGE_TOKENS, chars, images


def error_json(api, message, code):
    """an error body in the provider's own format for that route (OpenAI-style or Anthropic)"""
    if api == "anthropic": return {"type": "error", "error": {"type": "invalid_request_error", "message": message}}
    return {"error": {"message": message, "type": "invalid_request_error", "param": None, "code": code}}


_USAGE = re.compile(r'"usage"\s*:\s*\{')


def usage_of(*texts):
    """the token usage reported in a response (JSON body or SSE stream; head and tail of it) -> {in, cached, out, write, cost}
    (each the largest value seen; missing ones 0 / None) or None. OpenAI chat (prompt_/completion_tokens, usage.cost of
    OpenRouter), Responses (input_/output_tokens, input_tokens_details.cached_tokens), Anthropic (cache_read_ / cache_creation_)"""
    dec = json.JSONDecoder(); u = {"in": 0, "cached": 0, "out": 0, "write": 0, "cost": None}; seen = False
    for t in texts:
        if isinstance(t, (bytes, bytearray)): t = bytes(t).decode("utf-8", "replace")
        for m in _USAGE.finditer(t or ""):
            try: d, _ = dec.raw_decode(t, m.end() - 1)
            except ValueError: continue
            if not isinstance(d, dict): continue
            num = lambda v: v if isinstance(v, (int, float)) and not isinstance(v, bool) else 0
            det = d.get("input_tokens_details") or d.get("prompt_tokens_details") or {}
            vals = {"in": num(d.get("input_tokens")) or num(d.get("prompt_tokens")), "out": num(d.get("output_tokens")) or num(d.get("completion_tokens")),
                    "cached": num(det.get("cached_tokens") if isinstance(det, dict) else 0) or num(d.get("cache_read_input_tokens")),
                    "write": num(d.get("cache_creation_input_tokens"))}
            if any(vals.values()): seen = True
            for k, v in vals.items(): u[k] = max(u[k], v)
            if num(d.get("cost")): u["cost"] = max(u["cost"] or 0, float(d["cost"])); seen = True
    return u if seen else None


# The proxy reads each answer's usage, so it asks the upstream for an uncompressed answer (Accept-Encoding: identity). Claude
# Code (a bun binary) asks for "gzip, deflate, br, zstd" and api.anthropic.com answered compressed (brotli, by its bytes): the
# proxy found no usage and priced every request at the full input rate of its own input estimate, cache reads included
# (sonnet-5.5: ~$0.37 a request instead of ~$0.03; a trial Claude Code put at $2.66 hit the $20 cap at "~$20.35").
# An upstream that compresses anyway: gzip / deflate are decoded for the usage; any other encoding falls back to the estimate.
DECODABLE = ("gzip", "x-gzip", "deflate")


class _Inflate:
    """the decoded bytes of a gzip / zlib / raw-deflate answer, fed chunk by chunk (only for reading its usage)"""
    def __init__(self): self.d = zlib.decompressobj(47); self.raw = False; self.bad = False; self.n = 0   # 47: gzip / zlib header

    def feed(self, b):
        if self.bad or not b: return b""
        try: out = self.d.decompress(b); self.n += len(out); return out
        except zlib.error:
            if self.raw or self.n: self.bad = True; return b""
            self.raw = True; self.d = zlib.decompressobj(-15)                 # a bare deflate stream (some servers send that)
            return self.feed(b)


def rates(model):
    """USD per million tokens of `model` from gab/agent/prices.yaml (DEFAULT_RATES when it is not listed); an optional
    cache_write rate prices Anthropic cache writes (else they cost the input rate)"""
    if model not in _RATES:
        try:
            import yaml
            from ..agent.base import PRICES
            p = ((yaml.safe_load(PRICES.read_text()) or {}).get("models") or {}).get(model)
        except Exception: p = None
        _RATES[model] = {**DEFAULT_RATES, **{k: v for k, v in (p or {}).items() if k in ("input", "output", "cached_input", "cache_write")}} if p else dict(DEFAULT_RATES)
    return _RATES[model]


def estimate_usd(model, api, usage, est_in):
    """one request's cost from its reported usage (else the request's own input estimate) and the price table"""
    r = rates(model)
    if usage and usage.get("cost"): return float(usage["cost"])
    if not usage: return est_in * r["input"] / 1e6
    unc = usage["in"] if api == "anthropic" else max(usage["in"] - usage["cached"], 0)    # anthropic: in excludes cache reads/writes
    write = usage["write"] if api == "anthropic" else 0
    return (unc * r["input"] + write * r.get("cache_write", r["input"]) + usage["cached"] * r.get("cached_input", r["input"])
            + usage["out"] * r["output"]) / 1e6


# Kimi K3 behind a gateway streams a tool-call "announcement" chunk {"index": 0, "type": "function"} with no id and no
# "function" object; the AI SDK in OpenCode rejects it ("Type validation failed ... tool_calls,0,function: expected object") and
# the harness dies (after 126 s). Such an entry carries nothing (no id, name or arguments): the proxy drops it
# from the streamed chunk (and the emptied tool_calls key), leaving every other byte of the stream as sent.
#
# GPT-6 Astra behind a gateway streams `delta.reasoning` as an object (its own encrypted-reasoning item: an empty `content`
# list, `encrypted_content`, `id`, `summary`, `type: "reasoning"`) instead of the plain string the AI SDK's schema expects
# ("Type validation failed ... delta.reasoning: expected string, received object": opencode died ~10 min in,
# every time). `content` is always empty (the text itself stays encrypted, never sent in plain), so replacing the object with
# whatever text IS in `content` loses nothing the client could have shown anyway.
def _clean_chunk(d, seen=None):
    """seen: (choice index, tool index) pairs already opened in this response; a NEW tool call whose first delta has a
    function but no id gets one (qwen3.8-max via a gateway: AI SDK "Expected 'id' to be a string.")"""
    changed = False
    if isinstance(d, dict) and "choices" not in d and "error" not in d:      # kimi-k3 via a gateway sends {"model": .., "usage": {}}
        d["choices"] = []; changed = True                                    # (no choices): the AI SDK rejects such a chunk
    for ch in d.get("choices") or [] if isinstance(d, dict) else []:
        delta = ch.get("delta") if isinstance(ch, dict) else None
        if not isinstance(delta, dict): continue
        r = delta.get("reasoning")
        if isinstance(r, dict):
            parts = r.get("content") if isinstance(r.get("content"), list) else []
            delta["reasoning"] = "".join(p.get("text", "") for p in parts if isinstance(p, dict))
            changed = True
        tc = delta.get("tool_calls")
        if not isinstance(tc, list): continue
        keep = [t for t in tc if not (isinstance(t, dict) and "function" not in t and not t.get("id"))]
        if seen is not None:
            for t in keep:
                if not isinstance(t, dict): continue
                key = (ch.get("index", 0), t.get("index", 0))
                if key not in seen:
                    seen.add(key)
                    if not isinstance(t.get("id"), str) or not t.get("id"):
                        t["id"] = f"call_gab_{len(seen)}_{int(time.time() * 1000) % 10**8}"; changed = True
        if len(keep) != len(tc):
            changed = True
            if keep: delta["tool_calls"] = keep
            else: del delta["tool_calls"]
    return changed


def clean_sse_line(line, seen=None):
    """one SSE line (bytes, with its newline) -> the same line, or the chunk without empty tool-call deltas (and, with the
    response's `seen` set, an id for a new tool call that came without one)"""
    if not line.startswith(b"data:"): return line
    raw = line[5:].strip()
    if not raw or raw == b"[DONE]" or (b"tool_calls" not in raw and b'"reasoning"' not in raw and b'"choices"' in raw): return line
    try: d = json.loads(raw)
    except Exception: return line
    if not _clean_chunk(d, seen): return line
    end = b"\r\n" if line.endswith(b"\r\n") else b"\n" if line.endswith(b"\n") else b""
    return b"data: " + json.dumps(d, ensure_ascii=False, separators=(",", ":")).encode() + end


class _Server(socketserver.ThreadingMixIn, socketserver.UnixStreamServer):
    daemon_threads = True
    allow_reuse_address = True


class KeyProxy:
    def __init__(self, sock, upstream, key_env=None, key=None, log=None, owner_uid=None, timeout=900, api="openai", auth="bearer",
                 max_request_tokens=None, max_usd=None, spent_usd=0.0, model=None, live_pricing=False, shrink=None):
        """upstream: the provider's API base URL (https://openrouter.ai/api/v1, https://api.openai.com/v1; for api="anthropic"
        the base the Anthropic SDK appends /v1/messages to: https://api.anthropic.com);
        key: the value in memory, or key_env: the NAME of the host environment variable holding it (read once, never logged);
        api: "openai" | "anthropic" (path allowlist); auth: "bearer" | "x-api-key" | "oauth" (how the secret is added);
        owner_uid: chown the socket to this uid (the container's agent uid) when gab runs as root.
        Cost guards (None / 0 = off): max_request_tokens: a model request estimated above it is refused (estimate_tokens);
        max_usd: no model request is forwarded once this proxy's requests (plus spent_usd, what earlier runs of the same round
        cost) reached it. Each request is priced by its reported usage and the price table (model: the request's own "model"
        when it has none, else this one); live_pricing: the provider's own charge replaces that estimate when its generation
        lookup answers (looked up in the background after each request, only with max_usd).
        shrink: shrink_images() options of this proxy ({above, image, side, q}; None = the defaults: only requests over
        SHRINK_ABOVE); the critic's proxy passes CRITIC_SHRINK."""
        if api not in ALLOW: raise ValueError(f"unknown api {api!r} (one of {', '.join(ALLOW)})")
        if auth not in AUTHS: raise ValueError(f"unknown auth {auth!r} (one of {', '.join(AUTHS)})")
        self.api, self.auth = api, auth
        u = urlsplit(upstream.rstrip("/"))
        if u.scheme not in ("https", "http") or not u.hostname: raise ValueError(f"bad upstream URL {upstream!r}")
        self.scheme, self.host, self.port = u.scheme, u.hostname, u.port or (443 if u.scheme == "https" else 80)
        self.base = u.path.rstrip("/")                      # e.g. /api/v1
        self.allow = re.compile("^" + re.escape(self.base) + "/" + ALLOW[api] + r"(\?.*)?$")
        self._key = key if key is not None else (os.environ.get(key_env) if key_env else None)
        if not self._key and auth != "none":
            raise RuntimeError(f"no credential for the model proxy: environment variable {key_env or '(none given)'} is not set")
        self.sock, self.log_path, self.owner_uid, self.timeout = str(sock), log, owner_uid, timeout
        self.extra = EXTRA_HEADERS.get(self.host, {})
        self.server = None; self.n = 0; self._lock = threading.Lock()
        self.ids = []                                       # (request id from the provider, response body id, path, status)
        self.max_request_tokens = int(max_request_tokens or 0) or None
        self.max_usd = float(max_usd or 0) or None
        self.spent0, self.model, self.live_pricing = float(spent_usd or 0), model, live_pricing
        self.costs = []                                     # per forwarded model request: {est, provider} USD
        self.provider_found = {}                            # (rid, bid) -> the generation record (live_pricing)
        self.refused = {"request_too_large": 0, "budget_exceeded": 0}
        self.budget_hit = None                              # {max_usd, spent_usd, at} once the cap refused a request
        self._clock = threading.Lock()
        self.shrink = dict(shrink or {})

    def container_base_url(self, port): return f"http://127.0.0.1:{port}{self.base}"

    def spent(self):
        """USD of this proxy's model requests so far (provider charge where known, else the estimate) + spent_usd"""
        with self._clock: return self.spent0 + sum(c["provider"] if c["provider"] is not None else c["est"] for c in self.costs)

    def _guard(self, path, body):
        """-> (code, message) when this model request must not be forwarded, else None"""
        if self.max_usd and (s := self.spent()) >= self.max_usd:
            with self._clock:
                if not self.budget_hit: self.budget_hit = {"max_usd": self.max_usd, "spent_usd": round(s, 4), "at": time.strftime("%Y-%m-%dT%H:%M:%S")}
            return "budget_exceeded", OVER_BUDGET.format(cap=self.max_usd, spent=s)
        if self.max_request_tokens:
            n, chars, imgs = estimate_tokens(body)
            if n > self.max_request_tokens:
                return "request_too_large", TOO_LARGE.format(n=f"{n:,}", limit=f"{self.max_request_tokens:,}") + \
                    f" [{chars:,} text chars, {imgs} images]"
        return None

    def _account(self, body, head, tail, rid, bid, path, status):
        """price one forwarded model request (estimate now; the provider's charge later with live_pricing)"""
        model = self.model
        try:
            m = json.loads(body).get("model") if body else None
            if isinstance(m, str) and m: model = m
        except Exception: pass
        u = usage_of(head, tail)
        est = estimate_usd(model, self.api, u, estimate_tokens(body)[0] if not u else 0) if 200 <= status < 300 or u else 0.0
        rec = {"path": path, "status": status, "model": model, "est": round(est, 6), "provider": None, "usage": u is not None}
        with self._clock: self.costs.append(rec)
        if self.live_pricing and self.max_usd and (rid or bid) and self._key and self.auth != "oauth" and 200 <= status < 300:
            def look():
                x = self._lookup((rid, bid, path, status), tries=3)
                if x.get("cost") is not None:
                    with self._clock: rec["provider"] = x["cost"]; self.provider_found[(rid, bid)] = x
            threading.Thread(target=look, daemon=True, name="gab-keyproxy-price").start()
        return rec

    def _log(self, line):
        if not self.log_path: return
        with self._lock, open(self.log_path, "a") as f: f.write(time.strftime("%Y-%m-%dT%H:%M:%S ") + line + "\n")

    def _auth_headers(self, hd):
        """the credential headers for one request (hd: the client's headers without its own credential)"""
        if self.auth == "none": return {}                  # a local, unauthenticated upstream: nothing to add
        if self.auth == "x-api-key": return {"x-api-key": self._key}
        out = {"Authorization": f"Bearer {self._key}"}
        if self.auth == "oauth":
            k = next((k for k in hd if k.lower() == "anthropic-beta"), None)
            betas = [b.strip() for b in (hd.pop(k) if k else "").split(",") if b.strip()]
            out["anthropic-beta"] = ",".join(betas if OAUTH_BETA in betas else betas + [OAUTH_BETA])
            if not any(k.lower() == "anthropic-version" for k in hd): out["anthropic-version"] = "2023-06-01"
        return out

    def _connect(self):
        if self.scheme == "https": return http.client.HTTPSConnection(self.host, self.port, timeout=self.timeout)
        return http.client.HTTPConnection(self.host, self.port, timeout=self.timeout)

    def handler(self):
        proxy = self

        class H(BaseHTTPRequestHandler):
            protocol_version = "HTTP/1.1"
            def address_string(self): return "container"
            def log_message(self, *a): pass

            def _body(self):
                if self.headers.get("Transfer-Encoding", "").lower() == "chunked":
                    out = b""
                    while True:
                        n = int(self.rfile.readline().split(b";")[0].strip() or b"0", 16)
                        if n == 0:
                            while self.rfile.readline() not in (b"\r\n", b"\n", b""): pass
                            return out
                        out += self.rfile.read(n); self.rfile.readline()
                n = int(self.headers.get("Content-Length") or 0)
                return self.rfile.read(n) if n else None

            def _deny(self, code, msg):
                b = msg.encode(); self.send_response(code); self.send_header("Content-Type", "text/plain"); self.close_connection = True
                self.send_header("Content-Length", str(len(b))); self.send_header("Connection", "close")
                try: self.end_headers(); self.wfile.write(b)
                except (BrokenPipeError, ConnectionResetError):          # the client gave up too (e.g. an image server restart)
                    return proxy._log(f"{self.command} {self.path.split('?')[0]} {code} client gone")
                proxy._log(f"{self.command} {self.path.split('?')[0]} {code} denied")

            def _refuse(self, code, msg):
                """a cost guard: 400 in the route's own error format, never forwarded"""
                b = json.dumps(error_json(proxy.api, msg, code)).encode(); self.close_connection = True
                with proxy._clock: proxy.refused[code] = proxy.refused.get(code, 0) + 1
                proxy._log(f"{self.command} {self.path.split('?')[0]} 400 refused {code}: {msg}")
                try:
                    self.send_response(400, "Bad Request"); self.send_header("Content-Type", "application/json")
                    self.send_header("Content-Length", str(len(b))); self.send_header("Connection", "close")
                    self.end_headers(); self.wfile.write(b)
                except (BrokenPipeError, ConnectionResetError): pass

            def _go(self):
                t0 = time.time(); self.close_connection = True; proxy.n += 1
                body = self._body()                    # read first: closing on an unread body breaks the client's send (EPIPE)
                if not proxy.allow.match(self.path): return self._deny(403, "gab key proxy: only the model API is forwarded\n")
                model_req = self.command == "POST" and bool(MODEL_POST.search(self.path))
                if model_req and (g := proxy._guard(self.path, body)): return self._refuse(*g)
                fixed = (0, 0)
                if body and proxy.api == "openai":
                    body = clean_tool_schemas(body)
                    if "chat/completions" in self.path: body, fixed = repair_tool_history(body)
                if any(fixed): proxy._log(f"repaired tool history: +{fixed[0]} missing results, -{fixed[1]} orphan results")
                if body and self.command == "POST":
                    body, sh = shrink_images(body, **proxy.shrink)
                    if sh[0]: proxy._log(f"shrank {sh[0]} images: request {sh[1] / 1e6:.1f} MB -> {sh[2] / 1e6:.1f} MB")
                    if proxy.api == "openai":
                        body, nt = fix_tiny_images(body)
                        if nt: proxy._log(f"enlarged {nt} images under {TINY_MIN} px (the provider refuses them)")
                hd = {k: v for k, v in self.headers.items() if k.lower() not in HOP}
                hd.update(proxy.extra); hd.update(proxy._auth_headers(hd)); hd["Host"] = proxy.host
                for k in [k for k in hd if k.lower() == "accept-encoding"]: del hd[k]
                hd["Accept-Encoding"] = "identity"                 # an answer the proxy can read (usage, SSE fixes, 4xx dumps)
                if body is not None: hd["Content-Length"] = str(len(body))
                pre = None
                for attempt in range(RETRY_400_TRIES + 1):
                    try:
                        c = proxy._connect(); c.request(self.command, self.path, body=body, headers=hd); r = c.getresponse()
                    except Exception as e:
                        return self._deny(502, f"gab key proxy: upstream unreachable ({type(e).__name__})\n")
                    cl0 = r.getheader("Content-Length")
                    if r.status != 400 or not (cl0 and cl0.isdigit() and int(cl0) <= 65536): break
                    pre = r.read()                                       # a short error body: look at it before forwarding
                    if attempt < RETRY_400_TRIES and MEDIA_BUDGET_400.search(pre):
                        nb, k = drop_old_images(body, keep=max(MEDIA_KEEP >> attempt, 1))   # 16, 8, 4, 2, 1
                        if k:
                            body = nb; hd["Content-Length"] = str(len(body)); c.close()
                            proxy._log(f"{self.command} {self.path.split('?')[0]} 400 media budget: {k} older images omitted, retried"); pre = None
                            continue
                    if attempt == RETRY_400_TRIES or not RETRY_400.search(pre): break
                    if attempt >= 1 and SIG_400.search(pre):           # plain retries failed: give the calls the placeholder
                        nb, k = add_skip_signatures(body)
                        if k: body = nb; hd["Content-Length"] = str(len(body)); proxy._log(f"placeholder thought signature on {k} tool calls")
                    c.close(); proxy._log(f"{self.command} {self.path.split('?')[0]} 400 retried ({attempt + 1}): backend-dependent rejection")
                    time.sleep(RETRY_400_WAIT * (attempt + 1)); pre = None
                self.send_response(r.status, r.reason)
                for k, v in r.getheaders():
                    if k.lower() not in HOP: self.send_header(k, v)
                cl = r.getheader("Content-Length")
                enc = (r.getheader("Content-Encoding") or "identity").strip().lower()
                inflate = _Inflate() if enc in DECODABLE else None      # an upstream that compressed anyway
                sse = proxy.api == "openai" and "text/event-stream" in (r.getheader("Content-Type") or "") and cl is None and enc == "identity"
                if cl is not None: self.send_header("Content-Length", cl)
                self.send_header("Connection", "close")
                try: self.end_headers()
                except (BrokenPipeError, ConnectionResetError):          # the harness went away before the answer
                    c.close(); proxy._log(f"{self.command} {self.path.split('?')[0]} {r.status} client gone"); return
                sent = 0; head = b""; buf = b""; seen = set(); tail = bytearray()
                try:
                    while True:
                        if pre is not None: b, pre = pre, b""             # the error body read above (then end of stream)
                        else: b = r.read1(65536) if hasattr(r, "read1") else r.read(65536)
                        if not b: break
                        plain = inflate.feed(b) if inflate else b       # what the usage is read from
                        if len(head) < 8192: head += plain[:8192 - len(head)]
                        if model_req:                                    # the usage is reported at the end of the answer
                            tail += plain
                            if len(tail) > 2 * TAIL: del tail[:-TAIL]
                        if sse:                                  # whole lines only, each cleaned (clean_sse_line)
                            buf += b; *lines, buf = buf.split(b"\n")
                            b = b"".join(clean_sse_line(x + b"\n", seen) for x in lines)
                            if not b: continue
                        self.wfile.write(b); self.wfile.flush(); sent += len(b)
                    if sse and buf: self.wfile.write(clean_sse_line(buf, seen)); self.wfile.flush(); sent += len(buf)
                except (BrokenPipeError, ConnectionResetError): pass
                finally: c.close()
                if 400 <= r.status < 500 and self.command == "POST" and proxy.log_path:   # keep what was rejected
                    try:
                        dd = os.path.join(os.path.dirname(str(proxy.log_path)), "llm_4xx"); os.makedirs(dd, exist_ok=True)
                        k = len(os.listdir(dd)) + 1
                        with open(os.path.join(dd, f"{k:03d}_{r.status}.request.json"), "wb") as f: f.write(body or b"")
                        with open(os.path.join(dd, f"{k:03d}_{r.status}.response.txt"), "wb") as f: f.write(head)
                    except OSError: pass
                rid = r.getheader("x-request-id") or r.getheader("x-generation-id") or ""
                m = BODY_ID.search(head.decode("utf-8", "replace")); bid = m.group(1) if m else ""
                if self.command == "POST" and (rid or bid):
                    with proxy._lock: proxy.ids.append((rid, bid, self.path.split("?")[0], r.status))
                cost = ""
                if model_req:
                    try:
                        rec = proxy._account(body, head, tail, rid, bid, self.path.split("?")[0], r.status)
                        cost = f" est=${rec['est']:.4f}" + (f" spent=${proxy.spent():.2f}/{proxy.max_usd:.2f}" if proxy.max_usd else "")
                        if not rec["usage"] and 200 <= r.status < 300:   # priced from the request's own input estimate
                            cost += " usage=none" + (f" (content-encoding {enc})" if enc != "identity" else "")
                    except Exception as e: cost = f" est=? ({type(e).__name__})"
                proxy._log(f"{self.command} {self.path.split('?')[0]} {r.status} {sent}B {time.time() - t0:.1f}s req={len(body or b'')}B"
                           + (f" id={rid or bid}" if rid or bid else "") + cost)

            do_GET = do_POST = _go
            def do_PUT(self): self._deny(405, "method not allowed\n")
            do_DELETE = do_PATCH = do_PUT

        return H

    def start(self):
        if os.path.exists(self.sock): os.unlink(self.sock)
        old = os.umask(0o177)
        try: self.server = _Server(self.sock, self.handler())
        finally: os.umask(old)
        if self.owner_uid is not None and os.getuid() == 0: os.chown(self.sock, self.owner_uid, -1)
        threading.Thread(target=self.server.serve_forever, daemon=True, name="gab-keyproxy").start()
        self._log(f"start upstream {self.scheme}://{self.host}{self.base} api={self.api} auth={self.auth}")
        return self

    def _gen_path(self): return f"{self.base}/generation" if self.api == "openai" else f"{self.base}/v1/generation"   # anthropic base: SDK adds /v1

    def _lookup(self, rec, tries=3):
        """one request's charge from the provider's generation endpoint -> its record (cost None when not found)"""
        rid, bid, path, st = rec
        gen = self._gen_path()
        for k in range(tries):
            for i in [x for x in (rid, bid) if x]:
                key = self._key
                if not key: return {"id": rid or bid, "path": path, "status": st, "cost": None}
                try:
                    c = self._connect(); c.request("GET", f"{gen}?id={i}", headers={"Authorization": f"Bearer {key}", "Host": self.host,   # the lookup takes Bearer only (x-api-key: 401)
                                                                                      "User-Agent": "gameanything-bench-gab", "Accept": "application/json", **self.extra})
                    # (a request without User-Agent gets 403 from some gateways' front ends)
                    r = c.getresponse(); body = r.read(); c.close()
                    d = (json.loads(body) or {}).get("data") if r.status == 200 else None
                except Exception: d = None
                if isinstance(d, dict) and d.get("total_cost") is not None:
                    return {"id": i, "path": path, "status": st, "model": d.get("model"), "cost": float(d["total_cost"]),
                            "tokens_prompt": d.get("tokens_prompt") or d.get("native_tokens_prompt"),
                            "tokens_completion": d.get("tokens_completion") or d.get("native_tokens_completion"),
                            "cached": d.get("native_tokens_cached"), "provider": d.get("provider_name") or d.get("provider_id")}
            if k < tries - 1: time.sleep(3 * (k + 1))   # the charge can be recorded a few seconds after the response
        return {"id": rid or bid, "path": path, "status": st, "cost": None}

    def actual_costs(self, out=None, workers=8, tries=3):
        """the provider's own charge of every forwarded request, from its generation endpoint (<base>/generation?id=<id>:
        a gateway by the x-request-id header, OpenRouter by the generation id of the body). Call before stop() (needs the key).
        Requests already priced by the live lookup (live_pricing) are not asked again.
        out: a jsonl file for the per-request records (ids, tokens, cost; no secrets). -> {n, found, usd, source} or None"""
        if not self.ids or not self._key or self.auth == "oauth": return None     # a subscription has no per-request charge
        from concurrent.futures import ThreadPoolExecutor
        def one(rec):
            with self._clock: got = self.provider_found.get((rec[0], rec[1]))
            return got or self._lookup(rec, tries)
        with ThreadPoolExecutor(workers) as ex: recs = list(ex.map(one, list(self.ids)))
        if out:
            with open(out, "a") as f:
                for x in recs: f.write(json.dumps(x) + "\n")
        got = [x["cost"] for x in recs if x["cost"] is not None]
        self._log(f"costs: {len(got)} of {len(recs)} requests priced by the provider, ${sum(got):.4f}")
        return {"n": len(recs), "found": len(got), "usd": round(sum(got), 6), "source": f"{self.host}{self._gen_path()}"}

    def stop(self):
        if self.server:
            self.server.shutdown(); self.server.server_close(); self.server = None
            self._log(f"stop after {self.n} requests")
        try: os.unlink(self.sock)
        except FileNotFoundError: pass
        self._key = None


# gen-image / gen-sfx / gen-music / gen-3d router. gen_image.py keeps its own small API (POST <base>/images {prompt, n,
# aspect_ratio, resolution, images}); the router forwards it to the configured image endpoint: image_api "images" (that body
# as it is, e.g. OpenRouter's images API) or "generations" (translated to an OpenAI-style images/generations call per image),
# and to an optional loopback fallback server speaking gen_image.py's API when the online call is refused or fails.
SEEDREAM_SIZES = {"1:1": (2048, 2048), "16:9": (2560, 1440), "9:16": (1440, 2560), "4:3": (2304, 1728), "3:4": (1728, 2304),
                  "3:2": (2496, 1664), "2:3": (1664, 2496), "21:9": (3024, 1296)}      # the "2K" class (the API's smallest)


def generations_body(body, model):
    """gen_image.py's request -> one images/generations request (the API takes no n: one call per image)"""
    w, h = SEEDREAM_SIZES.get(str(body.get("aspect_ratio") or "1:1"), SEEDREAM_SIZES["1:1"])
    out = {"model": model, "prompt": str(body.get("prompt") or ""), "size": f"{w}x{h}", "response_format": "b64_json",
           "watermark": False}
    # reference images (gen-image --ref): data URIs, the edit / multi-image input of Seedream-style APIs
    refs = [str(x) for x in (body.get("images") or []) if str(x).startswith("data:image/")][:4]
    if refs: out["image"] = refs
    return out


def images_body(body, model):
    """gen_image.py's request -> one request of OpenRouter's images API (POST <base>/images): the configured model, and the
    reference images (gen-image --ref, data URIs or http(s) URLs) as `input_references` [{type: image_url, image_url: {url}}]
    -- the API's own field; an `images` list is silently ignored there"""
    out = {k: v for k, v in body.items() if k not in ("images", "input_references", "model")}
    out["model"] = model
    refs = [str(x) for x in (body.get("images") or []) if str(x).startswith(("data:image/", "http://", "https://"))][:4]
    if refs: out["input_references"] = [{"type": "image_url", "image_url": {"url": u}} for u in refs]
    return out


def as_png(b64):
    """the online API answers JPEG; the agents ask for .png files (and the local server gives PNG): convert when Pillow is here"""
    try:
        import base64, io
        from PIL import Image
        im = Image.open(io.BytesIO(base64.b64decode(b64))); out = io.BytesIO(); im.save(out, "PNG")
        return base64.b64encode(out.getvalue()).decode(), "image/png"
    except Exception:
        return b64, "image/jpeg"


class ImageRouter(KeyProxy):
    """the generation tools' host proxy: gen-image online first (upstream with the key, `model`, `image_api`), then `fallback`
    (a loopback server speaking gen_image.py's own API, no credential) when the online call is refused or fails; gen-sfx /
    gen-music / gen-3d through gab/host/media.py. image_on=False: gen-image answers that it is off (the other tools may work)"""
    def __init__(self, sock, upstream, model, fallback=None, image_api="generations", image_on=True, **kw):
        super().__init__(sock, upstream, api="openai_images", **kw)
        self.model, self.fallback, self.image_api, self.image_on = model, fallback, image_api, image_on
        self.backends = {}                                    # images served: {"online": n, "local": n}
        self.fb = KeyProxy.__new__(KeyProxy)                   # only for its URL parts / _connect (never started)
        if fallback:
            u = urlsplit(fallback.rstrip("/")); self.fb.scheme, self.fb.host = u.scheme, u.hostname
            self.fb.port, self.fb.base, self.fb.timeout = u.port or 80, u.path.rstrip("/"), self.timeout

    def _post(self, conn_of, host, path, obj, headers):
        c = conn_of(); c.request("POST", path, body=json.dumps(obj).encode(),
                                 headers={"Content-Type": "application/json", "Host": host, "User-Agent": "gameanything-bench-gab", **headers})
        r = c.getresponse(); b = r.read(); c.close()
        return r.status, b

    def online(self, body):
        """-> (data list, None) or (None, why)"""
        if not self.image_on: return None, "gen-image is not available this run (no image key configured)"
        if self.image_api == "images":                    # OpenRouter's images API (images_body)
            try: st, b = self._post(self._connect, self.host, f"{self.base}/images", images_body(body, self.model), self._auth_headers({}))
            except Exception as e: return None, f"{type(e).__name__}"
            if st != 200:
                try: code = (json.loads(b).get("error") or {}).get("code") or ""
                except Exception: code = ""
                return None, f"http{st} {code}".strip()
            try: items = json.loads(b).get("data") or []
            except Exception: items = []
            data = []
            for it in items:
                if it.get("b64_json"):
                    png, mt = as_png(it["b64_json"])          # the API answers JPEG; gen-image writes the .png asked for
                    data.append({"b64_json": png, "media_type": mt})
            return (data, None) if data else (None, "no image in the answer")
        n = max(1, min(int(body.get("n") or 1), 4)); data = []
        for _ in range(n):
            try:
                st, b = self._post(self._connect, self.host, f"{self.base}/images/generations", generations_body(body, self.model),
                                   self._auth_headers({}))
            except Exception as e:
                return (data or None), f"{type(e).__name__}"
            if st != 200:
                try: code = (json.loads(b).get("error") or {}).get("code") or ""
                except Exception: code = ""
                return (data or None), f"http{st} {code}".strip()
            try: items = json.loads(b).get("data") or []
            except Exception: items = []
            for it in items:
                if it.get("b64_json"):
                    png, mt = as_png(it["b64_json"]); data.append({"b64_json": png, "media_type": mt})
            if not items: return (data or None), "no image in the answer"
        return data, None

    def handler(self):
        router = self

        class H(BaseHTTPRequestHandler):
            protocol_version = "HTTP/1.1"
            def address_string(self): return "container"
            def log_message(self, *a): pass

            def _send(self, code, obj):
                b = json.dumps(obj).encode(); self.close_connection = True
                self.send_response(code); self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(b))); self.send_header("Connection", "close")
                try: self.end_headers(); self.wfile.write(b); return True
                except (BrokenPipeError, ConnectionResetError): return False

            def _media(self, kind, raw, t0):
                from . import media
                try: body = json.loads(raw or b"{}")
                except Exception: return self._send(400, {"error": {"message": "the request is not JSON"}})
                try:
                    if kind == "audio": res = media.audio(body)
                    else:
                        if not media.meshy_key(): raise ValueError("3D generation is not available this run (no Meshy key configured)")
                        res = media.model3d(body)
                except ValueError as e:
                    router._log(f"POST media/{kind} 400 {str(e)[:120]}"); return self._send(400, {"error": {"message": str(e)[:300]}})
                except Exception as e:
                    router._log(f"POST media/{kind} 502 {type(e).__name__}: {str(e)[:160]} {time.time() - t0:.1f}s")
                    return self._send(502, {"error": {"message": f"{kind} generation failed: {str(e)[:300]}"}})
                with router._lock: router.backends[kind] = router.backends.get(kind, 0) + 1
                router._log(f"POST media/{kind} 200 {body.get('kind') or ('image' if body.get('image') else 'text')} {time.time() - t0:.1f}s")
                if not self._send(200, res): router._log("  client gone")

            def do_POST(self):
                t0 = time.time(); router.n += 1
                raw = self.rfile.read(int(self.headers.get("Content-Length") or 0))
                path = self.path.split("?")[0]
                if path in (f"{router.base}/media/audio", f"{router.base}/media/3d"):   # gen-sfx / gen-music / gen-3d (gab/host/media.py)
                    return self._media(path.rsplit("/", 1)[1], raw, t0)
                if not router.allow.match(self.path):
                    router._log(f"POST {path} 403 denied"); return self._send(403, {"error": {"message": "gab image proxy: only <base>/images"}})
                try: body = json.loads(raw or b"{}")
                except Exception: return self._send(400, {"error": {"message": "the request is not JSON"}})
                data, why = router.online(body); backend = "online"
                if data is None and router.fallback:
                    router._log(f"online refused ({why}) -> local fallback")
                    try:
                        st, b = router._post(router.fb._connect, router.fb.host, f"{router.fb.base}/images", body, {})
                        d = json.loads(b or b"{}")
                    except Exception as e:
                        st, d = 502, {"error": {"message": f"local image server unreachable ({type(e).__name__})"}}
                    if st == 200 and d.get("data"): data, backend = d["data"], "local"
                    else: why = f"{why}; local: {st} {((d.get('error') or {}).get('message') or '')[:200]}"
                if data is None:                                 # (log first: the line is there when the client has the answer)
                    router._log(f"POST {path} 502 {why} {time.time() - t0:.1f}s")
                    if not self._send(502, {"error": {"message": f"image generation failed: {why}"}}): router._log("  client gone")
                    return
                with router._lock: router.backends[backend] = router.backends.get(backend, 0) + len(data)
                if body.get("images"): backend += "+ref"
                router._log(f"POST {path} 200 backend={backend} n={len(data)} {time.time() - t0:.1f}s req={len(raw)}B")
                if not self._send(200, {"created": int(time.time()), "data": data, "backend": backend, "usage": {"cost": None}}):
                    router._log("  client gone")
        return H

    def start(self):
        super().start()
        self._log(f"router: gen-image {(self.image_api + ' ' + self.model) if self.image_on else 'off'}, fallback {self.fallback or '-'}")
        return self


def request_via_socket(sock, method, path, body=None, headers=None, timeout=120):
    """test helper: one HTTP request over the unix socket -> (status, body bytes)"""
    class C(http.client.HTTPConnection):
        def connect(self):
            self.sock = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM); self.sock.settimeout(timeout); self.sock.connect(sock)
    c = C("localhost"); c.request(method, path, body=body, headers=headers or {}); r = c.getresponse()
    return r.status, r.read()
