"""Flare (empyrean campaign) regression probes. Ported from the original probes_flare.py (verified in game
2026-10-30, Flare 1.15 = upstream AppImage flare-linux64-v1.15: engine + fantasycore + empyrean_campaign 1.15; A/A + negative
checks in games/flare/pool.yaml). Changes: the host side runs through a gab Seat (any host backend) instead of docker exec on
a fixed container; install root / display / host screenshot dir are arguments; run dirs get a timestamp (two evaluations on
one sandbox never share a dir); no host CLI. The module itself stays stdlib-only at import time: it is copied into the sandbox
and run there as the per-run driver (`python3 probes.py driver RUNDIR SPEC` / `datadump MODS_JSON ROOT`).

Bridge (no engine changes, no gameplay content):
  * crafted save slots: Flare saves are plain text (saves/empyrean/<slot>/avatar.txt: class, xp, build, equipped,
    carried, spawn=map,x,y, actionbar, powers, campaign statuses). A probe writes one, starts
    `flare --load-slot=1 [--load-script=scripts/flareprobe/x.txt]` and reads every later save back.
  * a tiny harness mod "flareprobe" (enabled last): engine/misc.txt APPEND save_hpmp/save_onexit/save_pos_onexit (so a save
    also records HP/MP and quitting records the position), and scripts/flareprobe/*.txt = timed `save_game=true` events
    (state snapshots every 100 ms) and the enemy spawn of the fight probes.
  * the developer console (settings dev_mode=1, F5): `exec <event components>` runs any event at any time. Typed with xdotool.
  * real input with xdotool on a private Xvfb (1280x720); screenshots (ffmpeg x11grab, one frame) for the UI panels.
Mods = Flare mod dirs (<name>/settings.txt + data): copied to <user>/mods and listed in mods.txt after empyrean_campaign
(order: fantasycore, empyrean_campaign, <mods...>, flareprobe).
"""
import hashlib, json, os, re, shutil, signal, subprocess, sys, threading, time
from pathlib import Path

ROOT = "/home/player/oss-flare"                                         # default install: ROOT/squashfs-root (AppImage extracted)
VERSION = "Flare 1.15 (AppImage flare-linux64-v1.15; fantasycore 1.15, empyrean_campaign 1.15)"
BASE_MODS = ["fantasycore", "empyrean_campaign"]

SETTINGS = """fullscreen=0
resolution_w=1280
resolution_h=720
music_volume=0
sound_volume=0
renderer=sdl_hardware
vsync=0
max_fps=60
language=en
dev_mode=1
dev_hud=0
mouse_move=0
mouse_aim=1
auto_loot=1
setup_language=1
setup_mousemove=1
statbar_autohide=0
subtitles=0
"""
PROBE_MOD_SETTINGS = "description=GameAnything probe harness (timed saves; no gameplay content)\ngame=flare-game\nversion=1.0\n"
PROBE_MISC = "APPEND\nsave_hpmp=true\nsave_onload=true\nsave_onexit=true\nsave_pos_onexit=true\n"


def _snap_script(seconds=30, step_ms=100, pre=""):
    return pre + "".join(f"[event]\ndelay={i * step_ms}ms\nsave_game=true\n\n" for i in range(1, int(seconds * 1000 / step_ms) + 1))


PROBE_SCRIPTS = {
    "snap30.txt": _snap_script(30),
    "fight.txt": _snap_script(30, pre="[event]\nspawn=zombie_lvl2,22,20\n\n"),
}

# crafted save: a fresh Brute (same as a new game's), statuses that skip the arrival tutorial / backstory
SAVE_TMPL = """## flare-engine save file ##
name=Probe
permadeath=0
option=female,head_long,images/portraits/female09.png
class=Brute,Exiled
xp={xp}
{hpmp}build=5,1,1,2
equipped_quantity=1,0,0,0,1,1,0,1,1,1,1,0,0,0,0,0,0,1,1,1
equipped=7,0,0,0,8,11,0,4,5,6,7,0,0,0,0,0,0,4,5,6
active_equipment_set=1
carried_quantity={cq}
carried={ci}
spawn={spawn}
actionbar=162,163,0,0,0,0,0,0,0,0,1,7
transformed=
powers=7,1,42,116
campaign={campaign}
time_played=0
engine_version=1.15
questlog_dismissed=1
stash_tab=0
"""


def make_save(spawn="maps/arrival.txt,20,20", carried=((2, 5), (3, 5)), campaign="empyrean_backstory,emp_arrival", xp=0, hpmp=None):
    return SAVE_TMPL.format(spawn=spawn, cq=",".join(str(q) for _, q in carried), ci=",".join(str(i) for i, _ in carried),
                            campaign=campaign, xp=xp, hpmp=f"hpmp={hpmp[0]},{hpmp[1]}\n" if hpmp else "")


# ====================================================================================== sandbox side (stdlib only)
def parse_save(text):
    d = {}
    for line in text.splitlines():
        if "=" in line and not line.startswith("#"):
            k, v = line.split("=", 1)
            d[k.strip()] = v.strip()
    return d


def driver(run, spec_path):
    """One Flare run: fresh HOME, mods, optional crafted save, private Xvfb, timed steps, save snapshots -> run/result.json"""
    run = Path(run).resolve()
    spec = json.loads(Path(spec_path).read_text())
    root = Path(spec.get("root", ROOT))
    if run.exists():
        shutil.rmtree(run)
    home = run / "HOME"
    conf, user = home / ".config/flare", home / ".local/share/flare"
    for p in (conf, user / "mods", user / "saves/empyrean/1"):
        p.mkdir(parents=True, exist_ok=True)
    (conf / "settings.txt").write_text(SETTINGS)
    names = []
    for m in spec.get("mods", []):
        m = Path(m)
        shutil.copytree(m, user / "mods" / m.name)
        names.append(m.name)
    shutil.copytree(root / "bridge/flareprobe", user / "mods/flareprobe")
    names.append("flareprobe")
    (conf / "mods.txt").write_text("\n".join(BASE_MODS + names) + "\n")
    saves = user / "saves/empyrean"
    if spec.get("saves_from"):                               # continue from an earlier run's saves (save/load probe)
        shutil.rmtree(saves)
        shutil.copytree(Path(spec["saves_from"]) / "HOME/.local/share/flare/saves/empyrean", saves)
    save = saves / "1/avatar.txt"
    if spec.get("save"):
        save.write_text(spec["save"])
    elif not spec.get("saves_from"):
        shutil.rmtree(saves)                                 # new game: no save slot at all

    dn = int(spec.get("display", 151))
    disp = f":{dn}"
    xv = subprocess.Popen(["Xvfb", disp, "-screen", "0", "1280x720x24", "-nolisten", "tcp"],
                          stdout=open(run / "xvfb.log", "w"), stderr=subprocess.STDOUT)
    for _ in range(80):
        if os.path.exists(f"/tmp/.X11-unix/X{dn}"):
            break
        time.sleep(0.1)
    env = dict(os.environ, HOME=str(home), XDG_CONFIG_HOME=str(home / ".config"), XDG_DATA_HOME=str(home / ".local/share"),
               DISPLAY=disp, SDL_AUDIODRIVER="dummy")
    xenv = dict(os.environ, DISPLAY=disp)
    logf = open(run / "stdout.log", "w")
    t0 = time.time()
    fl = subprocess.Popen([str(root / "squashfs-root/AppRun"), "--no-audio"] + spec.get("args", []), cwd=run, env=env,
                          stdout=logf, stderr=subprocess.STDOUT)
    snaps, stop = [], threading.Event()

    def watch():
        last = None
        while not stop.is_set():
            try:
                st = save.stat()
                key = (st.st_mtime_ns, st.st_size)
                if key != last:
                    time.sleep(0.02)
                    txt = save.read_text()
                    if "stash_tab=" in txt:                 # complete file (the last line)
                        last = key
                        snaps.append({"t": round(time.time() - t0, 3), "save": parse_save(txt)})
            except OSError:
                pass
            time.sleep(0.03)
    th = threading.Thread(target=watch, daemon=True)
    th.start()

    def x(*a):
        subprocess.run(["xdotool"] + [str(v) for v in a], env=xenv, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)

    def log_text():
        logf.flush()
        return (run / "stdout.log").read_text(errors="replace")

    def console(cmd):
        x("key", "F5"); time.sleep(0.5)
        x("type", "--delay", "40", cmd); time.sleep(0.2)
        x("key", "Return"); time.sleep(0.4)

    marks, shots, notes = {}, {}, []
    deadline = t0 + float(spec.get("timeout", 120))
    try:
        for st in spec.get("steps", []):
            if time.time() > deadline or fl.poll() is not None:
                notes.append(f"stopped before step {st}: {'timeout' if fl.poll() is None else 'flare exited'}")
                break
            op = st[0]
            if op == "sleep":
                time.sleep(st[1])
            elif op == "wait_log":                       # ["wait_log", regex, timeout]
                end = time.time() + st[2]
                while time.time() < end and not re.search(st[1], log_text()) and fl.poll() is None:
                    time.sleep(0.1)
                if not re.search(st[1], log_text()):
                    notes.append(f"wait_log timeout {st[1]}")
            elif op == "wait_count":                     # ["wait_count", regex, n, timeout]
                end = time.time() + st[3]
                while time.time() < end and len(re.findall(st[1], log_text())) < st[2] and fl.poll() is None:
                    time.sleep(0.1)
                if len(re.findall(st[1], log_text())) < st[2]:
                    notes.append(f"wait_count timeout {st[1]} x{st[2]}")
            elif op == "key_until":                      # ["key_until", key, regex, n, timeout] -- press key every 2 s until regex x n in log
                end = time.time() + st[4]
                while time.time() < end and len(re.findall(st[2], log_text())) < st[3] and fl.poll() is None:
                    x("key", st[1])
                    t1 = time.time() + 2
                    while time.time() < t1 and len(re.findall(st[2], log_text())) < st[3]:
                        time.sleep(0.1)
                if len(re.findall(st[2], log_text())) < st[3]:
                    notes.append(f"key_until timeout {st[2]} x{st[3]}")
            elif op == "con":                            # developer console: F5, type, Return, F5
                console(st[1]); x("key", "F5"); time.sleep(0.4)
            elif op == "save":                           # console save_game, wait for the new snapshot
                n0 = len(snaps)
                console("exec save_game=true")
                end = time.time() + (st[1] if len(st) > 1 else 6)
                while time.time() < end and len(snaps) <= n0:
                    time.sleep(0.05)
                if len(snaps) <= n0:
                    notes.append("save: no new snapshot")
                time.sleep(0.2); x("key", "F5"); time.sleep(0.4)
            elif op == "mark":
                marks[st[1]] = {"t": round(time.time() - t0, 3), "nsnaps": len(snaps)}
            elif op == "key":
                x("key", "--delay", "120", *st[1:])
            elif op == "keydown":
                x("keydown", st[1])
            elif op == "keyup":
                x("keyup", st[1])
            elif op == "move":
                x("mousemove", st[1], st[2])
            elif op == "click":                          # ["click", x, y, button=1]
                x("mousemove", st[1], st[2]); time.sleep(0.15)
                x("click", st[3] if len(st) > 3 else 1)
            elif op == "ctrlclick":
                x("mousemove", st[1], st[2]); time.sleep(0.3)
                x("keydown", "ctrl"); time.sleep(0.2); x("click", 1); time.sleep(0.2); x("keyup", "ctrl")
            elif op == "mousedown":
                x("mousedown", st[1] if len(st) > 1 else 1)
            elif op == "mouseup":
                x("mouseup", st[1] if len(st) > 1 else 1)
            elif op == "type":
                x("type", "--delay", "60", st[1])
            elif op == "shot":
                p = run / f"{st[1]}.png"
                subprocess.run(["ffmpeg", "-loglevel", "error", "-y", "-f", "x11grab", "-video_size", "1280x720", "-i", disp,
                                "-frames:v", "1", str(p)], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
                shots[st[1]] = str(p)
            else:
                notes.append(f"unknown op {op}")
    finally:
        alive = fl.poll() is None
        n_before_quit = len(snaps)
        if alive:
            fl.send_signal(signal.SIGTERM)               # SDL quit -> save on exit (save_pos_onexit from the harness mod)
            try:
                fl.wait(15)
            except subprocess.TimeoutExpired:
                fl.kill(); fl.wait()
                notes.append("flare killed after SIGTERM timeout")
        time.sleep(0.3)
        stop.set(); th.join(2)
        try:
            txt = save.read_text()
            if not snaps or parse_save(txt) != snaps[-1]["save"]:
                snaps.append({"t": round(time.time() - t0, 3), "save": parse_save(txt)})
        except OSError:
            pass
        xv.terminate()
        try:
            xv.wait(5)
        except subprocess.TimeoutExpired:
            xv.kill()
        logf.close()
    log = (run / "stdout.log").read_text(errors="replace")
    res = {"alive_at_end": alive, "rc": fl.returncode, "wall": round(time.time() - t0, 2), "snaps": snaps,
           "n_snaps_before_quit": n_before_quit, "marks": marks, "shots": shots, "notes": notes,
           "errors": [l for l in log.splitlines() if l.startswith("ERROR")],
           "crash": bool(re.search(r"Segmentation|Aborted|terminate called|core dumped", log)) or not alive,
           "maps": re.findall(r"Map: Loading map '([^']+)'", log)}
    (run / "result.json").write_text(json.dumps(res))
    print(json.dumps({k: res[k] for k in ("alive_at_end", "rc", "wall", "notes", "maps")} | {"n_snaps": len(snaps)}))


DATA_DIRS = ("engine", "items", "powers", "enemies", "npcs", "loot", "quests", "maps", "scripts", "books", "cutscenes", "tilesetdefs",
             "menus", "animations")


def datadump(mods_json, root=ROOT):
    """Effective text definitions of every vanilla file under DATA_DIRS with the mod stack applied (Flare's own rule: the
    last mod that has a file wins; a file starting with APPEND is added to the one before it)."""
    mods = [Path(root) / "squashfs-root/usr/share/flare/mods" / m for m in BASE_MODS] + [Path(m) for m in json.loads(mods_json)]
    vanilla = set()
    for m in mods[:len(BASE_MODS)]:
        for d in DATA_DIRS:
            for p in (m / d).rglob("*.txt") if (m / d).is_dir() else []:
                vanilla.add(str(p.relative_to(m)))
    out = {}
    for rel in sorted(vanilla):
        parts = []
        for m in reversed(mods):
            p = m / rel
            if p.is_file():
                t = p.read_text(errors="replace")
                parts.insert(0, t)
                if not t.lstrip().startswith("APPEND"):
                    break
        out[rel] = "\n".join(re.sub(r"^\s*APPEND\s*\n", "", t) for t in parts)
    print(json.dumps(out))


# ====================================================================================== host side
def _ints(s):
    return [int(float(v)) for v in (s or "").split(",") if v.strip() != ""]


def items_of(save):
    """carried -> {item_id: quantity}"""
    out = {}
    for i, q in zip(_ints(save.get("carried")), _ints(save.get("carried_quantity"))):
        if i and q:
            out[i] = out.get(i, 0) + q
    return out


def hpmp(save):
    v = (save.get("hpmp") or "").split(",")
    return (float(v[0]), float(v[1])) if len(v) == 2 else (None, None)


def pos(save):
    sp = (save.get("spawn") or "").split(",")
    return (sp[0], int(sp[1]), int(sp[2])) if len(sp) == 3 else (None, None, None)


HERO = (640, 355)        # hero's feet on screen (1280x720 window, map tile 192x96 rendered at 0.75 -> 144x72 px)


def tile_px(dx, dy):
    return HERO[0] + 72 * (dx - dy), HERO[1] + 36 * (dx + dy)


class FLProbes:
    ALL = ["data", "newgame", "move", "transition", "fight", "powers", "potion", "loot", "town", "equip", "levelup", "save_load"]

    def __init__(self, seat, root=ROOT, mods=(), label="vanilla", display=151, log=print, shots_dir="/tmp/flprobe_shots"):
        self.seat, self.root, self.label, self.dn, self.log = seat, root, label, display, log
        self.base = f"{root}/runs/{label}-{time.strftime('%m%d-%H%M%S')}"
        self.shots = Path(shots_dir) / Path(self.base).name
        self.mods_host = list(mods)
        self.mods = []
        self.runs = {}

    # ---- plumbing (gab Seat)
    def sh(self, cmd, timeout=600):
        return self.seat.exec(cmd, timeout=timeout).stdout

    def put(self, path, text):
        """write a sandbox file atomically, and only when it changed (several instances may set up at the same time)"""
        t = f"{path}.tmp.{os.getpid()}.{time.time_ns()}"
        self.seat.put(t, text)
        self.sh(f"if cmp -s {t} {path}; then rm -f {t}; else mv -f {t} {path}; fi")

    def setup(self):
        """driver (this file) + harness mod into the sandbox; the trial's mods into <base>/mods"""
        R = self.root
        self.put(f"{R}/bridge/probes_flare.py", Path(__file__).read_text())
        self.put(f"{R}/bridge/flareprobe/settings.txt", PROBE_MOD_SETTINGS)
        self.put(f"{R}/bridge/flareprobe/engine/misc.txt", PROBE_MISC)
        for n, t in PROBE_SCRIPTS.items():
            self.put(f"{R}/bridge/flareprobe/scripts/flareprobe/{n}", t)
        self.sh(f"rm -rf {self.base} && mkdir -p {self.base}/mods")
        for m in self.mods_host:
            name = Path(m).name
            if str(m).startswith(R + "/"): self.sh(f"cp -r {m} {self.base}/mods/{name}")
            else: self.seat.copy_in(m, f"{self.base}/mods/{name}")
            self.mods.append(f"{self.base}/mods/{name}")

    def game(self, name, steps, save=None, script=None, timeout=120, saves_from=None):
        spec = {"root": self.root, "display": self.dn, "mods": self.mods, "save": save, "steps": steps, "timeout": timeout,
                "args": ["--load-slot=1"] * bool(save or saves_from) + ([f"--load-script=scripts/flareprobe/{script}"] if script else [])}
        if saves_from:
            spec["saves_from"] = f"{self.base}/{saves_from}"
        rd = f"{self.base}/{name}"
        self.seat.put(f"{self.base}/{name}.spec.json", json.dumps(spec))
        t = time.time()
        out = self.sh(f"python3 {self.root}/bridge/probes_flare.py driver {rd} {self.base}/{name}.spec.json", timeout=timeout + 120)
        res = json.loads(self.sh(f"cat {rd}/result.json") or "{}")
        self.runs[name] = res
        self.log(f"  [{self.label}] run {name}: {out.strip()[:200]} ({time.time() - t:.0f}s)")
        return res

    def fetch_shot(self, run, shot, dest):
        """copy one screenshot to the host (for image checks)"""
        Path(dest).parent.mkdir(parents=True, exist_ok=True)
        self.seat.copy_out(f"{self.base}/{run}/{shot}.png", dest)
        return str(dest) if Path(dest).exists() and Path(dest).stat().st_size > 1000 else None

    @staticmethod
    def last(res, before_quit=False):
        s = res.get("snaps") or []
        if before_quit:
            s = s[:res.get("n_snaps_before_quit", len(s))]
        return s[-1]["save"] if s else {}

    @staticmethod
    def snaps_between(res, a, b):
        m = res.get("marks", {})
        t0, t1 = m.get(a, {}).get("t", 0), m.get(b, {}).get("t", 1e9)
        return [s for s in res.get("snaps", []) if t0 <= s["t"] <= t1]

    # ---- probes
    def data(self):
        txt = self.sh(f"python3 {self.root}/bridge/probes_flare.py datadump '{json.dumps(self.mods)}' {self.root}", timeout=300)
        files = json.loads(txt)
        out = {}
        for rel, t in files.items():
            if rel.startswith("maps/"):
                out[rel] = {"sha1": hashlib.sha1(t.encode()).hexdigest()}
            else:
                out[rel] = _sections(t)
        return {"files": out, "n_files": len(out)}

    def newgame(self):
        steps = [["wait_log", r"Loading cutscene 'cutscenes/intro.txt'", 40], ["sleep", 4], ["shot", "title"],
                 ["click", 830, 498], ["sleep", 2.5], ["click", 757, 168], ["sleep", 0.5], ["shot", "create"],
                 ["click", 350, 690], ["wait_log", r"Loading cutscene 'cutscenes/backstory.txt'", 40], ["sleep", 2],
                 ["key_until", "Escape", r"Loading map 'maps/arrival.txt'", 2, 40], ["sleep", 4], ["shot", "ingame"]]
        r = self.game("newgame", steps, timeout=150)
        s = self.last(r)
        res = {k: s.get(k) for k in ("class", "xp", "build", "equipped", "actionbar", "powers", "active_equipment_set")}
        res.update(carried=items_of(s), campaign=sorted((s.get("campaign") or "").split(",")), map=pos(s)[0],
                   maps=r.get("maps"), hpmp=s.get("hpmp"), notes=r.get("notes"))
        return res

    def move(self):
        """best of two tries (the one that moved farther): under pod load a key press can start late and a walk loses tiles
        (gab selfcheck 2026-10-30, load ~11: east 10 -> 7 tiles once); a slower hero is slower in both tries"""
        def far(o): return sum(abs((o.get(k) or {}).get("dx") or 0) + abs((o.get(k) or {}).get("dy") or 0) for k in ("east", "nw"))
        a = self._move("move"); b = self._move("move2")
        return max((a, b), key=far)

    def _move(self, run):
        # Perdition Harbor (town, no enemies): east along row 22 (S+D = +x), then NW from (30,19) (W = -x -y); 10 Hz save snapshots
        steps = [["wait_log", "Loading map", 40], ["sleep", 2.5], ["mark", "e0"], ["keydown", "s"], ["keydown", "d"], ["sleep", 1.5],
                 ["keyup", "s"], ["keyup", "d"], ["mark", "e1"], ["sleep", 1], ["mark", "e2"],
                 ["con", "exec intramap=30,19"], ["sleep", 1], ["mark", "w0"], ["keydown", "w"], ["sleep", 1.5], ["keyup", "w"],
                 ["mark", "w1"], ["sleep", 1], ["mark", "w2"]]
        r = self.game(run, steps, save=make_save("maps/perdition_harbor.txt,3,22"), script="snap30.txt", timeout=90)
        out = {"notes": r.get("notes")}
        for seg, a, b, c in (("east", "e0", "e1", "e2"), ("nw", "w0", "w1", "w2")):
            ss = self.snaps_between(r, a, c)
            if len(ss) < 3:
                out[seg] = {"error": "no snapshots"}
                continue
            p0, p1 = pos(ss[0]["save"]), pos(ss[-1]["save"])
            moving = [(s["t"], pos(s["save"])) for s in ss]
            # speed: tiles moved between the first and the last snapshot whose position changed, over that time
            ch = [i for i in range(1, len(moving)) if moving[i][1] != moving[i - 1][1]]
            if len(ch) >= 2:
                (ta, pa), (tb, pb) = moving[ch[0]], moving[ch[-1]]
                dist = ((pb[1] - pa[1]) ** 2 + (pb[2] - pa[2]) ** 2) ** 0.5
                speed = round(dist / (tb - ta), 2) if tb > ta else None
            else:
                speed = None
            out[seg] = {"from": p0[1:], "to": p1[1:], "dx": p1[1] - p0[1], "dy": p1[2] - p0[2], "speed": speed}
        before_w0 = [s for s in r.get("snaps", []) if s["t"] <= r.get("marks", {}).get("w0", {}).get("t", -1)]
        out["teleport_to"] = list(pos(before_w0[-1]["save"])[1:]) if before_w0 else None
        return out

    def transition(self):
        steps = [["wait_log", "Loading map", 40], ["sleep", 3], ["keydown", "s"], ["keydown", "d"], ["sleep", 3], ["keyup", "s"],
                 ["keyup", "d"], ["wait_log", "river_trail", 10], ["sleep", 3], ["save"], ["mark", "river"],
                 ["keydown", "w"], ["keydown", "a"], ["sleep", 2.2], ["keyup", "w"], ["keyup", "a"],
                 ["wait_count", r"Loading map 'maps/arrival.txt'", 2, 15], ["sleep", 2]]
        r = self.game("transition", steps, save=make_save("maps/arrival.txt,35,34"), timeout=90)
        at_river = [s for s in r.get("snaps", []) if s["t"] <= r.get("marks", {}).get("river", {}).get("t", 0)]
        return {"maps": r.get("maps"), "arrived": pos(at_river[-1]["save"]) if at_river else None,
                "back_map": pos(self.last(r))[0], "notes": r.get("notes")}

    def fight(self):
        # zombie lvl 2 (category zombie_lvl2) spawned 2 tiles east of the hero; 8 s passive, then hold the left mouse
        # button (Swing) on it
        ex, ey = tile_px(1, 0)
        steps = [["wait_log", "Loading map", 40], ["sleep", 1], ["mark", "p0"], ["sleep", 7], ["mark", "atk"], ["move", ex, ey - 25],
                 ["mousedown", 1], ["sleep", 8], ["mouseup", 1], ["mark", "end"], ["sleep", 1]]
        r = self.game("fight", steps, save=make_save(), script="fight.txt", timeout=90)
        return self._fight_result(r)

    def _fight_result(self, r):
        sn = [s for s in r.get("snaps", []) if s["save"].get("hpmp")]
        if not sn:
            return {"error": "no snapshots", "notes": r.get("notes")}
        t_atk = r.get("marks", {}).get("atk", {}).get("t", 0)
        hp = [(s["t"], hpmp(s["save"])[0]) for s in sn]
        hits = sum(1 for (t0, a), (t1, b) in zip(hp, hp[1:]) if b < a - 1)
        passive = [(t, h) for t, h in hp if t <= t_atk]
        kill_t = next((s["t"] for s in sn if int(s["save"].get("xp", 0)) > 0), None)
        return {"enemy_hits_taken": hits, "hit_before_attack": any(b < a - 1 for (_, a), (_, b) in zip(passive, passive[1:])),
                "min_hp": round(min(h for _, h in hp), 1), "killed": kill_t is not None,
                "time_to_kill": round(kill_t - t_atk, 2) if kill_t else None, "xp": int(self.last(r).get("xp", 0)),
                "hero_alive": min(h for _, h in hp) > 0, "notes": r.get("notes")}

    def powers(self):
        # right mouse = Brute power 7 (melee, 6 MP, bleeding) twice on the zombie, then left mouse until it dies
        ex, ey = tile_px(1, 0)
        steps = [["wait_log", "Loading map", 40], ["sleep", 5], ["mark", "atk"], ["move", ex, ey - 25], ["mark", "c1"],
                 ["click", ex, ey - 25, 3], ["sleep", 2.5], ["mark", "c2"], ["click", ex, ey - 25, 3], ["sleep", 2.5],
                 ["mark", "kill"], ["mousedown", 1], ["sleep", 6], ["mouseup", 1],
                 ["sleep", 1]]
        r = self.game("powers", steps, save=make_save(), script="fight.txt", timeout=90)
        sn = [s for s in r.get("snaps", []) if s["save"].get("hpmp")]
        if not sn:
            return {"error": "no snapshots", "notes": r.get("notes")}
        mp = [(s["t"], hpmp(s["save"])[1]) for s in sn]
        drops = [round(a - b) for (_, a), (_, b) in zip(mp, mp[1:]) if b < a - 0.5]     # regen between snapshots: whole MP
        return {"mp_costs": drops, "casts": len(drops), "xp": int(self.last(r).get("xp", 0)), "notes": r.get("notes")}

    def potion(self):
        # no enemies; the crafted save starts at 100 HP / 40 MP; Q = health potion (bar slot 1, power 162), E = mana potion
        steps = [["wait_log", "Loading map", 40], ["sleep", 3], ["mark", "pre"], ["sleep", 2], ["mark", "q"], ["key", "q"], ["sleep", 4],
                 ["mark", "e"], ["key", "e"], ["sleep", 4], ["mark", "end"]]
        r = self.game("potion", steps, save=make_save(hpmp=(100, 40)), script="snap30.txt", timeout=60)
        sn = [s for s in r.get("snaps", []) if s["save"].get("hpmp")]
        m = r.get("marks", {})
        def at(t):
            ss = [s for s in sn if s["t"] <= t]
            return ss[-1] if ss else None
        out = {"notes": r.get("notes")}
        for key, k0, k1, idx, item in (("health", "q", "e", 0, 2), ("mana", "e", "end", 1, 3)):
            a0, a1, p0 = at(m.get(k0, {}).get("t", -1)), at(m.get(k1, {}).get("t", -1)), at(m.get("pre", {}).get("t", -1))
            if not (a0 and a1 and p0):
                out[key] = {"error": "no snapshots"}; continue
            rate = (hpmp(a0["save"])[idx] - hpmp(p0["save"])[idx]) / max(a0["t"] - p0["t"], 0.1)     # natural regen per s
            gain = hpmp(a1["save"])[idx] - hpmp(a0["save"])[idx] - rate * (a1["t"] - a0["t"])
            out[key] = {"used": items_of(a0["save"]).get(item, 0) - items_of(a1["save"]).get(item, 0), "restored": round(gain, 1)}
        return out

    def loot(self):
        # gold (auto pickup) and a slingshot (click) dropped by an event next to the hero
        steps = [["wait_log", "Loading map", 40], ["sleep", 3], ["con", "exec loot=currency,fixed,50,50"], ["sleep", 2],
                 ["con", "exec loot=9,fixed,1,1"], ["sleep", 1.5], ["shot", "drop"]]
        for dx, dy in ((0, 0), (1, 0), (0, 1), (-1, 0), (0, -1), (1, 1), (-1, -1), (1, -1), (-1, 1)):
            px, py = tile_px(dx, dy)
            steps += [["click", px + 1, py + 17], ["sleep", 0.8]]
        steps += [["save"]]
        r = self.game("loot", steps, save=make_save(), timeout=90)
        it = items_of(self.last(r))
        return {"gold": it.get(1, 0), "picked_item": it.get(9, 0), "carried": it, "notes": r.get("notes")}

    def town(self):
        # Perdition Harbor: click Udana (NPC), topic "Talk" (6 lines, sets emp_krolan_init, "Quest added"), Trade: ctrl+click
        # buys the Dagger (vendor slot 1, 100 gold), ctrl+click sells the Slingshot (25 gold); quest log (L)
        steps = [["wait_log", "Loading map", 40], ["sleep", 3], ["click", 640, 170], ["sleep", 1.5], ["shot", "talker"],
                 ["click", 562, 433], ["sleep", 0.8]] + [["click", 967, 374], ["sleep", 0.6]] * 6 + \
                [["shot", "topics"], ["click", 568, 455], ["sleep", 1.5], ["shot", "vendor"], ["ctrlclick", 72, 130], ["sleep", 0.8],
                 ["ctrlclick", 1038, 93], ["sleep", 0.8], ["shot", "traded"], ["key", "Escape"], ["sleep", 0.5], ["key", "l"],
                 ["sleep", 1], ["shot", "questlog"], ["key", "l"], ["sleep", 0.5], ["save"]]
        r = self.game("town", steps, save=make_save("maps/perdition_harbor.txt,10,15", [(2, 5), (3, 5), (1, 500), (12, 1), (9, 1)]),
                      timeout=120)
        s = self.last(r)
        it = items_of(s)
        st = sorted((s.get("campaign") or "").split(","))
        out = {"npc_status_set": "emp_krolan_init" in st, "campaign": st, "gold": it.get(1, 0), "bought_dagger": it.get(8, 0),
               "slingshot_left": it.get(9, 0), "carried": it, "notes": r.get("notes")}
        for n in ("talker", "topics", "questlog"):
            out["shot_" + n] = self.fetch_shot("town", n, f"{self.shots}/town_{n}.png") if n in r.get("shots", {}) else None
        return out

    def equip(self):
        # right-click the Fighting Cloth Shirt (id 12, +25 max HP) in the inventory; max HP/MP read by restore + save
        steps = [["wait_log", "Loading map", 40], ["sleep", 3], ["con", "exec restore=hpmp"], ["save"], ["mark", "before"],
                 ["key", "i"], ["sleep", 1], ["click", 990, 93, 3], ["sleep", 1], ["shot", "inv"], ["key", "i"], ["sleep", 0.5],
                 ["con", "exec restore=hpmp"], ["save"], ["key", "c"], ["sleep", 1], ["shot", "char"], ["key", "c"]]
        r = self.game("equip", steps, save=make_save("maps/perdition_harbor.txt,10,15", [(2, 5), (3, 5), (1, 500), (12, 1), (9, 1)]),
                      timeout=90)
        before = [s for s in r.get("snaps", []) if s["t"] <= r.get("marks", {}).get("before", {}).get("t", 0) and s["save"].get("hpmp")]
        b, a = (before[-1]["save"] if before else {}), self.last(r, before_quit=True)
        return {"max_hpmp_before": hpmp(b), "max_hpmp_after": hpmp(a), "equipped_after": _norm_list("equipped", a.get("equipped")),
                "shot_char": self.fetch_shot("equip", "char", f"{self.shots}/equip_char.png") if "char" in r.get("shots", {}) else None,
                "notes": r.get("notes")}

    def levelup(self):
        steps = [["wait_log", "Loading map", 40], ["sleep", 3], ["con", "exec restore=hpmp"], ["save"], ["mark", "l1"]]
        for xp in (256, 512, 1024):            # -> 256 (level 2), 768 (level 3), 1792 (level 4)
            steps += [["con", f"exec reward_xp={xp}"], ["sleep", 0.8], ["con", "exec restore=hpmp"], ["save"], ["mark", f"x{xp}"]]
        steps += [["key", "c"], ["sleep", 1], ["shot", "char"], ["key", "c"]]
        r = self.game("levelup", steps, save=make_save("maps/perdition_harbor.txt,10,15"), timeout=120)
        m, out = r.get("marks", {}), {}
        for k in ("l1", "x256", "x512", "x1024"):
            ss = [s for s in r.get("snaps", []) if s["t"] <= m.get(k, {}).get("t", -1) and s["save"].get("hpmp")]
            out[k] = {"xp": int(ss[-1]["save"].get("xp", 0)), "max_hpmp": hpmp(ss[-1]["save"])} if ss else None
        return {"steps": out, "notes": r.get("notes"),
                "shot_char": self.fetch_shot("levelup", "char", f"{self.shots}/levelup_char.png") if "char" in r.get("shots", {}) else None}

    def save_load(self):
        # run 1: walk east, gain gold / an item / a status, quit (save on exit); run 2: load that slot, quit at once
        steps = [["wait_log", "Loading map", 40], ["sleep", 3], ["con", "exec reward_currency=123 reward_item=8:1 set_status=probe_flag"],
                 ["sleep", 0.5], ["keydown", "s"], ["keydown", "d"], ["sleep", 1.0], ["keyup", "s"], ["keyup", "d"], ["sleep", 1.5]]
        r1 = self.game("save_1", steps, save=make_save("maps/perdition_harbor.txt,3,22"), timeout=60)
        r2 = self.game("save_2", [["wait_log", "Loading map", 40], ["sleep", 3]], saves_from="save_1", timeout=60)
        s1, s2 = self.last(r1), self.last(r2)
        keys = ("class", "xp", "build", "equipped", "carried", "carried_quantity", "spawn", "actionbar", "powers", "campaign", "hpmp")
        diff = {k: f"{s1.get(k)} -> {s2.get(k)}" for k in keys if _norm_list(k, s1.get(k)) != _norm_list(k, s2.get(k))}
        return {"_saved_pos": pos(s1), "saved": {"gold": items_of(s1).get(1, 0), "dagger": items_of(s1).get(8, 0),
                          "flag": "probe_flag" in (s1.get("campaign") or "")},
                "reload_same": not diff, "reload_diff": diff, "maps": r2.get("maps"), "notes": (r1.get("notes") or []) + (r2.get("notes") or [])}

    def run_all(self, names=None):
        self.setup()
        out = {}
        for n in names or self.ALL:
            t = time.time()
            try:
                out[n] = getattr(self, n)()
            except Exception as e:                       # noqa: BLE001 -- a broken probe must not stop the others
                out[n] = {"error": f"{type(e).__name__}: {e}"}
            self.log(f"[{self.label}] {n}: {json.dumps(out[n], default=str)[:300]} ({time.time() - t:.0f}s)")
        out["log_errors"] = self.log_errors()
        return out

    def log_errors(self):
        errs, crashed = [], []
        for n, r in self.runs.items():
            for e in r.get("errors", []):
                errs.append(_err_key(e))
            if r.get("crash"):
                crashed.append(n)
        cnt = {}
        for e in errs:
            cnt[e] = cnt.get(e, 0) + 1
        return {"runs": len(self.runs), "crashed": crashed, "errors": cnt, "n_errors": len(errs)}


def _norm_list(k, v):
    """'2,3,0,0' and '2,3' are the same inventory list (Flare pads carried / equipped lists with zeros)"""
    if v is None or k not in ("carried", "carried_quantity", "equipped", "equipped_quantity"):
        return v
    parts = v.split(",")
    while len(parts) > 1 and parts[-1] in ("0", ""):
        parts.pop()
    return ",".join(parts)


def _err_key(line):
    line = re.sub(r"/home/player/\S+?/HOME/", "<HOME>/", line)
    line = re.sub(r"fow/\d+\.txt", "fow/<n>.txt", line)
    return line


def _sections(text):
    """definition file -> {block key: [sha1 of the block lines]} (blocks keyed by [section] + id=/name=/ordinal; comments and blanks dropped)"""
    blocks, cur, sec, n = {}, [], "_top", {}
    def flush():
        if not cur and sec == "_top":
            return
        ident = next((l.split("=", 1)[1] for l in cur if l.startswith("id=")), None) or \
            next((l.split("=", 1)[1] for l in cur if l.startswith("name=")), None)
        n[sec] = n.get(sec, 0) + 1
        key = f"{sec}:{ident}" if ident is not None else f"{sec}#{n[sec]}"
        blocks.setdefault(key, []).append(hashlib.sha1("\n".join(cur).encode()).hexdigest()[:16])
    for raw in text.splitlines():
        l = raw.strip()
        if not l or l.startswith("#"):
            continue
        if l.startswith("[") and l.endswith("]"):
            flush(); cur, sec = [], l[1:-1]
            continue
        cur.append(l)
    flush()
    return blocks


# ====================================================================================== compare
SPEED_REL = 0.3           # A/A 2026-10-30: walking speed 5.4-6.5 tiles/s over 5 vanilla runs (snapshot timing); slow-hero mod ~2.7-3.0
TTK_ABS = 4.0


def _img_diff(a, b, box):
    from PIL import Image, ImageChops, ImageStat
    ia, ib = Image.open(a).convert("RGB").crop(box), Image.open(b).convert("RGB").crop(box)
    return round(sum(ImageStat.Stat(ImageChops.difference(ia, ib)).mean) / 3, 2)


SHOT_BOX = {"talker": (283, 357, 998, 640), "topics": (536, 395, 990, 630), "questlog": (0, 20, 480, 645), "char": (0, 20, 480, 645)}
SHOT_TOL = 2.0            # mean abs pixel difference in the panel (0-255); A/A panels are pixel-identical (0.0)


def compare(name, base, got):
    """-> (ok | None, detail)"""
    if not isinstance(got, dict) or not got:
        return None, "not probed"
    if "error" in got:
        return None, got["error"]
    if not isinstance(base, dict) or "error" in base:
        return None, f"baseline error: {(base or {}).get('error')}"
    if name == "log_errors":
        if got.get("crashed"):
            return False, f"crashed/hung in runs {got['crashed']}"
        extra = {k: v - base.get("errors", {}).get(k, 0) for k, v in got.get("errors", {}).items() if v > base.get("errors", {}).get(k, 0)}
        return not extra, extra or f"{got.get('n_errors')} error lines (vanilla {base.get('n_errors')}), no new ones"
    if name == "data":
        bf, gf = base["files"], got["files"]
        d = {}
        for rel, bv in bf.items():
            gv = gf.get(rel)
            if gv is None:
                d[rel] = "missing"; continue
            if "sha1" in bv:
                if bv != gv: d[rel] = "map changed"
                continue
            ch = [k for k, blocks in bv.items() if gv.get(k, [])[:len(blocks)] != blocks or len(gv.get(k, [])) != len(blocks)]
            if ch:
                d[rel] = f"{len(ch)} changed: " + ", ".join(ch[:4])
        # a block with a vanilla id may also be overridden from another file of the same kind (items/items.txt APPEND [item] id=8)
        def agg(files):
            a = {}
            for rel, blocks in files.items():
                if "sha1" in blocks: continue
                top = rel.split("/")[0]
                for k, hs in blocks.items():
                    if ":" in k: a.setdefault((top, k), []).extend(hs)
            return {k: sorted(v) for k, v in a.items()}
        ab, ag = agg(bf), agg(gf)
        ov = [f"{t}/{k}" for (t, k), hs in ab.items() if ag.get((t, k)) != hs]
        if ov:
            d["overridden ids"] = f"{len(ov)}: " + ", ".join(ov[:6])
        if not d:
            return True, f"{len(bf)} vanilla definition files unchanged (additions allowed)"
        return False, dict(list(d.items())[:10], **({"more": len(d) - 10} if len(d) > 10 else {}))
    d = {}
    def eq(k, a, b):
        if a != b: d[k] = f"{a} -> {b}"
    def near(k, a, b, rel=0.0, ab=0.0):
        if a is None or b is None:
            if a != b: d[k] = f"{a} -> {b}"
        elif abs(a - b) > max(ab, rel * abs(a)): d[k] = f"{a} -> {b}"
    if name == "newgame":
        for k in ("class", "xp", "build", "equipped", "actionbar", "powers", "carried", "campaign", "map", "maps"):
            eq(k, base.get(k), got.get(k))
    elif name == "move":
        for seg in ("east", "nw"):
            b, g = base.get(seg) or {}, got.get(seg) or {}
            if "error" in g: d[seg] = g["error"]; continue
            near(seg + ".dx", b.get("dx"), g.get("dx"), 0.2, 2); near(seg + ".dy", b.get("dy"), g.get("dy"), 0.2, 2)
            near(seg + ".speed", b.get("speed"), g.get("speed"), SPEED_REL, 0.5)
        eq("teleport_to", base.get("teleport_to"), got.get("teleport_to"))
    elif name == "transition":
        # map names only: the tile reached after arriving depends on walking speed (that is FL-MOVE's job)
        eq("maps", base.get("maps"), got.get("maps")); eq("arrived_map", (base.get("arrived") or [None])[0], (got.get("arrived") or [None])[0])
        eq("back_map", base.get("back_map"), got.get("back_map"))
    elif name == "fight":
        for k in ("hit_before_attack", "killed", "xp", "hero_alive"):
            eq(k, base.get(k), got.get(k))
        near("time_to_kill", base.get("time_to_kill"), got.get("time_to_kill"), 0, TTK_ABS)
        near("min_hp", base.get("min_hp"), got.get("min_hp"), 0.35, 40)
    elif name == "powers":
        # gab selfcheck A/A 2026-10-30: the zombie can die after the first cast (damage roll + bleeding), then the second
        # click costs nothing ([6, 6] -> [6]): compare the cost of every cast that happened and that at least one did
        eq("mp_cost_per_cast", sorted(set(base.get("mp_costs") or [])), sorted(set(got.get("mp_costs") or [])))
        eq("cast", bool(base.get("casts")), bool(got.get("casts")))
        eq("killed", bool(base.get("xp")), bool(got.get("xp")))
    elif name == "potion":
        for k in ("health", "mana"):
            b, g = base.get(k) or {}, got.get(k) or {}
            if "error" in g: d[k] = g["error"]; continue
            eq(k + ".used", b.get("used"), g.get("used")); near(k + ".restored", b.get("restored"), g.get("restored"), 0.1, 8)
    elif name == "loot":
        eq("gold", base.get("gold"), got.get("gold")); eq("picked_item", base.get("picked_item"), got.get("picked_item"))
    elif name == "town":
        for k in ("npc_status_set", "gold", "bought_dagger", "slingshot_left"):
            eq(k, base.get(k), got.get(k))
    elif name == "equip":
        for k in ("max_hpmp_before", "max_hpmp_after", "equipped_after"):
            eq(k, _tup(base.get(k)), _tup(got.get(k)))
    elif name == "levelup":
        eq("steps", base.get("steps"), got.get("steps"))
    elif name == "save_load":
        eq("saved", base.get("saved"), got.get("saved")); eq("reload_same", base.get("reload_same"), got.get("reload_same"))
        if got.get("reload_diff"): d["reload_diff"] = got["reload_diff"]
    else:
        for k in sorted(set(base) | set(got)):
            if not k.startswith("_") and not k.startswith("shot_") and k != "notes": eq(k, base.get(k), got.get(k))
    _shots(d, base, got, [k[5:] for k in base if k.startswith("shot_")])
    return not d, d or "same as vanilla"


def _tup(v):
    return list(v) if isinstance(v, (list, tuple)) else v


def _shots(d, base, got, names):
    for n in names:
        a, b = base.get("shot_" + n), got.get("shot_" + n)
        if not a or not b or not Path(a).exists() or not Path(b).exists():
            d[f"shot.{n}"] = "screenshot missing"; continue
        v = _img_diff(a, b, SHOT_BOX[n])
        if v > SHOT_TOL:
            d[f"shot.{n}"] = f"panel differs from vanilla (mean abs diff {v})"


def compare_item(spec, base_all, got_all):
    """pool item `probe:` spec ("name" or "name.f1+f2") -> (ok | None, detail)"""
    name, _, fields = spec.partition(".")
    b, g = base_all.get(name), got_all.get(name, {"error": "not run"})
    if fields and isinstance(b, dict) and isinstance(g, dict) and not ("error" in g and len(g) == 1):
        keep = set(fields.split("+"))
        b = {k: v for k, v in b.items() if k in keep}; g = {k: v for k, v in g.items() if k in keep}
    return compare(name, b, g)


def compare_all(base, got):
    return {n: dict(zip(("ok", "detail"), compare(n, base[n], got.get(n, {"error": "not run"})))) for n in base}


if __name__ == "__main__":                       # sandbox side only: the per-run driver and the data dump
    if sys.argv[1] == "driver": driver(sys.argv[2], sys.argv[3])
    elif sys.argv[1] == "datadump": datadump(sys.argv[2], *sys.argv[3:4])
