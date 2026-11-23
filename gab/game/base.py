"""GameAdapter interface (docs/SPEC.md) + what every adapter shares: the player-input vocabulary (xdotool on the
instance's X display), screenshots (x11grab of the display, copied to the host), record (frames grabbed in the sandbox,
encoded on the host: never encode video in a sandbox), and replay of an input trace.

An *instance* is one fresh game directory inside the seat (name = a string; dev and eval use different names). The deliverable
`mod/` is installed into an instance by install_mod(); start() launches the game with it.

Trace format (deliverable traces/*.yaml, also the `play` vocabulary):
    name: str            claims: [str]        what a viewer should see working (for the replay verifier)
    start: fresh         (default; the trace begins on a fresh start of the instance)
    steps: [ {do: key, keys: "ctrl+s"} | {do: hold, keys: [w, shift], seconds: 1.5} | {do: click, x, y, button: 1}
             | {do: move, x, y} | {do: look, dx, dy} | {do: drag, x1, y1, x2, y2, button: 1} | {do: type, text}
             | {do: wait, seconds} | {do: shot, name} ]
"""
import importlib, json, os, re, shlex, subprocess, tempfile, time
from pathlib import Path

from .. import config

# every gab/game/<dir>/adapter.py is a game; id = task.yaml target_game (a module name cannot hold `-`: DIR_IDS)
DIR_IDS = {"godot_tps": "godot-tps"}
GAMES = {DIR_IDS.get(d.name, d.name): f"gab.game.{d.name}.adapter" for d in sorted(Path(__file__).parent.iterdir()) if (d / "adapter.py").exists()}
STEP_KINDS = {"key", "hold", "click", "move", "look", "drag", "type", "wait", "shot"}
MAX_WAIT_S = 120
_BUTTON_NAMES = {"left": 1, "middle": 2, "right": 3}


def _button(b):
    """a click/drag button: xdotool's 1/2/3, or the name an agent naturally reaches for"""
    if isinstance(b, str) and b.strip().lower() in _BUTTON_NAMES: return _BUTTON_NAMES[b.strip().lower()]
    return int(b)


def get(game, seat):
    if game not in GAMES: raise KeyError(f"no adapter for game {game!r} (have: {', '.join(GAMES)})")
    return importlib.import_module(GAMES[game]).Adapter(seat)


def gdir(game):
    """the adapter's folder (devkit/, negmods/)"""
    return Path(__file__).parent / {v: k for k, v in DIR_IDS.items()}.get(game, game)


def title(game):
    """the game's display name (the agent brief)"""
    try: return importlib.import_module(GAMES[game]).Adapter.title or game
    except (KeyError, ImportError, AttributeError): return game


def check_trace(trace):
    """-> list of problems of a trace dict (format above)"""
    P = []
    if not isinstance(trace, dict) or not isinstance(trace.get("steps"), list) or not trace["steps"]: return ["no steps list"]
    for i, s in enumerate(trace["steps"]):
        if not isinstance(s, dict) or s.get("do") not in STEP_KINDS: P.append(f"step {i}: unknown kind {s!r:.60}"); continue
        need = {"key": ["keys"], "hold": ["keys", "seconds"], "click": ["x", "y"], "move": ["x", "y"], "look": ["dx", "dy"],
                "drag": ["x1", "y1", "x2", "y2"], "type": ["text"], "wait": ["seconds"], "shot": []}[s["do"]]
        P += [f"step {i}: {s['do']} needs {k}" for k in need if k not in s]
    return P


def _leaves(x, p, out):
    """flatten a state snapshot to {leaf path: value}; dict keys -> a.b, lists of dicts -> a[0].b, lists of plain values and
    empty containers are one leaf"""
    if isinstance(x, dict) and x:
        for k, v in x.items(): _leaves(v, f"{p}.{k}" if p else str(k), out)
    elif isinstance(x, list) and x and any(isinstance(v, (dict, list)) for v in x):
        for i, v in enumerate(x): _leaves(v, f"{p}[{i}]", out)
    else: out[p] = x
    return out


def state_diff(a, b):
    """two state() snapshots -> {"changed": {path: [old, new]}, "added": {path: new}, "removed": {path: old}} over the leaf
    paths (STATE_SPEC). A list of plain values (screen_text, a set of flags) that changed is given as {"+": [new items],
    "-": [gone items]} instead of [old, new]. Empty sections are left out; {} = no change."""
    A, B = _leaves(a or {}, "", {}), _leaves(b or {}, "", {})
    A.pop("", None); B.pop("", None)                                # an empty snapshot has no leaves
    ch = {}
    for k in A.keys() & B.keys():
        x, y = A[k], B[k]
        if x == y: continue
        if isinstance(x, list) and isinstance(y, list):
            ch[k] = {"+": [v for v in y if v not in x], "-": [v for v in x if v not in y]}
            if not ch[k]["+"] and not ch[k]["-"]: ch[k] = [x, y]          # same items, other order / counts
        else: ch[k] = [x, y]
    add, rem = B.keys() - A.keys(), A.keys() - B.keys()
    inside = lambda k, others: any(o.startswith(k + ".") or o.startswith(k + "[") for o in others)
    # an empty container that got filled (or emptied) is shown by its leaves only
    out = {"changed": dict(sorted(ch.items())), "added": {k: B[k] for k in sorted(add) if not (B[k] in ({}, []) and inside(k, rem))},
           "removed": {k: A[k] for k in sorted(rem) if not (A[k] in ({}, []) and inside(k, add))}}
    return {k: v for k, v in out.items() if v}


def diff_brief(d, limit=800, cut=80):
    """a state_diff -> one compact text line for a prompt / an answer ("no change" when empty), at most `limit` chars"""
    if not d: return "no change"
    j = lambda v: (s if len(s := json.dumps(v, ensure_ascii=False, default=str)) <= cut else s[:cut - 3] + "...")
    parts = []
    for k, v in (d.get("changed") or {}).items():
        parts.append(f"{k}: " + (" ".join([*(f"+{j(x)}" for x in v["+"]), *(f"-{j(x)}" for x in v["-"])]) if isinstance(v, dict) else f"{j(v[0])} -> {j(v[1])}"))
    parts += [f"+{k} = {j(v)}" for k, v in (d.get("added") or {}).items()]
    parts += [f"-{k} (was {j(v)})" for k, v in (d.get("removed") or {}).items()]
    rank = lambda t: 0 if t.lstrip("+-").startswith("mod") else 1 if t.lstrip("+-").startswith("screen_text") else 2
    parts.sort(key=rank)                       # mod and on-screen text first: they survive the cut
    s = "; ".join(parts)
    return s if len(s) <= limit else s[:limit - 30].rsplit("; ", 1)[0] + f"; ... ({len(parts)} changes in all)"


# ---- the error text of a failed build / load (a gate that answers only {"rc": 124, "errors": 15} tells neither the
# agent's `game build` nor the evaluation what broke). load_check() / build() put it FIRST in their log (gate_log);
# `game build` answers it as reason + errors, eval/gate.log keeps the whole text.
ERR_N, ERR_W = 40, 220
_STAMP = re.compile(r"^\s*(?:\[[^\]]*\d[^\]]*\]\s*|\d{4}-\d\d-\d\d[ T][\d:.,]+:?\s*|\d\d:\d\d:\d\d(?:[.,]\d+)?:?\s+)+")
_ANSI = re.compile(r"\x1b\[[0-9;]*[A-Za-z]")


def error_lines(text, pat, ignore=None, n=ERR_N, after=0, width=ERR_W):
    """the first n relevant error lines of a log: lines matching `pat` (+ the `after` lines that follow, e.g. a traceback), not
    `ignore`; ANSI codes and leading timestamps removed; deduplicated in log order (a repeat adds " (xN)"); each cut to `width`;
    a last line counts the distinct lines left out"""
    pat = re.compile(pat) if isinstance(pat, str) else pat
    ign = re.compile(ignore) if isinstance(ignore, str) else ignore
    L = (text or "").splitlines(); seen = {}; k = 0
    while k < len(L):
        if not pat.search(L[k]) or (ign and ign.search(L[k])): k += 1; continue
        for x in L[k:k + 1 + after]:
            s = _STAMP.sub("", _ANSI.sub("", x)).strip()
            if not s or (ign and ign.search(x)): continue
            s = s if len(s) <= width else s[:width - 3] + "..."
            seen[s] = seen.get(s, 0) + 1
        k += 1 + after
    out = [s + (f" (x{c})" if c > 1 else "") for s, c in seen.items()]
    return out[:n] + ([f"... {len(out) - n} more distinct error lines"] if len(out) > n else [])


def gate_log(errors=(), reason=None, detail=""):
    """a build / load_check log: the reason of a failure and the error lines first, then the adapter's detail (a reader may cut
    it): "REASON: ...\\nERRORS (k):\\n  line\\n---\\ndetail" """
    h = (f"REASON: {' / '.join(str(reason).splitlines())}\n" if reason else "") + \
        (f"ERRORS ({len(errors)}):\n" + "".join(f"  {l}\n" for l in errors) if errors else "")
    return h + ("---\n" if h else "") + (detail or "")


def read_gate_log(text):
    """gate_log() text -> {"reason", "errors", "detail"}; a log without that head is all detail"""
    t = text or ""; r = {"reason": None, "errors": [], "detail": t}
    if not t.startswith(("REASON: ", "ERRORS (")) or "\n---\n" not in t: return r
    head, _, r["detail"] = t.partition("\n---\n")
    for l in head.splitlines():
        if l.startswith("REASON: "): r["reason"] = l[8:]
        elif l.startswith("  "): r["errors"].append(l[2:])
    return r


def auto_shot_points(steps, cap, every):
    """{step index: [None] (after the step) | [seconds into a wait, ...]}: candidate points after every non-shot step and every
    `every` s inside a wait, `cap` of them chosen evenly over the trace (the agent's own shot steps are not counted)"""
    pts = []
    for i, s in enumerate(steps):
        if s["do"] == "shot": continue
        if s["do"] == "wait" and float(s.get("seconds") or 0) > every:
            n = int(min(float(s["seconds"]), MAX_WAIT_S) // every); pts += [(i, k * every) for k in range(1, n + 1)]
        elif s["do"] != "wait": pts.append((i, None))
    if cap <= 0: pts = []
    elif len(pts) > cap: pts = [pts[round(j * (len(pts) - 1) / max(cap - 1, 1))] for j in range(cap)] if cap > 1 else [pts[len(pts) // 2]]
    out = {}
    for i, at in dict.fromkeys(pts): out.setdefault(i, []).append(at)
    return out


class GameAdapter:
    name = "base"
    title = None                   # display name in the agent brief
    screen = (1024, 768)
    extra_help = ""                # extra `game` commands of this game (lines for `game help`)

    def __init__(self, seat):
        self.seat = seat

    # ---- to implement per game
    def requirements(self) -> dict: raise NotImplementedError
    def provision(self, inst) -> dict: raise NotImplementedError
    def dev_kit(self) -> Path: return Path(importlib.import_module(type(self).__module__).__file__).parent / "devkit"
    def install_mod(self, inst, mod_dir) -> dict: raise NotImplementedError
    def build(self, inst) -> tuple: raise NotImplementedError          # (ok, log text)
    def start(self, inst) -> tuple: raise NotImplementedError          # (ok, log text)
    def stop(self, inst): raise NotImplementedError
    def state(self, inst) -> dict: raise NotImplementedError
    def display(self, inst) -> str: return self.seat.display
    def probe_runner(self): raise NotImplementedError                  # object with run(names, mod_dirs) -> data
    def compare(self, spec, base, got): raise NotImplementedError       # pool `probe:` spec -> (ok | None, detail)
    def version(self) -> dict: raise NotImplementedError
    def log_errors(self, inst) -> dict: return {}
    def paths(self, inst) -> dict: return {}                           # useful sandbox paths for the dev kit (`game info`)
    def attach(self, inst, mod_dir): pass                              # which mod the instance holds (None = vanilla); each `game` call is a new process
    def dev_command(self, inst, cmd, args, mod, wd): return None       # game-specific `game` command -> answer dict, None = unknown
    def inst_dir(self, inst): return (getattr(self, "idir", None) or getattr(self, "mdir"))(inst)   # cwd of `game sh`

    def reset(self, inst):
        self.stop(inst); return self.start(inst)

    # ---- shared: input, screenshots, record, replay
    def _x(self, inst, cmd, timeout=180):
        return self.seat.exec(f"export DISPLAY={self.display(inst)}; {cmd}", timeout=timeout)

    def input(self, inst, s):
        k = s["do"]; q = shlex.quote
        if k == "key": return self._x(inst, " ; ".join(f"xdotool key {q(x)}" for x in str(s["keys"]).split()))
        if k == "hold":
            keys = s["keys"] if isinstance(s["keys"], list) else str(s["keys"]).split()
            sec = min(float(s["seconds"]), MAX_WAIT_S)
            return self._x(inst, "; ".join([*(f"xdotool keydown {q(x)}" for x in keys), f"sleep {sec}", *(f"xdotool keyup {q(x)}" for x in keys)]))
        if k == "click": return self._x(inst, f"xdotool mousemove {int(s['x'])} {int(s['y'])} sleep 0.1 click {_button(s.get('button', 1))}")
        if k == "move": return self._x(inst, f"xdotool mousemove {int(s['x'])} {int(s['y'])}")
        if k == "look": return self._x(inst, f"xdotool mousemove_relative -- {int(s['dx'])} {int(s['dy'])}")
        if k == "drag":
            b = _button(s.get("button", 1))
            return self._x(inst, f"xdotool mousemove {int(s['x1'])} {int(s['y1'])} mousedown {b} sleep 0.2 mousemove {int(s['x2'])} {int(s['y2'])} sleep 0.2 mouseup {b}")
        if k == "type": return self._x(inst, f"xdotool type --delay 60 {q(str(s['text']))}")
        if k == "wait": time.sleep(min(float(s["seconds"]), MAX_WAIT_S)); return None
        return None

    def screenshot(self, inst, host_path):
        """PNG of the instance's display -> host_path (single frame: an image, not a video)"""
        tmp = f"{self.seat.home}/.gab_shot_{inst}_{os.getpid()}_{time.time_ns() % 10**9}.png"   # unique: seats share the home dir
        w, h = self.screen
        r = self._x(inst, f"ffmpeg -loglevel error -y -f x11grab -video_size {w}x{h} -i $DISPLAY -frames:v 1 {tmp}", timeout=60)
        if r.returncode: return None
        ok = self.seat.copy_out(tmp, host_path); self.seat.exec(f"rm -f {tmp}")
        return str(host_path) if ok else None

    def record(self, inst, seconds, host_path, fps=2):
        """grab PNG frames in the sandbox, encode the mp4 on the host"""
        d = f"{self.seat.home}/.gab_rec_{inst}"; w, h = self.screen
        self._x(inst, f"rm -rf {d}; mkdir -p {d}; for i in $(seq -w 1 {int(seconds * fps)}); do "
                      f"ffmpeg -loglevel error -y -f x11grab -video_size {w}x{h} -i $DISPLAY -frames:v 1 {d}/f$i.png; sleep {1 / fps}; done",
                timeout=seconds * 4 + 60)
        with tempfile.TemporaryDirectory() as t:
            self.seat.copy_out(d, Path(t) / "f")
            subprocess.run(["ffmpeg", "-loglevel", "error", "-y", "-framerate", str(fps), "-pattern_type", "glob", "-i", f"{t}/f/*.png",
                            "-c:v", "libx264", "-pix_fmt", "yuv420p", str(host_path)])
        self.seat.exec(f"rm -rf {d}")
        return str(host_path)

    def read_state(self, inst):
        """state() that never raises (an error snapshot instead)"""
        try: return self.state(inst)
        except Exception as e: return {"running": False, "error": str(e)[:200]}

    def replay(self, inst, trace, out_dir, fresh=True):
        """play a trace on the instance (fresh start unless trace.start == 'continue') -> {shots: [paths], log: [...], ok,
        state, timeline}. Screenshots: every `shot` step of the agent, always; plus automatic ones sampled
        evenly over the other steps (after a step, and every `replay_wait_shot_s` inside a long wait), at most
        `replay_auto_shots` per trace; and one at the end. Automatic files are named NNN_auto.png, the agent's NNN_<name>.png.
        State (STATE_SPEC): read at every screenshot point (and once after the start), never between: each timeline entry
        {step, actions (the steps since the previous snapshot), screenshot, diff (state_diff vs the previous snapshot), snapshot (file under state/)}; replay.json
        holds log, final state and timeline."""
        out = Path(out_dir); out.mkdir(parents=True, exist_ok=True); log, shots, timeline = [], [], []
        probs = check_trace(trace)
        if probs: return {"ok": False, "log": probs, "shots": [], "timeline": []}
        if fresh and trace.get("start", "fresh") == "fresh":
            ok, l = self.reset(inst)
            if not ok: return {"ok": False, "log": [f"start failed: {l[-300:]}"], "shots": [], "timeline": []}
        auto = auto_shot_points(trace["steps"], int(config.get("replay_auto_shots")), float(config.get("replay_wait_shot_s")))
        (out / "state").mkdir(exist_ok=True)
        prev = [self.read_state(inst)]; (out / "state" / "start.json").write_text(json.dumps(prev[0], indent=1, default=str))
        acts = []                                         # steps since the previous snapshot
        def snap(name, i):
            p = self.screenshot(inst, out / name)
            if p: shots.append(p)
            st = self.read_state(inst); sf = out / "state" / (Path(name).stem + ".json")
            sf.write_text(json.dumps(st, indent=1, default=str))
            timeline.append({"step": i, "actions": list(acts), "screenshot": p and Path(p).name, "diff": state_diff(prev[0], st),
                             "snapshot": f"state/{sf.name}"})
            prev[0] = st; acts.clear()
        for i, s in enumerate(trace["steps"]):
            t0 = time.time()
            try:
                if s["do"] == "shot": snap(f"{i:03d}_{str(s.get('name') or 'shot').replace('/', '_')[:40]}.png", i)
                elif s["do"] == "wait" and auto.get(i):          # a long wait: screenshots inside it, same total time
                    total = min(float(s["seconds"]), MAX_WAIT_S)
                    for k, at in enumerate(auto[i]):
                        time.sleep(max(0.0, at - (time.time() - t0))); acts.append({**s, "at_s": at}); snap(f"{i:03d}_auto{k}.png", i)
                    time.sleep(max(0.0, total - (time.time() - t0))); acts.append({**s, "at_s": total})
                else:
                    self.input(inst, s); acts.append(s)
                    if auto.get(i) == [None]: snap(f"{i:03d}_auto.png", i)
                log.append({"i": i, "step": s, "s": round(time.time() - t0, 2)})
            except Exception as e:
                log.append({"i": i, "step": s, "error": str(e)[:200]})
        snap("zz_end.png", len(trace["steps"]))
        st = prev[0]
        (out / "replay.json").write_text(json.dumps({"log": log, "state": st, "timeline": timeline}, indent=1, default=str))
        return {"ok": True, "log": log, "shots": shots, "state": st, "timeline": timeline}


def vglrun():
    """the VirtualGL wrapper of the 3D games: `vglrun -d <config vglrun_device>` (egl = headless GPU rendering; an X display
    such as :99 where the host renders through an X server)"""
    from .. import config
    return f"vglrun -d {config.get('vglrun_device') or 'egl'}"
