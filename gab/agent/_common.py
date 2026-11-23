"""Shared helpers of the CLI-harness adapters (codex, opencode).

- child_env():   the harness process environment: the parent environment minus every credential-looking variable, plus
                 only the credentials this adapter needs (passed in memory, never via argv / files / logs);
- launch():      start the harness headless in its own process group, stream stdout (the event log) to the transcript, stop
                 at the wall budget (base.watchdog_kill);
- scrub():       remove anything that looks like a key from a saved transcript;
- audit():       paths into hidden material that appear in the agent log (a hit marks the trial `leak`);
- tool_use():    did the agent extract frames / run transcribe (read from the transcript + the workdir).
"""
import os, re, subprocess, time
from pathlib import Path

from gab.agent.base import watchdog_kill

SECRET_NAME = re.compile(r"(KEY|TOKEN|SECRET|PASSWORD|CREDENTIAL|AUTH)", re.I)
KEEP = {"SSH_AUTH_SOCK"}
KEY_LIKE = re.compile(r"(sk-or-v1-[A-Za-z0-9]{20,}|sk-ant-[A-Za-z0-9_\-]{20,}|sk-proj-[A-Za-z0-9_\-]{20,}|sk-[A-Za-z0-9]{32,}"
                      r"|AIza[0-9A-Za-z_\-]{30,}|ghp_[A-Za-z0-9]{30,}|github_pat_[A-Za-z0-9_]{30,}|hf_[A-Za-z0-9]{30,}"
                      r"|eyJ[A-Za-z0-9_\-]{20,}\.[A-Za-z0-9_\-]{20,}\.[A-Za-z0-9_\-]{10,})")


def child_env(tools: dict | None = None, secrets: dict | None = None, extra: dict | None = None, keep_auth: tuple = ()) -> dict:
    """parent env without credential variables (names in keep_auth survive), tool dirs first on PATH, + secrets + extra"""
    env = {k: v for k, v in os.environ.items() if k in KEEP or k in keep_auth or not SECRET_NAME.search(k)}
    dirs = []
    for p in (tools or {}).values():
        d = str(Path(p).resolve().parent if Path(p).is_file() else Path(p).parent)
        if d not in dirs: dirs.append(d)
    env["PATH"] = ":".join(dirs + [env.get("PATH", "/usr/bin:/bin")])
    env.update(extra or {})
    env.update(secrets or {})
    return env


def tools_dir(tools: dict, into: Path) -> dict:
    """one directory holding exactly the allowed tools (symlinks), so PATH exposes nothing else from their source dirs"""
    into.mkdir(parents=True, exist_ok=True)
    out = {}
    for name, p in (tools or {}).items():
        link = into / name
        if link.is_symlink() or link.exists(): link.unlink()
        link.symlink_to(Path(p).resolve() if name not in ("transcribe",) else Path(p))
        out[name] = str(link)
    return out


def launch(cmd, cwd: Path, env: dict, transcript: Path, budget_minutes: float, stderr: Path | None = None, stdin_text=None, stop=None):
    """-> (exit_reason, rc, wall_s). stdout (the harness's event stream) goes straight to the transcript file."""
    transcript.parent.mkdir(parents=True, exist_ok=True)
    stderr = stderr or transcript.with_suffix(".stderr.txt")
    t0 = time.time()
    with open(transcript, "w") as out, open(stderr, "w") as err:
        proc = subprocess.Popen(cmd, cwd=cwd, env=env, stdout=out, stderr=err,
                                stdin=subprocess.PIPE if stdin_text is not None else subprocess.DEVNULL, start_new_session=True)
        if stdin_text is not None:
            try: proc.stdin.write(stdin_text.encode()); proc.stdin.close()
            except BrokenPipeError: pass
        reason = watchdog_kill(proc, t0 + budget_minutes * 60, stop)
    return reason, proc.returncode, round(time.time() - t0, 1)


def scrub(paths, secrets: dict | None = None):
    """replace key-looking strings and the exact values of the given secrets in saved logs (in place)"""
    vals = [v for v in (secrets or {}).values() if v and len(v) >= 8]
    for p in paths:
        p = Path(p)
        if not p.exists() or p.is_dir(): continue
        try: t = p.read_text(errors="replace")
        except Exception: continue
        n = t
        for v in vals: n = n.replace(v, "[REDACTED]")
        n = KEY_LIKE.sub("[REDACTED]", n)
        if n != t: p.write_text(n)


B64 = re.compile(r"[A-Za-z0-9+/=]{2000,}")


def strip_media(src: Path, dst: Path):
    """copy a log with inline base64 media (frames the agent viewed) replaced by a size marker"""
    dst.write_text(B64.sub(lambda m: f"[base64 stripped: {len(m.group(0))} chars]", Path(src).read_text(errors="replace")))


def jsonl(path: Path):
    import json
    out = []
    if not path or not Path(path).exists(): return out
    for line in Path(path).read_text(errors="replace").splitlines():
        line = line.strip()
        if not line.startswith("{"): continue
        try: out.append(json.loads(line))
        except Exception: pass
    return out


def audit(texts, hidden_paths, allow=()) -> list:
    """every path into hidden material mentioned in the agent's logs (Video2World-style audit, Apache-2.0 idea)"""
    hits = set()
    isfile = lambda t: isinstance(t, Path) or (len(str(t)) < 1024 and "\n" not in str(t) and Path(str(t)).exists())
    blob = "\n".join(Path(t).read_text(errors="replace") if isfile(t) else str(t) for t in texts)
    for h in hidden_paths:
        if not h: continue
        for m in re.findall(re.escape(str(h).rstrip("/")) + r"[^\s\"'`)\]\\]*", blob):
            if not any(m.startswith(str(a)) for a in allow): hits.add(m)
    return sorted(hits)


IMG = (".png", ".jpg", ".jpeg", ".webp", ".bmp")


def tool_use(transcript: Path, workdir: Path) -> dict:
    t = Path(transcript).read_text(errors="replace") if transcript and Path(transcript).exists() else ""
    imgs = [p for p in Path(workdir).rglob("*") if p.suffix.lower() in IMG]
    ff_frames = bool(re.search(r"ffmpeg[^\n]{0,400}?(\.png|\.jpg|\.jpeg|image2|-frames:v|-vframes|fps=|select=|thumbnail|tile=)", t))
    return {"frames_extracted": bool(imgs) or ff_frames, "n_images": len(imgs),
            "transcribe_used": bool(re.search(r"(^|[\s\"'`;&|(/])transcribe\s+[^\s<(-]", t, re.M)),   # not the prompt's `transcribe <file>`
            "ffprobe_used": "ffprobe" in t}


# ---------------------------------------------------------------------------------------------------------------------
from gab.agent.base import AgentAdapter, AgentRun   # noqa: E402


def hidden_paths() -> list:
    """GAB_HIDDEN_DIR + the operator's deny list (gab.config); used only for the log audit"""
    try:
        from gab import config
        return [str(config.get("hidden_dir"))] + list(config.get("agent_deny_read") or [])
    except Exception:
        return [os.environ["GAB_HIDDEN_DIR"]] if os.environ.get("GAB_HIDDEN_DIR") else []


class CLIAdapter(AgentAdapter):
    """Template for a headless CLI harness. Options (all optional):
       base_url, api_key_env (NAME of the variable holding the key; the value is read from `secrets` or os.environ only),
       reasoning_effort, secrets (dict name -> value, in memory, for the child env only), home (state dir),
       binary (path of the harness executable)."""
    name = "cli"
    binary = None
    keep_auth = ()            # parent variables of this harness's own login that must survive child_env()
    can_resume = False        # `resume` opt continues the previous session (gab/agent/until_done.py)

    def session_of(self, transcript):
        """the harness's session id from its event stream (session_id / sessionID / thread_id); None if not found"""
        try:
            for e in jsonl(transcript):
                for k in ("session_id", "sessionID", "sessionId", "thread_id"):
                    if e.get(k): return e[k]
        except Exception: pass
        return None

    # -- to implement ----------------------------------------------------------------------------------------------
    def command(self, workdir: Path, instruction: str, home: Path) -> tuple[list, dict]:
        raise NotImplementedError

    def usage(self, events: list, run: AgentRun):
        """fill tokens_in (uncached) / tokens_out / tokens_cached / usd from the parsed event stream"""

    # -- shared ------------------------------------------------------------------------------------------------------
    def bin(self): return self.opts.get("binary") or self.binary

    def secrets(self) -> dict:
        need = self.opts.get("api_key_env")
        given = dict(self.opts.get("secrets") or {})
        if need and need not in given and os.environ.get(need): given[need] = os.environ[need]
        return given

    def version(self):
        try: return subprocess.run([self.bin(), "--version"], capture_output=True, text=True, timeout=60).stdout.strip().splitlines()[-1]
        except Exception: return None

    def run(self, workdir, instruction, budget, tools) -> AgentRun:
        workdir = Path(workdir).resolve(); workdir.mkdir(parents=True, exist_ok=True)
        (workdir / "deliverable").mkdir(exist_ok=True)
        home = Path(self.opts.get("home") or workdir.parent / "agent_home").resolve(); home.mkdir(parents=True, exist_ok=True)
        transcript = Path(self.opts.get("transcript") or workdir.parent / "transcript.jsonl")
        stderr = transcript.with_suffix(".stderr.txt")
        tl = tools_dir(tools, home / "bin")
        sec = self.secrets()
        if self.opts.get("api_key_env") and self.opts["api_key_env"] not in sec:
            transcript.write_text(""); stderr.write_text(f"missing credential: environment variable {self.opts['api_key_env']} is not set\n")
            return AgentRun(transcript=str(transcript), exit_reason="crash", model=self.model, harness_version=self.version(),
                            extra={"error": f"{self.opts['api_key_env']} not set"})
        cmd, extra = self.command(workdir, instruction, home)
        (home / "tmp").mkdir(exist_ok=True)
        env = child_env(tl, sec, {"TMPDIR": str(home / "tmp"), **extra}, keep_auth=self.keep_auth)
        (workdir.parent / "command.txt").write_text(" ".join(c if len(c) < 200 else "<prompt>" for c in cmd) + "\n")
        reason, rc, wall = launch(cmd, workdir, env, transcript, budget.minutes, stderr, stop=budget.stop_file)
        scrub([transcript, stderr], sec)
        run = AgentRun(transcript=str(transcript), exit_reason=reason, wall_s=wall, rc=rc, model=self.model,
                       harness_version=self.version())
        try: self.usage(jsonl(transcript), run)
        except Exception as e: run.extra["usage_error"] = repr(e)
        if reason != "done" and stderr.exists(): run.extra["stderr_tail"] = stderr.read_text(errors="replace")[-800:]
        run.extra.update(tool_use(transcript, workdir))
        run.extra["leak"] = audit([transcript, stderr], hidden_paths(), allow=[str(workdir)])
        if reason == "done" and not any((workdir / "deliverable").iterdir()): run.extra["note"] = "no deliverable written"
        return run
