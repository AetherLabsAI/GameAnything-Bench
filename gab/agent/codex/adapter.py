"""Codex CLI (`codex exec`, headless). Default route: your own Codex login (ChatGPT account, $CODEX_HOME/auth.json;
never touched here). With --base-url, a Responses-API provider `gab` is configured on the command line (`-c`), and the key is
read from the variable named by --api-key-env (codex's `env_key`), so the value never appears in argv or files.

Isolation: `--ignore-user-config --ignore-rules`, sandbox `workspace-write` (writes only in the workdir and
/tmp; network on so the `game` tool reaches the docker socket), approvals never asked, web search off.

Resume: sessions are kept in the trial's own agent home ($HOME/.codex/sessions; no --ephemeral), and a
continuation of gab/agent/until_done.py is `codex exec resume <thread id>` in the SAME session: the conversation and the
provider's prompt cache are kept (a fresh session per continuation dropped all context; refine rejects DONE before 3/4 of the
round, and codex often ends its turn after a few minutes). `exec resume` takes no -s / -C: the sandbox is given as config
(sandbox_mode) and the working directory is the process's own (the workdir).

    gab run --agent codex --model gpt-6-astra [--reasoning-effort high] [--base-url URL --api-key-env VAR]
"""
import json
from pathlib import Path

from gab import config
from gab.agent._common import CLIAdapter


class Adapter(CLIAdapter):
    name = "codex"
    binary = "codex"
    can_resume = True

    def command(self, workdir: Path, instruction: str, home: Path):
        o = self.opts
        resume = o.get("resume")
        if resume and resume not in self._totals(): self._totals()[resume] = self._rollout_total(home, resume)
        cmd = [self.bin(), "exec"] + (["resume"] if resume else []) + ["--json", "--skip-git-repo-check", "--ignore-user-config", "--ignore-rules"]
        cmd += ["-c", f'sandbox_mode="{o.get("sandbox", "workspace-write")}"'] if resume else ["-s", o.get("sandbox", "workspace-write"), "-C", str(workdir)]
        cmd += ["-c", 'approval_policy="never"',
               # network on: the `game` tool talks to the docker socket, which the network-off sandbox refuses
               # (EPERM: every game command failed and the agent drew fake screenshots). Same reach as the other host harnesses.
               "-c", 'web_search="disabled"', "-c", "sandbox_workspace_write.network_access=true"]
        # auto-compaction: with a custom provider codex never compacted its context (one refine request carried ~3.6M input
        # tokens, about $19); compact once the thread passes this many tokens
        ac = int(o.get("auto_compact_tokens", config.get("codex_auto_compact_tokens")) or 0)     # 0 = codex's own default
        if ac > 0: cmd += ["-c", f"model_auto_compact_token_limit={ac}"]
        if self.model: cmd += ["-m", self.model]
        if o.get("reasoning_effort"): cmd += ["-c", f'model_reasoning_effort="{o["reasoning_effort"]}"']
        if o.get("base_url"):
            env_key = o.get("api_key_env") or "OPENAI_API_KEY"
            cmd += ["-c", f'model_providers.gab={{name="gab", base_url="{o["base_url"]}", env_key="{env_key}", wire_api="responses"}}',
                    "-c", 'model_provider="gab"']
        return cmd + ["--"] + ([resume] if resume else []) + [instruction], {}

    def has_session(self, sid, workdir):
        """is the session `sid` recorded in this trial's agent home (the same home run() / the container use)?"""
        home = Path(self.opts.get("home") or Path(workdir).resolve().parent / "agent_home")
        return any((home / ".codex" / "sessions").rglob(f"*{sid}*.jsonl"))

    # The usage of `turn.completed` is the THREAD's running total: after `exec resume` it includes every earlier segment
    # (e.g. a resumed one-line answer reported 799k input = 771k of the first segment + 28k). The segment's
    # own usage is that total minus the total at the end of the previous segment: kept per thread id here, or, for a
    # session from another gab process, the last total_token_usage of its rollout file (resumed turns are not written there,
    # so it can be lower than the true total).
    def _totals(self):
        if not hasattr(self, "_thread_totals"): self._thread_totals = {}
        return self._thread_totals

    @staticmethod
    def _rollout_total(home, sid):
        last = None
        for f in (Path(home) / ".codex" / "sessions").rglob(f"*{sid}*.jsonl"):
            try:
                for line in open(f, errors="replace"):
                    if '"total_token_usage"' not in line: continue
                    try: info = (json.loads(line).get("payload") or {}).get("info") or {}
                    except ValueError: continue
                    if isinstance(info.get("total_token_usage"), dict): last = info["total_token_usage"]
            except OSError: pass
        return last

    def usage(self, events, run):
        turns = 0; tot = None; tid = None
        for e in events:
            if e.get("type") == "thread.started": tid = e.get("thread_id") or tid
            if e.get("type") == "turn.completed" and e.get("usage"): turns += 1; tot = e["usage"]
            if e.get("type") in ("error", "turn.failed"): run.extra.setdefault("errors", []).append(json.dumps(e)[:500])
        base = (self._totals().get(tid) if tid and self.opts.get("resume") else None) or {}
        if tid and tot: self._totals()[tid] = tot
        tot = tot or {}
        d = {k: max(int(tot.get(k) or 0) - int(base.get(k) or 0), 0) for k in ("input_tokens", "cached_input_tokens", "output_tokens")}
        tin, tcache, tout = d["input_tokens"], d["cached_input_tokens"], d["output_tokens"]
        run.tokens_in, run.tokens_cached, run.tokens_out = max(tin - tcache, 0), tcache, tout
        run.extra["turns"] = turns
        bw = [e for e in events if "bwrap: " in json.dumps(e)]
        if bw: run.extra["error"] = ("codex sandbox could not start on this host (bwrap: Failed to make / slave: Permission denied); "
                                     "every shell command / file write of the agent failed")
        run.extra["tool_calls"] = sum(1 for e in events if e.get("type") == "item.completed"
                                      and (e.get("item") or {}).get("type") in ("command_execution", "file_change"))
