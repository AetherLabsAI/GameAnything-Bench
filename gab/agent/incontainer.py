"""Agent harness INSIDE the trial container (`gab run --agent-in-container`, host `docker` only). Generic for every CLI harness
(gab/agent/_common.CLIAdapter: codex, opencode), for Claude Code (claude_code: an adapter that declares
container_route() / container_command()) and for `--agent-cmd`. Codex runs in the container with its own sandbox off (the
container is the sandbox); Claude Code uses your own Claude subscription (the `claude setup-token` token stays in the host
shell, the proxy adds it).

What the container gets (bind mounts, made at `docker run` time; the same absolute paths inside and outside, so every path in
the brief, in `game` answers and in the transcript is valid on both sides):
  <trial>/work         rw   the staged public workspace (brief, clips, dev kit, deliverable/) = the agent's cwd
  <trial>/agent_home   rw   HOME / CODEX_HOME / TMPDIR of the harness (fresh per trial: no user config, rules, AGENTS.md)
  $TMP/gab-<hash>/<random> -> /run/gab   tools.sock (ToolBridge: `game`, `transcribe` run on the host as for a host agent),
                                  llm.sock (gab/host/keyproxy.py: the model key is added on the HOST), the shims, the entry script,
                                  images.sock (same idea, OPENROUTER_API_KEY: `gen-image`, only when that variable is set)
Nothing else: no hidden material, no repository, no credential file, no network (`--network none`; the harness reaches its model
only through llm.sock via a loopback forwarder). The harness runs as the image's user `agent` (uid docker_agent_uid, default
2000), never as the game's user `player`; at the end every process of that uid is killed and the files it made are chowned back
to the host user.

Credentials: only the NAME of a host environment variable is passed (--api-key-env); its value is read by the proxy on the host.
Inside, the harness sees a placeholder value ("gab-proxy-no-key") in that variable, because CLIs refuse to start without one.
An outside user: `export OPENROUTER_API_KEY=...` (or OPENAI_API_KEY with --base-url https://api.openai.com/v1) in their own shell
and `gab run --agent codex --agent-in-container --base-url ... --api-key-env ...`.
Claude Code: `gab run --agent claude --agent-in-container [--auth subscription]` (token in CLAUDE_CODE_OAUTH_TOKEN, typed into
scripts/gab-session) or `--auth api [--base-url URL] --api-key-env VAR` (an Anthropic API key, sent as x-api-key); inside,
CLAUDE_CODE_OAUTH_TOKEN (resp. ANTHROPIC_API_KEY) = the placeholder and ANTHROPIC_BASE_URL = the loopback forwarder.
"""
import json, os, shlex, shutil, socket, socketserver, subprocess, tarfile, threading, time
from pathlib import Path

from .. import config
from ..util import log
from .base import AgentAdapter, AgentRun, price_usd

FILES = Path(__file__).resolve().parent / "container_files"
BRIDGE = "/run/gab"
LLM_PORT = 18080
IMAGE_PORT = 18081
IMAGE_KEY_ENV = "OPENROUTER_API_KEY"             # gen-image's own credential, independent of the trial's model route
PLACEHOLDER_KEY = "gab-proxy-no-key"
CONTAINER_PATH = f"{BRIDGE}/bin:/opt/gab-agent/bin:/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin"
HOST_TOOLS = ("game", "play", "transcribe")      # forwarded to the host; ffmpeg / ffprobe / python3 are native in the image

# harness binaries copied into the container (/opt/gab-agent/bin/<name>): pinned official static builds, downloaded once into
# config agents_dir and checked by sha256 (GitHub's asset digest), unless config <name>_container_binary points at one
BINARIES = {
    "codex": {"version": "0.160.0",
              "url": "https://github.com/openai/codex/releases/download/rust-v0.160.0/codex-x86_64-unknown-linux-musl.tar.gz",
              "sha256": "306865417d4ee7a927785852910a527f41e1e159add390ac5ae3accb67d44a13",
              "member": "codex-x86_64-unknown-linux-musl"},
}
# harnesses whose container binary is, by default, the host's own CLI (binary name -> config key of the host CLI): Claude
# Code's native build is a glibc ELF that runs in the game images (Ubuntu 24.04, glibc 2.39; checked with 2.1.318)
HOST_BINARIES = {"claude": "claude", "opencode": "opencode"}   # OpenCode: a Bun-compiled glibc ELF as well (1.19.5)
# helper executables shipped next to a harness binary that it spawns at run time (copied along when present)
COMPANIONS = {"codex": ("codex-code-mode-host",)}
# per-harness option changes inside the container: Codex's own sandbox (bwrap) is replaced by the container itself
CONTAINER_OPTS = {"codex": {"sandbox": "danger-full-access"}}


def bridge_dir(trial_dir):
    """the host side of /run/gab for this trial. A SHORT path (unix socket paths are limited to 107 bytes; trial dirs are long):
    $GAB_BRIDGE_ROOT (or the system temp dir) / gab-<hash of the trial dir> (mode 0711: not listable) / <random> -- the sockets
    inside are mode 0666 so the container's agent uid can connect, and the unguessable directory keeps other local users out.
    The chosen path is remembered in <trial>/.gab-bridge so acquire() (mounts) and the agent run agree."""
    import hashlib, secrets, tempfile
    d = Path(trial_dir).resolve(); rec = d / ".gab-bridge"
    if rec.exists():
        b = Path(rec.read_text().strip())
        if b.is_dir() and os.access(b, os.W_OK): return b
        # (a dir docker created itself for a missing mount source is root-owned and useless to us: also replaced)
        # the recorded dir is gone (the temp dir was cleaned, or the trial was moved from another host): a new one
    root = Path(os.environ.get("GAB_BRIDGE_ROOT") or tempfile.gettempdir()) / ("gab-" + hashlib.sha1(str(d).encode()).hexdigest()[:12])
    root.mkdir(parents=True, exist_ok=True); os.chmod(root, 0o711)
    b = root / secrets.token_hex(8); b.mkdir(mode=0o755); os.chmod(b, 0o755)
    d.mkdir(parents=True, exist_ok=True); rec.write_text(str(b) + "\n")
    return b


def mounts(trial_dir):
    """(host, container, mode) bind mounts for requirements["mounts"]; creates the dirs (work is staged into later)"""
    d = Path(trial_dir).resolve(); b = bridge_dir(d)
    for p in (d / "work", d / "agent_home"): p.mkdir(parents=True, exist_ok=True)
    return [(str(d / "work"), str(d / "work"), "rw"), (str(d / "agent_home"), str(d / "agent_home"), "rw"), (str(b), BRIDGE, "rw")]


# ---------------------------------------------------------------------------------------------------------------------
class _UServer(socketserver.ThreadingMixIn, socketserver.UnixStreamServer):
    daemon_threads = True


class ToolBridge:
    """unix-socket server on the host: one JSON line {tool, args, cwd} -> runs the host tool of that name (only the given
    ones) with cwd inside the allowed roots -> one JSON line {rc, stdout, stderr}"""

    def __init__(self, sock, tools, roots, deadline, log=None, owner_uid=None):
        self.sock, self.tools, self.deadline, self.log, self.owner_uid = str(sock), dict(tools), deadline, log, owner_uid
        self.roots = [Path(r).resolve() for r in roots]; self.server = None; self.calls = 0

    def _cwd(self, c):
        try:
            p = Path(c).resolve()
            if any(p == r or r in p.parents for r in self.roots): return p
        except Exception: pass
        return self.roots[0]

    def handle(self, req):
        t = req.get("tool"); args = req.get("args") or []
        if t not in self.tools or not isinstance(args, list): return {"rc": 127, "stdout": "", "stderr": f"{t}: not available\n"}
        from ._common import child_env
        left = max(30, self.deadline - time.time() + 60)
        t0 = time.time(); self.calls += 1
        try:
            r = subprocess.run([self.tools[t], *map(str, args)], cwd=self._cwd(req.get("cwd") or ""), capture_output=True,
                               text=True, errors="replace", timeout=left, env=child_env(), stdin=subprocess.DEVNULL)
            out = {"rc": r.returncode, "stdout": r.stdout, "stderr": r.stderr}
        except subprocess.TimeoutExpired:
            out = {"rc": 124, "stdout": "", "stderr": f"{t}: timed out\n"}
        if self.log:
            with open(self.log, "a") as f:
                f.write(json.dumps({"t": time.strftime("%H:%M:%S"), "tool": t, "args": [str(a)[:200] for a in args],
                                    "rc": out["rc"], "s": round(time.time() - t0, 1)}) + "\n")
        return out

    def start(self):
        bridge = self

        class H(socketserver.StreamRequestHandler):
            def handle(self):
                try: req = json.loads(self.rfile.readline().decode(errors="replace"))
                except Exception: req = {}
                out = json.dumps(bridge.handle(req)).encode()
                try: self.wfile.write(out)
                except (BrokenPipeError, ConnectionResetError): pass    # the agent's call went away (stopped / timed out)

        if os.path.exists(self.sock): os.unlink(self.sock)
        old = os.umask(0o177)
        try: self.server = _UServer(self.sock, H)
        finally: os.umask(old)
        if self.owner_uid is not None and os.getuid() == 0: os.chown(self.sock, self.owner_uid, -1)
        threading.Thread(target=self.server.serve_forever, daemon=True, name="gab-toolbridge").start()
        return self

    def stop(self):
        if self.server: self.server.shutdown(); self.server.server_close(); self.server = None
        try: os.unlink(self.sock)
        except FileNotFoundError: pass


def call_bridge(sock, tool, args, cwd="/"):
    """test helper: the in-container shim's protocol from the host side"""
    s = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM); s.connect(str(sock))
    s.sendall((json.dumps({"tool": tool, "args": args, "cwd": cwd}) + "\n").encode()); buf = b""
    while (b := s.recv(65536)): buf += b
    return json.loads(buf)


# ---------------------------------------------------------------------------------------------------------------------
def harness_binary(name):
    """host path of the harness binary to copy into the container (None = the harness has no container build yet):
    config <name>_container_binary, else the pinned download (BINARIES), else the host's own CLI (HOST_BINARIES)"""
    over = None
    try: over = config.get(f"{name}_container_binary")
    except KeyError: pass
    # resolved: `docker cp` copies a symlink as a symlink, which dangles in the container (e.g. a
    # codex_container_binary pointing at an npm symlink: every in-container codex round died in 4 s, "no such file")
    if over: return os.path.realpath(over)
    if name in HOST_BINARIES: return _host_binary(name)
    spec = BINARIES.get(name)
    if not spec: return None
    d = Path(config.get("agents_dir")) / name / spec["version"]; exe = d / name
    if exe.exists(): return str(exe)
    d.mkdir(parents=True, exist_ok=True)
    tgz = d / Path(spec["url"]).name
    import hashlib, urllib.request
    with urllib.request.urlopen(spec["url"], timeout=600) as r, open(tgz, "wb") as f: shutil.copyfileobj(r, f)
    h = hashlib.sha256(tgz.read_bytes()).hexdigest()
    if h != spec["sha256"]: tgz.unlink(); raise RuntimeError(f"{name} download: sha256 mismatch ({h})")
    with tarfile.open(tgz) as t:
        m = t.getmember(spec["member"]); m.name = name; t.extract(m, d)
    tgz.unlink(); exe.chmod(0o755)
    return str(exe)


def _host_binary(name):
    """the host CLI (config key HOST_BINARIES[name]) resolved to its real file; it must be a native Linux executable (the
    game images have no node: an npm-installed Claude Code is a JS file -> set <name>_container_binary to a native build)"""
    c = config.get(HOST_BINARIES[name])
    p = shutil.which(c) or c
    if not p or not Path(p).exists(): raise RuntimeError(f"{name}: host CLI {c!r} not found (set {name}_container_binary)")
    p = os.path.realpath(p)
    with open(p, "rb") as f:
        if f.read(4) != b"\x7fELF":
            raise RuntimeError(f"{name}: {p} is not a native Linux executable (an npm install?); set {name}_container_binary "
                               "to the native build (e.g. ~/.local/share/claude/versions/<version>)")
    return p


def _secret_like(k): return k.upper().endswith(("KEY", "TOKEN", "SECRET"))


class InContainer(AgentAdapter):
    """wraps an AgentAdapter so that its harness runs inside the seat's container (see the module docstring).

    Three kinds of inner adapter:
      container  defines container_route() + container_command() (claude_code): the adapter says how its model is reached
                 (api family, auth style, upstream, secret / placeholder variable) and what command + env runs inside;
      cli        a CLIAdapter (codex, opencode): OpenAI-style route from --base-url / --api-key-env,
                 its own command() with base_url pointed at the proxy;
      cmd        --agent-cmd: the user's shell command (GAB_LLM_BASE_URL given when --base-url is)."""

    def __init__(self, inner, seat, trial_dir, max_usd=None, spent_usd=0.0, proxy_shrink=None):
        """max_usd: the USD cap of every model request this wrapper's runs make together (a trial's agent run, a refine round,
        a critic review; the key proxy refuses requests past it); spent_usd: what the same run / round already cost before
        (a resumed one). The spend carries over the runs of this wrapper (until_done continuations).
        proxy_shrink: the key proxy's image-shrink options (keyproxy.shrink_images; the critic's CRITIC_SHRINK), None = default."""
        super().__init__(model=inner.model, **inner.opts)
        self.max_usd = float(max_usd or 0) or None
        self.spent_usd = float(spent_usd or 0)
        self.proxy_shrink = proxy_shrink
        self.inner, self.seat, self.d = inner, seat, Path(trial_dir).resolve()
        self.name = f"{inner.name}@container"
        self.uid = int(config.get("docker_agent_uid") or 2000)
        self.bridge_dir = bridge_dir(self.d)
        self.can_resume = getattr(inner, "can_resume", False)   # the agent home persists across runs of a trial: sessions too
        self._opts0 = dict(inner.opts)                      # each run (iterative rounds) starts from the user's options
        self._exe = None                                    # the harness path inside the container, once installed

    def session_of(self, transcript): return getattr(self.inner, "session_of", lambda _t: None)(transcript)
    def has_session(self, sid, workdir): return getattr(self.inner, "has_session", lambda _s, _w: True)(sid, workdir)

    @property
    def kind(self):
        from ._common import CLIAdapter
        if hasattr(self.inner, "container_command"): return "container"
        if isinstance(self.inner, CLIAdapter): return "cli"
        if self.inner.name == "cmd": return "cmd"
        return None

    # -- docker helpers
    def _docker(self, *args, timeout=600):
        b = self.seat.backend
        return b._run(list(args), timeout=timeout)

    def _root(self, cmd, timeout=600):
        return self.seat.backend.exec(self.seat, cmd, timeout, {}, user="0")

    def version(self):
        if not self._exe: return self.inner.version()
        r = self._docker("exec", self.seat.sandbox, self._exe, "--version", timeout=60)
        return (r.stdout.strip().splitlines() or [None])[-1]

    def _own_uid(self):
        """A Steam-game trial shares the Steam hub's pid namespace with every other Steam trial (gab/host/steam.py): with one agent
        uid for all, an agent could see / signal the other trials' agents and read their files through /proc/<pid>/root, and the
        cleanup's `pkill -u` would hit them. So such a trial's agent gets its own uid (20001..29999, free in the shared pid
        namespace and in the image), a passwd entry in the trial, group agent; kept in <trial>/agent/uid for later runs."""
        if not (self.seat.info or {}).get("steam_hub"): return self.uid
        f = self.d / "agent" / "uid"; f.parent.mkdir(parents=True, exist_ok=True)
        uid = int(f.read_text()) if f.exists() and f.read_text().strip().isdigit() else None
        if uid is None:
            used = set((getattr(self._root("ps -eo uid= ; getent passwd | cut -d: -f3", 60), "stdout", "") or "").split())
            import random
            free = [u for u in range(20001, 30000) if str(u) not in used]
            uid = random.choice(free[:2000]); f.write_text(str(uid))
        self._root(f"getent passwd {uid} >/dev/null || useradd -M -N -g agent -u {uid} -d /home/agent -s /bin/bash agent{uid}", 60)
        self.uid = uid
        return uid

    def _prepare(self, workdir, tools, deadline):
        bd = self.bridge_dir; (bd / "bin").mkdir(parents=True, exist_ok=True)
        for f in ("llm_forward.py", "gab_tool.py", "agent_entry.sh", "gen_image.py", "gen_media.py"):
            shutil.copy(FILES / f, bd / f); (bd / f).chmod(0o755)
        fwd = {k: v for k, v in (tools or {}).items() if k in HOST_TOOLS}
        for k in fwd:
            link = bd / "bin" / k
            if link.is_symlink() or link.exists(): link.unlink()
            link.symlink_to("../gab_tool.py")
        # always on PATH; a clear error if GAB_IMAGE_PORT is not set this run (gen-sfx / gen-music / gen-3d: gab/host/media.py,
        # through the same image router; they answer an error when the router has no online upstream / no 3D key)
        for k, f in (("gen-image", "gen_image.py"), ("gen-sfx", "gen_media.py"), ("gen-music", "gen_media.py"), ("gen-3d", "gen_media.py")):
            link = bd / "bin" / k
            if link.is_symlink() or link.exists(): link.unlink()
            link.symlink_to(f"../{f}")
        os.chmod(bd, 0o755); os.chmod(bd / "bin", 0o755)
        home = self.d / "agent_home"; (home / "tmp").mkdir(parents=True, exist_ok=True)
        for root in (Path(workdir), home):                     # the agent uid writes here (host user gets it back at the end)
            subprocess.run(["chmod", "-R", "a+rwX", str(root)], capture_output=True)
        owner = self.uid if os.getuid() == 0 else None
        tb = ToolBridge(bd / "tools.sock", fwd, [workdir, home], deadline, log=self.d / "agent" / "tool_bridge.jsonl",
                        owner_uid=owner).start()
        if owner is None: os.chmod(bd / "tools.sock", 0o666)    # reachable only via the unguessable bridge dir (bridge_dir())
        return tb, home

    def _route(self):
        """the model route of this run: {api, auth, upstream, secret_env, placeholder_env} or None (no proxy)"""
        i = self.inner; o = i.opts
        if hasattr(i, "container_route"): return i.container_route()
        if o.get("auth") not in (None, "api"):
            raise RuntimeError(f"--auth {o['auth']}: only --agent claude has a subscription route (the others take --auth api)")
        if not o.get("base_url"): return None
        k = o.get("api_key_env") or "OPENAI_API_KEY"
        return {"api": "openai", "auth": "bearer", "upstream": o["base_url"], "secret_env": k, "placeholder_env": k}

    def _proxy(self, route):
        from ..host.keyproxy import KeyProxy
        sec = (self.inner.opts.get("secrets") or {}).get(route["secret_env"])
        p = KeyProxy(self.bridge_dir / "llm.sock", route["upstream"], key_env=None if sec else route["secret_env"], key=sec,
                     log=self.d / "agent" / "llm_proxy.log", owner_uid=self.uid if os.getuid() == 0 else None,
                     api=route["api"], auth=route["auth"], max_request_tokens=config.get("max_request_tokens"),
                     max_usd=self.max_usd, spent_usd=self.spent_usd, model=self.model,
                     live_pricing=bool(config.get("provider_costs")), shrink=self.proxy_shrink).start()
        if self.max_usd: p._log(f"spend cap ${self.max_usd:.2f} (already spent ${self.spent_usd:.2f})")
        if os.getuid() != 0: os.chmod(self.bridge_dir / "llm.sock", 0o666)
        return p

    @staticmethod
    def image_route():
        """-> (upstream, key_env, key, auth) of gen-image, or None when it is off: a loopback image_base_url needs no credential
        (auth="none"); otherwise gen-image is on only when the variable named by image_key_env is set (auth="bearer")"""
        return image_route()

    def _image_proxy(self):
        """the generation tools (container_files/gen_image.py, gen_media.py) through their OWN host proxy, independent of this
        trial's model route: the image / audio / 3D keys stay in gab's environment and never enter the container. Only the
        image and media paths are reachable (keyproxy.py ALLOW, ImageRouter)."""
        p = start_image_router(self.bridge_dir / "images.sock", self.d / "agent" / "image_proxy.log",
                               owner_uid=self.uid if os.getuid() == 0 else None)
        if os.getuid() != 0: os.chmod(self.bridge_dir / "images.sock", 0o666)
        return p

    def _install_harness(self):
        """copy the harness binary to /opt/gab-agent/bin/<name> (skipped when the same size is already there: Claude Code is
        ~300 MB and until_done / iterative start the harness several times per trial)"""
        name = getattr(self.inner, "container_binary", None) or self.inner.name
        exe = harness_binary(name)
        if not exe: raise RuntimeError(f"agent {name} has no container build yet (gab/agent/incontainer.py BINARIES)")
        dst = f"/opt/gab-agent/bin/{name}"
        have = self._root(f"stat -c %s {dst} 2>/dev/null || true", 60)
        if (getattr(have, "stdout", "") or "").strip() != str(os.path.getsize(exe)):
            self._root("mkdir -p /opt/gab-agent/bin", 60)
            r = self._docker("cp", exe, f"{self.seat.sandbox}:{dst}", timeout=600)
            if r.returncode: raise RuntimeError(f"docker cp {exe}: {r.stderr[-300:]}")
            self._root(f"chown root:root {dst} && chmod 755 {dst}", 60)
        # helper executables the harness spawns from its own directory (codex 0.160's command runner needs
        # codex-code-mode-host next to it; without it every shell call failed and the agent gave up after 24 s)
        for helper in COMPANIONS.get(name, ()):
            src = Path(exe).parent / helper
            if not src.exists(): continue
            hdst = f"/opt/gab-agent/bin/{helper}"
            have = self._root(f"stat -c %s {hdst} 2>/dev/null || true", 60)
            if (getattr(have, "stdout", "") or "").strip() == str(src.stat().st_size): continue
            r = self._docker("cp", str(src), f"{self.seat.sandbox}:{hdst}", timeout=600)
            if r.returncode: raise RuntimeError(f"docker cp {src}: {r.stderr[-300:]}")
            self._root(f"chown root:root {hdst} && chmod 755 {hdst}", 60)
        self._exe = dst
        return dst

    def _cleanup(self, workdir):
        self._root(f"pkill -KILL -u {self.uid} || true", 60)
        ug = f"{os.getuid()}:{os.getgid()}"
        self._root(f"chown -R {ug} {shlex.quote(str(workdir))} {shlex.quote(str(self.d / 'agent_home'))} 2>/dev/null; true", 600)

    def run(self, workdir, instruction, budget, tools) -> AgentRun:
        from ._common import audit, child_env, hidden_paths, jsonl, launch, scrub, tool_use
        workdir = Path(workdir).resolve(); (workdir / "deliverable").mkdir(exist_ok=True)
        (self.d / "agent").mkdir(parents=True, exist_ok=True)
        deadline = time.time() + budget.minutes * 60
        resume = self.opts.get("resume")                    # set by until_done on this wrapper
        self.inner.opts.clear(); self.inner.opts.update(self._opts0)
        if resume and self.can_resume: self.inner.opts["resume"] = resume
        kind = self.kind
        if kind is None: raise RuntimeError(f"agent {self.inner.name} cannot run inside a container yet")
        route = self._route()
        if route is None and kind != "cmd" and not self.inner.opts.get("allow_no_proxy"):
            raise RuntimeError(f"{self.inner.name} in a container needs a model route through the host proxy: give --base-url and "
                               "--api-key-env (no login file or key is ever copied into a container)")
        if route and not ((self.inner.opts.get("secrets") or {}).get(route["secret_env"]) or os.environ.get(route["secret_env"])):
            hint = " (`claude setup-token` in your own terminal, then type it into scripts/gab-session)" \
                if route["secret_env"] == "CLAUDE_CODE_OAUTH_TOKEN" else ""
            raise RuntimeError(f"no credential for the model proxy: environment variable {route['secret_env']} is not set{hint}")
        self._own_uid()
        tb, home = self._prepare(workdir, tools, deadline)
        proxy = None; img_proxy = None; reason, rc, wall = "crash", None, 0.0; costs = None
        transcript = self.d / "agent" / "transcript.jsonl"; stderr = transcript.with_suffix(".stderr.txt")
        try:
            env = {"HOME": str(home), "TMPDIR": str(home / "tmp"), "PATH": CONTAINER_PATH, "LANG": "C.UTF-8", "GAB_IN_CONTAINER": "1"}
            url = None
            if route:
                proxy = self._proxy(route); url = proxy.container_base_url(LLM_PORT)
                env["GAB_LLM_PORT"] = str(LLM_PORT); env[route["placeholder_env"]] = PLACEHOLDER_KEY
            if gen_tools_on():
                img_proxy = self._image_proxy(); env["GAB_IMAGE_PORT"] = str(IMAGE_PORT)
                env["GAB_IMAGE_BASE"] = img_proxy.base          # the upstream's path (/api/v1, or "" for a local server):
                                                                  # gen-image asks <base>/images
            o = self.inner.opts; o.pop("secrets", None)
            if kind == "container":
                cmd, extra = self.inner.container_command(workdir, instruction, budget, home, self._install_harness(), url)
            elif kind == "cli":
                if url: o["base_url"] = url
                o.update(CONTAINER_OPTS.get(self.inner.name, {}))
                o["binary"] = self._install_harness()
                cmd, extra = self.inner.command(workdir, instruction, home)
            else:
                from .cmd.adapter import placeholders
                cmd = ["bash", "-c", o["agent_cmd"].format(**placeholders(workdir, self.model))]
                extra = {"GAB_LLM_BASE_URL": url} if url else {}
            env.update({k: v for k, v in extra.items() if not _secret_like(k)})
            # the adapter's command() may create dirs under the agent home after _prepare() opened it (OpenCode: opencode/
            # {config,data,state,cache}); open them too, or the agent uid cannot write there (EACCES at start)
            subprocess.run(["chmod", "-R", "a+rwX", str(home)], capture_output=True)
            # ... and hand the home to the agent uid: _cleanup() gave it to the host user after the previous run, and a file mode
            # change needs ownership. Codex chmods its own state on start ($CODEX_HOME/tmp/arg0, ...): every second codex run in
            # a trial (a refine round, a continuation) died at once with "failed to initialize in-process app-server client:
            # Operation not permitted (os error 1)". _cleanup() gives it back to the host user at the end.
            self._root(f"chown -R {self.uid}:{self.uid} {shlex.quote(str(home))} 2>/dev/null; true", 600)
            dex = [*self.seat.backend.docker, "exec", "-u", f"{self.uid}:{self.uid}", "-w", str(workdir)]
            for k, v in env.items(): dex += ["-e", f"{k}={v}"]
            full = [*dex, self.seat.sandbox, "/bin/bash", f"{BRIDGE}/agent_entry.sh", *cmd]
            (self.d / "agent" / "command.txt").write_text(" ".join(shlex.quote(c) if len(c) < 200 else "<prompt>" for c in full) + "\n")
            denv = child_env(); denv.update({k: v for k, v in config.docker_env().items() if k == "DOCKER_HOST"})
            reason, rc, wall = launch(full, workdir, denv, transcript, budget.minutes, stderr, stop=budget.stop_file)
        finally:
            try: self._cleanup(workdir)
            finally:
                tb.stop()
                if proxy:
                    try: costs = proxy.actual_costs(out=self.d / "agent" / "llm_costs.jsonl") if config.get("provider_costs") else None
                    except Exception as e: costs = {"error": repr(e)[:300]}
                    proxy.stop()
                    seg = proxy.spent() - self.spent_usd
                    if costs and costs.get("n") and costs.get("found") == costs["n"]: seg = costs["usd"]   # the provider's own charge
                    self.spent_usd += max(seg, 0.0)
                if img_proxy: img_proxy.stop()                 # gen-image already reports usd per call; no generation lookup needed
        scrub([transcript, stderr])
        run = AgentRun(transcript=str(transcript), exit_reason=reason, wall_s=wall, rc=rc, model=self.model, harness_version=self.version())
        if hasattr(self.inner, "usage"):
            try: self.inner.usage(jsonl(transcript), run)
            except Exception as e: run.extra["usage_error"] = repr(e)
        if run.usd is None and (run.tokens_in or run.tokens_out): run.usd = price_usd(self.model, run.tokens_in, run.tokens_out, run.tokens_cached)
        if costs and costs.get("n"):                        # the provider's own charge replaces the list-price estimate when complete
            run.extra.update(usd_estimate=run.usd or 0.0, cost_requests=costs["n"], cost_priced=costs["found"], cost_source=costs["source"])
            if costs["found"] == costs["n"]: run.usd = costs["usd"]; run.extra["usd_from"] = "provider"
            else: run.extra["usd_provider_partial"] = costs["usd"]; run.extra["usd_from"] = "estimate (provider priced only part)"
        elif costs: run.extra["cost_error"] = costs.get("error")
        if run.exit_reason != "done" and stderr.exists(): run.extra["stderr_tail"] = stderr.read_text(errors="replace")[-800:]
        run.extra.update(tool_use(transcript, workdir))
        run.extra["leak"] = audit([transcript, stderr], hidden_paths(), allow=[str(workdir)])
        if proxy:                                           # the cost guards of the key proxy (gab/host/keyproxy.py)
            run.extra["spend"] = {"max_usd": self.max_usd, "spent_usd": round(self.spent_usd, 4),
                                  "max_request_tokens": proxy.max_request_tokens}
            if any(proxy.refused.values()): run.extra["refused"] = dict(proxy.refused)
            if proxy.budget_hit: run.extra["budget_exceeded"] = proxy.budget_hit
        run.extra["in_container"] = {"container": self.seat.sandbox, "image": self.seat.info.get("image"), "uid": self.uid,
                                     "tool_calls": tb.calls, "model_requests": proxy.n if proxy else 0,
                                     "image_requests": img_proxy.n if img_proxy else 0,
                                     "image_upstream": (self.image_route() or (None,))[0] if img_proxy else None,
                                     "image_backends": dict(getattr(img_proxy, "backends", {})) or None,   # router: online / local counts
                                     "route": {k: route[k] for k in ("api", "auth", "upstream", "secret_env")} if route else None}
        return run


_GEN_LOGGED = set()


def _gen_log(what, msg):
    """one log line per process for a generation tool that is off"""
    if what in _GEN_LOGGED: return
    _GEN_LOGGED.add(what); log(msg)


def image_route():
    """-> (upstream, key_env, key, auth) of gen-image, or None when it is off (no key; logged once)"""
    from urllib.parse import urlsplit
    up = str(config.get("image_base_url") or "").strip()
    if not up:
        _gen_log("image", "gen-image is off: image_base_url is not set (agents may score lower without generated art)"); return None
    if (urlsplit(up).hostname or "") in ("127.0.0.1", "localhost", "::1"): return up, None, None, "none"
    env = config.get("image_key_env") or IMAGE_KEY_ENV
    if os.environ.get(env): return up, env, None, "bearer"
    _gen_log("image", f"gen-image and the target pictures are off: ${env} is not set (agents may score lower without generated art)")
    return None


def gen_tools_on():
    """-> {"image": bool, "audio": bool, "3d": bool}-like truthy dict when any generation tool is available this run, else {}"""
    from ..host import media
    on = {"image": image_route() is not None, "audio": media.audio_route() is not None, "3d": bool(media.meshy_key())}
    if not on["audio"]: _gen_log("audio", "gen-sfx / gen-music are off: audio_base_url / audio_key_env / audio_*_model not configured")
    if not on["3d"]: _gen_log("3d", f"gen-3d is off: ${config.get('meshy_key_env') or 'MESHY_API_KEY'} is not set")
    return on if any(on.values()) else {}


def start_image_router(sock, log_path, owner_uid=None):
    """the trial's gen-image / gen-sfx / gen-music / gen-3d router on the unix socket `sock` (mode 0600; the caller opens it
    when a container uid must connect), started; None when no generation tool is available this run. The same for an agent
    in a container (_image_proxy) and one on the host (gab/agent/hostgen.py). Credentials: read from gab's own environment by
    the proxy, never passed on."""
    from ..host.keyproxy import ImageRouter
    if not gen_tools_on(): return None
    r = image_route()
    fb = str(config.get("image_fallback_url") or "").strip() or None
    model = config.get("image_model") or ""
    api = str(config.get("image_api") or "images")
    if api not in ("images", "generations"): raise ValueError(f"image_api {api!r}: images or generations")
    if r:
        up, key_env, key, auth = r
        return ImageRouter(sock, up, model, fallback=fb if auth != "none" else None, image_api=api, key_env=key_env, key=key,
                           log=log_path, owner_uid=owner_uid, auth=auth).start()
    return ImageRouter(sock, "http://127.0.0.1:9", model, image_on=False, log=log_path, owner_uid=owner_uid, auth="none").start()


def wrap(adapter, seat, trial_dir, max_usd=None, spent_usd=0.0, proxy_shrink=None):
    """max_usd / spent_usd: the USD cap of the run / round and what it already cost; proxy_shrink: the key proxy's image-shrink
    options (only the critic passes them) (InContainer)"""
    if seat.backend.name != "docker": raise RuntimeError("--agent-in-container needs the docker host backend (config host = docker)")
    return InContainer(adapter, seat, trial_dir, max_usd, spent_usd, proxy_shrink)
