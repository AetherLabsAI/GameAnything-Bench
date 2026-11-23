"""Infrastructure errors: failures of the environment, not of the agent. Once, a Codex sandbox refused
the docker socket, every `game build/start` failed with EPERM, the agent drew fake "screenshots" with ffmpeg, wrote DONE and the
trial was recorded as a normal `agent_done`. Now every such failure is classified by a signature (below), recorded with its
SPECIFIC cause, marks the trial `infra_error` (not scored), is retried when transient, and stops a `gab run` when systematic,
so each root cause is fixed once (the hint of each code says how).

  classify(text)        -> matches [{code, kind, component, hint, message}] (message = redacted excerpt <= 500 chars)
  record(trial, inc)    -> <trial>/infra.jsonl + the machine-wide ledger <runs_dir>/_infra/ledger.jsonl
  assess(...)           -> the trial's infra block {status: None | "infra_error", primary, incidents}
kind: systematic = every trial hits it until someone fixes the cause (stop the run); transient = retry later.
Never writes secrets: excerpts pass redact(). Imported by gab.run.control on every tool call: stdlib only here."""
import json, os, re, socket, time
from collections import namedtuple
from pathlib import Path

Sig = namedtuple("Sig", "code kind component regex hint")
S, T, G = "systematic", "transient", "guard"            # guard: gab's own cost guard stopped the agent (not the environment)
BUDGET = "budget_exceeded"                                 # the infra status (and code) of a run stopped by its USD cap
_N = r"(?:status|code|error|HTTP)[\s\"':=]{1,4}"          # "status": 429 / code=402 / HTTP 503 / error 401
SIGS = [   # order = priority (the more specific first); regex None = set by code, never matched in text
    Sig("docker_socket_denied", S, "host/docker",
        r"(?:permission denied|operation not permitted)[^\n]{0,160}docker|docker\.sock[^\n]{0,160}(?:permission denied|operation not permitted)",
        "the docker socket refuses the caller. codex: its sandbox needs sandbox_workspace_write.network_access=true "
        "(gab/agent/codex/adapter.py); a user: must be in group `docker`"),
    Sig("docker_unreachable", S, "host/docker",
        r"Cannot connect to the Docker daemon|Is the docker daemon running|error during connect:|docker: command not found"
        r"|No such file or directory: 'docker'",
        "the docker daemon does not answer: `docker version`; DOCKER_HOST / config docker_host"),
    Sig("bwrap_sandbox", S, "agent-sandbox",
        r"\bbwrap: |Failed RTM_NEWADDR|Failed to make / slave|setting up uid map|No permissions to creat\w* new namespace"
        r"|creating new namespace failed",
        "the agent harness's bubblewrap sandbox cannot start (unprivileged user namespaces): on Ubuntu 24.04 an AppArmor "
        "profile for the codex binary with `userns,`"),
    Sig("agent_binary_missing", S, "agent-binary",
        r"host executable was not found|codex-code-mode-host|No such file or directory: '(?:codex|opencode|claude|gemini|kimi)[^']*'"
        r"|(?:codex|opencode|claude|gemini|kimi): command not found|spawn (?:codex|opencode|claude)\S* ENOENT",
        "the harness binary or a companion is missing: codex needs codex-code-mode-host next to it (npm i -g @openai/codex@0.160.0)"),
    Sig("agent_home_permission", S, "harness",
        r"(?:EACCES|permission denied|PermissionError)[^\n]{0,200}agent_home",
        "the agent uid in the container cannot write its home (<trial>/agent_home): gab/agent/incontainer.py must open every "
        "dir an adapter creates there (chmod a+rwX after command())"),
    Sig("api_credits", S, "model-api",
        r"insufficient[ _]credits|insufficient_quota|exceeded your current quota|credit balance is too low|requires more credits"
        r"|" + _N + r"402\b|Payment Required",
        "the API account has no credits / quota left: top up (OpenRouter: openrouter.ai/settings/credits) or raise the key limit"),
    # Claude subscription's per-model quota (seen with Claude Code): the 429 body is a generic
    # rate_limit_error ("would exceed your account's rate limit"), only the stream-json says it is the quota -- api_error
    # model_requires_usage_credits, rate_limit_info status rejected / rateLimitType seven_day_overage_included, result "You've
    # reached your Fable limit." Before api_rate_limit (transient): resuming / retrying only burned the trial's minutes
    Sig("api_quota_exhausted", S, "model-api",
        r"model_requires_usage_credits"
        r"|\\?\"status\\?\":\s*\\?\"rejected\\?\"[^{}\n]{0,200}\\?\"rateLimitType\\?\":\s*\\?\"seven_day"
        r"|you(?:['’]|\\u2019)?ve reached your (?!(?:weekly |usage )?limit)[\w .-]{1,40}? limit",
        "account quota exhausted; wait for reset or switch account (the Claude subscription's model quota, e.g. \"You've "
        "reached your Fable limit\"; resetsAt in the transcript's rate_limit_event): the run stops; after the reset `gab run` "
        "again with the same --name resumes each interrupted trial in its session"),
    Sig("api_tos_block", S, "model-api",
        r"violation of provider Terms Of Service|request is prohibited due to a violation",
        "the provider refuses the request by its terms (OpenRouter 403): pin another provider (config openrouter_providers) "
        "or drop the model"),
    Sig("api_auth", S, "model-api",
        _N + r"401\b|\b401\b[^\n]{0,20}Unauthorized|invalid[ _-]api[ _-]key|incorrect api key|invalid x-api-key|authentication_error"
        r"|not logged in|please run /login|No auth credentials found|missing credential: environment variable",
        "the API key / login is missing or invalid: the --api-key-env variable (scripts/gab-session) or the harness's own login"),
    Sig("api_schema_rejected", S, "model-api",
        r"Invalid JSON payload received\. Unknown name|function_declarations\[\d+\]\.parameters",
        "the provider (Gemini behind an OpenAI-compatible gateway) rejects JSON-Schema keywords in the tool definitions ($schema, "
        "exclusiveMinimum, ...): run in the container (--agent-in-container) -- the host proxy strips them (gab/host/keyproxy.py)"),
    Sig("api_stream_invalid", T, "model-api",
        r"Type validation failed: Value: \{\"choices\"|AI_TypeValidationError|Invalid input: expected object, received undefined"
        r"|Expected 'id' to be a string|Expected 'function\.name' to be a string|Invalid input: expected array, received undefined",
        "the harness's SDK rejects a streamed chunk of the provider (e.g. a gateway streaming a tool-call delta without 'function'): "
        "gab/host/keyproxy.py clean_sse_line drops such deltas; a new shape needs a rule there"),
    Sig("api_history_rejected", T, "model-api",
        r"number of function response parts is equal to the number of function call parts|missing a thought_signature in functionCall",
        "the provider (Gemini) rejected the conversation's tool history (call / response counts): the key proxy repairs the "
        "history (repair_tool_history) and keeps rejected requests in <trial>/agent/llm_4xx/ for diagnosis"),
    Sig("api_model_unavailable", T, "model-api",
        r"已下架|" + _N + r"410\b|\b410\b[^\n]{0,20}Gone|model[^\n]{0,80}(?:has been (?:removed|retired|deprecated)|is no longer available)",
        "the gateway routed the request to a model backend that is gone (410, '已下架'; the next requests "
        "usually work): resumed in the same session; if it persists, the model id is gone (check /v1/models)"),
    Sig("api_media_budget", T, "model-api",
        r"exceed the encoded media budget",
        "the provider refused a request with too many images: the key proxy replaces all but the latest "
        "images by a text note and retries (drop_old_images); resumed in the same session"),
    Sig("api_media_timeout", T, "model-api",
        r"Download multimodal file timed out|multimodal file[^\n]{0,60}tim(?:ed|e) ?out|Failed to download multimodal content",
        "the provider timed out fetching an image of the request (seen with DashScope behind a gateway): resumed in the same session"),
    Sig("subscription_limit", S, "model-api",
        r"usage limit reached|you(?:['’]|\\u2019)?ve (?:hit|reached) your (?:weekly |usage )?limit|weekly (?:usage )?limit|limit will reset",
        "the Claude subscription's usage (weekly) is used up: the run stops; after the reset `gab run` again with the same "
        "--name resumes each interrupted trial in its session with its remaining time"),
    # gab's own cost guards (gab/host/keyproxy.py): kind G, component cost-guard -- not systematic (the run
    # goes on), not model-api (never resumed as a transient API failure), not an environment failure of the agent
    Sig("budget_exceeded", G, "cost-guard",
        r"gab proxy: spend cap \$[\d.]+ reached",
        "the trial / round reached its USD cap (config run_max_usd / refine_max_usd / critic_max_usd): the key proxy refused "
        "further model requests. Recorded as status budget_exceeded (not scored, not resumed or re-run automatically); "
        "`gab run --force` / a higher cap to run it again"),
    Sig("request_too_large", G, "cost-guard",
        r"gab proxy: request too large",
        "one model request was estimated above config max_request_tokens (text chars / 4 + images): the key proxy refused it; "
        "the agent read / dumped too much into its context (codex compacts past codex_auto_compact_tokens)"),
    Sig("api_rate_limit", T, "model-api",
        r"\b429\b[^\n]{0,40}(?:too many|rate|limit)|" + _N + r"429\b|rate[ _-]?limit(?:ed|_error)?\b|too many requests|overloaded",
        "the API is rate limited / overloaded: retried with backoff; fewer --workers if it persists"),
    Sig("api_content_filter", T, "model-api",
        r"ContentFilterError|blocked by the provider'?s content filter|DataInspectionFailed|data_inspection_failed",
        "the provider's content filter blocked a response (e.g. 400 InternalError.Algo.DataInspectionFailed, opencode exits "
        "rc 1): a provider-side event, not the model's failure -> resumed in the same session"),
    Sig("api_proxy_unreachable", T, "model-api",
        r"Cannot connect to API: ECONNREFUSED",
        "the harness could not reach gab's own model proxy on the loopback : "
        "resumed in the same session"),
    Sig("api_image_too_small", T, "model-api",
        r"image length and width do not meet the model restrictions",
        "the provider (DashScope behind a gateway) refuses an image with a side under 11 px (an agent viewing a texture strip); "
        "the key proxy now enlarges such images (fix_tiny_images); resumed in the same session"),
    Sig("agent_killed_harness", T, "agent-self", None,
        "the agent's own pkill / killall ended the harness (rc 143): resumed in the same session"),
    Sig("api_upstream", T, "model-api",
        _N + r"5(?:00|02|03|04|29)\b|Internal Server Error|Bad Gateway|Service Unavailable|Gateway Time-?out|upstream (?:connect )?error"
        r"|stream disconnected before completion|\bapi_error\b|ECONNRESET|upstream_stream_read_error|Upstream response stream was interrupted"
        r"|\"type\":\s*\"upstream_error\"|\\\"type\\\":\\\"upstream_error"
        r"|ERR_PROVIDER_005|Request contains an invalid argument"   # gateway + Gemini: type server_error, the next request passes
        r"|\\?\"(?:type|code)\\?\":\s*\\?\"KeyError"           # a gateway's own Python KeyError leaking out as the API error
        r"|Please use a valid role: user, model"               # gateway + Gemini 400 on a well-formed body (the one before passed)
        r"|Provider response headers timed out|ProviderHeaderTimeoutError"   # OpenCode's 300 s wait (the proxy was retrying 400s)
        r'|\\?"msg\\?":\s*\\?"bad request\\?"',        # a gateway's bare 400 "bad request" on a well-formed request
        "the API / provider failed upstream (5xx): retried"),
    Sig("container_gone", T, "host/docker",
        r"No such container|container [\w.-]+ is not running|Error response from daemon:[^\n]*(?:not running|is restarting|is paused)",
        "the trial container vanished or stopped (removed by someone, daemon restart, OOM): retried; check `docker ps -a`"),
    Sig("seat_display", T, "host/docker",
        r"seat screen \S+ did not come up|[Cc]an'?t open display|cannot open display|unable to open display",
        "the seat's Xvfb did not come up / went away: retried"),
    Sig("disk_full", S, "host",
        r"No space left on device|\bENOSPC\b|Disk quota exceeded",
        "a disk is full: `df -h` (docker root, runs_dir, /tmp); `docker system df`"),
    Sig("oom_killed", T, "host",
        r"Out of memory|OOMKilled|oom-kill|Killed process \d+|\bMemoryError\b|exit (?:code|status) 137\b|non-zero exit status 137"
        r"|Cannot allocate memory",
        "out of memory (killed, 137): retried; fewer --workers or a larger trial_ram_gb"),
    Sig("image_pull_failed", S, "host/docker",
        r"docker pull \S+ failed|manifest unknown|pull access denied|is not present and docker_pull is never|no docker image for game"
        r"|failed to resolve reference",
        "the game image is missing / cannot be pulled: `gab setup --pull GAME` or docker/build.sh"),
    Sig("game_env_offline", S, "game-env",
        r"Unable to load the service index for source|\bNU1301\b|Resource temporarily unavailable \(|nuget[^\n]{0,200}Resource temporarily "
        r"unavailable|Temporary failure in name resolution|Could not resolve host|getaddrinfo (?:ENOTFOUND|EAI_AGAIN)",
        "a build in the (network-less) game container tries to download: build from the local cache (celeste64: "
        "`dotnet restore --source $HOME/.nuget/packages`)"),
    Sig("gl_init_failed", S, "game-env",
        r"Couldn't find matching GLX visual|Failed to create OpenGL [Cc]ontext|GLXBadFBConfig|glXCreateContext failed"
        r"|Could not create (?:GL|OpenGL) context|No matching fbConfigs or visuals found|libGL error: failed to load driver",
        "OpenGL cannot start on the seat display: SDL games SDL_VIDEO_X11_FORCE_EGL=1; check the GPU / Mesa in the image"),
    Sig("tool_timeout", T, "harness",
        r"subprocess\.TimeoutExpired|TimeoutExpired: Command",
        "a gab tool call ran past its timeout: retried"),
    Sig("harness_interrupted", T, "operator", None,
        "the operator stopped `gab run` while the agent was working (e.g. to restart the run with other --workers): "
        "the same `gab run` resumes such trials in their session with the time they had left"),
    Sig("game_start_slow", T, "host", None,
        "the vanilla start check did not finish in its time limit (not a failed start): the machine is overloaded (load above the "
        "core count, every 3D game on one GPU): retried later; fewer --workers if it repeats"),
    Sig("game_vanilla_start_failed", S, "game-env", None,
        "the game does not start even WITHOUT the agent's mod on a fresh instance: `gab game check GAME` / `gab selfcheck GAME`"),
    Sig("gate_baseline_broken", S, "game-env", None,
        "a gate probe (STABLE / host-present item of benchmark/pools/<game>/pool.yaml `gate:`) fails on the VANILLA game too, so the gate "
        "could not be decided: `gab selfcheck GAME`; the trial is re-evaluated once the probe works on vanilla"),
    Sig("agent_start_crash", S, "harness", None,
        "the agent harness crashed before any model call (0 tokens) and no known cause matched: read <trial>/agent/"
        "transcript.stderr.txt, fix the harness setup, then the same `gab run` re-runs the trial"),
    Sig("judge_failed", T, "eval-judge", None,
        "an LLM judge of the evaluation (replay / play verifier, arbiter, visual judge) failed twice (no verdict, no parseable "
        "JSON, a permission denial, an error / timeout, items missing; gab/eval/judge.py): the trial is not scored and the next "
        "`gab run` evaluates it again (the deliverable stays); <trial>/eval/<stage>.json has the answers"),
    Sig("harness_bug", S, "harness", None,
        "an uncaught exception in gab's own code (traceback in the evidence): fix gab, then re-run the trials"),
]
BY = {s.code: s for s in SIGS}
_RX = [(s, re.compile(s.regex, re.I)) for s in SIGS if s.regex]

_SECRETS = [re.compile(p) for p in (
    r"sk-[A-Za-z0-9_\-]{8,}", r"AIza[0-9A-Za-z_\-]{30,}", r"gh[pousr]_[A-Za-z0-9]{20,}", r"github_pat_[A-Za-z0-9_]{20,}", r"hf_[A-Za-z0-9]{20,}",
    r"eyJ[A-Za-z0-9_\-]{10,}\.[A-Za-z0-9_\-]{10,}\.[A-Za-z0-9_\-]{5,}")]
_BEARER = re.compile(r"(Bearer|Basic|token)(\s+)[A-Za-z0-9._~+/=\-]{8,}", re.I)
_ASSIGN = [re.compile(r"\b([A-Z0-9_]*(?:KEY|TOKEN|SECRET|PASSWORD|PASSWD|CREDENTIAL)S?)(\s*[=:]\s*[\"']?)[^\s\"',;]+"),   # env style
           re.compile(r"\b((?:api|access|auth|refresh|secret)[_-]?(?:key|token)|token|password|authorization)([\"']?\s*[=:]\s*[\"']?)[^\s\"',;]+", re.I)]


def redact(text):
    """key-looking strings (sk-..., Bearer ..., *_KEY=..., *_TOKEN=...) -> [REDACTED]"""
    t = str(text or "")
    for rx in _SECRETS: t = rx.sub("[REDACTED]", t)
    t = _BEARER.sub(lambda m: f"{m.group(1)}{m.group(2)}[REDACTED]", t)
    for rx in _ASSIGN: t = rx.sub(lambda m: f"{m.group(1)}{m.group(2)}[REDACTED]", t)
    return t


def excerpt(text, m=None, n=500):
    """the lines around a match (or the tail), redacted, <= n chars"""
    t = str(text or "")
    if m is None: s = t[-n:]
    else:
        a = t.rfind("\n", 0, max(m.start() - 150, 0)) + 1; b = t.find("\n", m.end() + 200); b = len(t) if b < 0 else b
        s = t[max(a, m.start() - 200): min(b, m.end() + 300)]
    return redact(" ".join(s.split()))[:n]


def make(code, message="", **kw):
    s = BY[code]
    return {"code": code, "kind": s.kind, "component": s.component, "hint": s.hint, "message": excerpt(message), **kw}


def classify(text, components=None, exclude=()):
    """-> one match per signature found in text (priority order). components / exclude: restrict by component (a game log
    full of numbers must not count as an API 401)"""
    t = str(text or "")
    if not t.strip(): return []
    out = []
    for s, rx in _RX:
        if components is not None and s.component not in components or s.component in exclude: continue
        m = rx.search(t)
        if m: out.append(make(s.code, t, _m=m))
    for o in out:                                         # excerpt around the match, not the text's tail
        m = o.pop("_m"); o["message"] = excerpt(t, m)
    return out


def merge(incidents):
    """one incident per code: n occurrences, first message, every source"""
    by = {}
    for i in incidents:
        x = by.get(i["code"])
        if x is None: by[i["code"]] = x = {**i, "n": 0, "sources": []}
        x["n"] += i.get("n", 1)
        for s in ([i["source"]] if i.get("source") else []) + i.get("sources", []):
            if s not in x["sources"]: x["sources"].append(s)
    for x in by.values(): x.pop("source", None)
    return sorted(by.values(), key=lambda x: (x["kind"] != S, -x["n"]))


DIED = ("model-api", "host/docker", "host", "harness", "agent-self")   # transient causes that can end an agent run
ALWAYS = ("judge_failed",)      # transient codes that make the trial an infra_error by themselves (no score from a failed judge)


def decide(incidents, exit_reason=None, early=False, infra_calls=0):
    """-> (status, primary). infra_error when: any systematic incident; or an ALWAYS code (judge_failed); or the agent crashed /
    stopped early with a transient API / container / host incident; or >= 3 tool calls failed with infra codes. budget_exceeded
    (status and primary) when the key proxy's USD cap stopped the run (and nothing systematic happened): not scored, not
    resumed, not re-run by itself"""
    if not incidents: return None, None
    M = merge(incidents)
    sysm = [i for i in M if i["kind"] == S]
    if sysm: return "infra_error", sysm[0]["code"]
    if any(i["code"] == BUDGET for i in M): return BUDGET, BUDGET
    forced = [i for i in M if i["code"] in ALWAYS]
    if forced: return "infra_error", forced[0]["code"]
    died = exit_reason == "crash" or bool(early)
    tr = [i for i in M if i["component"] in DIED]
    if died and tr: return "infra_error", tr[0]["code"]
    if infra_calls >= 3: return "infra_error", M[0]["code"]
    return None, M[0]["code"]


def assess(incidents, exit_reason=None, early=False, infra_calls=0):
    st, pr = decide(incidents, exit_reason, early, infra_calls)
    return {"status": st, "primary": pr, "infra_calls": infra_calls, "incidents": merge(incidents)}


def transient_only(block):
    """an infra_error whose every incident is transient (-> retry the trial)"""
    return bool(block and block.get("incidents")) and all(i["kind"] == T for i in block["incidents"])


RESUMABLE = ("model-api", "operator", "agent-self")   # causes that leave the agent's world intact: its session can go on
# (agent-self: the agent's own pkill ended its harness -- its game and files are as it left them: resume)


def resumable(block):
    """an infra_error caused only by the model API (no game / docker / harness incident): the agent saw nothing wrong in its
    environment, so its session can continue after the fix instead of starting over"""
    inc = (block or {}).get("incidents") or []
    return bool(block and block.get("status") == "infra_error" and inc) and all(i["component"] in RESUMABLE for i in inc)


# ---- evidence readers
def calls(paths):
    """tool-call log records (gab.run.control: <ctx dir>/calls.jsonl, or workdir/.gab/calls.jsonl when the agent's sandbox
    cannot write outside its workdir)"""
    out = []
    for p in paths:
        p = Path(p)
        if not p.exists(): continue
        for line in p.read_text(errors="replace").splitlines():
            try: out.append({**json.loads(line), "_file": str(p)})
            except ValueError: pass
    return out


INPUT_CMDS = ("key", "hold", "click", "move", "look", "drag", "type", "seq")
BUILD_STOPS_GAME = ("minecraft",)                    # games whose `game build` closes the agent's game (adapter load_check)


_OSERR = re.compile(r"(?:PermissionError|FileNotFoundError|IsADirectoryError|NotADirectoryError): \[Errno \d+\] [^:']*: '([^']+)'")


def _agent_path_error(r):
    """a logged harness_bug that is an OSError on (a parent of) a path the agent passed (control.agent_file_error, logged before
    that rule, e.g. `game shot /home/player/...`)"""
    m = _OSERR.search(str(r.get("error") or ""))
    if not m: return False
    f = m.group(1).rstrip("/") + "/"
    return any(str(x).startswith("/") and (str(x).rstrip("/") + "/").startswith(f) for x in r.get("args") or [])


def from_calls(recs, build_stops=False):
    """-> (incidents, number of calls that failed with an infra code). An input to the game's display while the game is not
    running in this log (no display: "Can't open display") is the agent's mistake, not seat_display: before the first ok
    `start` (e.g. after a resume the agent sent `game hold` to the game of its previous machine), or after a `stop` or a failed
    `start` (its mod did not build and it kept sending input), or, with build_stops (BUILD_STOPS_GAME), after a `build`
    (Minecraft: `game build` load-tests on <inst>-gate and closes the dev game first). A log line is written when its call ends and `t` is when it began:
    calls are taken in `t` order (a `hold` sent while a `build` ran comes after the build's start)."""
    inc, running = [], {}
    for r in sorted(recs, key=lambda r: (str(r.get("_file") or ""), str(r.get("t") or ""))):
        f = r.get("_file")
        if r.get("cmd") in ("start", "restart"): running[f] = bool(r.get("ok"))
        elif r.get("cmd") == "stop" or (build_stops and r.get("cmd") == "build"): running[f] = False
        if r.get("infra") not in BY: continue
        if r["infra"] == "seat_display" and r.get("cmd") in INPUT_CMDS and not running.get(f): continue
        if r["infra"] == "harness_bug" and _agent_path_error(r): continue
        inc.append({**make(r["infra"], r.get("error") or ""), "source": f"{r.get('mode', 'game')} {r.get('cmd')}", "evidence": f})
    return inc, len(inc)


ERR_TYPES = ("error", "turn.failed", "stream_error")


def errors_of_transcript(path):
    """the harness-level error text of a transcript: error events (codex / opencode), Claude Code API-error messages and the
    final result when is_error, failed shell commands' output; plain-text lines that say error. -> (error text, command output)"""
    p = Path(path) if path else None
    if not p or not p.exists(): return "", ""
    E, C = [], []
    for line in p.read_text(errors="replace").splitlines()[-20000:]:
        s = line.strip()
        if not s.startswith("{"):
            if re.search(r"error|fail|denied|refused", s, re.I): E.append(s[:2000])
            continue
        try: e = json.loads(s)
        except ValueError: continue
        if not isinstance(e, dict): continue
        t = e.get("type")
        it = e.get("item") or {}
        if t in ERR_TYPES or e.get("is_error") or e.get("isApiErrorMessage") or (t == "assistant" and e.get("error")):
            E.append(json.dumps(e)[:3000])
        elif t == "item.completed" and it.get("type") == "command_execution" and it.get("exit_code") not in (0, None):
            C.append(str(it.get("aggregated_output") or "")[-3000:])
        elif t == "tool_use" and ((e.get("part") or {}).get("state") or {}).get("status") == "error":   # opencode
            C.append(json.dumps((e.get("part") or {}).get("state"))[-3000:])
    return "\n".join(E), "\n".join(C)


# ---- recording
def ledger_path(runs_dir): return Path(runs_dir) / "_infra" / "ledger.jsonl"


def _append(p, rec):
    p.parent.mkdir(parents=True, exist_ok=True)
    with open(p, "a") as f: f.write(json.dumps(rec, ensure_ascii=False, default=str) + "\n")   # one short line: O_APPEND keeps it whole


def record(trial_dir, inc, run=None, task=None, level=None, attempt=None, runs_dir=None):
    """append one incident to <trial>/infra.jsonl and the machine-wide ledger (<runs_dir>/_infra/ledger.jsonl; runs_dir defaults
    to the trial's own: <runs_dir>/<run>/<task>/<level>)"""
    d = Path(trial_dir)
    rec = {"ts": time.strftime("%Y-%m-%dT%H:%M:%S"), "host": socket.gethostname(), "user": os.environ.get("USER"),
           "run": run or d.parent.parent.name, "task": task or d.parent.name, "level": level or d.name, "attempt": attempt,
           "code": inc["code"], "kind": inc["kind"], "component": inc["component"], "n": inc.get("n", 1),
           "message": redact(inc.get("message", ""))[:500], "evidence": inc.get("evidence"), "sources": inc.get("sources") or inc.get("source")}
    for p in (d / "infra.jsonl", ledger_path(runs_dir or d.parent.parent.parent)):
        try: _append(p, rec)
        except OSError: pass
    return rec


def banner(blocks):
    """the stop banner of `gab run`: blocks = [(trial key, infra block)] -> text (empty if nothing systematic)"""
    by = {}
    for key, b in blocks:
        for i in (b or {}).get("incidents") or []:
            if i["kind"] != S: continue
            x = by.setdefault(i["code"], {**i, "trials": []})
            if key not in x["trials"]: x["trials"].append(key)
    if not by: return ""
    L = ["", "=" * 100, "SYSTEMATIC INFRASTRUCTURE ERROR: no new trials were started in this run (running ones finished).", ""]
    for c, x in by.items():
        L += [f"  code       {c}   ({x['component']})", f"  example    {x['message'][:400]}", f"  fix        {x['hint']}",
              f"  trials     {', '.join(x['trials'])}", ""]
    L += ["  fix the cause, then re-run the same command (infra_error trials are re-run).",
          "  all incidents: <runs_dir>/_infra/ledger.jsonl", "=" * 100, ""]
    return "\n".join(L)
