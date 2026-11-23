"""Preflight of `gab run` (skip: --no-preflight): the environment checks that catch the usual setup failures before a
single trial starts, in the code / hint vocabulary of gab/run/infra.py. No model is called.
  agent binary (+ version); codex: its companion codex-code-mode-host, network_access=true in the adapter's command, and
  `codex sandbox` with the same permissions runs a command and reaches the docker socket (EPERM on the socket,
  bwrap / AppArmor userns); docker reachable; the API key variable set (never printed); an OpenRouter key's remaining limit
  and credits; the selected games' images present."""
import json, os, shutil, subprocess, tempfile, urllib.error, urllib.request
from pathlib import Path

from .. import config
from . import infra

OR_API = "https://openrouter.ai/api/v1"


def _fail(name, detail, code):
    return (name, False, detail, code)


def _run(cmd, timeout=60, **kw):
    try: r = subprocess.run(cmd, capture_output=True, text=True, errors="replace", timeout=timeout, **kw); return r.returncode, (r.stdout or "") + (r.stderr or "")
    except FileNotFoundError as e: return 127, f"{e}"
    except subprocess.TimeoutExpired: return 124, f"timeout after {timeout} s: {' '.join(map(str, cmd[:3]))}"


def _code(text, default):
    m = infra.classify(text); return m[0]["code"] if m else default


def codex_checks(A, binary, host):
    out = []
    real = Path(shutil.which(binary) or binary).resolve()
    comp = real.parent / "codex-code-mode-host"
    out.append(("codex companion", comp.exists(), str(comp) if comp.exists() else f"{comp} missing (host executable was not found)",
                None if comp.exists() else "agent_binary_missing"))
    with tempfile.TemporaryDirectory(prefix="gab-preflight-") as tmp:
        cmd, _ = A.command(Path(tmp), "x", Path(tmp)); net = "sandbox_workspace_write.network_access=true" in cmd
        out.append(("codex network_access", net or host != "docker", "sandbox_workspace_write.network_access=" + str(net).lower() +
                    ("" if net else ": the `game` tool cannot reach the docker socket"), None if net or host != "docker" else "docker_socket_denied"))
        probe = "touch .gab_probe && " + (f"{config.get('docker') or 'docker'} version --format '{{{{.Server.Version}}}}'" if host == "docker" else "true")
        rc, txt = _run([binary, "sandbox", "-P", "gab", "-C", tmp, "-c", f"permissions.gab.network.enabled={str(net).lower()}",
                        "-c", 'permissions.gab.filesystem={":root"="read", ":workspace_roots"="write"}', "--", "sh", "-c", probe], timeout=90)
    if rc and ("Usage:" in txt or "unrecognized" in txt or "unexpected argument" in txt):
        out.append(("codex sandbox", True, "no `codex sandbox -P` in this codex version: not checked", None))
    else:
        out.append(("codex sandbox", rc == 0, ("runs commands" + (f", docker {txt.strip()[-20:]}" if host == "docker" else "")) if rc == 0
                    else infra.excerpt(txt, n=300), None if rc == 0 else _code(txt, "bwrap_sandbox")))
    return out


def openrouter(var, base_url):
    """GET /key (limit_remaining) and /credits (total_credits - total_usage) with the key from the environment"""
    key = os.environ.get(var) or ""; out = []
    def get(path):
        req = urllib.request.Request(base_url.rstrip("/") + path, headers={"Authorization": f"Bearer {key}"})
        with urllib.request.urlopen(req, timeout=20) as r: return json.loads(r.read().decode()).get("data") or {}
    try:
        k = get("/key"); rem = k.get("limit_remaining")
        ok = rem is None or float(rem) > 0
        out.append(("openrouter key", ok, f"limit {k.get('limit')}, remaining {rem}, usage {k.get('usage')}", None if ok else "api_credits"))
    except urllib.error.HTTPError as e:
        code = {401: "api_auth", 402: "api_credits", 403: "api_auth"}.get(e.code)
        return [("openrouter key", code is None, f"HTTP {e.code} from {base_url}/key", code)]
    except Exception as e:
        return [("openrouter key", True, f"not checked ({type(e).__name__}: {str(e)[:120]})", None)]
    try:
        c = get("/credits"); left = float(c.get("total_credits") or 0) - float(c.get("total_usage") or 0)
        out.append(("openrouter credits", left > 0, f"{left:.2f} USD left", None if left > 0 else "api_credits"))
    except Exception as e:                               # some keys may not read account credits: the key check above stands
        out.append(("openrouter credits", True, f"not checked ({type(e).__name__})", None))
    return out


def judge_checks():
    """the judges' Claude Code CLI (every evaluation needs it) and ffmpeg: binary on PATH and some login / key. The login is
    looked up, not tried (no model call): ANTHROPIC_API_KEY, CLAUDE_CODE_OAUTH_TOKEN or the CLI's credentials file."""
    out = []; b = config.get("claude") or "claude"; w = shutil.which(b)
    out.append(("judge claude", bool(w), f"{w}" if w else f"{b!r} not found on PATH (install Claude Code; config key `claude`)",
                None if w else "agent_binary_missing"))
    if w:
        cred = Path(os.environ.get("CLAUDE_CONFIG_DIR") or Path.home() / ".claude") / ".credentials.json"
        how = ("ANTHROPIC_API_KEY" if os.environ.get("ANTHROPIC_API_KEY") else "CLAUDE_CODE_OAUTH_TOKEN"
               if os.environ.get("CLAUDE_CODE_OAUTH_TOKEN") else f"login {cred}" if cred.exists() else
               "macOS keychain (not checked)" if os.uname().sysname == "Darwin" else None)
        out.append(("judge login", bool(how), f"{how} (value not shown)" if how else
                    "no ANTHROPIC_API_KEY, CLAUDE_CODE_OAUTH_TOKEN or `claude login`: the evaluation cannot call the judge", None if how else "api_auth"))
    f = shutil.which("ffmpeg")
    out.append(("ffmpeg", bool(f), f or "not found on PATH (recordings, keyframes)", None if f else "agent_binary_missing"))
    return out


def checks(agent, opts, model, games, judge=True):
    from ..agent import base as abase
    host = config.get("host"); out = judge_checks() if judge else []
    if agent not in ("stub", "cmd") and not opts.get("in_container"):
        A = abase.get(agent, model=model, **{k: v for k, v in opts.items() if k not in ("in_container", "brief", "reference_ablation")})
        b = config.get("claude") if agent == "claude_code" else A.bin()
        w = shutil.which(b) if b else None
        out.append((f"agent {agent}", bool(w), (f"{w}  {A.version() or '(version unknown)'}" if w else f"{b!r} not found on PATH"),
                    None if w else "agent_binary_missing"))
        if w and agent == "codex": out += codex_checks(A, b, host)
        var = getattr(A, "opts", {}).get("api_key_env")
        if var:
            have = bool(os.environ.get(var))
            out.append((f"api key {var}", have, "set (value not shown)" if have else "not set (export it, or scripts/gab-session)", None if have else "api_auth"))
            base = A.opts.get("base_url") or ""
            if have and "openrouter.ai" in base: out += openrouter(var, base)
    if host == "docker":
        from ..host.docker import Backend, image_ref
        bk = Backend()
        r = bk._run(["version", "--format", "{{.Server.Version}}"], timeout=60)
        out.append(("docker", r.returncode == 0, (r.stdout or r.stderr).strip()[-200:], None if r.returncode == 0 else _code(r.stderr, "docker_unreachable")))
        if r.returncode == 0:
            for g in sorted(set(games)):
                try: ref = image_ref(g)
                except RuntimeError as e: out.append((f"image {g}", False, str(e)[:200], "image_pull_failed")); continue
                have = bk.has_image(ref); never = (config.get("docker_pull") or "missing") == "never"
                out.append((f"image {g}", have or not never, ref + ("" if have else "   (not present: pulled by the first trial)" if not never
                                                                 else "   (not present and docker_pull is never)"), None if have or not never else "image_pull_failed"))
    return out


def preflight(agent, opts, model, games, show=None, judge=True):
    """-> True if every check passed; prints one line per check and the code + fix of each failure"""
    show = show or (lambda m: print(m, flush=True))          # flushed: in order with the stderr log lines in a log file
    C = checks(agent, opts, model, games, judge=judge); bad = [c for c in C if not c[1]]
    show(f"\nPreflight ({agent}{' ' + model if model else ''}; host {config.get('host')}):")
    for name, ok, detail, code in C:
        show(f"  {'ok ' if ok else '-- '} {name:22} {detail}")
        if not ok and code in infra.BY:
            s = infra.BY[code]; show(f"      {code} ({s.kind}, {s.component}): {s.hint}")
    show("  preflight " + ("passed" if not bad else f"FAILED ({len(bad)}): fix the cause (the hint above) or --no-preflight") + "\n")
    return not bad
