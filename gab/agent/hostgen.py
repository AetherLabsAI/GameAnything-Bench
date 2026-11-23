"""gen-image / gen-3d / gen-sfx / gen-music for an agent that runs on the HOST (no --agent-in-container, e.g. Codex with your
own ChatGPT login), so the brief's tool list (gab/run/trial.py GUIDE) is true there too.

The same pieces as inside a container (gab/agent/incontainer.py), minus the container:
  - the trial's generation router (incontainer.start_image_router: keyproxy.ImageRouter, the same upstreams, the same log
    <trial>/agent/image_proxy.log), started for each run of the agent and stopped when it ends, on a unix socket in a private
    short dir (mode 0700, the socket 0600: only this uid can connect; no TCP port another local user could reach). Codex's
    workspace-write sandbox with network on (gab's setting) can connect to it (unix socket and loopback both reachable, the
    dir itself read-only);
  - the same client scripts (container_files/gen_image.py, gen_media.py), copied to <trial>/tools/gen/ and started by small
    wrappers in <trial>/tools/bin/ (next to `game`: outside the work dir, read-only files) that set GAB_IMAGE_SOCK /
    GAB_IMAGE_BASE for the tool process only. The agent's environment gets nothing new; no credential is in argv, a file,
    a log or an answer -- the router reads it from gab's own process environment.
When no generation tool is available this run (no keys configured), the wrappers are installed without a socket and answer
{"ok": false, "error": ...}, as in a container.
Run record: run.extra["host_gen"] = {image_requests, image_upstream, image_backends} (the in-container keys of
run.extra["in_container"])."""
import hashlib, os, shutil, stat, sys, tempfile, traceback
from pathlib import Path

from .base import AgentAdapter

FILES = Path(__file__).resolve().parent / "container_files"
GEN_TOOLS = {"gen-image": "gen_image.py", "gen-sfx": "gen_media.py", "gen-music": "gen_media.py", "gen-3d": "gen_media.py"}


def sock_dir(trial_dir):
    """a private SHORT dir for the router's socket (unix socket paths are limited to 107 bytes; trial dirs are long):
    $GAB_BRIDGE_ROOT, else $XDG_RUNTIME_DIR (per-user, 0700), else the system temp dir / gabh-<uid>-<hash of the trial dir>,
    mode 0700 and owned by this uid (anything else there is refused)"""
    d = Path(trial_dir).resolve()
    xdg = os.environ.get("XDG_RUNTIME_DIR")
    root = os.environ.get("GAB_BRIDGE_ROOT") or (xdg if xdg and os.path.isdir(xdg) and os.access(xdg, os.W_OK) else None) or tempfile.gettempdir()
    p = Path(root) / f"gabh-{os.getuid()}-{hashlib.sha1(str(d).encode()).hexdigest()[:12]}"
    try: p.mkdir(mode=0o700)
    except FileExistsError: pass
    st = os.lstat(p)
    if not stat.S_ISDIR(st.st_mode) or st.st_uid != os.getuid():
        raise RuntimeError(f"{p}: not a directory of this user; refusing to put the image router's socket there")
    os.chmod(p, 0o700)
    return p


def install(trial_dir, sock=None, base=""):
    """the gen tools in <trial>/tools/bin (wrappers, read-only) + their scripts in <trial>/tools/gen -> {name: path}.
    sock: the router's socket (None: the tools answer that generation is off this run); base: its upstream path prefix"""
    t = Path(trial_dir).resolve() / "tools"; g, b = t / "gen", t / "bin"
    g.mkdir(parents=True, exist_ok=True); b.mkdir(parents=True, exist_ok=True)
    for f in set(GEN_TOOLS.values()):
        (g / f).unlink(missing_ok=True); shutil.copy(FILES / f, g / f); (g / f).chmod(0o555)
    out = {}
    for name, f in GEN_TOOLS.items():
        link = g / name                                   # gen_media.py picks its tool by the name it is called as
        if link.is_symlink() or link.exists(): link.unlink()
        link.symlink_to(f)
        env = (f"export GAB_IMAGE_SOCK={sh_quote(sock)} GAB_IMAGE_BASE={sh_quote(base or '')}\n" if sock else "")
        body = ("#!/bin/bash\nunset GAB_IMAGE_PORT GAB_IMAGE_SOCK GAB_IMAGE_BASE PYTHONHOME PYTHONPATH PYTHONSTARTUP\n" + env +
                f'exec {sh_quote(sys.executable)} -I {sh_quote(str(link))} "$@"\n')
        w = b / name; w.unlink(missing_ok=True); w.write_text(body); w.chmod(0o555)
        out[name] = str(w)
    return out


def sh_quote(s):
    import shlex
    return shlex.quote(str(s))


class HostGen(AgentAdapter):
    """wraps a host-mode adapter (codex, claude, opencode, ...): each run() starts the trial's image router, gives the agent
    the gen tools next to its other tools, and stops the router when the agent ends. Everything else is the inner adapter's
    (options are shared, so until_done's resume reaches it)."""

    def __init__(self, inner, trial_dir):
        self.inner, self.d = inner, Path(trial_dir).resolve()
        self.model, self.name = getattr(inner, "model", None), getattr(inner, "name", type(inner).__name__)
        if getattr(inner, "opts", None) is None: inner.opts = {}
        self.opts = inner.opts
        self.can_resume = getattr(inner, "can_resume", False)

    def __getattr__(self, k):                              # session_of, has_session, usage, bin, ... of the inner adapter
        if k == "inner": raise AttributeError(k)
        return getattr(self.inner, k)

    def session_of(self, transcript): return getattr(self.inner, "session_of", lambda _t: None)(transcript)
    def has_session(self, sid, workdir): return getattr(self.inner, "has_session", lambda _s, _w: True)(sid, workdir)
    def version(self): return self.inner.version()

    def run(self, workdir, instruction, budget, tools, **kw):
        from .incontainer import InContainer, start_image_router
        (self.d / "agent").mkdir(parents=True, exist_ok=True)
        router, err, sd = None, None, None
        try:
            sd = sock_dir(self.d)
            router = start_image_router(sd / "images.sock", self.d / "agent" / "image_proxy.log")
        except Exception as e:                              # the agent still runs; its gen tools answer an error
            err = f"{type(e).__name__}: {e}"[:300]
            with open(self.d / "agent" / "image_proxy.log", "a") as f: f.write(f"host router did not start: {err}\n")
        try:
            T = {**(tools or {}), **install(self.d, router.sock if router else None, router.base if router else "")}
            run = self.inner.run(workdir, instruction, budget, T, **kw)
        finally:
            if router: router.stop()
            if sd:
                try: os.rmdir(sd)                           # made again (same name) by the next run
                except OSError: pass
        hg = {"image_requests": router.n if router else 0,
              "image_upstream": (InContainer.image_route() or (None,))[0] if router else None,
              "image_backends": dict(getattr(router, "backends", {}) or {}) or None}
        if err: hg["error"] = err
        run.extra["host_gen"] = hg
        return run


def wrap(adapter, trial_dir):
    """the host-mode adapter with the gen tools"""
    return HostGen(adapter, trial_dir)


def check(out, prompt="a small red potion bottle, flat game icon", sandbox=True):
    """one real gen-image call the way a host agent makes it (a live check; ONE image is generated and paid for):
    a throw-away trial dir `out`, the router started from this shell's environment, gen-image run from the trial's tools
    bin -- inside `codex sandbox` (workspace-write, network on, as gab runs codex) when codex is on PATH. -> the tool's answer"""
    import json, subprocess
    from .base import AgentRun, Budget
    d = Path(out).resolve(); w = d / "work"; (w / "deliverable").mkdir(parents=True, exist_ok=True)

    class One(AgentAdapter):
        name = "check"
        def run(self, workdir, instruction, budget, tools, **kw):
            cmd = [tools["gen-image"], prompt, str(Path(workdir) / "check.png")]
            if sandbox and shutil.which("codex"):
                cmd = ["codex", "sandbox", "-c", 'sandbox_mode="workspace-write"', "-c", "sandbox_workspace_write.network_access=true", *cmd]
            r = subprocess.run(cmd, cwd=workdir, capture_output=True, text=True, timeout=900)
            self.answer = (r.stdout.strip().splitlines() or [""])[-1] or r.stderr[-500:]
            self.via = "codex sandbox" if cmd[0] == "codex" else "plain shell"
            return AgentRun(exit_reason="done")
    a = One()
    run = wrap(a, d).run(w, "", Budget(minutes=15), {})
    print(json.dumps({"via": a.via, "answer": a.answer, "host_gen": run.extra["host_gen"], "log": str(d / "agent" / "image_proxy.log")}, indent=1))


if __name__ == "__main__":
    import argparse
    ap = argparse.ArgumentParser(description="live check of the host gen tools: generates ONE image")
    ap.add_argument("out"); ap.add_argument("--prompt", default="a small red potion bottle, flat game icon")
    ap.add_argument("--no-sandbox", action="store_true")
    a = ap.parse_args(); check(a.out, a.prompt, not a.no_sandbox)
