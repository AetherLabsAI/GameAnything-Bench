"""Claude Code as the development agent. It runs on the HOST and drives the game sandbox through the `game` tool (or,
with --agent-in-container, inside the trial container: gab/agent/incontainer.py). Isolation without a container:
  - working dir = the staged public workspace; `--setting-sources ""`, `--strict-mcp-config`, no session persistence;
  - tools Bash, Read, Write, Edit, Glob, Grep in permission mode dontAsk (deny by default): Bash only for the allowed tools
    (game, ffmpeg, ffprobe, transcribe, python3) and the usual file / text commands incl. rm and sed; Write / Edit only inside the workspace;
  - deny rules: Read / Edit / Write of hidden material, the benchmark data and the credential files
    (config `agent_deny_read`); the log is audited for those paths afterwards (trial.audit);
  - credentials: the CLI's own login on the host, reached only through its own process (nothing is passed on).
Usage from the stream-json transcript (result event: usage + the run's own cost, model_usage_since_last_result -- its
total_cost_usd is the session's running total over resumed runs; on a timeout kill, tokens summed per message id).

Inside the trial container (`gab run --agent claude --agent-in-container`, gab/agent/incontainer.py): the same command line
(tools, allow / deny rules, dontAsk, stream-json) with the host's Claude Code binary copied into the container
(config `claude_container_binary`, default: the host CLI). The model is reached through the host key proxy
(gab/host/keyproxy.py, api "anthropic"): ANTHROPIC_BASE_URL = the loopback forwarder, and the CLI holds only a placeholder
credential. container_route() picks the auth:
  --auth subscription (default)   Claude subscription: the long-lived token of `claude setup-token` in the host variable
                                  CLAUDE_CODE_OAUTH_TOKEN (or --api-key-env NAME); the proxy adds `Authorization: Bearer`;
                                  inside, CLAUDE_CODE_OAUTH_TOKEN=<placeholder> puts the CLI in subscription (OAuth) mode
  --auth api                      an Anthropic API key in --api-key-env (default ANTHROPIC_API_KEY); the proxy adds
                                  `x-api-key`; inside, ANTHROPIC_API_KEY=<placeholder>
  --base-url URL                  another Anthropic-compatible upstream (default https://api.anthropic.com)
The container has no network: telemetry, auto-update, error reporting and other non-essential traffic are switched off."""
import json, os, subprocess, time
from pathlib import Path

from ... import config
from ..base import AgentAdapter, AgentRun, watchdog_kill

ANTHROPIC_API = "https://api.anthropic.com"
# inside a container (no network): no telemetry / update / error-report traffic (documented Claude Code variables). Model
# calls stay as on the host (DISABLE_NON_ESSENTIAL_MODEL_CALLS is NOT set: same requests and cost as a host run)
CONTAINER_ENV = {"CLAUDE_CODE_DISABLE_NONESSENTIAL_TRAFFIC": "1", "DISABLE_TELEMETRY": "1", "DISABLE_AUTOUPDATER": "1",
                 "DISABLE_ERROR_REPORTING": "1", "DISABLE_BUG_COMMAND": "1",
                 "CLAUDE_CODE_DISABLE_FEEDBACK_SURVEY": "1", "CLAUDE_CODE_DISABLE_TERMINAL_TITLE": "1",
                 # the CLI's temp dir in the container's own /tmp, not in the bind-mounted agent_home: between segments the
                 # wrapper chowns agent_home back to the host user, and the CLI then refuses its claude-<uid> dir (owned by
                 # another uid) and denies every Bash call)
                 "CLAUDE_CODE_TMPDIR": "/tmp"}

TOOLS = "Bash,Read,Write,Edit,Glob,Grep"
BASH_OK = ["game", "ffmpeg", "ffprobe", "transcribe", "python3", "python", "ls", "mkdir", "cp", "mv", "rm", "rmdir", "touch", "cat",
           "echo", "head", "tail", "wc", "file", "sort", "uniq", "cut", "tr", "grep", "sed", "awk", "find", "diff", "cmp", "stat", "du",
           "zip", "unzip", "tar", "chmod", "cd", "pwd", "date", "basename", "dirname", "realpath", "sha256sum", "xxd", "tee",
           "gen-image", "gen-3d", "gen-sfx", "gen-music"]     # the generation tools on the host too (gab/agent/hostgen.py)
# python3 and file deletion are allowed: agents need python3 (PIL / zipfile), rm, sed -i, heredoc cat


class Adapter(AgentAdapter):
    name = "claude_code"
    can_resume = True
    container_binary = "claude"          # incontainer.harness_binary("claude"): config claude_container_binary or the host CLI

    def session_of(self, transcript):
        for line in open(transcript, errors="replace"):
            try: e = json.loads(line)
            except ValueError: continue
            if e.get("session_id"): return e["session_id"]
        return None

    def _totals(self):
        """session id -> the session's cumulative total_cost_usd at its last result event seen by this adapter (the
        fallback of usage_events for a result without model_usage_since_last_result)"""
        if not hasattr(self, "_session_totals"): self._session_totals = {}
        return self._session_totals

    def version(self):
        try: return subprocess.run([config.get("claude"), "--version"], capture_output=True, text=True, timeout=60).stdout.strip()
        except Exception: return None

    def cmd(self, prompt, budget, workdir, binary=None, container=False):
        """dontAsk = deny-by-default: only the allow rules below run (with acceptEdits, an unlisted
        Bash command and a Write outside the workspace were allowed). binary: the CLI path (default config `claude`).
        container: inside the trial container every Bash command / write is allowed: the container is the
        sandbox, and the host's command-prefix allowlist refused 23-54 Bash calls per task (`cd x; ...`, `a && b`),
        a handicap Codex / OpenCode do not have"""
        deny = [f"{t}(/{p}/**)" for p in config.get("agent_deny_read") for t in ("Read", "Edit", "Write")]
        w = str(Path(workdir).resolve())
        c = [binary or config.get("claude"), "-p", prompt, "--output-format", "stream-json", "--verbose", "--tools", TOOLS,
             "--allowedTools", *(["Bash", "Read", "Glob", "Grep", "Write", "Edit"] if container else
                                 [*[f"Bash({b}:*)" for b in BASH_OK], "Read", "Glob", "Grep", f"Write(/{w}/**)", f"Edit(/{w}/**)"]),
             "--disallowedTools", *deny, "--permission-mode", "dontAsk",
             "--setting-sources", "", "--strict-mcp-config", "--disable-slash-commands"]   # sessions persist: until_done resumes them
        if self.opts.get("resume"): c += ["--resume", self.opts["resume"]]
        if self.model: c += ["--model", self.model]
        if budget.max_turns: c += ["--max-turns", str(budget.max_turns)]
        if self.opts.get("reasoning_effort"): c += ["--effort", self.opts["reasoning_effort"]]
        return c

    # -- inside the trial container (gab/agent/incontainer.py) ----------------------------------------------------------
    def container_route(self):
        """how the host key proxy reaches the model for this harness (see the module docstring)"""
        o = self.opts; auth = o.get("auth") or "subscription"
        if auth == "subscription":
            return {"api": "anthropic", "auth": "oauth", "upstream": o.get("base_url") or ANTHROPIC_API,
                    "secret_env": o.get("api_key_env") or "CLAUDE_CODE_OAUTH_TOKEN", "placeholder_env": "CLAUDE_CODE_OAUTH_TOKEN"}
        if auth == "api":
            return {"api": "anthropic", "auth": "x-api-key", "upstream": o.get("base_url") or ANTHROPIC_API,
                    "secret_env": o.get("api_key_env") or "ANTHROPIC_API_KEY", "placeholder_env": "ANTHROPIC_API_KEY"}
        raise ValueError(f"--auth {auth!r}: claude takes subscription or api")

    def container_command(self, workdir, instruction, budget, home, binary, base_url):
        """-> (argv, env) of the CLI inside the container: the host command line with the container's binary, the proxy as
        ANTHROPIC_BASE_URL, no non-essential traffic, and no Bash / write allowlist (the container is the sandbox). The placeholder credential is set by the wrapper (route placeholder_env)."""
        return self.cmd(instruction, budget, workdir, binary=binary, container=True), {"ANTHROPIC_BASE_URL": base_url, **CONTAINER_ENV}

    def usage(self, events, run):
        """fill an AgentRun from the parsed stream-json events (the wrapper's interface, as CLIAdapter.usage)"""
        u = usage_events(events, self._totals())
        run.tokens_in, run.tokens_out, run.tokens_cached, run.usd = u["tokens_in"], u["tokens_out"], u["tokens_cached"], u.get("usd")
        run.model = u.get("model") or run.model
        if run.exit_reason == "crash" and u.get("result_seen"): run.exit_reason = "done" if not u.get("is_error") else "crash"
        run.extra.update({k: u.get(k) for k in ("turns", "tool_calls", "num_turns", "result", "is_error", "cache_write", "cache_read", "usd_session_total")})

    def run(self, workdir, instruction, budget, tools):
        w = Path(workdir); tr = w.parent / "agent" / "transcript.jsonl"; tr.parent.mkdir(parents=True, exist_ok=True)
        dirs = list(dict.fromkeys(str(Path(t).parent) for t in (tools or {}).values()))   # works without a `game` tool too
        tmp = w / ".gab" / "tmp"; tmp.mkdir(parents=True, exist_ok=True)                    # private TMPDIR per trial
        env = dict(os.environ, PATH=":".join(dirs + [os.environ["PATH"]]), TMPDIR=str(tmp), TMP=str(tmp), TEMP=str(tmp))
        for k in [k for k in env if k.startswith(("CLAUDE_CODE_SESSION", "CLAUDE_CODE_CHILD", "CLAUDE_CODE_MESSAGING", "CLAUDECODE", "CLAUDE_PID"))]:
            env.pop(k)                                     # a fresh top-level session, not a child of the operator's session
        (w.parent / "agent" / "cmd.json").write_text(json.dumps([x if x != instruction else "<brief.md>" for x in self.cmd(instruction, budget, w)], indent=0))
        t0 = time.time()
        with open(tr, "w") as f, open(w.parent / "agent" / "stderr.txt", "w") as fe:
            p = subprocess.Popen(self.cmd(instruction, budget, w), cwd=w, stdout=f, stderr=fe, env=env, start_new_session=True, stdin=subprocess.DEVNULL)
            reason = watchdog_kill(p, t0 + budget.minutes * 60, budget.stop_file)
        u = usage(tr, self._totals())
        return AgentRun(transcript=str(tr), exit_reason=reason if reason != "crash" or not u.get("result_seen") else ("done" if not u.get("is_error") else "crash"),
                        wall_s=round(time.time() - t0, 1), rc=p.returncode, model=u.get("model") or self.model, harness_version=self.version(),
                        tokens_in=u["tokens_in"], tokens_out=u["tokens_out"], tokens_cached=u["tokens_cached"], usd=u.get("usd"),
                        extra={k: u.get(k) for k in ("turns", "tool_calls", "num_turns", "result", "is_error", "cache_write", "cache_read", "usd_session_total")})


def usage(path, totals=None):
    """token / cost totals of a stream-json transcript"""
    evs = []
    for line in open(path, errors="replace"):
        try: evs.append(json.loads(line))
        except ValueError: continue
    return usage_events(evs, totals)


def result_cost(ev, totals=None):
    """USD of ONE run (`claude -p`) from its result event. total_cost_usd is the SESSION's running total: after `--resume`
    it includes every earlier run of the session (e.g. 74 resumed segments of one refine round reported
    0.40, 0.53, 0.57, ..., 2.57, and the per-segment sum came to 108.85 for a real 2.57). model_usage_since_last_result
    (per model: costUSD, tokens) is the run's own share. The `usage` block (tokens) is the run's own already.
    Without that field (older CLI): the total minus the session's previous total kept in `totals` (session id -> total;
    a session first seen here counts in full). Not used to rebuild the total: Claude Code does not always restore the
    session's running cost on resume (a resumed run whose total restarted from its own cost was seen)."""
    tot = ev.get("total_cost_usd"); sid = ev.get("session_id")
    since = ev.get("model_usage_since_last_result")
    if isinstance(since, dict):
        usd = sum(float((m or {}).get("costUSD") or 0) for m in since.values() if isinstance(m, dict))
    elif tot is None: usd = None
    else:
        base = (totals or {}).get(sid) if sid else None
        usd = tot - base if base is not None and tot >= base else tot
    if totals is not None and sid and tot is not None: totals[sid] = tot
    return usd


def usage_events(events, totals=None):
    """token / cost totals of parsed stream-json events (cost: the run's own, result_cost)"""
    out = {"tokens_in": 0, "tokens_out": 0, "tokens_cached": 0, "cache_read": 0, "cache_write": 0, "turns": 0, "tool_calls": 0, "usd": None}
    per_msg = {}
    for ev in events:
        if not isinstance(ev, dict): continue
        t = ev.get("type")
        if t == "system" and ev.get("subtype") == "init": out["model"] = ev.get("model")
        elif t == "assistant":
            m = ev.get("message") or {}; out["turns"] += 1
            out["tool_calls"] += sum(1 for c in m.get("content") or [] if c.get("type") == "tool_use")
            if m.get("id") and m.get("usage"): per_msg[m["id"]] = m["usage"]
        elif t == "result":
            out.update(result_seen=True, is_error=ev.get("is_error"), num_turns=ev.get("num_turns"), result=(ev.get("result") or "")[:1500])
            c = result_cost(ev, totals)
            if c is not None: out["usd"] = (out["usd"] or 0) + c
            if ev.get("total_cost_usd") is not None: out["usd_session_total"] = ev["total_cost_usd"]
            if isinstance(ev.get("usage"), dict):   # the tokens since the previous result: one process can emit several
                fu = out.setdefault("final_usage", {})
                for k, v in ev["usage"].items():
                    if isinstance(v, (int, float)) and not isinstance(v, bool): fu[k] = fu.get(k, 0) + v
    u = out.get("final_usage")
    if not u:                                            # killed before the result event: sum the per-message usage
        u = {}
        for x in per_msg.values():
            for k, v in x.items():
                if isinstance(v, (int, float)): u[k] = u.get(k, 0) + v
    out["tokens_in"] = int(u.get("input_tokens") or 0); out["tokens_out"] = int(u.get("output_tokens") or 0)
    out["cache_read"] = int(u.get("cache_read_input_tokens") or 0); out["cache_write"] = int(u.get("cache_creation_input_tokens") or 0)
    out["tokens_cached"] = out["cache_read"] + out["cache_write"]
    return out
