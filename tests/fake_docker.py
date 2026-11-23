#!/usr/bin/env python3
"""A fake `docker` CLI for the docker-backend tests (no Docker Engine needed). State in $FAKE_DOCKER_DIR:
  calls.jsonl   every invocation's argv (the tests assert on it)
  images.json   images present locally          containers.json   running containers
A container's filesystem is the host's own (commands of `exec` run here with bash, `cp NAME:/p` reads /p), which is enough to
test the backend's plumbing. `exec` of the agent entry script prints a fake Codex event stream instead of running a harness."""
import json, os, shutil, subprocess, sys
from pathlib import Path

D = Path(os.environ["FAKE_DOCKER_DIR"]); D.mkdir(parents=True, exist_ok=True)
argv = sys.argv[1:]
with open(D / "calls.jsonl", "a") as f: f.write(json.dumps(argv) + "\n")


def load(n, d): p = D / n; return json.loads(p.read_text()) if p.exists() else d
def save(n, v): (D / n).write_text(json.dumps(v))


cmd = argv[0] if argv else ""
if cmd == "version": print("27.0.0 / server 27.0.0"); sys.exit(0)
if cmd == "info": print('{"runc":{}' + (',"nvidia":{}' if os.environ.get("FAKE_NVIDIA") else "") + "} null"); sys.exit(0)
if cmd == "image":
    img = argv[-1]; have = load("images.json", [])
    if img not in have: print(f"Error: No such image: {img}", file=sys.stderr); sys.exit(1)
    if "player-uid" in " ".join(argv): print(os.environ.get("FAKE_IMAGE_UID", str(os.getuid()))); sys.exit(0)
    print(json.dumps([img.split(":")[0] + "@sha256:" + "ab" * 32]) if "RepoDigests" in " ".join(argv) else "sha256:" + "cd" * 32); sys.exit(0)
if cmd == "pull":
    if os.environ.get("FAKE_PULL_DENIED"): print("Error response from daemon: denied", file=sys.stderr); sys.exit(1)
    have = load("images.json", []); have.append(argv[1]); save("images.json", have); sys.exit(0)
if cmd == "run":
    name = argv[argv.index("--name") + 1]; c = load("containers.json", {}); c[name] = argv; save("containers.json", c)
    print("f" * 64); sys.exit(0)
if cmd == "rm":
    c = load("containers.json", {}); c.pop(argv[-1], None); save("containers.json", c); sys.exit(0)
if cmd == "logs": sys.exit(0)
if cmd == "inspect":                                   # the Steam hub (gab/host/steam.py Hub.inspect): State|Labels|IpcMode
    c = load("containers.json", {})
    if argv[-1] not in c: print(f"Error: No such object: {argv[-1]}", file=sys.stderr); sys.exit(1)
    if "Config.Env" in " ".join(argv): print("GAB_STEAM_VNC=1" if os.environ.get("FAKE_HUB_VNC") else "GAB_STEAM_VNC=0"); sys.exit(0)
    print(json.dumps({"Running": True}) + "|" + json.dumps({}) + "|shareable"); sys.exit(0)
if cmd == "cp":
    src, dst = argv[1], argv[2]
    def strip(p):        # NAME:/path -> the container path; container-only dirs land in a private fake rootfs
        if ":" not in p or p.startswith("/"): return p
        q = p.split(":", 1)[1]
        return str(D / "rootfs") + q if q.startswith(("/opt/", "/run/", "/home/player")) else q
    s, t = Path(strip(src)), Path(strip(dst))
    if not s.exists(): print(f"no such path {s}", file=sys.stderr); sys.exit(1)
    t.parent.mkdir(parents=True, exist_ok=True)
    (shutil.copytree if s.is_dir() else shutil.copy)(s, t); sys.exit(0)
if cmd == "exec":
    i, env = 1, dict(os.environ); user = None; stdin_used = False
    while argv[i].startswith("-"):
        if argv[i] in ("-i", "-t", "-d"): stdin_used |= argv[i] == "-i"; i += 1; continue
        if argv[i] == "-e": k, _, v = argv[i + 1].partition("="); env[k] = v
        if argv[i] == "-u": user = argv[i + 1]
        i += 2
    name, rest = argv[i], argv[i + 1:]
    if name not in load("containers.json", {}): print(f"No such container: {name}", file=sys.stderr); sys.exit(1)
    if "/run/gab/agent_entry.sh" in rest and "/opt/gab-agent/bin/claude" in rest:   # Claude Code: a fake stream-json transcript
        u = {"input_tokens": 700, "output_tokens": 80, "cache_read_input_tokens": 300, "cache_creation_input_tokens": 20}
        print(json.dumps({"type": "system", "subtype": "init", "session_id": "sess-1", "model": "claude-fake-model"}))
        print(json.dumps({"type": "assistant", "session_id": "sess-1", "message": {"id": "m1", "usage": u, "content": [
            {"type": "tool_use", "name": "Bash", "input": {"command": "game info"}}]}}))
        print(json.dumps({"type": "result", "session_id": "sess-1", "is_error": False, "num_turns": 2, "result": "done",
                          "total_cost_usd": 0.0123, "usage": u}))
        sys.exit(0)
    if "/run/gab/agent_entry.sh" in rest:              # the agent harness: a fake Codex JSON event stream
        print(json.dumps({"type": "thread.started"}))
        print(json.dumps({"type": "item.completed", "item": {"type": "command_execution", "command": "game info"}}))
        print(json.dumps({"type": "turn.completed", "usage": {"input_tokens": 1000, "cached_input_tokens": 400, "output_tokens": 50}}))
        sys.exit(0)
    if rest[:2] == ["bash", "-c"]:
        c = rest[2]
        import re as _re
        hm = _re.search(r"(?:^|\$G |gab-steam )(status|wait|stop|restart|manifest|install|shot|windows)\b(.*)$", c)
        if "gab-steam" in c and hm:                    # the hub's helper (gab/host/steam.py HELPER)
            with open(D / "helper.jsonl", "a") as f: f.write(json.dumps([hm.group(1), hm.group(2).strip()]) + "\n")
            if hm.group(1) in ("status", "wait"):      # status_seq.json: one JSON per call (the last one repeats), else $FAKE_STEAM_STATUS
                seq = load("status_seq.json", None)
                if seq:
                    cur = seq.pop(0) if len(seq) > 1 else seq[0]; save("status_seq.json", seq); print(json.dumps(cur))
                    sys.exit(0 if hm.group(1) == "status" or cur.get("logged_on") else 2)
                print(os.environ.get("FAKE_STEAM_STATUS") or json.dumps({"running": True, "pid": 77, "logged_on": True, "account": "acct",
                                                                          "client": True, "apps": {}})); sys.exit(0)
            sys.exit(0)
        if "xdotool" in c:                             # input into the hub's screen: recorded; what `type --file -` reads from stdin
            typed = sys.stdin.read() if (stdin_used and "--file -" in c) else None
            with open(D / "xdotool.jsonl", "a") as f: f.write(json.dumps({"cmd": c, "stdin": typed}) + "\n")
            sys.exit(0)
        if c.startswith("test -x /opt/gab-steam/"): print("" if os.environ.get("FAKE_HUB_OLD") else "ok"); sys.exit(0)
        if c.startswith("test -S") and "vnc.sock" in c: print("ok" if os.environ.get("FAKE_HUB_VNC") else ""); sys.exit(0)
        if "xdpyinfo" in c: print("up"); sys.exit(0)
        if c.startswith("pkill") or c.startswith("chown") or c.startswith("mkdir -p /opt/gab-agent"): sys.exit(0)
        if c.startswith("stat -c %s /opt/"):           # harness already installed? (the container-only dir is the fake rootfs)
            q = Path(str(D / "rootfs") + c.split()[3])
            if q.exists(): print(q.stat().st_size)
            sys.exit(0)
        r = subprocess.run(["bash", "-c", c], env=env)
        sys.exit(r.returncode)
    if rest and rest[-1] == "--version": print("2.1.318 (Claude Code)" if rest[0].endswith("/claude") else "codex-cli 0.160.0"); sys.exit(0)
    sys.exit(0)
print(f"fake docker: unknown command {argv}", file=sys.stderr); sys.exit(2)
