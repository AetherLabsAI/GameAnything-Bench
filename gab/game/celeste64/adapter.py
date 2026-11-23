"""Celeste 64: Fragments of the Mountain v1.1.1 adapter. Install (under <seat home>/oss-celeste64/gab, next to the verified pool
reference install): src/ = the upstream source, tag v1.1.1 (github.com/ExOK/Celeste64; copied from the pool's vanilla-src/ or
cloned), + the GameAnything bridge (bridge/C64Probe.cs, a Foster module that does nothing unless $C64_PROBE / $C64_STATE is
set; one hook line in Program.cs, one in UI.Text), build/ = `dotnet build -c Release` of it (.NET 8 SDK of the pod). MIT code
+ the game's own content licence; no EULA / ToS / account / network use; a fresh HOME per start means no first-run state (the
game asks nothing on its first start). Software GL (llvmpipe) on the seat's X display (the pool's choice: the shared GPU X
server ran it at 5-9 updates/s), audio to an ALSA null device; the game opens fullscreen = the 1280x720 seat screen.

Deliverable mod/ (devkit/README.md):  Content/  files laid over the stock Content/ (Maps/*.map, Levels.json, Text/*.json,
Models, Textures, Sprites ...)  |  Source/  C# files laid over the stock Source/ (new files, or replaced stock files): the
game is rebuilt from it (builds cached by the hash of the Source/ files). Several such directories one level down are applied
in name order. A fresh start is the title screen.

state() (docs/STATE_SPEC.md), read live through the bridge's state server (<run>/state/req -> state.json, ~0.1-0.3 s):
  engine: screen (scene on top: Startup | Titlescreen | Overworld | World), transition, paused, level_id, map, checkpoint,
          submap, world_time_s, player {pos, vel, state, on_ground, dashes, dead, facing} | null, record {strawberries,
          strawberry_ids, deaths, time_s, checkpoint, completed_submaps, flags (stock NPC talk flags)} | null,
          strawberries_total, actors (counts of stock actor types in the world), menu (pause menu title / items / index, or
          the overworld's state / selected level / levels)
  mod:    actor_counts + actors (nearest 20 actors whose type the stock game does not have: type, pos, dist, public fields),
          map (when not a stock map), flags (save-record flags the stock game does not use, "LEVEL:flag"), statics (static
          fields of C# types the stock game does not have), maps / levels the stock game does not have
  screen_text: every UI.Text drawn in the last 300 ms (HUD counters, menus, level cards, prompts) + "dialog: <visible part of
          the current dialog line>"
"""
import hashlib, json, re, shlex, time
from pathlib import Path

from .. import base as gbase
from ..base import GameAdapter
from . import probes as P

GIT = "https://github.com/ExOK/Celeste64.git"
TAG = "v1.1.1"
BRIDGE = Path(__file__).parent / "bridge"
GATE_PROBES = ["boot", "hub", "maps"]
MAX_BUILDS = 3


CS_ERR = r": error [A-Z]+\d+"                   # dotnet build errors (each printed twice: deduplicated)


def cs_errors(build_log):
    """dotnet build output -> its error lines, paths shortened to Source/... and the project suffix dropped"""
    return gbase.error_lines(re.sub(r" \[[^\]\n]*\.csproj\]", "", re.sub(r"/\S*?/src/Source/", "Source/", build_log)), CS_ERR)

class Adapter(GameAdapter):
    name = "celeste64"
    title = "Celeste 64: Fragments of the Mountain"
    screen = (1280, 720)            # fullscreen on the 1280x720 seat screen

    def __init__(self, seat, root=None):
        super().__init__(seat)
        self.root = root or f"{seat.home}/oss-celeste64"
        self.g = f"{self.root}/gab"

    def requirements(self):
        return {"game": "celeste64", "gpu": True, "mem_gb": 1.0, "steam": False, "hosts": ["pool", "docker", "local"],
                "image": "gameanything/celeste64:1.1.1", "eula": None, "note": "renders with software GL (llvmpipe); .NET 8 SDK for C# mods"}

    def mdir(self, inst): return f"{self.g}/inst/{inst}"
    def rdir(self, inst): return f"{self.mdir(inst)}/run"
    def paths(self, inst): return {"stock_source": f"{self.g}/src/Source", "stock_content": f"{self.g}/src/Content", "instance_dir": self.mdir(inst),
                                   "game_dir": f"{self.rdir(inst)}/game", "user_dir (save.json)": f"{self.rdir(inst)}/home/Celeste64",
                                   "game_log": f"{self.rdir(inst)}/game.log", "last_build_log": f"{self.mdir(inst)}/build.log"}

    # ---- install / instance
    def _bridge_hash(self): return hashlib.sha256(b"".join((BRIDGE / f).read_bytes() for f in ("C64Probe.cs", "apply_bridge.sh"))).hexdigest()[:16]

    def install(self):
        """source + bridge + build, once per sandbox (the build again when the bridge changed)"""
        g = self.g
        if self.seat.exec(f"test -f {g}/src/Celeste64.csproj && echo ok").stdout.strip() != "ok":
            self.seat.exec(f"mkdir -p {g} && rm -rf {g}/src.tmp && (test -d {self.root}/vanilla-src/Source && mkdir {g}/src.tmp && "
                           f"cp -r {self.root}/vanilla-src/Source {self.root}/vanilla-src/Content {self.root}/vanilla-src/Celeste64.csproj "
                           f"{self.root}/vanilla-src/Celeste64.sln {g}/src.tmp/ || git clone -q --branch {TAG} --depth 1 {GIT} {g}/src.tmp) && "
                           f"rm -rf {g}/src.tmp/.git && mv {g}/src.tmp {g}/src", timeout=1800, check=True)
        for f in ("C64Probe.cs", "apply_bridge.sh"): self.seat.put(f"{g}/bridge/{f}", (BRIDGE / f).read_text())
        if self.seat.exec(f"test -f {g}/bridge/vanilla.txt && echo ok").stdout.strip() != "ok":   # what the stock game has (state: mod vs stock)
            self.seat.exec(f"cd {g}/src && {{ grep -rhoE '\\b(class|struct|enum|interface|record)\\s+((struct|class)\\s+)?[A-Za-z_][A-Za-z0-9_]*' Source "
                           f"--include=*.cs --exclude-dir=Probe | awk '{{print \"type \" $NF}}'; ls Content/Maps | sed -n 's/\\.map$//p' | sed 's/^/map /'; "
                           f"python3 -c 'import json; [print(\"level\", l[\"ID\"]) for l in json.load(open(\"Content/Levels.json\", encoding=\"utf-8-sig\"))]'; "
                           f"grep -rhoE 'TALK_FLAG = \"[A-Za-z0-9_]+\"' Source | cut -d'\"' -f2 | sed 's/^/flag /'; }} | sort -u > {g}/bridge/vanilla.txt", check=True)
        h = self._bridge_hash()
        if self.seat.exec(f"cat {g}/build.stamp 2>/dev/null").stdout.strip() != h or self.seat.exec(f"test -x {g}/build/Celeste64 && echo ok").stdout.strip() != "ok":
            ok, log = P.build(self.seat, f"{g}/bridge", f"{g}/src", f"{g}/build")
            if not ok: raise RuntimeError("Celeste 64 build failed:\n" + log)
            self.seat.put(f"{g}/build.stamp", h)
        return True

    def provision(self, inst):
        self.install(); self.stop(inst)
        self.seat.exec(f"rm -rf {self.mdir(inst)} && mkdir -p {self.mdir(inst)}/mod", check=True)
        self.seat.put(f"{self.mdir(inst)}/mod.json", json.dumps({"units": [], "src": None}))
        return {"instance": inst, "dir": self.mdir(inst), "display": self.seat.display}

    @staticmethod
    def _layout(mod_dir):
        """-> the mod's units: mod/ itself when it holds Content/ or Source/, else its subdirectories that do (name order)"""
        m = Path(mod_dir) if mod_dir else None
        if not m or not m.exists(): return []
        unit = lambda p: (p / "Content").is_dir() or (p / "Source").is_dir()
        return [m] if unit(m) else sorted(p for p in m.iterdir() if p.is_dir() and unit(p))

    @staticmethod
    def _src_hash(units):
        """hash of every Source/ file of the mod (None: no C# change, the stock build is used)"""
        fs = [(f.relative_to(u / "Source").as_posix(), f) for u in units if (u / "Source").is_dir() for f in sorted((u / "Source").rglob("*")) if f.is_file()]
        if not fs: return None
        h = hashlib.sha256()
        for r, f in fs: h.update(r.encode() + b"\0" + f.read_bytes() + b"\0")
        return h.hexdigest()[:16]

    def _stage(self, d, units):
        """copy the units into the sandbox dir d (d/00_name/{Content,Source}) -> unit dirs"""
        self.seat.exec(f"rm -rf {d} && mkdir -p {d}")
        out = []
        for i, u in enumerate(units):
            dst = f"{d}/{i:02d}_{re.sub(r'[^A-Za-z0-9_.-]', '_', u.name)}"
            self.seat.exec(f"mkdir -p {dst}")
            for sub in ("Content", "Source"):
                if (u / sub).is_dir(): self.seat.copy_in(u / sub, f"{dst}/{sub}")
            out.append(dst)
        return out

    def install_mod(self, inst, mod_dir):
        """the mod's Content/ and Source/ into the instance; start() lays them over a fresh copy of the game (and rebuilds for
        Source/) -- what a player does when dropping files into the game folder / building a code mod"""
        units = self._layout(mod_dir)
        dirs = self._stage(f"{self.mdir(inst)}/mod", units)
        self.seat.put(f"{self.mdir(inst)}/mod.json", json.dumps({"units": dirs, "src": self._src_hash(units)}))
        return {"units": [u.name for u in units], "csharp": bool(self._src_hash(units))}

    def _mod(self, inst):
        r = self.seat.exec(f"cat {self.mdir(inst)}/mod.json 2>/dev/null").stdout.strip()
        return json.loads(r) if r else {"units": [], "src": None}

    def _binaries(self, units, src_hash, logf):
        """-> (ok, dir with the game binaries, log): the stock build, or a build of the stock source + the mods' Source/"""
        if not src_hash: return True, f"{self.g}/build", "stock build"
        b = f"{self.g}/builds/{src_hash}"
        if self.seat.exec(f"test -x {b}/out/Celeste64 && echo ok").stdout.strip() == "ok": return True, f"{b}/out", "cached build"
        self.seat.exec(f"rm -rf {b} && mkdir -p {b}/src && cp -r {self.g}/src/Source {self.g}/src/Celeste64.csproj {self.g}/src/Celeste64.sln {b}/src/ && "
                       + " && ".join(f"(test -d {u}/Source && cp -r {u}/Source/. {b}/src/Source/ || true)" for u in units or ["/nonexistent"]), check=True)
        ok, log = P.build(self.seat, f"{self.g}/bridge", f"{b}/src", f"{b}/out")
        self.seat.exec(f"cp {b}/out.log {logf} 2>/dev/null; rm -rf {b}/src; " + (f"rm -rf {b}" if not ok else
                       f"cd {self.g}/builds && ls -1t | tail -n +{MAX_BUILDS + 1} | xargs -r rm -rf"))
        return ok, f"{b}/out", log

    def build(self, inst, mod_dir=None):
        """structure check of the deliverable -> (ok, log); the C# build and the load test are load_check()"""
        if mod_dir is None: return True, "no mod dir given"
        units = self._layout(mod_dir); L = []
        if not units: L.append("mod/ holds no Content/ or Source/ directory (nor subdirectories that do)")
        for u in units:
            c = u / "Content"
            for f in (c.rglob("*") if c.is_dir() else []):
                if f.is_dir(): continue
                r = f.relative_to(c).as_posix()
                if " " in r: L.append(f"{u.name}/Content/{r}: no spaces in paths")
                if f.suffix == ".json":
                    try: d = json.loads(f.read_text(encoding="utf-8-sig"))
                    except (ValueError, UnicodeDecodeError) as e: L.append(f"{u.name}/Content/{r}: not valid JSON ({str(e)[:80]})"); continue
                    if r == "Levels.json" and (not isinstance(d, list) or not all(isinstance(x, dict) and {"ID", "Map"} <= set(x) for x in d)):
                        L.append(f"{u.name}/Content/Levels.json: must be a list of levels with ID, Name, Label, Strawberries, Preview, Map")
                if f.suffix == ".map" and "classname" not in f.read_text(errors="replace")[:20000]: L.append(f"{u.name}/Content/{r}: not a .map file (no entities)")
            s = u / "Source"
            for f in (s.rglob("*") if s.is_dir() else []):
                if f.is_file() and f.suffix not in (".cs", ".so", ".dll", ".txt", ".md"): L.append(f"{u.name}/Source/{f.relative_to(s)}: unexpected file type")
            if s.is_dir() and not any(s.rglob("*.cs")): L.append(f"{u.name}/Source/ holds no .cs file")
        return not L, "\n".join(L) or f"ok: {[u.name for u in units]}" + (" (C# changes: rebuilt in the load test)" if self._src_hash(units) else "")

    def load_check(self, inst, out_dir=None):
        """the gate: (C# build) + one probe run with the mod: the content loads (boot), title -> overworld -> the first level
        with the Confirm key (hub), every map incl. the mod's loads and the player spawns alive (maps)"""
        md = self._mod(inst)
        ok, bins, blog = self._binaries(md["units"], md.get("src"), f"{self.mdir(inst)}/build.log")
        if not ok: return False, gbase.gate_log(cs_errors(blog), "the C# build of the mod's Source/ failed", "C# build failed:\n" + blog[-3000:])
        d = f"{self.mdir(inst)}/gate"
        P.prepare_game(self.seat, d, bins, f"{self.g}/src/Content", [f"{u}/Content" for u in md["units"]])
        try: r = P.session(self.seat, d, GATE_PROBES, display=self.seat.display)
        finally: self.seat.exec(f"rm -rf {d}")
        L = [f"{n}: {r[n]['error']}" for n in GATE_PROBES if isinstance(r.get(n), dict) and "error" in r[n]]
        if not r.get("alive_at_end"): L.append(f"the game crashed or hung: {r.get('_runs')}")
        if not (r.get("hub") or {}).get("entered"): L.append(f"title -> overworld -> first level did not reach the level: {r.get('hub')}")
        for m, x in ((r.get("maps") or {}).get("maps") or {}).items():
            if not (x.get("loaded") and x.get("player") and x.get("player_alive")): L.append(f"map {m}: {json.dumps(x)[:300]}")
        summ = {"hub": r.get("hub"), "maps": sorted(((r.get("maps") or {}).get("maps") or {})), "levels": [l.get("id") for l in (r.get("boot") or {}).get("levels") or []],
                "log_errors": r.get("log_errors"), "build": blog[-300:]}
        return not L, gbase.gate_log(r.get("_error_lines") or [], "; ".join(L)[:600] or None,
                                     ("\n".join(L) + "\n" if L else "ok: ") + json.dumps(summ, default=str)[:3000])

    def start(self, inst):
        """fresh copy of the game (+ the mod) with a fresh HOME on the seat display; ok at the title screen"""
        self.stop(inst); md = self._mod(inst)
        ok, bins, blog = self._binaries(md["units"], md.get("src"), f"{self.mdir(inst)}/build.log")
        if not ok: return False, "C# build failed:\n" + blog
        d = self.rdir(inst)
        P.prepare_game(self.seat, d, bins, f"{self.g}/src/Content", [f"{u}/Content" for u in md["units"]])
        self.seat.exec(f"mkdir -p {d}/state")
        cmd = (f"echo $$ > {d}/game.pid; cd {d}/game && HOME={d}/home XDG_DATA_HOME={d}/home DISPLAY={self.seat.display} C64_STATE={d}/state "
               f"C64_VANILLA={self.g}/bridge/vanilla.txt LIBGL_ALWAYS_SOFTWARE=1 SDL_VIDEO_X11_FORCE_EGL=1 __GL_SYNC_TO_VBLANK=0 vblank_mode=0 exec ./Celeste64 > {d}/game.log 2>&1")
        self.seat.exec(f"setsid -f bash -c {shlex.quote(cmd)} > /dev/null 2>&1 < /dev/null")
        t0 = time.time()
        while time.time() - t0 < 120:
            time.sleep(2)
            st = self.state(inst)
            if not st.get("running"): return False, "the game exited\n" + self.log_tail(inst)
            e = st.get("engine") or {}
            if e.get("screen") == "Titlescreen" and not e.get("transition"):
                self._x(inst, "xdotool mousemove 1279 719")     # keyboard focus follows the pointer (no window manager)
                time.sleep(1); return True, "title screen"
        return False, "no title screen after 120 s\n" + self.log_tail(inst)

    def _pid(self, inst): return self.seat.exec(f"cat {self.rdir(inst)}/game.pid 2>/dev/null").stdout.strip()

    def stop(self, inst):
        """kill the game's own process group (setsid: pgid = the recorded pid)"""
        self.seat.exec(f"p=$(cat {self.rdir(inst)}/game.pid 2>/dev/null); [ -n \"$p\" ] && {{ kill -- -$p 2>/dev/null; sleep 1; kill -9 -- -$p 2>/dev/null; }}; "
                       f"rm -f {self.rdir(inst)}/game.pid; true")

    def log_tail(self, inst, n=40): return self.seat.exec(f"tail -{n} {self.rdir(inst)}/game.log 2>/dev/null").stdout

    def state(self, inst):
        """one request to the bridge's state server (answered within 6 game frames) + the process status, in one exec"""
        s = f"{self.rdir(inst)}/state"
        r = self.seat.exec(f"p=$(cat {self.rdir(inst)}/game.pid 2>/dev/null); if [ -n \"$p\" ] && ps -o stat= -p $p | grep -qv Z; then echo UP; "
                           f"rm -f {s}/state.json; touch {s}/req; for i in $(seq 1 25); do [ -f {s}/state.json ] && break; sleep 0.1; done; "
                           f"cat {s}/state.json 2>/dev/null; else echo DOWN; fi", timeout=20).stdout
        up = r.startswith("UP"); body = r[2:].strip() if up else ""
        try: st = json.loads(body) if body else {"error": "no answer from the game within 2.5 s (loading?)" if up else "the game is not running"}
        except ValueError: st = {"error": f"bad answer: {body[:200]}"}
        return {"running": up, "engine": st.get("engine") or {}, "mod": st.get("mod") or {}, "screen_text": st.get("screen_text") or [],
                **({"error": st["error"]} if "error" in st else {})}

    def input(self, inst, s):
        """a key tap is a short press (~0.1 s): the game reads key state once per 60 Hz frame"""
        if s["do"] == "key":
            return self._x(inst, " ; ".join(f"xdotool keydown {shlex.quote(x)}; sleep 0.1; xdotool keyup {shlex.quote(x)}; sleep 0.05" for x in str(s["keys"]).split()))
        return super().input(inst, s)

    def log_errors(self, inst):
        t = self.seat.exec(f"cat {self.rdir(inst)}/game.log 2>/dev/null").stdout
        lines = [l for l in t.splitlines() if P.ERR.search(l) and not P.ERR_IGNORE.search(l)]
        return {"errors": len(lines), "lines": lines[:20]}

    # ---- regression
    def run_probes(self, names, mod_dir=None, label="vanilla"):
        """all probes in one fresh probe instance (+ mod) with the probe arena map, as probes_celeste64 session()"""
        d = f"{self.g}/probe/{re.sub(r'[^A-Za-z0-9_.-]', '_', label)}"
        units = self._layout(mod_dir)
        dirs = self._stage(f"{d}-mod", units)
        try:
            ok, bins, blog = self._binaries(dirs, self._src_hash(units), f"{d}-build.log")
            if not ok: return {"_build": blog[-2000:], "alive_at_end": False, "log_errors": None, **{n: {"error": "C# build failed"} for n in names if n != "log_errors"}}
            P.prepare_game(self.seat, d, bins, f"{self.g}/src/Content", [f"{u}/Content" for u in dirs], arena=True)
            return P.session(self.seat, d, [n for n in names if n != "log_errors"], display=self.seat.display)
        finally: self.seat.exec(f"rm -rf {d} {d}-mod {d}-build.log")

    def compare(self, spec, base, got): return P.item_result(spec, base, got)

    def version(self):
        return {"game": "celeste64", "build": "1.1.1", "mod_loader": "Content/ overlay + C# Source/ rebuild (.NET 8)", "image": self.seat.sandbox,
                "bridge": self._bridge_hash()}
