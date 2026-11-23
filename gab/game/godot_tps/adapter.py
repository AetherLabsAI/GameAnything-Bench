"""Godot TPS demo adapter (godotengine/tps-demo on Godot 4.7.2). Install: <seat home>/oss-godot-tps/{godot, vanilla/ (+ .godot
import cache)} -- the verified reference install or the image's
(godot/godot + vanilla at tps-demo a82f154); `install()` reproduces it on another seat sandbox. MIT / CC-BY game, no EULA / ToS,
no first-run dialog, no telemetry; the network is never used (Play Online is a LAN host / join on 127.0.0.1 at most).

A fresh instance per mod (<root>/gab/<inst>/game: hard-link copy of vanilla + a real copy of the import cache + the mod laid
over it + `godot --headless --import`), a fresh user dir per start (settings.ini seeded: windowed, vsync off, 60 fps cap: the seat GPU is shared); the game runs
windowed 1280x720 at 0,0 (= the seat screen, no window manager) on the seat display, Compatibility (OpenGL) renderer through
VirtualGL (Vulkan loses the device on Xvfb). A fresh start shows the main menu (Play / Play Online / Settings / Quit).

Deliverable mod/ (devkit/README.md): files laid over the Godot project (res://), same paths (replace or add): scenes (.tscn),
scripts (.gd), resources (.tres), shaders, assets (imported on install), project.godot or an override.cfg (e.g. new autoloads).

state() (docs/STATE_SPEC.md) through the GABState autoload (gab/game/godot_state.gd + state_engine.gd), live from the running
game: engine = {screen: loading | menu | level | other, menu_panel, fps, uptime_s; in the level: level_time_s, online {server,
peers}, player {pos, heading_deg, velocity, on_floor, airborne_s, aiming, shooting, crosshair, camera_pos}, robots {count,
alive, nearest 8: name, pos, health, dead, state, dist}, players, bullets}; mod = {nodes (script / scene added or replaced by
the mod: name, kind, class, path, script, scene, pos, visible, dist, vars), nodes_total, autoloads (added: script vars),
input_actions (added)}; screen_text = visible Label / Button / RichTextLabel / LineEdit / Label3D texts.
"""
import json, re, shlex, tempfile, time
from pathlib import Path

from .. import base as gbase
from ..base import GameAdapter
from .. import godot_common as GC
from . import probes as P
from gab.game.base import vglrun

HERE = Path(__file__).parent
GODOT_ZIP = "https://github.com/godotengine/godot/releases/download/4.7.2-stable/Godot_v4.7.2-stable_linux.x86_64.zip"
DEMO_GIT, DEMO_COMMIT = "https://github.com/godotengine/tps-demo.git", "a82f15448e9b015440d3bbdf5e10801b260c4e9f"
RESERVED = (".godot", "gtprobe", "gab_state")
TEXT_HEAD = {".tscn": "[gd_scene", ".tres": "[gd_resource"}
TOPS = ("door", "effects_shared", "enemies", "level", "main", "menu", "player", "project.godot", "override.cfg", "default_bus_layout.tres")


class Adapter(GameAdapter):
    name = "godot-tps"
    title = "Godot TPS Demo (third-person shooter, Godot 4)"
    screen = (1280, 720)

    def __init__(self, seat, root=None):
        super().__init__(seat)
        self.root = root or f"{seat.home}/oss-godot-tps"
        self.gl = vglrun()

    def requirements(self):
        return {"game": "godot-tps", "gpu": True, "mem_gb": 1.5, "steam": False, "hosts": ["pool", "docker", "local"],
                "image": "gameanything/godot-tps:4.7.2", "eula": None}

    def idir(self, inst): return f"{self.root}/gab/{inst}"
    def udir(self, inst): return f"{self.idir(inst)}/user/godot/app_userdata/{P.APPNAME}"
    def paths(self, inst):
        d = self.idir(inst)
        return {"vanilla_project": f"{self.root}/vanilla", "instance_project": f"{d}/game", "user_dir": self.udir(inst),
                "game_log": f"{d}/game.log", "import_log": f"{d}/import.log", "godot": self._godot_path()}

    def _godot_path(self):
        return self.seat.exec(f"{P.GODOT.format(root=self.root)}; echo $G").stdout.strip()

    # ---- install / instance
    def install(self):
        R = self.root
        ok = lambda: self.seat.exec(f"{P.GODOT.format(root=R)}; test -x $G && test -f {R}/vanilla/project.godot && test -d {R}/vanilla/.godot/imported && echo ok").stdout.strip() == "ok"
        if not ok():
            if self.seat.exec("test -f /games/tpsdemo/tps-demo-imported.tar && echo y").stdout.strip() == "y":   # the pool's source (read only)
                self.seat.exec(f"set -e; mkdir -p {R} && cd {R}; [ -e godot ] || cp /games/tpsdemo/Godot_v4.7.2-stable_linux.x86_64 godot; "
                               f"if [ ! -d vanilla ]; then tar xf /games/tpsdemo/tps-demo-imported.tar && mv tps-demo-master vanilla && chmod -R a-w vanilla; fi", timeout=1800, check=True)
            else:                                                                                                 # as docker/godot-tps/install.sh
                self.seat.exec(f"set -e; mkdir -p {R}/godot && cd {R}/godot && curl -sSL -o g.zip {GODOT_ZIP} && unzip -qo g.zip && rm g.zip && "
                               f"ln -sf Godot_v4.7.2-stable_linux.x86_64 godot && cd {R} && git clone -q {DEMO_GIT} vanilla && cd vanilla && git checkout -q {DEMO_COMMIT} && "
                               f"for i in 1 2; do timeout 3000 {R}/godot/godot --headless --path . --import > /dev/null 2>&1 || true; done", timeout=7200, check=True)
            if not ok(): raise RuntimeError("Godot TPS demo install failed")
        self.seat.exec(f"mkdir -p {R}/gab {R}/inst {R}/mods {R}/probe")
        self.seat.put(f"{R}/probe/gab_state.gd", GC.bridge(HERE / "state_engine.gd"))
        return True

    def provision(self, inst):
        self.install(); self.stop(inst)
        self.seat.exec(f"rm -rf {self.idir(inst)} && mkdir -p {self.idir(inst)}", check=True)
        self._build(inst)
        return {"instance": inst, "dir": self.idir(inst), "display": self.seat.display}

    def _build(self, inst):
        """<inst>/game: vanilla (hard links) + import cache copy + staged mod + the state bridge autoload, imported headless"""
        d, R = self.idir(inst), self.root
        self.seat.put(f"{R}/probe/gab_state.gd", GC.bridge(HERE / "state_engine.gd"))          # always the current bridge
        r = self.seat.exec(f"""set -e; cd {R}; {P.GODOT.format(root=R)}; I={d}/game; rm -rf $I {d}/state {d}/imp; mkdir -p $I {d}/imp
            cp -al vanilla/. $I/; find $I -type d -exec chmod u+w {{}} +
            rm -rf $I/.godot; cp -r vanilla/.godot $I/.godot; chmod -R u+w $I/.godot
            if [ -d {d}/mod ]; then cp -r --remove-destination {d}/mod/. $I/; (cd {d}/mod && find . -type f | sed 's|^\\./||' | while read -r f; do if [ -e "{R}/vanilla/$f.import" ]; then cp --remove-destination "{R}/vanilla/$f.import" "$I/$f.import"; fi; done; true); fi
            mkdir -p $I/gab_state; cp {R}/probe/gab_state.gd $I/gab_state/gab_state.gd
            if [ -f $I/override.cfg ]; then cp --remove-destination $I/override.cfg $I/override.cfg.tmp; mv $I/override.cfg.tmp $I/override.cfg; fi
            printf '\\n[autoload]\\n\\nGABState="*res://gab_state/gab_state.gd"\\n' >> $I/override.cfg
            {GC.meta_sh(f"{R}/vanilla", f"{d}/mod", f"{d}/state")}
            XDG_DATA_HOME={d}/imp XDG_CONFIG_HOME={d}/imp XDG_CACHE_HOME={d}/imp timeout 900 $G --headless --path $I --import > {d}/import.log 2>&1 || true
            rm -rf {d}/imp""", timeout=1200)
        if r.returncode: raise RuntimeError(f"instance build failed: {r.stdout[-300:]} {r.stderr[-600:]}")

    def install_mod(self, inst, mod_dir):
        """the mod's files laid over a fresh copy of the project (what a player does with a mod for a Godot game from source), imported"""
        d = self.idir(inst); self.stop(inst)
        self.seat.exec(f"rm -rf {d}/mod")
        with tempfile.TemporaryDirectory() as t:
            m = GC.merged(mod_dir, TOPS, t)
            files = [p for p in m.rglob("*") if p.is_file()] if m else []
            if m: self.seat.copy_in(m, f"{d}/mod")
        self._build(inst)
        return {"files": len(files), "mods": [x.name for x in GC.layout(mod_dir, TOPS)]}

    def build(self, inst, mod_dir=None):
        """structure check of the deliverable -> (ok, log); the load test is load_check()"""
        if mod_dir is None: return True, "no mod dir given"
        files = [(d, p) for d in GC.layout(mod_dir, TOPS) for p in d.rglob("*") if p.is_file()]; L = []
        if not files: L.append("mod/ is empty: it holds files laid over the Godot project (same paths as res://)")
        for m, f in files:
            rel = f.relative_to(m); top = rel.parts[0]
            if top in RESERVED: L.append(f"{rel}: {top}/ is reserved (import cache / evaluation bridge)")
            if f.suffix in TEXT_HEAD:
                h = f.read_text(errors="replace").lstrip()[:40]
                if not h.startswith(TEXT_HEAD[f.suffix]): L.append(f"{rel}: a text {f.suffix} must start with {TEXT_HEAD[f.suffix]} (binary .scn / .res are fine)")
            if f.name == "project.godot" and "config_version=5" not in f.read_text(errors="replace"): L.append("project.godot: not a Godot 4 project file (config_version=5)")
            if f.suffix == ".import": L.append(f"{rel}: .import files are made by the import; ship the asset itself")
        return not L, "\n".join(L) or f"ok: {len(files)} files ({len([f for _, f in files if f.suffix == '.gd'])} scripts, {len([f for _, f in files if f.suffix == '.tscn'])} scenes)"

    def load_check(self, inst, out_dir=None):
        """the gate: a headless run of the modded project in which every scene / script / resource loads, compiles and
        instantiates (GTProbe gate mode), then the real game comes up on the seat display (start())"""
        pr = P.GTProbes(self.seat, self.root, self.seat.display, self.gl, label=f"gate-{inst}", log=lambda s: None)
        d = self.idir(inst)
        try:
            self.seat.put(f"{self.root}/probe/gt_probe.gd", (HERE / "gt_probe.gd").read_text())
            r = self.seat.exec(P.build_sh(self.root, pr.name, f"{d}/mod" if self.seat.exec(f"test -d {d}/mod && echo y").stdout.strip() else None), timeout=1200)
            if r.returncode:
                return False, gbase.gate_log(gbase.error_lines(r.stdout + "\n" + r.stderr, P.ERR, P.ERR_IGNORE), "the import of the modded project failed",
                                             "instance build failed: " + r.stderr[-800:])
            g = pr.gate()
        finally: pr.cleanup()
        res = g.get("resources") or {}; E = g.get("_error_lines") or []
        info = {"resources": {k: res.get(k) for k in ("files", "ok", "failed")}, "level_loaded_headless": g.get("level_loaded"),
                "finished": g.get("_finished"), "exit": g.get("_exit")}
        if not g.get("_finished") or not res or res.get("failed"):
            return False, gbase.gate_log(E, "headless load test failed: " + GC.gate_why(res, g.get("_finished"), f" (exit {g.get('_exit')})"), json.dumps(info)[:2500])
        ok, log = self.start(inst)
        E += [x for x in GC.error_lines(self.seat.exec(f"cat {self.idir(inst)}/game.log 2>/dev/null").stdout, P.ERR, P.ERR_IGNORE) if x not in E]
        self.stop(inst)
        why = None if ok else "the game did not reach its first screen: " + (log.splitlines() or ["?"])[0][:300]
        return ok, gbase.gate_log(E[:gbase.ERR_N], why, json.dumps(info)[:1500] + "\ngame: " + log[-1500:])

    def start(self, inst):
        """fresh user dir -> the game on the seat display; ok when the first screen is up (main menu, or the mod's own first scene)"""
        d = self.idir(inst); self.stop(inst)
        if self.seat.exec(f"test -f {d}/game/project.godot && echo y").stdout.strip() != "y": self._build(inst)
        self.seat.exec(f"rm -rf {d}/user && mkdir -p '{self.udir(inst)}' && printf '[video]\\n\\ndisplay_mode=0\\nvsync=0\\nmax_fps=60\\n' > '{self.udir(inst)}/settings.ini' && "
                       f"rm -f {d}/state/req {d}/state/state.json {d}/state/last.json", check=True)
        env = f"DISPLAY={self.seat.display} XDG_DATA_HOME={d}/user XDG_CONFIG_HOME={d}/user/config XDG_CACHE_HOME={d}/user/cache GAB_STATE_DIR={d}/state"
        run = (f"exec env {env} {self.gl} $G --path {d}/game --rendering-driver opengl3 --display-driver x11 --windowed "
               f"--resolution {self.screen[0]}x{self.screen[1]} --position 0,0")
        # setsid -f: own process group (stop() kills it), the game does not hold the exec session's pipes
        self.seat.exec(f"cd {d} && {P.GODOT.format(root=self.root)}; export G; setsid -f bash -c {shlex.quote('echo $$ > game.pid; ' + run)} > {d}/game.log 2>&1 < /dev/null")
        t0 = time.time(); last = None
        while time.time() - t0 < 300:
            time.sleep(3)
            if not self._alive(inst): return False, "the game exited\n" + self.log_tail(inst)
            _, s = GC.read_state(self.seat, f"{d}/state", f"{d}/game.pid")
            if s: last = s["engine"].get("screen")
            if last in ("menu", "level", "other"): time.sleep(2); return True, f"first screen: {last}"
        return False, f"no first screen in 300 s (bridge: {last})\n" + self.log_tail(inst)

    def _pid(self, inst): return self.seat.exec(f"cat {self.idir(inst)}/game.pid 2>/dev/null").stdout.strip()

    def _alive(self, inst):
        p = self._pid(inst)
        return bool(p) and self.seat.exec(f"ps -o stat= -p {p} | grep -qv Z && echo up").stdout.strip() == "up"

    def stop(self, inst):
        self.seat.exec(f"p=$(cat {self.idir(inst)}/game.pid 2>/dev/null); [ -n \"$p\" ] && {{ kill -- -$p 2>/dev/null; sleep 1; kill -9 -- -$p 2>/dev/null; }}; "
                       f"rm -f {self.idir(inst)}/game.pid; true")

    def log_tail(self, inst, n=40): return self.seat.exec(f"tail -{n} {self.idir(inst)}/game.log 2>/dev/null").stdout

    def state(self, inst):
        d = self.idir(inst); return GC.state_of(*GC.read_state(self.seat, f"{d}/state", f"{d}/game.pid"))

    def log_errors(self, inst):
        t = self.seat.exec(f"cat {self.idir(inst)}/game.log 2>/dev/null").stdout
        lines = [l for l in t.splitlines() if P.ERR.search(l) and not P.ERR_IGNORE.search(l)]
        return {"errors": len(lines), "lines": lines[:20]}

    # ---- regression
    def run_probes(self, names, mod_dir=None, label="vanilla"):
        """one fresh probe instance (+ mod): the headless probes in one run, the render probes (main menu) in a windowed run"""
        self.install()
        pr = P.GTProbes(self.seat, self.root, self.seat.display, self.gl, label=label)
        try:
            with tempfile.TemporaryDirectory() as t: return pr.session([n for n in names if n != "log_errors"], GC.merged(mod_dir, TOPS, t))
        finally: pr.cleanup()

    def compare(self, spec, base, got): return P.item_result(spec, base, got)

    def version(self):
        v = self.seat.exec(f"{P.GODOT.format(root=self.root)}; $G --version 2>/dev/null | tail -1").stdout.strip()
        return {"game": "godot-tps", "build": f"tps-demo (Godot {v or '?'})", "mod_loader": "project overlay + import (no loader)",
                "image": self.seat.sandbox}
