#!/usr/bin/env python3
"""Inside a trial container: `game ...` / `transcribe ...` (symlinks to this file) run the host's tool of that name through the
tool bridge socket /run/gab/tools.sock (gab/agent/incontainer.py ToolBridge): argv and the working directory go to the host,
stdout / stderr / exit code come back. The work dir is mounted at the same path inside and outside, so file paths agree."""
import json, os, socket, sys

tool = os.path.basename(sys.argv[0])
s = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
try: s.connect(os.environ.get("GAB_TOOLS_SOCK", "/run/gab/tools.sock"))
except OSError as e: sys.exit(f"{tool}: tool bridge unavailable ({e})")
s.sendall((json.dumps({"tool": tool, "args": sys.argv[1:], "cwd": os.getcwd()}) + "\n").encode())
buf = b""
while True:
    b = s.recv(1 << 16)
    if not b: break
    buf += b
try: r = json.loads(buf.decode(errors="replace"))
except Exception: sys.exit(f"{tool}: bad answer from the tool bridge")
sys.stdout.write(r.get("stdout", "")); sys.stderr.write(r.get("stderr", "")); sys.stdout.flush()
sys.exit(int(r.get("rc", 1)))
