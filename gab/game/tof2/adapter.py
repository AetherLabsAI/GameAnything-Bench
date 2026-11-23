"""Tanks of Freedom II adapter (P1X-in/tanks-of-freedom-ii dbe629e = 1.0.9, run from source on the official Godot 4.4.1; no
binary release exists). Install: <seat home>/oss-tof2/{src (+ .godot import cache), godot/Godot_v4.4.1-stable_linux.x86_64} --
the verified reference install and the image's; `install()` reproduces it on another seat sandbox. MIT game (music / sfx
CC BY-SA / CC BY), no EULA / ToS dialog, no telemetry. Network off: online maps / relay / LAN play are never opened (nothing
connects at start). First run answered in setup: user://settings.json seeded with the intro camera flight and the
controls overlay off (the rest stays at the game's defaults); a fresh start shows the main menu.

A fresh instance per mod (<root>/gab/<inst>/game: assets hard-linked to src, scenes / scripts / campaign / import cache copied,
the mod laid over it, re-imported when it brings non-text assets), a fresh user dir per start (+ the mod's user/ files);
the game runs windowed 1280x720 at 0,0 on the seat display, Compatibility (OpenGL) renderer (Vulkan crashed on the pool seats).

Deliverable mod/ (devkit/README.md): files laid over the project (res://), same paths (replace or add): skirmish maps
assets/maps/skirmish/<name>.tofmap.json, campaigns assets/campaigns/<name>/ (campaign.json + maps/*.tofmap.json with their
story scripts) or campaign/<name>/ (custom campaigns), scenes / scripts / assets; a top-level user/ goes to user:// (custom
maps <name>.tofmap.json, saves).

state() (docs/STATE_SPEC.md) through the GABState autoload (gab/game/godot_state.gd + state_engine.gd), live from the running
game: engine = {screen: main_menu | board | map_editor | other, panel, fps, uptime_s; on the board: mode, map, campaign, mission,
turn, turn_limit, players (side, type, team, ap, alive, units, buildings), current, cursor, selected, units {count, nearest 20},
buildings (side:type -> n), story {shown, text}, summary_shown, triggers (name: suspended, one_off)}; mod = {nodes / nodes_total /
autoloads / input_actions (generic, as godot-tps), maps (added / replaced map files), campaigns (added / replaced: progress),
current_map_from_mod}; screen_text = visible Label / Button / RichTextLabel texts (objectives, story dialog, AP, menus).
"""
import json, shlex, tempfile, time
from pathlib import Path

from .. import base as gbase
from ..base import GameAdapter
from .. import godot_common as GC
from . import probes as P
from gab.game.base import vglrun

HERE = Path(__file__).parent
GODOT_ZIP = "https://github.com/godotengine/godot/releases/download/4.4.1-stable/Godot_v4.4.1-stable_linux.x86_64.zip"
TOF_GIT, TOF_COMMIT = "https://github.com/P1X-in/tanks-of-freedom-ii.git", "dbe629ea05691599dde4fd65ab9a13b78a4ccfe1"
APPNAME = "Tanks of Freedom II"
SETTINGS = {"show_intro": False, "show_controls": False}
RESERVED = (".godot", "_probe", "gab_state")
TOPS = ("assets", "scenes", "scripts", "campaign", "user", "project.godot", "override.cfg")


class Adapter(GameAdapter):
    name = "tof2"
    title = "Tanks of Freedom II"
    screen = (1280, 720)

    def __init__(self, seat, root=None):
        super().__init__(seat)
        self.root = root or f"{seat.home}/oss-tof2"
        self.g = f"{self.root}/{P.GODOT_BIN}"
        self.gl = vglrun()

    def requirements(self):
        return {"game": "tof2", "gpu": True, "mem_gb": 1.5, "steam": False, "hosts": ["pool", "docker", "local"],
                "image": "gameanything/tof2:1.0.9", "eula": None}

    def idir(self, inst): return f"{self.root}/gab/{inst}"
    def udir(self, inst): return f"{self.idir(inst)}/data/godot/app_userdata/{APPNAME}"
    def paths(self, inst):
        d = self.idir(inst)
        return {"vanilla_project": f"{self.root}/src", "instance_project": f"{d}/game", "user_dir": self.udir(inst),
                "skirmish_maps": f"{self.root}/src/assets/maps/skirmish", "campaigns": f"{self.root}/src/assets/campaigns",
                "game_log": f"{d}/game.log", "godot": self.g}

    # ---- install / instance
    def install(self):
        R = self.root
        ok = lambda: self.seat.exec(f"test -x {self.g} && test -f {R}/src/project.godot && test -d {R}/src/.godot/imported && echo ok").stdout.strip() == "ok"
        if not ok():                                                     # as docker/tof2/install.sh
            self.seat.exec(f"set -e; mkdir -p {R}/godot && cd {R}/godot && curl -sSL -o g.zip {GODOT_ZIP} && unzip -qo g.zip && rm g.zip && "
                           f"cd {R} && git clone -q {TOF_GIT} src && cd src && git checkout -q {TOF_COMMIT} && rm -rf docs && "
                           f"for i in 1 2; do timeout 3000 {self.g} --headless --path . --import > /dev/null 2>&1 || true; done", timeout=7200, check=True)
            if not ok(): raise RuntimeError("Tanks of Freedom II install failed")
        self.seat.exec(f"mkdir -p {R}/gab {R}/inst {R}/bridge")
        self.seat.put(f"{R}/bridge/gab_state.gd", GC.bridge(HERE / "state_engine.gd"))
        return True

    def provision(self, inst):
        self.install(); self.stop(inst)
        self.seat.exec(f"rm -rf {self.idir(inst)} && mkdir -p {self.idir(inst)}", check=True)
        self._build(inst)
        return {"instance": inst, "dir": self.idir(inst), "display": self.seat.display}

    def _build(self, inst):
        """<inst>/game: assets hard-linked, the rest copied, the staged mod over it (a replaced asset's .import link is broken
        too, so a re-import never writes through to src), the state bridge autoload; re-import if the mod brings binary assets"""
        d, R = self.idir(inst), self.root
        self.seat.put(f"{R}/bridge/gab_state.gd", GC.bridge(HERE / "state_engine.gd"))          # always the current bridge
        r = self.seat.exec(f"""set -e; I={d}/game; rm -rf $I {d}/state; mkdir -p $I
            cp -al {R}/src/assets $I/assets
            cp -a {R}/src/.godot {R}/src/scenes {R}/src/scripts {R}/src/campaign {R}/src/project.godot $I/
            imp=0
            if [ -d {d}/mod ]; then cd {d}/mod; while read -r f; do f=${{f#./}}; case "$f" in user/*) continue;; esac
                mkdir -p "$I/$(dirname "$f")"; cp --remove-destination "$f" "$I/$f"
                [ -e "{R}/src/$f.import" ] && cp --remove-destination "{R}/src/$f.import" "$I/$f.import"
                case "$f" in *.json|*.gd|*.tscn|*.tres|*.cfg|*.txt|*.md|*.csv|*.uid) ;; *) imp=1;; esac
              done < <(find . -type f); cd {d}; fi
            mkdir -p $I/gab_state; cp {R}/bridge/gab_state.gd $I/gab_state/gab_state.gd
            if [ -f $I/override.cfg ]; then cp --remove-destination $I/override.cfg $I/override.cfg.tmp; mv $I/override.cfg.tmp $I/override.cfg; fi
            printf '\\n[autoload]\\n\\nGABState="*res://gab_state/gab_state.gd"\\n' >> $I/override.cfg
            {GC.meta_sh(f"{R}/src", f"{d}/mod", f"{d}/state")}
            if [ $imp = 1 ]; then mkdir -p {d}/imp; XDG_DATA_HOME={d}/imp XDG_CONFIG_HOME={d}/imp XDG_CACHE_HOME={d}/imp timeout 900 {self.g} --headless --path $I --import > {d}/import.log 2>&1 || true; rm -rf {d}/imp; fi""",
                           timeout=1200)
        if r.returncode: raise RuntimeError(f"instance build failed: {r.stdout[-300:]} {r.stderr[-600:]}")

    def install_mod(self, inst, mod_dir):
        """the mod's files laid over a fresh copy of the project, user/ into the player's user dir at every start"""
        d = self.idir(inst); self.stop(inst)
        self.seat.exec(f"rm -rf {d}/mod")
        with tempfile.TemporaryDirectory() as t:
            m = GC.merged(mod_dir, TOPS, t)
            files = [p for p in m.rglob("*") if p.is_file()] if m else []
            if m: self.seat.copy_in(m, f"{d}/mod")
        self._build(inst)
        return {"files": len(files), "mods": [x.name for x in GC.layout(mod_dir, TOPS)]}

    def build(self, inst, mod_dir=None):
        """structure check of the deliverable (JSON maps / campaigns parse, map files in the right places) -> (ok, log)"""
        if mod_dir is None: return True, "no mod dir given"
        files = [(d, p) for d in GC.layout(mod_dir, TOPS) for p in d.rglob("*") if p.is_file()]; L = []; maps, camps = [], set()
        if not files: L.append("mod/ is empty: it holds files laid over the project (same paths as res://; user/ -> user://)")
        for m, f in files:
            rel = f.relative_to(m); top = rel.parts[0]
            if top in RESERVED: L.append(f"{rel}: {top}/ is reserved (import cache / evaluation bridge)")
            if f.suffix == ".json":
                try: j = json.loads(f.read_text())
                except ValueError as e: L.append(f"{rel}: JSON error {e}"); continue
                if f.name.endswith(".tofmap.json"):
                    maps.append(str(rel))
                    if not isinstance(j, dict) or "tiles" not in j: L.append(f"{rel}: a .tofmap.json needs a `tiles` object (save one with the map editor)")
                if f.name == "campaign.json":
                    camps.add(rel.parts[-2])
                    if not isinstance(j, dict) or not j.get("missions"): L.append(f"{rel}: campaign.json needs a `missions` list")
            if f.suffix == ".import": L.append(f"{rel}: .import files are made by the import; ship the asset itself")
            if f.suffix in (".tscn", ".tres") and not f.read_text(errors="replace").lstrip().startswith("[gd_"): L.append(f"{rel}: not a Godot text resource")
        return not L, "\n".join(L) or f"ok: {len(files)} files, maps {maps[:10]}, campaigns {sorted(camps)}"

    def load_check(self, inst, out_dir=None):
        """the gate: the probe bridge's `maps`, `campaign` and `menu` runs in a modded probe instance (every skirmish map and
        campaign mission starts, story scripts run, the menu lists them; no crash, no script stop), then the real game comes up"""
        pr = P.TOFProbes(self.seat, self.root, label=f"gate-{inst}", log=lambda s: None)
        d = self.idir(inst)
        try:
            has = self.seat.exec(f"test -d {d}/mod && echo y").stdout.strip() == "y"
            pr.build()
            if has: self.seat.exec(f"{self.root}/bridge/applymod.sh {pr.name} {d}/mod", timeout=1200, check=True)
            g = pr.run(["maps", "menu", "campaign"])
        finally: pr.cleanup()
        g = {p: v for p, v in g.items() if isinstance(v, dict) and not p.startswith("_")}
        bad = {p: v.get("error") or v.get("_crashed") for p, v in g.items() if v.get("error") or v.get("_crashed")}
        E = gbase.error_lines("\n".join(l for v in g.values() for l in v.get("_error_lines") or []), r".")
        info = {p: {"errors": v.get("_log_errors", 0), **({"problem": bad[p]} if p in bad else {})} for p, v in g.items()}
        info["maps"] = {**info.get("maps", {}), "stock_maps": len(g.get("maps", {}).get("stock_maps") or [])}
        hung = lambda x: str(x).startswith("rc=?")
        if bad: return False, gbase.gate_log(E, "headless load test failed: " + "; ".join(
            f"{p} run: " + ("did not finish (crashed or hung)" if hung(x) else str(x)) for p, x in bad.items())[:600], json.dumps(info)[:2500])
        ok, log = self.start(inst)
        E += [x for x in GC.error_lines(self.seat.exec(f"cat {self.idir(inst)}/game.log 2>/dev/null").stdout, P.ERR, P.ERR_IGNORE) if x not in E]
        self.stop(inst)
        why = None if ok else "the game did not reach its main menu: " + (log.splitlines() or ["?"])[0][:300]
        return ok, gbase.gate_log(E[:gbase.ERR_N], why, json.dumps(info)[:1500] + "\ngame: " + log[-1500:])

    def start(self, inst):
        """fresh user dir (seeded settings + the mod's user/ files) -> the game on the seat display; ok when the main menu (or
        the mod's first scene) is up"""
        d = self.idir(inst); u = self.udir(inst); self.stop(inst)
        if self.seat.exec(f"test -f {d}/game/project.godot && echo y").stdout.strip() != "y": self._build(inst)
        self.seat.exec(f"rm -rf {d}/data && mkdir -p '{u}' && ([ -d {d}/mod/user ] && cp -r {d}/mod/user/. '{u}/' || true) && "
                       f"rm -f {d}/state/req {d}/state/state.json {d}/state/last.json", check=True)
        self.seat.put(f"{u}/settings.json", json.dumps(SETTINGS))
        env = f"DISPLAY={self.seat.display} XDG_DATA_HOME={d}/data XDG_CONFIG_HOME={d}/data/config XDG_CACHE_HOME={d}/data/cache GAB_STATE_DIR={d}/state"
        run = (f"exec env {env} {self.gl} {self.g} --path {d}/game --rendering-method gl_compatibility --rendering-driver opengl3 "
               f"--display-driver x11 --windowed --resolution {self.screen[0]}x{self.screen[1]} --position 0,0")
        # cwd = the project: the game finds custom campaigns under ./campaign; setsid -f: own process group (stop() kills it)
        self.seat.exec(f"cd {d}/game && setsid -f bash -c {shlex.quote(f'echo $$ > {d}/game.pid; ' + run)} > {d}/game.log 2>&1 < /dev/null")
        t0 = time.time(); last = None
        while time.time() - t0 < 240:
            time.sleep(3)
            alive, s = GC.read_state(self.seat, f"{d}/state", f"{d}/game.pid")
            if not alive: return False, "the game exited\n" + self.log_tail(inst)
            if s: last = s["engine"].get("screen")
            if last in ("main_menu", "board", "map_editor", "other"): time.sleep(2); return True, f"first screen: {last}"
        return False, f"no first screen in 240 s (bridge: {last})\n" + self.log_tail(inst)

    def stop(self, inst):
        self.seat.exec(f"p=$(cat {self.idir(inst)}/game.pid 2>/dev/null); [ -n \"$p\" ] && {{ kill -- -$p 2>/dev/null; sleep 1; kill -9 -- -$p 2>/dev/null; }}; "
                       f"rm -f {self.idir(inst)}/game.pid; true")

    def log_tail(self, inst, n=40): return self.seat.exec(f"tail -{n} {self.idir(inst)}/game.log 2>/dev/null").stdout

    def state(self, inst):
        d = self.idir(inst); return GC.state_of(*GC.read_state(self.seat, f"{d}/state", f"{d}/game.pid"))

    def log_errors(self, inst):
        lines = P.log_errors(self.seat.exec(f"cat {self.idir(inst)}/game.log 2>/dev/null").stdout)
        return {"errors": len(lines), "kinds": sorted(set(lines))[:20]}

    # ---- regression
    def run_probes(self, names, mod_dir=None, label="vanilla"):
        """one fresh probe instance (+ mod), every asked probe its own short headless run (TOFProbes)"""
        self.install()
        pr = P.TOFProbes(self.seat, self.root, label=label)
        want = sorted({P.SUB.get(n, (n, None))[0] for n in names if n != "log_errors"})   # produce / move / capture / win are views
        try: return pr.session(want or None, GC.layout(mod_dir, TOPS) if mod_dir else [])
        finally: pr.cleanup()

    def compare(self, spec, base, got): return P.item_result(spec, base, got)

    def version(self):
        v = self.seat.exec(f"{self.g} --version 2>/dev/null | tail -1; grep -m1 'config/version' {self.root}/src/project.godot").stdout.split("\n")
        return {"game": "tof2", "build": f"Tanks of Freedom II {v[1].split('=')[-1].strip(chr(34)) if len(v) > 1 else '?'} (Godot {v[0].strip() or '?'})",
                "mod_loader": "project overlay (+ user://)", "image": self.seat.sandbox}
