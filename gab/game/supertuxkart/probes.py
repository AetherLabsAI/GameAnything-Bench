"""SuperTuxKart 1.5 regression probes + in-sandbox runner. Ported from the original probes_supertuxkart.py
(verified in game 2026-10-30, upstream Linux release 1.5; A/A + negative checks in games/supertuxkart/pool.yaml). Changes: the gab
adapter pushes THIS file into the sandbox (<root>/bridge/stkprobe.py) and runs it there (python3 stdlib + PIL for the ui
thumbnails); the root comes from $STK_ROOT (or the bridge's parent dir); the session result is written atomically (the adapter
starts sessions detached and polls for the file: long graphics sessions outlive a remote docker exec); runner commands for the
adapter: seed / instance (fresh instance for the player session) / launch (the game on the seat display, detached) / gate
(headless load test of a deliverable); the host side (docker cp, lease lookup, yaml compare CLI) is gone: the adapter calls
item_result(). Probe logic and tolerances unchanged except two fixes for vanilla noise seen in the gab selfcheck: a follow-the-
leader start-up segfault is retried up to twice (p_modes_fast); in p_ui / p_fps a game that dies during start-up is started again (up
to twice), the overworld counts as reached only once the overworld track itself loaded (not the 2nd intro cutscene part), the main menu also needs its bottom bar on the screen, the screenshots are taken once the screen has settled and the
overworld one after the log went quiet.

Runtime (inside the sandbox): the upstream Linux release 1.5 in <root>/SuperTuxKart-1.5-linux-x86_64. An instance (inst/<name>/) is
  data/   a hard-link copy of the game's data dir with the mod's data files laid over it (replaced files, never written in place)
  home/   its own HOME / XDG dirs (config, add-ons in home/data/supertuxkart/addons/{karts,tracks}, cache)
  ptracks/ the probe tracks (built from this instance's own data/tracks/<base>, so track mods are inherited)
A mod is a directory with  data/  (files laid over the game data: stk_config.xml, kart_characteristics.xml, karts/tux/kart.xml,
tracks/<t>/scripting.as, ...) and/or  addons/  (karts/<id>, tracks/<id> add-ons). A directory with neither is treated as data/.

Bridge (all the game's own mechanisms, no patched binary):
  * --no-graphics runs (headless, ~30x real time): profile mode (--profile-laps / --profile-time: AI-only race, the game prints
    a per-kart table: finish time, average / top speed, rescues, item boxes, bananas, nitro ...), --history (replays a scripted
    input file history.dat: accelerate / brake / steer / drift / rescue for kart 0, one event per physics tick so every log
    line carries its tick), --dump-official-karts.
  * probe tracks = stock track copies with an AngelScript scripting.as and a grid of point action-triggers added to scene.xml;
    each trigger calls stkp_t(kart) once when a kart comes near and the script logs Kart::getLocation / getVelocity
    ("STKP s ..."). Utils::setTimeout does not run without graphics and creating triggers at runtime crashes 1.5, so the
    trigger grid is static (every 1 m along the straight, 2 m across, 0.6 m layers).
  * every instance gets the seeded config (seed_cfg: enable_internet=2, i.e. "No" to the first-start internet-permission
    dialog, the game's default local player, no tutorial question), so a fresh instance starts straight into the main menu.
  * graphics probes (modes = battle, ui = main menu / options / story -> overworld driven with xdotool on the game's own
    window, fps = profile-race 'Average FPS') only under vglrun on the seat display; arena modes segfault with --no-graphics.

    python3 stkprobe.py session --name A [--mod DIR ...] [--only p1,p2] [--display :101] --out a.json      (inside the sandbox)
"""
import argparse, json, math, os, re, shutil, signal, statistics, subprocess, sys, time
from pathlib import Path

ROOT = Path(os.environ.get("STK_ROOT") or Path(__file__).resolve().parent.parent)   # <root>/bridge/stkprobe.py
GAME = ROOT / "SuperTuxKart-1.5-linux-x86_64"
VERSION = "1.5 (upstream Linux x86_64 release, 2025-10-20)"
GL = os.environ.get("STK_GL", "vglrun -d :99").split()                 # GPU wrapper for graphics runs (GPU Xorg :99 where the sandbox has one)

RACE_TRACKS = ["abyss", "black_forest", "candela_city", "cocoa_temple", "cornfield_crossing", "fortmagma", "gran_paradiso_island",
               "hacienda", "lighthouse", "mines", "minigolf", "olivermath", "ravenbridge_mansion", "sandtrack", "scotland",
               "snowmountain", "snowtuxpeak", "stk_enterprise", "volcano_island", "xr591", "zengarden"]
STOCK_KARTS = ["adiumy", "amanda", "beastie", "emule", "gavroche", "gnu", "hexley", "kiki", "konqi", "nolok", "pidgin", "puffy",
               "sara_the_racer", "sara_the_wizard", "suzanne", "tux", "wilber", "xue"]
RACE_SET = ["lighthouse", "zengarden", "snowtuxpeak"]       # profile races (AI) for SK-AI / SK-ITEMS
RACE_KARTS = ("tux", ["gnu", "kiki", "puffy"])               # player slot (driven by AI in profile mode) + AI karts
RACE_RUNS = 3
MODES_EXTRA = []        # [("soccer", 3, "soccer_field"), ("ctf", 5, "stadium")]: crash when started from the command line
SETTINGS_FILES = ["stk_config.xml", "kart_characteristics.xml", "powerup.xml", "items.xml"]
# probe track: lighthouse start straight. Kart 0 starts at (16.81, -13.87, -50.70) heading +x, ~58 m of road before a bend.
BASE_TRACK, X0, Y0, Z0 = "lighthouse", 16.81, -13.87, -50.70
TICKS = 120                                                   # physics ticks per second (stk_config physics fps)
PA = {"left": 0, "right": 1, "accel": 2, "brake": 3, "nitro": 4, "drift": 5, "rescue": 6, "fire": 7}
FULL = 32768
ERR = re.compile(r"\[(error|fatal) *\]|Segmentation fault|Aborting|Exception caught|core dumped", re.I)
ERR_IGNORE = re.compile(r"SFXOpenAL|SFXManager|OpenAL|ALSA|MusicManager|sound device|Translation|Can.t create dir .*cached-textures", re.I)
ANSI = re.compile(r"\x1b\[[0-9;]*m|\[[0-9;]*;?;?m")


def log_errors(text):
    return sum(1 for l in text.splitlines() if ERR.search(l) and not ERR_IGNORE.search(l))


# ------------------------------------------------------------------ instance + runner ------------------------------------------------
SEED = ROOT / "seed_cfg"


def seed_cfg():
    """Benchmark-runtime setup baked into every instance (the benchmark's runtime policy: setup choices go into the config, always
    the privacy-preserving option). config.xml / players.xml / input.xml as the game itself writes them on a first headless run,
    with enable_internet = 2 (IPERM_NOT_ALLOWED, what answering "No" to the first-start internet-permission dialog stores:
    no add-on / news server, no hardware reports) and the game's own default local player "Player" (use-frequency 1, so no
    tutorial question). With these a fresh instance starts straight into the main menu."""
    if (SEED / "players.xml").exists(): return SEED
    tmp = ROOT / "seed_tmp"; shutil.rmtree(tmp, ignore_errors=True); (tmp / "home").mkdir(parents=True)
    e = dict(os.environ, HOME=str(tmp / "home"), XDG_CONFIG_HOME=str(tmp / "home/cfg"), XDG_DATA_HOME=str(tmp / "home/data"),
             XDG_CACHE_HOME=str(tmp / "home/cache"), SUPERTUXKART_DATADIR=str(GAME), SUPERTUXKART_ASSETS_DIR=str(GAME / "data") + "/",
             LD_LIBRARY_PATH=str(GAME / "lib"))
    subprocess.run([str(GAME / "bin/supertuxkart"), "--no-graphics", "--track=lighthouse", "--numkarts=1", "--profile-time=1"],
                   cwd=tmp, env=e, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, timeout=120)
    src = tmp / "home/cfg/supertuxkart/config-0.10"
    SEED.mkdir(exist_ok=True)
    c = (src / "config.xml").read_text()
    c, n = re.subn(r'<enable_internet value="\d+" />', '<enable_internet value="2" />', c)
    if n != 1: raise RuntimeError("enable_internet not found in the generated config.xml")
    c = re.sub(r'hw-report-enabled="\w+"', 'hw-report-enabled="false"', c)
    (SEED / "config.xml").write_text(c)
    # use-frequency 1: the main menu does not ask "Would you like to play the tutorial of the game?" (asked only for a new profile)
    pl = (src / "players.xml").read_text()
    pl, n = re.subn(r'use-frequency="0"', 'use-frequency="1"', pl)
    if n != 1: raise RuntimeError("player profile not found in the generated players.xml")
    (SEED / "players.xml").write_text(pl)
    for f in ("input.xml", "1.png"): shutil.copy(src / f, SEED / f)
    shutil.rmtree(tmp, ignore_errors=True)
    return SEED

class Instance:
    def __init__(self, name):
        self.name = name
        self.dir = ROOT / "inst" / name
        self.data, self.home, self.ptracks = self.dir / "data", self.dir / "home", self.dir / "ptracks"
        self.logs = []

    def build(self, mods=()):
        if self.dir.exists(): subprocess.run(["rm", "-rf", str(self.dir)], check=True)
        self.dir.mkdir(parents=True)
        subprocess.run(["cp", "-al", str(GAME / "data"), str(self.data)], check=True)
        addons = self.home / "data" / "supertuxkart" / "addons"
        for p in ("cfg", "cache", "data/supertuxkart/addons/karts", "data/supertuxkart/addons/tracks"): (self.home / p).mkdir(parents=True, exist_ok=True)
        for m in mods:
            m = Path(m)
            parts = [(m / "data", self.data), (m / "addons", addons)]
            if not (m / "data").exists() and not (m / "addons").exists(): parts = [(m, self.data)]
            for src, dst in parts:
                if src.exists():   # --remove-destination: replace the hard link, never write through it into the stock install
                    subprocess.run(["cp", "-r", "--remove-destination", "-T", str(src), str(dst)], check=True)
        self.ptracks.mkdir()
        cfg = self.home / "cfg" / "supertuxkart" / "config-0.10"; cfg.mkdir(parents=True, exist_ok=True)
        for f in seed_cfg().iterdir(): shutil.copy(f, cfg / f.name)
        return self

    def env(self, display=None):
        e = dict(os.environ)
        e.update(HOME=str(self.home), XDG_CONFIG_HOME=str(self.home / "cfg"), XDG_DATA_HOME=str(self.home / "data"),
                 XDG_CACHE_HOME=str(self.home / "cache"), SUPERTUXKART_DATADIR=str(self.dir), SUPERTUXKART_ASSETS_DIR=str(self.data) + "/",
                 LD_LIBRARY_PATH=str(GAME / "lib"))
        if display: e["DISPLAY"] = display
        return e

    def run(self, tag, args, timeout=120, stop_on=None, display=None, files=None, linger=0, on_match=None):
        """Run the game once in inst/<name>/runs/<tag>/ (cwd; history.dat etc. from `files`); returns (log text, info)."""
        rd = self.dir / "runs" / tag
        if rd.exists(): shutil.rmtree(rd)
        rd.mkdir(parents=True)
        for k, v in (files or {}).items(): (rd / k).write_text(v)
        cmd = [str(GAME / "bin" / "supertuxkart")] + list(args)
        if display: cmd = GL + cmd + ["-w", "--screensize=1024x600"]
        elif "--no-graphics" not in args: cmd.append("--no-graphics")
        cmd.append(f"--trackdir={self.ptracks}/")
        logf = rd / "game.log"
        t0 = time.time()
        with open(logf, "w") as lf:
            p = subprocess.Popen(cmd, cwd=rd, env=self.env(display), stdout=lf, stderr=subprocess.STDOUT, stdin=subprocess.DEVNULL,
                                 start_new_session=True)
            stopped, pos, buf = None, 0, ""
            while True:
                rc = p.poll()
                if rc is not None: break
                if time.time() - t0 > timeout: stopped = "timeout"; break
                if stop_on:
                    with open(logf, errors="replace") as f: f.seek(pos); new = f.read(); pos += len(new.encode("utf-8", "replace"))
                    buf = (buf + new)[-4000:]
                    if re.search(stop_on, buf):
                        if on_match: on_match(p)
                        if linger:     # keep it running a while (crash check), then stop
                            t1 = time.time()
                            while time.time() - t1 < linger and p.poll() is None: time.sleep(0.5)
                        stopped = "done" if p.poll() is None else None; break
                time.sleep(0.05 if not display else 0.5)
            if p.poll() is None:
                try: os.killpg(p.pid, signal.SIGKILL)
                except ProcessLookupError: pass
                p.wait(10)
        text = ANSI.sub("", logf.read_text(errors="replace"))
        info = {"rc": p.returncode, "stopped": stopped, "secs": round(time.time() - t0, 1)}
        crashed = (stopped is None and p.returncode not in (0,)) or "Segmentation fault" in text
        self.logs.append({"tag": tag, "errors": log_errors(text), "crashed": bool(crashed), **info})
        return text, info

    # ---------------- probe tracks ----------------
    def probe_track(self, name, script, triggers=(), base=BASE_TRACK):
        src, dst = self.data / "tracks" / base, self.ptracks / f"stkp_{name}"
        if dst.exists(): shutil.rmtree(dst)
        subprocess.run(["cp", "-al", str(src), str(dst)], check=True)
        for f in ("track.xml", "scene.xml", "scripting.as"):
            if (dst / f).exists(): (dst / f).unlink()
        tx = (src / "track.xml").read_text(errors="replace")
        (dst / "track.xml").write_text(re.sub(r'name(\s*)=(\s*)"[^"]*"', f'name\\1=\\2"STK probe {name}"', tx, count=1))
        sc = (src / "scene.xml").read_text(errors="replace")
        objs = "\n".join(f'  <object type="action-trigger" trigger-type="point" id="stkp{i}" action="{a}" distance="{r}" '
                         f'reenable-timeout="999999.9" triggered-object="" xyz="{x:.2f} {y:.2f} {z:.2f}" hpr="0 0 0" scale="1 1 1"/>'
                         for i, (a, x, y, z, r) in enumerate(triggers))
        (dst / "scene.xml").write_text(sc.replace("</scene>", objs + "\n</scene>"))
        (dst / "scripting.as").write_text(script)
        return f"stkp_{name}"


SAMPLER = """
void stkp_t(int k)
{
    Vec3 p = Kart::getLocation(k); Vec3 v = Kart::getVelocity(k);
    Utils::logInfo("STKP s " + k + " " + p.getX() + " " + p.getY() + " " + p.getZ() + " " + v.getX() + " " + v.getY() + " " + v.getZ());
}
"""
JUMP_SCRIPT = SAMPLER + """
void stkp_launch(int k)
{
    if (k != 0) return;
    Vec3 v = Kart::getVelocity(0);
    Kart::setVelocity(0, Vec3(v.getX(), 7.0, v.getZ()));
    Utils::logInfo("STKP launch " + v.getLength());
}
"""
COLLIDE_SCRIPT = """
int ncoll = 0;
Vec3 spot;
void onStart()
{
    Vec3 p = Kart::getLocation(1);
    spot = Vec3(p.getX() + 22.0, p.getY() + 0.3, p.getZ());
    Kart::teleport(0, spot);
    Utils::logInfo("STKP placed");
}
void stkp_t(int k)
{
    Vec3 p = Kart::getLocation(k); Vec3 v = Kart::getVelocity(k);
    Utils::logInfo("STKP s " + k + " " + p.getX() + " " + p.getY() + " " + p.getZ() + " " + v.getX() + " " + v.getY() + " " + v.getZ());
    if (k == 0 && ncoll == 0) { Kart::teleport(0, spot); Kart::setVelocity(0, Vec3(0, 0, 0)); }
}
void onKartKartCollision(int a, int b)
{
    ncoll++;
    Vec3 v0 = Kart::getVelocity(0); Vec3 v1 = Kart::getVelocity(1);
    if (ncoll <= 5) Utils::logInfo("STKP coll " + a + " " + b + " " + v0.getLength() + " " + v1.getLength());
}
"""
INFO_SCRIPT = """
void onStart()
{
    for (int i = 0; i < Track::getNumberOfKarts(); i++)
        Utils::logInfo("STKP kart " + i + " max " + Kart::getMaxSpeed(i));
}
"""
MODE_SCRIPT = """
int ncoll = 0;
void onStart()
{
    Utils::logInfo("STKP start karts=" + Track::getNumberOfKarts() + " minor=" + Track::getMinorRaceMode());
}
void onKartKartCollision(int a, int b)
{
    ncoll++;
    if (ncoll <= 3 || ncoll % 25 == 0) Utils::logInfo("STKP coll " + ncoll);
}
"""


def drive_grid(jump=False):
    """Static trigger grid over the lighthouse start straight (x 14..80 every 1 m, z -58..-44 every 2 m, 6 layers 0.6 m apart
    around the road; the jump track adds layers up to +5 m between x0+14 and x0+44, and the launch trigger at x0+15)."""
    t = []
    for xi in range(14, 81):
        for zi in range(-58, -43, 2):
            layers = [Y0 - 2.4 + 0.6 * k for k in range(6)]
            if jump and X0 + 14 <= xi <= X0 + 44: layers += [Y0 + 1.2 + 0.6 * k for k in range(7)]
            for y in layers: t.append(("stkp_t", float(xi), y, float(zi), 1.5))
    if jump: t.append(("stkp_launch", X0 + 15, Y0 + 0.3, Z0, 3.0))
    return t


def history(track, karts, plan, ticks, players=1, drv=0):
    """history.dat for STK 1.5: plan = {kart: [(tick, action, value), ...]}; the driven kart's (drv) accel state is re-sent on
    every tick so every log line can be placed on its physics tick ("history: time T"). With AI karts in the race the
    history-driven player kart is the LAST kart index (AI karts come first)."""
    ev, acc = [], 0
    ch = {}
    for k, lst in plan.items():
        for t, a, v in lst: ch.setdefault(t, []).append((k, PA[a], v))
    for t in range(ticks):
        for k, a, v in ch.get(t, []):
            ev.append((t, k, a, v))
            if k == drv and a == PA["accel"]: acc = v
        ev.append((t, drv, PA["accel"], acc))
    hdr = (f"STK-version:      1.5\nHistory-version:  1\nnumkarts:         {len(karts)}\nnumplayers:       {players}\n"
           f"difficulty:       2\nreverse: n\ntrack: {track}\n" + "".join(f"model {i}: {k}\n" for i, k in enumerate(karts)) +
           f"count:     {len(ev)}\n")
    return hdr + "".join(f"{t} {k} {a} {v}\n" for t, k, a, v in ev) + "History file end.\n"


def parse_samples(text):
    """-> list of (tick, kart, (x,y,z), (vx,vy,vz)) for the first replay pass, deduplicated per (tick, kart)."""
    out, seen, tick = [], set(), 0
    for l in text.splitlines():
        if "Replay finished" in l: break
        m = re.search(r"history: time (\d+)", l)
        if m: tick = int(m.group(1)); continue
        m = re.search(r"STKP s (\d+) (\S+) (\S+) (\S+) (\S+) (\S+) (\S+)", l)
        if m:
            k = int(m.group(1)); vals = [float(x) for x in m.groups()[1:]]
            if (tick, k) in seen: continue
            seen.add((tick, k)); out.append((tick, k, tuple(vals[:3]), tuple(vals[3:])))
    return out


def _spd(v): return math.hypot(v[0], v[2])
def _hdg(v): return math.degrees(math.atan2(v[2], v[0]))


def _r(x, n=2): return None if x is None else round(x, n)


def _at_x(samples, x):
    """(tick, speed) where kart 0 first passes x (linear interpolation between samples)."""
    prev = None
    for t, k, p, v in samples:
        if k != 0: continue
        if prev and prev[1][0] < x <= p[0]:
            f = (x - prev[1][0]) / (p[0] - prev[1][0])
            return prev[0] + f * (t - prev[0]), _spd(prev[2]) + f * (_spd(v) - _spd(prev[2]))
        prev = (t, p, v)
    return None, None


# ------------------------------------------------------------------ probes ------------------------------------------------------------
class STKProbes:
    ORDER = ["settings", "karts", "drive", "physics", "race", "tracks", "modes_fast", "perf", "modes", "ui", "fps"]
    GRAPHICS = ["modes", "ui", "fps"]

    def __init__(self, inst, display=None):
        self.I, self.display = inst, display

    def run(self, names=None):
        out, timing = {}, {}
        for n in names or self.ORDER:
            if n in self.GRAPHICS and not self.display: out[n] = {"error": "needs --display"}; continue
            t0 = time.time()
            try: out[n] = getattr(self, "p_" + n)()
            except Exception as ex: out[n] = {"error": f"{type(ex).__name__}: {str(ex)[:300]}"}
            timing[n] = round(time.time() - t0, 1)
        out["_timing"] = timing
        return out

    # ---- settings snapshot: every attribute of the game's main tuning files as loaded from this instance's data dir
    def p_settings(self):
        import xml.etree.ElementTree as ET
        res = {}
        for f in SETTINGS_FILES:
            root = ET.parse(self.I.data / f).getroot(); cnt = {}
            def walk(e, path):
                for k, v in sorted(e.attrib.items()): res[f"{f}:{path}@{k}"] = v.strip()
                seen = {}
                for c in e:
                    if not isinstance(c.tag, str): continue
                    i = seen.get(c.tag, 0); seen[c.tag] = i + 1
                    walk(c, f"{path}/{c.tag}[{i}]")
            walk(root, root.tag)
        for k in STOCK_KARTS:
            p = self.I.data / "karts" / k / "kart.xml"
            if not p.exists(): res[f"karts/{k}/kart.xml"] = "MISSING"; continue
            root = ET.parse(p).getroot()
            def walk2(e, path):
                for a, v in sorted(e.attrib.items()):
                    if path.endswith(("/sounds[0]", "/animations[0]")) or a in ("model-file", "icon-file", "minimap-icon-file", "shadow-file"): continue
                    res[f"karts/{k}:{path}@{a}"] = v.strip()
                seen = {}
                for c in e:
                    if not isinstance(c.tag, str) or c.tag in ("wheels", "speed-weighted-objects", "nitro-emitter", "headlights", "hat", "exhaust"): continue
                    i = seen.get(c.tag, 0); seen[c.tag] = i + 1
                    walk2(c, f"{path}/{c.tag}[{i}]")
            walk2(root, "kart")
        return {"n": len(res), "values": res}

    # ---- karts: the game's own roster dump (class + model size) and per-kart top speed (expert difficulty) read by script
    def p_karts(self):
        I = self.I
        txt, _ = I.run("karts_dump", ["--dump-official-karts", "--no-graphics"], timeout=60)
        f = I.dir / "runs" / "karts_dump" / "official_karts.xml"
        roster = {}
        if f.exists():
            for m in re.finditer(r'<kart name="([^"]+)" type="([^"]+)" width="([^"]+)" height="([^"]+)" length="([^"]+)"', f.read_text()):
                roster[m.group(1)] = {"class": m.group(2), "size": [round(float(m.group(i)), 3) for i in (3, 4, 5)]}
        tr = I.probe_track("info", INFO_SCRIPT)
        txt, _ = I.run("karts_speed", ["--track=" + tr, "--kart=" + STOCK_KARTS[-1], "--ai=" + ",".join(STOCK_KARTS[:-1]),
                                       f"--numkarts={len(STOCK_KARTS)}", "--difficulty=2", "--profile-time=1"], timeout=90)
        speeds = {}
        for m in re.finditer(r"STKP kart (\d+) max (\S+)", txt):
            i = int(m.group(1))
            if i < len(STOCK_KARTS): speeds[STOCK_KARTS[i]] = round(float(m.group(2)), 3)
        missing = [k for k in STOCK_KARTS if k not in roster]
        return {"roster": roster, "max_speed": speeds, "n_karts": len(roster), "missing": missing}

    # ---- drive / physics: scripted input replays (history.dat) on the lighthouse start straight, state from the trigger grid
    def _replay(self, tag, track, plan, ticks, karts=("tux",), drv=0):
        txt, info = self.I.run(tag, ["--history"], timeout=90, stop_on=r"Replay finished",
                               files={"history.dat": history(track, list(karts), plan, ticks, drv=drv)})
        if "STKP" not in txt and "history: time" not in txt: raise RuntimeError(f"{tag}: replay did not run ({info})")
        return txt, parse_samples(txt)

    def p_drive(self):
        tr = self.I.probe_track("drive", SAMPLER, drive_grid())
        r = {}
        # accelerate from the start line
        txt, s = self._replay("drive_accel", tr, {0: [(0, "accel", FULL)]}, 1000)
        s0 = [x for x in s if x[1] == 0]
        t2, _ = _at_x(s0, X0 + 2)
        for d in (10, 30, 50):
            t, v = _at_x(s0, X0 + d)
            r[f"t{d}"] = _r((t - t2) / TICKS, 3) if t is not None and t2 is not None else None
            r[f"v{d}"] = _r(v)
        r["vmax"] = _r(max((_spd(v) for t, k, p, v in s0 if p[0] < X0 + 55), default=None))
        # the straight ends in a barrier at ~x0+58: where the kart hits it and its speed 0.5 s later (kart-track collision)
        hit = next((i for i in range(1, len(s0)) if s0[i][2][0] > X0 + 45 and _spd(s0[i - 1][3]) - _spd(s0[i][3]) > 2.0), None)
        if hit is not None:
            th = s0[hit][0]; aft = [x for x in s0 if x[0] >= th + 60]
            r["wall_x"] = _r(s0[hit][2][0] - X0, 1); r["wall_speed_after"] = _r(_spd(aft[0][3])) if aft else None
        # brake at tick 380 (~17.5 m/s): stopping distance
        txt, s = self._replay("drive_brake", tr, {0: [(0, "accel", FULL), (380, "accel", 0), (380, "brake", FULL)]}, 900)
        s0 = [x for x in s if x[1] == 0]
        before = [x for x in s0 if x[0] <= 380]
        if before:
            tb, pb, vb = before[-1][0], before[-1][2], before[-1][3]
            xb = pb[0] + _spd(vb) * (380 - tb) / TICKS
            after = [x for x in s0 if x[0] > 380]
            xmax = max([p[0] for t, k, p, v in after] + [xb])
            stop = next((t for t, k, p, v in after if _spd(v) < 1.0), None)
            r["brake_speed"] = _r(_spd(vb)); r["brake_dist"] = _r(xmax - xb); r["brake_time"] = _r((stop - 380) / TICKS, 3) if stop else None
        # steer left 0.5 s at ~14 m/s
        r.update(self._turn("drive_steer", tr, [(300, "left", FULL), (360, "left", 0)], 300, 360, "steer"))
        # drift (skid) left 0.5 s, then release
        r.update(self._turn("drive_drift", tr, [(300, "left", FULL), (300, "drift", FULL), (360, "drift", 0), (360, "left", 0)], 300, 360, "drift"))
        return r

    def _turn(self, tag, tr, evs, t_on, t_off, key):
        txt, s = self._replay(tag, tr, {0: [(0, "accel", FULL)] + evs}, t_off + 140)
        s0 = [x for x in s if x[1] == 0]
        pre = [x for x in s0 if x[0] <= t_on]; post = [x for x in s0 if x[0] >= t_off]
        if not pre or not post: return {f"{key}_heading": None, "_" + key: f"{len(pre)}/{len(post)} samples"}
        h0, h1 = _hdg(pre[-1][3]), _hdg(post[0][3])
        dh = (h1 - h0 + 180) % 360 - 180
        out = {f"{key}_heading": _r(dh, 1), f"{key}_speed_in": _r(_spd(pre[-1][3])), f"{key}_speed_out": _r(_spd(post[0][3]))}
        if key == "drift":
            later = [x for x in s0 if t_off < x[0] <= t_off + 120]
            out["drift_boost_speed"] = _r(max((_spd(v) for t, k, p, v in later), default=None))
        return out

    def p_physics(self):
        r = {}
        for f in (self._rescue, self._jump):   # _collide (kart-kart) not used: see the report
            try: r.update(f())
            except Exception as ex: r[f.__name__[1:] + "_error"] = f"{type(ex).__name__}: {str(ex)[:200]}"
        return r

    def _rescue(self):
        """rescue pressed at tick 300 (~14 m/s): the kart is lifted off and put back on the road. Off-road time = gap in the
        samples; where it is put back (x relative to the start line)."""
        tr = self.I.probe_track("drive", SAMPLER, drive_grid()) if not (self.I.ptracks / "stkp_drive").exists() else "stkp_drive"
        txt, s = self._replay("phys_rescue", tr, {0: [(0, "accel", FULL), (300, "rescue", FULL), (306, "rescue", 0)]}, 900)
        s0 = [x for x in s if x[1] == 0]
        pre = [x for x in s0 if x[0] <= 300]; post = [x for x in s0 if x[0] > 302]
        if not pre or not post: return {"rescued": False}
        gap = (post[0][0] - pre[-1][0]) / TICKS
        later = [x for x in post if x[0] > post[0][0] + 120]
        return {"rescued": gap > 0.5, "rescue_gap_s": _r(gap, 2), "rescue_put_x": _r(post[0][2][0] - X0, 1),
                "rescue_drives_on": bool(later and _spd(later[0][3]) > 3)}

    def _jump(self):
        """at x0+15 the track script sets the kart's vertical speed to 7 m/s; apex / air time / jump length from the grid."""
        trj = self.I.probe_track("jump", JUMP_SCRIPT, drive_grid(jump=True))
        txt, s = self._replay("phys_jump", trj, {0: [(0, "accel", FULL)]}, 700)
        s0 = [x for x in s if x[1] == 0]
        lt, tick = None, 0
        for l in txt.splitlines():
            m = re.search(r"history: time (\d+)", l)
            if m: tick = int(m.group(1))
            if "STKP launch" in l: lt = tick; break
        if lt is None: return {"launched": False}
        before = [x for x in s0 if x[0] <= lt]
        yl = before[-1][2][1] if before else Y0
        air = [x for x in s0 if x[0] > lt]
        apex_s = max(air[:60], key=lambda x: x[2][1], default=None)
        land = next((x for x in air if apex_s and x[0] > apex_s[0] and x[2][1] <= yl + 0.05), None)
        return {"launched": True, "apex": _r(apex_s[2][1] - yl, 2) if apex_s else None,
                "apex_time": _r((apex_s[0] - lt) / TICKS, 3) if apex_s else None,
                "air_time": _r((land[0] - lt) / TICKS, 3) if land else None,
                "jump_dist": _r(land[2][0] - before[-1][2][0], 1) if land and before else None}

    def _collide(self):
        """kart 0 (AI, held in place by the script until the first contact) sits 22 m ahead of the player on its lane; the history-driven
        player kart (index 1) drives into it."""
        trc = self.I.probe_track("collide", COLLIDE_SCRIPT, drive_grid())
        txt, s = self._replay("phys_collide", trc, {1: [(0, "accel", FULL)]}, 700, karts=("tux", "tux"), drv=1)
        coll = re.findall(r"STKP coll (\d+) (\d+) (\S+) (\S+)", txt)
        if not coll: return {"collided": False}
        tick = 0; ct = None
        for l in txt.splitlines():
            m = re.search(r"history: time (\d+)", l)
            if m: tick = int(m.group(1))
            if "STKP coll" in l: ct = tick; break
        s1 = [x for x in s if x[1] == 0 and x[0] >= ct]            # the parked kart, pushed
        s0 = [x for x in s if x[1] == 1 and ct <= x[0] <= ct + 60]  # the driven kart
        return {"collided": True, "hit_speed": _r(float(coll[0][3])),
                "pushed_speed": _r(max((_spd(v) for t, k, p, v in s1[:30]), default=0.0)),
                "hitter_speed_after": _r(min((_spd(v) for t, k, p, v in s0), default=None))}

    # ---- AI profile races: all karts finish, finish times, items picked
    def _profile(self, tag, track, laps=1, extra=()):
        k0, ai = RACE_KARTS
        txt, info = self.I.run(tag, [f"--track={track}", f"--kart={k0}", "--ai=" + ",".join(ai), f"--numkarts={1 + len(ai)}",
                                     "--difficulty=2", f"--laps={laps}", f"--profile-laps={laps}"] + list(extra), timeout=180)
        rows = []
        for m in re.finditer(r"profile: (\S+) \S+ (\d+) (\d+) (\S+) (\S+) (\S+) (\S+) (\S+) (\S+) (\S+) (\S+) (\S+) (\S+) (\S+) (\S+) (\S+) (\S+)", txt):
            g = m.groups()
            rows.append({"kart": g[0], "start": int(g[1]), "end": int(g[2]), "time": float(g[3]), "avg_speed": float(g[4]),
                         "top_speed": float(g[5]), "rescues": int(float(g[8])), "items": int(float(g[12])), "bananas": int(float(g[13])),
                         "nitro_small": int(float(g[14])), "nitro_big": int(float(g[15])), "bubblegum": int(float(g[16]))})
        fps = re.search(r"Average FPS: ([\d.]+)", txt)
        return rows, (float(fps.group(1)) if fps else None), info

    def p_race(self):
        res, items = {}, {"items": 0, "bananas": 0, "nitro": 0, "bubblegum": 0}
        fin_all = True
        for tr in RACE_SET:
            times, avs, n = [], [], 0
            for i in range(RACE_RUNS):
                rows, fps, info = self._profile(f"race_{tr}_{i}", tr)
                n += 1
                ok = len(rows) == 1 + len(RACE_KARTS[1]) and sorted(r["end"] for r in rows) == list(range(1, len(rows) + 1))
                fin_all &= ok
                if rows:
                    times.append(statistics.mean(r["time"] for r in rows)); avs.append(statistics.mean(r["avg_speed"] for r in rows))
                    for r in rows:
                        items["items"] += r["items"]; items["bananas"] += r["bananas"]; items["nitro"] += r["nitro_small"] + r["nitro_big"]
                        items["bubblegum"] += r["bubblegum"]
            res[tr] = {"mean_time": _r(statistics.mean(times)) if times else None, "times": [_r(t) for t in times],
                       "avg_speed": _r(statistics.mean(avs)) if avs else None}
        # laps: 2-lap race on lighthouse, all finish and take about twice the 1-lap time
        rows, fps, info = self._profile("race_laps2", "lighthouse", laps=2)
        t2 = statistics.mean(r["time"] for r in rows) if rows else None
        lap_ratio = _r(t2 / res["lighthouse"]["mean_time"], 3) if t2 and res["lighthouse"]["mean_time"] else None
        return {"all_finished": fin_all and len(rows) == 4, "tracks": res, "laps2_time": _r(t2), "lap_ratio": lap_ratio,
                "items_per_race": {k: _r(v / (len(RACE_SET) * RACE_RUNS), 2) for k, v in items.items()}}

    # ---- every stock race track loads and AI karts drive on it (6 s of AI racing each)
    def p_tracks(self):
        res = {}
        for tr in RACE_TRACKS:
            k0, ai = RACE_KARTS
            txt, info = self.I.run(f"track_{tr}", [f"--track={tr}", f"--kart={k0}", "--ai=" + ai[0], "--numkarts=2", "--profile-time=6"], timeout=120)
            sp = [float(m.group(1)) for m in re.finditer(r"profile: \S+ \S+ \d+ \d+ \S+ (\S+) ", txt)]
            res[tr] = "ok" if len(sp) == 2 and min(sp) > 2.0 else f"fail ({len(sp)} karts, speeds {sp[:2]}, rc {info['rc']})"
        return {"tracks": res, "ok": sum(v == "ok" for v in res.values()), "n": len(res)}

    # ---- time trial and follow-the-leader (profile mode works for linear worlds)
    def p_modes_fast(self):
        r = {}
        rows, fps, info = self._profile("mode_time_trial", "lighthouse", extra=["--mode=1"])
        r["time_trial"] = {"karts": len(rows), "finished": len(rows) == 4 and sorted(x["end"] for x in rows) == [1, 2, 3, 4],
                           "mean_time": _r(statistics.mean(x["time"] for x in rows)) if rows else None}
        # follow the leader has no laps: 40 s of profile time (karts behind the leader are eliminated); --profile-laps crashes 1.5
        k0, ai = RACE_KARTS
        # (an explicit --ai list crashes FTL in 1.5: the leader is an extra AI kart; the other AI karts are the game's pick)
        # gab: vanilla 1.5 segfaults at FTL start-up now and then (sandbox B 2 of 16 runs, sandbox A 2 of 4 in one selfcheck; right
        # after "RaceManager: Nb of karts", no race table) -> such a crash is retried up to twice, kept as retried_crash
        for k in range(3):
            txt, info = self.I.run("mode_follow_leader" + (f"_r{k}" if k else ""), ["--track=lighthouse", f"--kart={k0}", "--numkarts=4",
                                                          "--difficulty=2", "--mode=4", "--profile-time=40"], timeout=180)
            n = len(re.findall(r"profile: \S+ \S+ \d+ \d+ \S+ \S+ ", txt))
            if k == 2 or info["rc"] is None or info["rc"] >= 0 or n: break
            L = self.I.logs[-1]; L.update(crashed=False, retried_crash=True, retried_errors=L["errors"], errors=0)
        r["follow_leader"] = {"karts": n, "finished": n == 4 and info["rc"] == 0}
        return r

    # ---- simulation speed (frames/s of a headless AI race: physics + AI + scripts, no rendering)
    def p_perf(self):
        f = []
        for i in range(3):
            rows, fps, info = self._profile(f"perf_{i}", "lighthouse")
            if fps: f.append(fps)
        return {"sim_fps": _r(statistics.median(f), 0) if f else None, "_all": [_r(x, 0) for x in f]}

    # ---- graphics: battle / soccer / CTF (arena copies log start + kart-kart collisions), story intro + overworld, main menu
    def _gfx(self, tag, args, secs):
        return self.I.run(tag, args, timeout=secs, display=self.display)

    def p_modes(self):
        """3-strikes battle on the stadium arena (graphics; ~8-10 min to start on the shared pod): an arena copy whose script
        logs the race mode at start and kart-kart contacts; 90 s of play after the start must not crash."""
        import threading
        jobs, r = [], {}
        def one(name, mode, base):
            try:
                tr = self.I.probe_track(f"mode_{base}", MODE_SCRIPT, base=base)
                txt, info = self.I.run(f"gmode_{name}", ["-R", f"--mode={mode}", f"--track={tr}", "--numkarts=4"], timeout=1500,
                                       display=self.display, stop_on=r"STKP start", linger=90)
                st = re.search(r"STKP start karts=(\d+) minor=(\d+)", txt)
                col = re.findall(r"STKP coll (\d+)", txt)
                r[name] = {"started": bool(st), "minor": int(st.group(2)) if st else None, "karts": int(st.group(1)) if st else None,
                           "contacts": int(col[-1]) if col else 0, "crashed": info["stopped"] != "done", "secs": info["secs"]}
            except Exception as ex: r[name] = {"error": str(ex)[:200]}
        # soccer (--mode=3) and CTF (--mode=5) started from the command line segfault in 1.5 (teams are never assigned: the log
        # shows "World: Blue AI: 538976288 red AI: 538976288"), with and without graphics; they are only startable from the menu
        for name, mode, base in (("battle", 2, "stadium"),) + tuple(MODES_EXTRA):
            if name == "ctf": time.sleep(2)   # stadium copy built by the battle job
            th = threading.Thread(target=one, args=(name, mode, base)); th.start(); jobs.append(th)
        for th in jobs: th.join()
        return r

    def p_story(self):
        return {"error": "measured by the 'ui' probe (fields story_*)"}

    def p_menu(self):
        return {"error": "measured by the 'ui' probe (fields menu / options)"}

    def p_fps(self):
        """rendered frame rate: graphics profile race (lighthouse, 4 AI karts, 25 s, 1024x600), the game's own 'Average FPS'."""
        if not self.display: return {"error": "needs --display"}
        k0, ai = RACE_KARTS
        f = []
        for i in range(2):
            for k in range(3):    # gab: a graphics start-up that dies before the race (SIGABRT, busy pod) is started again
                txt, info = self.I.run(f"gfps_{i}" + (f"_r{k}" if k else ""), ["--track=lighthouse", f"--kart={k0}", "--ai=" + ",".join(ai), "--numkarts=4",
                                                     "--difficulty=2", "--profile-time=25"], timeout=900, display=self.display)
                m = re.search(r"Average FPS: ([\d.]+)", txt)
                if m or k == 2 or not self.I.logs[-1]["crashed"] or "Track: Overall" in txt: break
                L = self.I.logs[-1]; L.update(crashed=False, retried_crash=True, retried_errors=L["errors"], errors=0)
            if m: f.append(float(m.group(1)))
        return {"fps": _r(max(f), 1) if f else None, "_all": f}

    def p_ui(self):
        """gab: a run in which the game dies is started again (up to twice; the dead run is kept in the run log as
        retried_crash): vanilla 1.5 died (SIGABRT) in ~5 of ~25 graphics runs on a test sandbox while the pod's GPU was busy from
        elsewhere (2026-10-31), at start-up and once in the overworld; a mod that breaks this path dies every time"""
        for k in range(3):
            r = self._ui_once()
            if k == 2 or not r.get("crashed"): break
            L = self.I.logs[-1]; L.update(crashed=False, retried_crash=True, retried_errors=L["errors"], errors=0)
        return {**r, "retries": k} if k else r

    def _ui_once(self):
        """One graphics run on the seat display, driven like a player (xdotool on the game's own window, 1024x600):
        main menu (screenshot) -> Options (wrench, screenshot) -> Escape -> Story Mode: intro cutscene -> kart selection (Return)
        -> overworld (screenshot). Needs the seeded config (seed_cfg) so no first-start dialog appears."""
        if not self.display: return {"error": "needs --display"}
        I, disp = self.I, self.display
        shots = I.dir / "shots"; shots.mkdir(exist_ok=True)
        rd = I.dir / "runs" / "ui"; shutil.rmtree(rd, ignore_errors=True); rd.mkdir(parents=True)
        logf = rd / "game.log"; env = I.env(disp)
        p = subprocess.Popen(GL + [str(GAME / "bin/supertuxkart"), "-w", "--screensize=1024x600"], cwd=rd, env=env,
                             stdout=open(logf, "w"), stderr=subprocess.STDOUT, stdin=subprocess.DEVNULL, start_new_session=True)
        res, t0 = {}, time.time()
        def log(): return ANSI.sub("", logf.read_text(errors="replace"))
        def xdo(*a): return subprocess.run(["xdotool", *a], env=env, capture_output=True, text=True).stdout
        def shot(name):
            g = xdo("getwindowgeometry", win)
            pos = re.search(r"Position: (\d+),(\d+)", g); size = re.search(r"Geometry: (\d+)x(\d+)", g)
            if not pos or not size: return None          # gab: the window is gone (the game died)
            f = shots / f"{name}.png"
            subprocess.run(["ffmpeg", "-loglevel", "error", "-y", "-f", "x11grab", "-video_size", f"{size.group(1)}x{size.group(2)}",
                            "-i", f"{disp}+{pos.group(1)},{pos.group(2)}", "-frames:v", "1", str(f)], env=env)
            return thumb(f) if f.exists() else None
        def steady(name, tol=0.004, limit=240, gap=5, n=3):
            """gab: a screenshot once the screen has settled (n shots gap s apart all within tol of the last): on a busy GPU the
            menu icons / text, the options panel and the overworld HUD were drawn late, with pauses of several seconds
            (selfcheck A/A 2026-10-31, start-up 89 s / 145 s)"""
            seq = [shot(name)]; t1 = time.time()
            while time.time() - t1 < limit and p.poll() is None:
                time.sleep(gap); seq = (seq + [shot(name)])[-n:]
                if len(seq) == n and all(x and image_diff(x, seq[-1]) <= tol for x in seq): return seq[-1]
            return seq[-1]
        def wait(cond, limit, step=3):
            t1 = time.time()
            while time.time() - t1 < limit:
                if p.poll() is not None: return False
                if cond(): return True
                time.sleep(step)
            return False
        def idle(secs): return time.time() - logf.stat().st_mtime > secs
        try:
            win = None
            def found():
                nonlocal win
                w = xdo("search", "--pid", str(p.pid), "--name", "SuperTuxKart").split(); win = w[0] if w else None
                return bool(win)
            if not wait(found, 900): raise RuntimeError("no game window")
            # main menu: start-up loads every kart (log active); the menu is up once the log has been quiet for 25 s
            # gab: + the main menu's yellow bottom bar is on the screen (on a busy GPU the loading screen stayed up for minutes
            # after the log went quiet; the probe then shot and clicked the loading screen)
            def menu_up(): shot("menu_wait"); return is_menu(shots / "menu_wait.png")
            if not wait(lambda: "Loading Grand Prix" in log() and idle(25) and menu_up(), 1500, 5): raise RuntimeError("main menu not reached")
            res["startup_s"] = round(time.time() - t0)
            time.sleep(min(90, max(0, res["startup_s"] - 40) * 0.5))   # gab: a slow start-up (busy GPU) draws the menu slowly too
            res["menu_shot"] = steady("menu")
            xdo("mousemove", "--window", win, "618", "568", "click", "1"); time.sleep(8)       # wrench = Options
            res["options_shot"] = steady("options")
            xdo("key", "--window", win, "Escape"); time.sleep(5)
            n0 = log().count("Track: Overall")
            xdo("mousemove", "--window", win, "142", "390", "click", "1")                       # Story Mode
            time.sleep(6); shot("after_story_click")      # gab: what the click opened (diagnosis only, not compared)
            res["story_intro"] = wait(lambda: log().count("Track: Overall") > n0, 600)
            def overworld():
                L = log(); i = L.rfind("tracks/overworld")     # gab: the overworld itself loaded (not the 2nd intro cutscene part)
                if i >= 0 and "Track: Overall" in L[i:] and L.count("Track: Overall") > n0 + (1 if res["story_intro"] else 0): return True
                if idle(20): xdo("key", "--window", win, "Return")                              # kart selection: take the default kart
                return False
            res["story_overworld"] = wait(overworld, 900, 5)
            if res["story_overworld"]:     # gab: + wait for the log to go quiet (still loading after 8 s once: a loading-screen shot)
                wait(lambda: idle(4), 120, 1); time.sleep(8); res["overworld_shot"] = steady("overworld", tol=0.01)
        except Exception as ex:
            res["error"] = f"{type(ex).__name__}: {str(ex)[:200]}"
        crashed = p.poll() is not None
        if not crashed:
            os.killpg(p.pid, signal.SIGKILL); p.wait(10)
        text = log()
        res["crashed"] = crashed
        I.logs.append({"tag": "ui", "errors": log_errors(text), "crashed": crashed, "rc": p.returncode, "stopped": "done", "secs": round(time.time() - t0, 1)})
        return res

# ------------------------------------------------------------------ compare ------------------------------------------------------------
def _close(b, g, rel, ab):
    if b is None or g is None: return b == g
    return abs(g - b) <= max(abs(b) * rel, ab)


DRIVE_TOL = {"t10": (0.05, 0.05), "t30": (0.05, 0.08), "t50": (0.05, 0.1), "v10": (0.05, 0.5), "v30": (0.05, 0.5), "v50": (0.05, 0.6),
             "vmax": (0.03, 0.5), "wall_x": (0, 2.0), "wall_speed_after": (0.3, 2.5), "brake_speed": (0.05, 0.6), "brake_dist": (0.1, 1.5), "brake_time": (0.1, 0.15),
             "steer_heading": (0.15, 3.0), "steer_speed_in": (0.05, 0.6), "steer_speed_out": (0.08, 0.8),
             "drift_heading": (0.15, 4.0), "drift_speed_in": (0.05, 0.6), "drift_speed_out": (0.1, 1.0), "drift_boost_speed": (0.08, 1.0)}
PHYS_TOL = {"rescue_gap_s": (0.1, 0.2), "rescue_put_x": (0, 1.5), "apex": (0.12, 0.15), "apex_time": (0.12, 0.05),
            "air_time": (0.1, 0.08), "jump_dist": (0.12, 1.0), "hit_speed": (0.1, 1.0), "pushed_speed": (0.35, 2.0),
            "hitter_speed_after": (0.35, 2.0)}
RACE_TIME_TOL = 0.12       # mean finish time per track (3 races x 4 AI karts) within 12% of vanilla
ITEMS_MIN_RATIO = 0.4      # item / banana / nitro pickups per race: not below 40% of vanilla
SIM_FPS_MIN = 0.5
FPS_MIN_RATIO = 0.75     # A/A 82-95 fps (ratio >= 0.86); 60 extra track meshes -> 69 fps (0.73)
UI_DIFF_MAX = {"menu": 0.01, "options": 0.01, "overworld": 0.06}   # mean grey-level difference 0..1 (set from the UI A/A)


THUMB = (160, 94)


def is_menu(png, loose=False):
    """gab: the main menu's yellow bottom bar fills the bottom 6% of the screen (the loading screen there is grey); loose: or
    the orange SuperTuxKart logo is in the top middle (with a busy GPU the menu skin / text came minutes after the logo)"""
    from PIL import Image
    im = Image.open(png).convert("RGB"); w, h = im.size
    r, g, b = [sum(c) / len(c) for c in zip(*im.crop((0, int(h * 0.94), w, h)).resize((64, 4)).getdata())]
    if r > 150 and g > 120 and r - b > 80: return True
    if not loose: return False
    px = list(im.crop((int(w * 0.38), int(h * 0.1), int(w * 0.62), int(h * 0.4))).resize((48, 30)).getdata())
    return sum(1 for R, G, B in px if R > 200 and 70 < G < 200 and B < 90) / len(px) > 0.15


def thumb(path):
    """Grey 160x94 thumbnail of a screenshot as base64 (stored in the session JSON so compare() works on the host)."""
    import base64
    from PIL import Image
    return base64.b64encode(Image.open(path).convert("L").resize(THUMB).tobytes()).decode()


def image_diff(a, b):
    """Mean absolute grey-level difference (0..1) of two thumbnails (the overworld one is a live 3D scene)."""
    import base64
    x, y = base64.b64decode(a), base64.b64decode(b)
    if len(x) != len(y): return 1.0
    return round(sum(abs(i - j) for i, j in zip(x, y)) / (255.0 * len(x)), 4)


def compare(name, base, got):
    if not isinstance(got, dict) or "error" in got: return None if got is None else False, (got or {}).get("error", "not probed")
    if not isinstance(base, dict) or "error" in base: return None, f"baseline: {(base or {}).get('error')}"
    if name == "settings":
        b, g = base["values"], got["values"]
        d = {k: f"{b.get(k)} -> {g.get(k)}" for k in sorted(set(b) | set(g)) if b.get(k) != g.get(k)}
        return not d, (dict(list(d.items())[:12], n_diffs=len(d)) if d else f"{len(b)} values unchanged")
    if name == "karts":
        d = {}
        for k in STOCK_KARTS:
            if base.get("roster", {}).get(k) != got.get("roster", {}).get(k): d[k + ".roster"] = f"{base['roster'].get(k)} -> {got.get('roster', {}).get(k)}"
            if "max_speed" in base and base["max_speed"].get(k) != got.get("max_speed", {}).get(k):
                d[k + ".max_speed"] = f"{base['max_speed'].get(k)} -> {got.get('max_speed', {}).get(k)}"
        if base.get("n_karts") != got.get("n_karts"): d["n_karts"] = f"{base.get('n_karts')} -> {got.get('n_karts')}"
        return not d, d or f"{got.get('n_karts')} stock karts, classes / sizes / top speeds as vanilla"
    if name in ("drive", "physics"):
        tol = DRIVE_TOL if name == "drive" else PHYS_TOL
        d = {}
        for k in sorted(set(base) | set(got)):
            if k.startswith("_"): continue
            b, g = base.get(k), got.get(k)
            if k in tol:
                if not _close(b, g, *tol[k]): d[k] = f"{b} -> {g}"
            elif b != g: d[k] = f"{b} -> {g}"
        return not d, d or "same as vanilla"
    if name == "race":
        d = {}
        if base.get("all_finished") != got.get("all_finished"): d["all_finished"] = f"{base.get('all_finished')} -> {got.get('all_finished')}"
        for tr, b in base.get("tracks", {}).items():
            g = got.get("tracks", {}).get(tr, {})
            if not _close(b.get("mean_time"), g.get("mean_time"), RACE_TIME_TOL, 0): d[tr + ".mean_time"] = f"{b.get('mean_time')} -> {g.get('mean_time')}"
        if not _close(base.get("lap_ratio"), got.get("lap_ratio"), 0.12, 0): d["lap_ratio"] = f"{base.get('lap_ratio')} -> {got.get('lap_ratio')}"
        bi, gi = base.get("items_per_race", {}), got.get("items_per_race", {})
        for k in ("items", "nitro"):     # bananas / bubblegum hits are reported but too rare (~1 per race) to compare
            b, g = bi.get(k), gi.get(k)
            if b and (g is None or g < ITEMS_MIN_RATIO * b): d["items." + k] = f"{b} -> {g} per race"
        ok_det = {"all_finished": got.get("all_finished")}
        if got.get("tracks"): ok_det["mean_time"] = {tr: got["tracks"][tr]["mean_time"] for tr in got["tracks"]}
        if "lap_ratio" in got: ok_det["lap_ratio"] = got["lap_ratio"]
        if gi: ok_det["items_per_race"] = gi
        return not d, d or ok_det
    if name == "tracks":
        d = {k: v for k, v in got.get("tracks", {}).items() if v != "ok" and base.get("tracks", {}).get(k) == "ok"}
        return not d, d or f"{got.get('ok')}/{got.get('n')} stock race tracks load and AI drives"
    if name == "modes_fast":
        d = {}
        for k, b in base.items():
            g = got.get(k) or {}
            if b.get("finished") != g.get("finished") or b.get("karts") != g.get("karts"): d[k] = f"{b} -> {g}"
            elif k == "time_trial" and not _close(b.get("mean_time"), g.get("mean_time"), RACE_TIME_TOL, 0): d[k + ".mean_time"] = f"{b.get('mean_time')} -> {g.get('mean_time')}"
        return not d, d or "time trial and follow-the-leader run to the end with 4 karts"
    if name == "modes":
        d = {}
        for k, b in base.items():
            g = got.get(k) or {}
            if g.get("crashed") or b.get("started") != g.get("started") or b.get("minor") != g.get("minor") or b.get("karts") != g.get("karts"):
                d[k] = f"{b} -> {g}"
        return not d, d or {k: f"started (mode {v.get('minor')}, {v.get('karts')} karts), {v.get('contacts')} kart contacts, no crash" for k, v in got.items()}
    if name == "ui":
        d, det = {}, {}
        for k in ("menu", "options", "overworld"):
            if k + "_shot" in base or k + "_shot" in got:
                if not got.get(k + "_shot") or not base.get(k + "_shot"): d[k] = "no screenshot"; continue
                dd = image_diff(base[k + "_shot"], got[k + "_shot"]); det[k] = dd
                if dd > UI_DIFF_MAX.get(k, 0.05): d[k] = f"screenshot differs by {dd} (max {UI_DIFF_MAX.get(k)})"
        for k in ("story_intro", "story_overworld"):
            if k in base and base.get(k) != got.get(k): d[k] = f"{base.get(k)} -> {got.get(k)}"
        if got.get("crashed"): d["crashed"] = True
        return not d, d or {"image_diff": det, "story": "intro + overworld as vanilla"}
    if name == "fps":
        b, g = base.get("fps"), got.get("fps")
        if not b or not g: return None, "no reading"
        return g >= FPS_MIN_RATIO * b, f"{g} rendered fps vs vanilla {b} (min {FPS_MIN_RATIO}x)"
    if name == "perf":
        b, g = base.get("sim_fps"), got.get("sim_fps")
        if not b or not g: return None, "no reading"
        return g >= SIM_FPS_MIN * b, f"{g} simulated frames/s vs vanilla {b} (min {SIM_FPS_MIN}x)"
    d = {k: f"{base.get(k)} -> {got.get(k)}" for k in sorted(set(base) | set(got)) if base.get(k) != got.get(k) and not k.startswith("_")}
    return not d, d or "unchanged"


def item_result(spec, base, got):
    """Pool item `probe:` spec (probe, or probe.f1+f2 as in g1_regress.item_result) on two session JSONs -> (ok | None, detail)."""
    if spec == "log_errors":
        cr = [x["tag"] for x in got.get("_runs", []) if x.get("crashed")]
        bcr = {x["tag"] for x in base.get("_runs", []) if x.get("crashed")}
        cr = [c for c in cr if c not in bcr]
        if cr: return False, f"game crashed in {cr[:5]}"
        b, g = base.get("log_errors"), got.get("log_errors")
        return (g is not None and b is not None and g <= b), f"{g} error lines vs vanilla {b}"
    p = spec.split(".")[0]
    b, g = base.get(p), got.get(p)
    if "." in spec and isinstance(b, dict) and isinstance(g, dict):
        fields = spec.split(".", 1)[1].split("+")
        b = {k: v for k, v in b.items() if k in fields}; g = {k: v for k, v in g.items() if k in fields or k == "error"}
    return compare(p, b, g)


# ------------------------------------------------------------------ CLI ------------------------------------------------------------------
def session(name, mods, only, out, display=None, keep=False):
    inst = Instance(name)
    res = {"version": VERSION, "mods": [str(m) for m in mods], "started": time.strftime("%Y-%m-%d %H:%M:%S")}
    try:
        inst.build(mods)
        res.update(STKProbes(inst, display).run(only))
    except Exception as ex:
        res["error"] = f"{type(ex).__name__}: {str(ex)[:300]}"
    res["_runs"] = inst.logs
    res["log_errors"] = sum(x["errors"] for x in inst.logs)
    Path(out + ".tmp").write_text(json.dumps(res, indent=1)); os.replace(out + ".tmp", out)
    if not keep:   # keep only the logs
        for p in (inst.data, inst.home / "cache", inst.ptracks): shutil.rmtree(p, ignore_errors=True)
    return res



# ------------------------------------------------------------------ gab runner commands --------------------------------------------------
MODES = {"normal": 0, "time_trial": 1, "battle": 2, "follow_leader": 4}     # --mode values (soccer 3 / CTF 5 segfault from the command line)


def track_kind(d):
    """'race' | 'arena' | 'soccer' | 'internal' | None from a track dir's track.xml"""
    f = Path(d) / "track.xml"
    if not f.exists(): return None
    t = f.read_text(errors="replace")
    for k in ("internal", "cutscene", "soccer", "ctf", "arena"):
        if re.search(rf'\b{k}\s*=\s*"[yY]', t): return {"ctf": "arena", "cutscene": "internal"}.get(k, k)
    return "race"


def added(inst):
    """karts / tracks the mods add, by the id the game knows them under (add-ons: addon_<dir>, as the game's add-on loader
    names them; new dirs in the data overlay: <dir>) -> {karts: [...], tracks: {id: kind}}"""
    ad = inst.home / "data" / "supertuxkart" / "addons"
    karts = sorted({"addon_" + p.name for p in (ad / "karts").glob("*") if (p / "kart.xml").exists()} |
                   {p.name for p in (inst.data / "karts").glob("*") if (p / "kart.xml").exists() and not (GAME / "data" / "karts" / p.name).exists()})
    tracks = {"addon_" + p.name: track_kind(p) for p in (ad / "tracks").glob("*") if (p / "track.xml").exists()}
    tracks.update({p.name: track_kind(p) for p in (inst.data / "tracks").glob("*") if (p / "track.xml").exists() and not (GAME / "data" / "tracks" / p.name).exists()})
    return {"karts": karts, "tracks": tracks}


def game_id(inst, kind, name):
    """start.yaml names a kart / track by its directory: an add-on is known to the game as addon_<dir>"""
    if inst and not name.startswith("addon_") and (inst.home / "data" / "supertuxkart" / "addons" / kind / name).is_dir(): return "addon_" + name
    return name


NOT_FOUND = re.compile(r"(Kart|Track) '([^']+)' not found|Track .{0,40}does not exist|Unknown track", re.I)


def start_args(st, inst=None):
    """start.yaml -> command line: mode menu (default) = no args; race = straight into a race (-R)"""
    if st.get("mode", "menu") == "menu": return []
    a = ["-R", f"--track={game_id(inst, 'tracks', st.get('track', 'lighthouse'))}", f"--kart={game_id(inst, 'karts', st.get('kart', 'tux'))}", f"--numkarts={int(st.get('numkarts', 4))}",
         f"--mode={MODES[st.get('race_mode', 'normal')]}", f"--difficulty={int(st.get('difficulty', 1))}"]
    if st.get("race_mode", "normal") in ("normal", "time_trial"): a.append(f"--laps={int(st.get('laps', 3))}")
    if st.get("reverse"): a.append("--reverse")
    return a


def gate(name, mods, st, out):
    """headless load test: every added race track (tux + 1 AI) and every added kart (on lighthouse), or the start race target,
    8 s of AI racing each; ok when every run shows both karts driving and nothing crashed. Arenas need graphics (start())."""
    inst = Instance(name); res = {"ok": False}
    try:
        inst.build(mods); res["added"] = A = added(inst)
        combos = [(t, "tux") for t, k in A["tracks"].items() if k == "race"] + [("lighthouse", k) for k in A["karts"]]
        if st.get("mode") == "race" and st.get("race_mode", "normal") != "battle":
            combos.insert(0, (game_id(inst, "tracks", st.get("track", "lighthouse")), game_id(inst, "karts", st.get("kart", "tux"))))
        combos = list(dict.fromkeys(combos))[:6] or [("lighthouse", "tux")]
        runs = []
        for i, (t, k) in enumerate(combos):
            txt, info = inst.run(f"gate_{i}", [f"--track={t}", f"--kart={k}", "--ai=" + RACE_KARTS[1][0], "--numkarts=2", "--profile-time=8"], timeout=180)
            sp = [float(m.group(1)) for m in re.finditer(r"profile: \S+ \S+ \d+ \d+ \S+ (\S+) ", txt)]
            bad = [ANSI.sub("", l) for l in txt.splitlines() if ERR.search(l) and not ERR_IGNORE.search(l)][:60]   # deduplicated on the host
            nf = sorted({m.group(0) for m in NOT_FOUND.finditer(txt)})
            runs.append({"track": t, "kart": k, "karts_driving": sum(v > 0.5 for v in sp), "rc": info["rc"], "stopped": info["stopped"],
                         "crashed": inst.logs[-1]["crashed"], "not_found": nf, "error_lines": bad})
        res["runs"] = runs
        res["ok"] = all(r["karts_driving"] == 2 and not r["crashed"] and not r["not_found"] for r in runs)
    except Exception as ex: res["error"] = f"{type(ex).__name__}: {str(ex)[:300]}"
    Path(out + ".tmp").write_text(json.dumps(res, indent=1)); os.replace(out + ".tmp", out)
    for p in (inst.data, inst.home / "cache", inst.ptracks): shutil.rmtree(p, ignore_errors=True)
    return res


# gab state bridge (player sessions only, never in probe instances): every track of the instance gets one invisible point
# action-trigger covering the whole track (re-armed every 0.5 s, fires once per kart) whose action logs that kart's engine
# values (Kart:: / Track:: script API, read live inside the running race) as "GABST" lines; state() reads the newest per kart.
# The function is appended to the track's own scripting.as (a mod's script keeps working: unique names, no onStart).
STATE_AS = """
void gabst_tick(int k)
{
    Vec3 p = Kart::getLocation(k); Vec3 v = Kart::getVelocity(k);
    Utils::logInfo("GABST " + k + " " + Track::getNumberOfKarts() + " " + Track::getMinorRaceMode() + " " + p.getX() + " " + p.getY()
                   + " " + p.getZ() + " " + v.getX() + " " + v.getY() + " " + v.getZ() + " " + Kart::getMaxSpeed(k));
}
"""
# the GUI message calls of a track script are routed through wrappers that log the text the game is about to show
# ("GABMSG ..."; state()["screen_text"]) and then call the engine's own function: STK has no getter for on-screen text
MSG_FUNCS = ("displayMessage", "displayModalMessage", "displayOverlayMessage", "displayStaticMessage")
STATE_MSG_AS = "".join(f"""
void gabst_{f}(const string &in m) {{ Utils::logInfo("GABMSG " + m); GUI::{f}(m); }}""" for f in MSG_FUNCS) + """
void gabst_displayMessage(const string &in m, int t) { Utils::logInfo("GABMSG " + m); GUI::displayMessage(m, t); }
void gabst_displayStaticMessage(const string &in m, int t) { Utils::logInfo("GABMSG " + m); GUI::displayStaticMessage(m, t); }
"""
STATE_TRIGGER = ('  <object type="action-trigger" trigger-type="point" id="gabst" action="gabst_tick" distance="100000" '
                 'reenable-timeout="0.5" triggered-object="" xyz="0 0 0" hpr="0 0 0" scale="1 1 1"/>\n')


def mod_static(inst, mods):
    """what the mods put into the game that the engine loads as data (state()["mod"], computed once per instance build, before
    the bridge): added karts / tracks, objects added to a track's scene.xml, tuning values changed, overlaid files"""
    import types
    A = added(inst); out = {"karts_added": A["karts"], "tracks_added": A["tracks"]}
    objs = []
    for t in sorted((inst.data / "tracks").glob("*")) + sorted((inst.home / "data/supertuxkart/addons/tracks").glob("*")):
        sc = t / "scene.xml"
        if not sc.exists(): continue
        stock = GAME / "data" / "tracks" / t.name / "scene.xml"
        if stock.exists() and os.path.samefile(stock, sc): continue
        base = set(stock.read_text(errors="replace").splitlines()) if stock.exists() else set()
        for l in sc.read_text(errors="replace").splitlines():
            l = l.strip()
            if l.startswith("<") and not l.startswith(("</", "<!--")) and l not in {x.strip() for x in base}:
                m = dict(re.findall(r'([\w-]+)\s*=\s*"([^"]*)"', l)); tag = re.match(r"<([\w-]+)", l)
                objs.append({"track": t.name, "tag": tag.group(1) if tag else "?", **{k: m[k][:80] for k in ("id", "type", "model", "name", "xyz") if k in m}})
    out["objects_added"] = objs[:20]; out["n_objects_added"] = len(objs)
    van = STKProbes(types.SimpleNamespace(data=GAME / "data")).p_settings()["values"]
    got = STKProbes(inst).p_settings()["values"]
    ch = {k: [van.get(k), got.get(k)] for k in sorted(set(van) | set(got)) if van.get(k) != got.get(k)}
    out["settings_changed"] = dict(list(ch.items())[:30]); out["n_settings_changed"] = len(ch)
    files = sorted(str(f.relative_to(m)) for m in map(Path, mods) for f in m.rglob("*") if f.is_file())
    out["files"] = files[:20]; out["n_files"] = len(files)
    return out


def add_state_bridge(inst):
    """write the bridge into every track of the instance (files replaced, never written through the hard links)"""
    ad = inst.home / "data" / "supertuxkart" / "addons" / "tracks"
    n = 0
    for t in list((inst.data / "tracks").glob("*")) + list(ad.glob("*")):
        sc, sa = t / "scene.xml", t / "scripting.as"
        if not sc.exists() or not (t / "track.xml").exists(): continue
        x = sc.read_text(errors="replace")
        if 'id="gabst"' in x or "</scene>" not in x: continue
        a = sa.read_text(errors="replace") if sa.exists() else ""
        for f in (sc, sa):
            if f.exists(): f.unlink()
        a = re.sub(r"GUI\s*::\s*(" + "|".join(MSG_FUNCS) + r")\s*\(", r"gabst_\1(", a)
        i = x.rindex("</scene>"); sc.write_text(x[:i] + STATE_TRIGGER + x[i:]); sa.write_text(a + "\n" + STATE_AS + STATE_MSG_AS); n += 1
    return n


def launch(name, display, st, size="1280x720"):
    """the real game on the seat display, detached (own session; pid in inst/<name>/game.pid, log inst/<name>/game.log)"""
    I = Instance(name)
    if not I.data.exists(): raise SystemExit(f"instance {name} not built")
    args = start_args(st, I)
    lf = open(I.dir / "game.log", "w")
    p = subprocess.Popen(GL + [str(GAME / "bin/supertuxkart"), "-w", f"--screensize={size}"] + list(args), cwd=I.dir, env=I.env(display),
                         stdout=lf, stderr=subprocess.STDOUT, stdin=subprocess.DEVNULL, start_new_session=True)
    (I.dir / "game.pid").write_text(str(p.pid))
    print(p.pid)


def main():
    ap = argparse.ArgumentParser(); sp = ap.add_subparsers(dest="cmd", required=True)
    s = sp.add_parser("session"); s.add_argument("--name", required=True); s.add_argument("--mod", action="append", default=[])
    s.add_argument("--only"); s.add_argument("--out", required=True); s.add_argument("--display"); s.add_argument("--keep", action="store_true")
    sp.add_parser("seed"); s = sp.add_parser("ismenu"); s.add_argument("png"); s.add_argument("--loose", action="store_true")
    s = sp.add_parser("instance"); s.add_argument("name"); s.add_argument("--mod", action="append", default=[])
    s = sp.add_parser("gate"); s.add_argument("name"); s.add_argument("--mod", action="append", default=[]); s.add_argument("--start", default="{}")
    s.add_argument("--out", required=True)
    s = sp.add_parser("launch"); s.add_argument("name"); s.add_argument("--display", required=True); s.add_argument("--size", default="1280x720")
    s.add_argument("--start", default="{}")
    a = ap.parse_args()
    if a.cmd == "session":
        r = session(a.name, a.mod, a.only.split(",") if a.only else None, a.out, a.display, a.keep)
        print(json.dumps({"timing": r.get("_timing"), "error": r.get("error"), "log_errors": r.get("log_errors")}))
    elif a.cmd == "seed": print(seed_cfg())
    elif a.cmd == "ismenu": print("menu" if is_menu(a.png, a.loose) else "no")
    elif a.cmd == "instance":
        I = Instance(a.name).build(a.mod); ms = mod_static(I, a.mod); (I.dir / "mod_static.json").write_text(json.dumps(ms))
        print(json.dumps({**added(I), "state_bridge_tracks": add_state_bridge(I)}))
    elif a.cmd == "gate": print(json.dumps(gate(a.name, a.mod, json.loads(a.start), a.out))[:3000])
    elif a.cmd == "launch": launch(a.name, a.display, json.loads(a.start), a.size)


if __name__ == "__main__":
    main()
