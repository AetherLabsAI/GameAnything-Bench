"""Terraria via tModLoader (commercial: the user brings Terraria, Steam app 105600, and installs the free tModLoader, Steam app 1281930,
whose EULA they accept in Steam's install dialog; Linux builds). Runs in a Steam-enabled trial container (the
machine's Steam hub, Steam logged on with the user's account; tModLoader needs the running Steam client). Pool verified in game 2026-10-30
(tModLoader v2026.8.3.0 stable on Terraria 1.4.4.9).

Install (`install()`, reproduces the pool's): a PRIVATE copy of the Steam tModLoader folder at <root>/steamapps/common/tModLoader
(root = <seat home>/pool-probes/terraria; Terraria linked read only), so its logs and files never touch the user's own tModLoader;
the harness mods GAProbe (the pool's probe bridge, regression only) and GABState (the live state of STATE_SPEC, every player session)
built with tModLoader's own packer; the runners bin/trp.sh (probes) and bin/gtr.sh (player sessions).

An instance (<root>/gab/<inst>) = its own tModLoader save folder (-tmlsavedirectory: Mods/ with enabled.json, Players/, Worlds/,
config.json seeded so no first-run screen shows: language, tModLoader's Welcome, windowed 1280x720, autosave off), the start world
and player made WITH the instance's mods (a headless server: world from start.yaml's seed / size, or the mod's own world file; the
player like the character creation screen, start-inventory hooks of the mods included), kept in fresh/ so every start / reset is the
same fresh state. The client runs on a private Xvfb (:152+seat) through VirtualGL, single player, straight into the world
(-skipselect) or at the title menu (start.yaml mode: menu). No network play, no Workshop.

Deliverable mod/ (devkit/README.md): one folder per tModLoader mod (mod/<ModName>/ with build.txt, *.cs, assets; folder = mod name),
or built .tmod files, + optional worlds/<name>.wld (+ .twld) and start.yaml {mode: game | menu, world, seed, size, player}.
"""
import hashlib, json, re, shlex, time
from pathlib import Path

import yaml

from .. import base as gbase
from ..base import GameAdapter
from . import probes as P

HERE = Path(__file__).parent
STEAM_COMMON = ".steam/debian-installation/steamapps/common"
NAME_RE = re.compile(r"^[A-Za-z][A-Za-z0-9_]*$")
RESERVED = {"ModLoader", "Terraria", "GABState", "GAProbe", "tModLoader"}
START_KEYS = {"mode", "world", "seed", "size", "player"}
SIZES = {"small": 1, "medium": 2, "large": 3}
ENGINE_KEYS = ["screen", "menus_open", "menu_mode", "world", "time", "player", "progress", "npcs", "bosses", "mods"]
LOAD_ERR = re.compile(r"Disabling Mod|An error occurred while loading|Missing texture|failed to load|could not load", re.I)


def shape_state(raw, running=None):
    """GABState's answer -> {running, engine (fixed keys), mod, screen_text} (+ error)"""
    raw = raw if isinstance(raw, dict) else {}
    e = raw.get("engine") if isinstance(raw.get("engine"), dict) else {}
    out = {"running": bool(raw.get("running", running)), "engine": {k: e.get(k) for k in ENGINE_KEYS},
           "mod": raw.get("mod") if isinstance(raw.get("mod"), dict) else {},
           "screen_text": [str(x)[:200] for x in (raw.get("screen_text") or []) if isinstance(x, str)][:30]}
    for k, empty in (("menus_open", []), ("npcs", {}), ("bosses", []), ("mods", [])):
        if not isinstance(out["engine"][k], type(empty)): out["engine"][k] = empty
    if raw.get("error"):
        out["error"] = str(raw["error"])[:300]
        if out["running"] and not e: out["engine"]["screen"] = "starting"
    return out


def error_lines(text, limit=gbase.ERR_N):
    """the gate's / `game build`'s error text: tModLoader log entries at ERROR / FATAL (and WARN carrying an exception, joined with
    their continuation lines), plus the mod loader's own messages (Disabling Mod, An error occurred while loading ...): through
    gab.game.base.error_lines (timestamps off, deduplicated with counts, first `limit`)"""
    strip = lambda e: re.sub(r"^\[[^\]]*\] \[[^\]]*\] ", "", e)
    entries = [strip(e) for e in P.log_error_entries(text or "")]
    heads = {e.split(" | ")[0] for e in entries}
    entries += [strip(l) for l in (text or "").splitlines() if LOAD_ERR.search(l) and strip(l) not in heads]
    return gbase.error_lines("\n".join(entries), r".", r"GAPROBE|GABSTATE", n=limit)


def tree_hash(paths):
    h = hashlib.sha256()
    for root in paths:
        root = Path(root)
        for f in sorted(p for p in root.rglob("*") if p.is_file() and not {"bin", "obj"} & set(p.relative_to(root).parts)):
            h.update(str(f.relative_to(root.parent)).encode()); h.update(f.read_bytes())
    return h.hexdigest()[:16]


class Adapter(GameAdapter):
    name = "terraria"
    title = "Terraria (tModLoader)"
    screen = (1280, 720)
    extra_help = ("  worldgen                     make the instance's start world again with the current mods (next start uses it)\n")

    def __init__(self, seat, root=None):
        super().__init__(seat)
        self.root = root or f"{seat.home}/pool-probes/terraria"
        self.steam = f"{seat.home}/{STEAM_COMMON}"
        self.tml = f"{self.root}/steamapps/common/tModLoader"
        n = int(seat.seat) if str(seat.seat).isdigit() else 1
        self.dn = f":{152 + n}"
        self._ready = False

    def requirements(self):
        return {"game": "terraria", "gpu": True, "mem_gb": 3.0, "steam": True, "hosts": ["pool", "docker"],
                "own_copy": "Terraria (Steam app 105600) and tModLoader (free, Steam app 1281930), Linux builds, in the Steam sandbox's library "
                            "(docker: the Steam hub's, gab/host/steam.py)",
                "eula": "tModLoader EULA: accepted by the user in Steam's install dialog "}

    # ---- paths
    def idir(self, inst): return f"{self.root}/gab/{inst}"
    def display(self, inst): return self.dn
    def paths(self, inst):
        return {"tmodloader": self.tml, "terraria": f"{self.steam}/Terraria", "tmodloader_api_docs": f"{self.tml}/tModLoader.xml",
                "instance": self.idir(inst), "save_dir": f"{self.idir(inst)}/save", "mods": f"{self.idir(inst)}/save/Mods",
                "worlds": f"{self.idir(inst)}/save/Worlds", "players": f"{self.idir(inst)}/save/Players",
                "game_log": f"{self.idir(inst)}/client.log (live: {self.tml}/tModLoader-Logs/client.log)", "build_logs": f"{self.root}/build/"}

    def _sh(self, cmd, timeout=110, check=False):
        # XDG_RUNTIME_DIR must exist: .NET 8 keeps its named mutexes there ('NuGet-Migrations' IOException otherwise; docker 2026-11-05)
        r = self.seat.exec(f"export TRP_ROOT={self.root} XDG_RUNTIME_DIR=${{XDG_RUNTIME_DIR:-/tmp/xdg-runtime}}; "
                           f"mkdir -p -m 700 $XDG_RUNTIME_DIR 2>/dev/null; {cmd}", timeout=timeout)
        if check and r.returncode: raise RuntimeError(f"sandbox command failed ({r.returncode}): {cmd[:160]}\n{r.stdout[-1500:]}\n{r.stderr[-800:]}")
        return r

    def _bg(self, cmd, out, marker, timeout):
        """cmd detached in the sandbox (docker exec calls are cut after ~2 min), poll `out` until a line matches marker -> text"""
        self._sh(f"mkdir -p $(dirname {out}) && rm -f {out}; setsid nohup bash -c {shlex.quote(cmd + f' > {out} 2>&1')} > /dev/null 2>&1 < /dev/null &")
        t0 = time.time()
        while time.time() - t0 < timeout:
            time.sleep(3)
            t = self._sh(f"cat {out} 2>/dev/null").stdout
            if re.search(marker, t, re.M): return t
        return self._sh(f"cat {out} 2>/dev/null").stdout + f"\n(timed out after {timeout} s)"

    # ---- install
    def install(self):
        """the private tModLoader copy, runners and harness mods (once per adapter object; cheap when nothing changed)"""
        if self._ready: return True
        chk = self._sh(f"test -f {self.steam}/tModLoader/tModLoader.dll && echo tml; test -d {self.steam}/Terraria/Content && echo terraria").stdout
        if "tml" not in chk or "terraria" not in chk:
            raise RuntimeError(f"Terraria / tModLoader are not installed in this Steam sandbox ({self.steam}): the user installs Terraria "
                               "(their own copy) and tModLoader (free, its EULA accepted by the user) with their Steam account")
        # the private copy: again when the Steam install changed (tModLoader.dll size + time)
        sig = self._sh(f"stat -c '%s %Y' {self.steam}/tModLoader/tModLoader.dll").stdout.strip()
        have = self._sh(f"cat {self.root}/steamapps/.gab-copy-of 2>/dev/null").stdout.strip()
        if sig != have or "ok" not in self._sh(f"test -f {self.tml}/tModLoader.dll && echo ok").stdout:
            out = self._bg(f"rm -rf {self.tml} && mkdir -p {self.root}/steamapps/common && cp -a {self.steam}/tModLoader {self.tml} && rm -rf {self.tml}/tModLoader-Logs "
                           f"&& ln -sfn {self.steam}/Terraria {self.root}/steamapps/common/Terraria && echo '{sig}' > {self.root}/steamapps/.gab-copy-of && echo COPY-OK",
                           f"{self.root}/copy.out", r"^COPY-OK|rror", 1200)
            if "COPY-OK" not in out: raise RuntimeError("could not copy the Steam tModLoader folder: " + out[-500:])
        for f in ("trp.sh", "gtr.sh"):
            self._put_if_changed(f"{self.root}/bin/{f}", (HERE / "bridge" / f).read_text())
        self._harness("GAProbe", f"{self.root}/mods/GAProbe.tmod")
        self._harness("GABState", f"{self.root}/gab/_harness/GABState.tmod")
        self._ready = True
        return True

    def _put_if_changed(self, path, text):
        if hashlib.md5(text.encode()).hexdigest() not in self._sh(f"md5sum {path} 2>/dev/null").stdout: self.seat.put(path, text)

    def _harness(self, name, tmod):
        """a harness mod built from bridge/<name>/ when its sources changed (md5 kept next to the .tmod)"""
        src = HERE / "bridge" / name; sig = tree_hash([src])
        if sig in self._sh(f"cat {tmod}.src 2>/dev/null").stdout and "ok" in self._sh(f"test -f {tmod} && echo ok").stdout: return
        d = f"{self.root}/gab/_harness/src/{name}"
        self._sh(f"rm -rf {d} && mkdir -p {d}")
        for f in sorted(src.iterdir()):
            if f.is_file(): self.seat.put(f"{d}/{f.name}", f.read_bytes())
        ok, log, built = self._build_one(d, str(Path(tmod).parent))
        if not ok: raise RuntimeError(f"building the harness mod {name} failed:\n{log}")
        self._sh(f"echo {sig} > {tmod}.src")

    def _build_one(self, src_in_sandbox, out_dir):
        """tModLoader's packer (trp.sh build: dotnet build + tModLoader -build) -> (ok, error text, .tmod path)"""
        n = Path(src_in_sandbox).name
        out = self._bg(f"bash {self.root}/bin/trp.sh build {src_in_sandbox} {out_dir}", f"{self.root}/build/{n}.run", r"^built |^BUILD FAILED", 600)
        m = re.search(r"^built (\S+\.tmod)", out, re.M)
        if m: return True, f"built {n}", m.group(1)
        full = self._sh(f"cat {self.root}/build/{n}.log 2>/dev/null").stdout
        clean = "\n".join(re.sub(r"\s+\[[^\]]*\.csproj\]$", "", l).replace(f"{self.root}/build/ModSources/", "") for l in full.splitlines())
        errs = gbase.error_lines(clean, r"error CS\d+|: error |\berror\b|Exception", r"warning|0 Error") or \
            [l for l in out.splitlines() if l.strip() and "BUILD FAILED" not in l and "timed out" not in l][:gbase.ERR_N]
        return False, gbase.gate_log(errs, f"{n}: tModLoader could not build the mod" + (" (timed out)" if "timed out" in out else ""),
                                     full[-3000:] or out[-1500:]), None

    # ---- the deliverable
    @staticmethod
    def _layout(mod_dir):
        """-> (source mod dirs, .tmod files, world files, start dict)"""
        m = Path(mod_dir) if mod_dir else None
        if not m or not m.exists(): return [], [], [], {}
        srcs = sorted(p for p in m.iterdir() if p.is_dir() and (p / "build.txt").exists())
        tmods = sorted(m.glob("*.tmod"))
        worlds = sorted((m / "worlds").glob("*.wld")) + sorted((m / "worlds").glob("*.twld")) if (m / "worlds").is_dir() else []
        st = yaml.safe_load((m / "start.yaml").read_text()) if (m / "start.yaml").exists() else {}
        return srcs, tmods, worlds, st if isinstance(st, dict) else {"_bad": st}

    def check_layout(self, mod_dir):
        """host-side structure check -> list of problems"""
        srcs, tmods, worlds, st = self._layout(mod_dir); L = []
        if not srcs and not tmods:
            L.append("mod/ holds no tModLoader mod: one folder per mod, mod/<ModName>/build.txt (+ *.cs, assets), or a built <ModName>.tmod")
        m = Path(mod_dir)
        for p in sorted(m.iterdir()) if m.exists() else []:
            if p.is_dir() and p.name != "worlds" and p not in srcs: L.append(f"{p.name}/: no build.txt (every mod folder needs one)")
        for s in [*srcs, *tmods]:
            n = s.stem if s.suffix == ".tmod" else s.name
            if not NAME_RE.match(n): L.append(f"{n}: a mod name is letters, digits and _ (folder name = mod name = namespace)")
            if n in RESERVED: L.append(f"{n}: name taken by tModLoader / the harness")
        for s in srcs:
            if not any(s.rglob("*.cs")): L.append(f"{s.name}/: no .cs file")
            for c in s.rglob("*.cs"):
                if re.search(r"^\s*(?:public\s+)?(?:sealed\s+)?class\s+\w+\s*:\s*Mod(?:Item|NPC|Projectile|Tile|Wall|Buff)\b", c.read_text(errors="replace"), re.M):
                    stem = c.with_suffix(".png")
                    txt = c.read_text(errors="replace")
                    if "Texture =>" not in txt and "override string Texture" not in txt and not stem.exists() and not any(c.parent.glob("*.png")):
                        L.append(f"{c.relative_to(m)}: a ModItem / ModNPC / ModTile ... needs a texture PNG named like its class next to it "
                                 f"(or override Texture); tModLoader refuses to load the mod otherwise")
        if st.get("_bad") is not None: L.append("start.yaml must be a mapping")
        for k in set(st) - START_KEYS - {"_bad"}: L.append(f"start.yaml: unknown key {k} (have: {', '.join(sorted(START_KEYS))})")
        if st.get("mode", "game") not in ("game", "menu"): L.append("start.yaml: mode must be game or menu")
        if st.get("size", "small") not in SIZES: L.append("start.yaml: size must be small, medium or large")
        if st.get("world") and st["world"] not in {w.stem for w in worlds if w.suffix == ".wld"}:
            L.append(f"start.yaml: world {st['world']} is not mod/worlds/{st['world']}.wld")
        for k in ("player", "world"):
            if st.get(k) is not None and not re.match(r"^[A-Za-z0-9 _-]{1,20}$", str(st[k])): L.append(f"start.yaml: {k} must be 1-20 letters / digits / space _ -")
        return L

    def _built(self, srcs, tmp):
        """each source mod built (cached by its content hash) -> (ok, log, [.tmod paths in the sandbox])"""
        out, logs = [], []
        for s in srcs:
            sig = tree_hash([s]); d = f"{self.root}/gab/_built/{sig}"
            have = self._sh(f"test -f {d}/{s.name}.tmod && echo ok").stdout
            if "ok" not in have:
                self.seat.copy_in(s, f"{tmp}/{s.name}")
                ok, log, path = self._build_one(f"{tmp}/{s.name}", d)
                logs.append(log)
                if not ok: return False, log, out
            else: logs.append(f"{s.name}: built before (same sources)")
            out.append(f"{d}/{s.name}.tmod")
        return True, "\n".join(logs), out

    def build(self, inst, mod_dir=None):
        """the gate's first half: structure check + every source mod compiled and packed by tModLoader -> (ok, log with the errors)"""
        if mod_dir is None: return True, "no mod dir given"
        L = self.check_layout(mod_dir)
        if L: return False, gbase.gate_log(L, "the hand-in mod/ is malformed", "devkit/README.md: What you hand in")
        self.install()
        srcs, tmods, worlds, st = self._layout(mod_dir)
        ok, log, _ = self._built(srcs, f"{self.root}/gab/{inst}-build")
        self._sh(f"rm -rf {self.root}/gab/{inst}-build")
        return ok, log + (f"\nprebuilt: {[t.name for t in tmods]}" if tmods else "")

    def provision(self, inst):
        self.install(); self.stop(inst)
        d = self.idir(inst)
        self._sh(f"rm -rf {d} && mkdir -p {d}/save/Mods {d}/save/Players {d}/save/Worlds && cp {self.root}/gab/_harness/GABState.tmod {d}/save/Mods/", check=True)
        self.seat.put(f"{d}/save/Mods/enabled.json", json.dumps(["GABState"]))
        self.seat.put(f"{d}/start.json", "{}"); self.seat.put(f"{d}/mods.json", "[]")
        return {"instance": inst, "dir": d, "display": self.dn}

    def install_mod(self, inst, mod_dir):
        """what a player does: the .tmod files into the save folder's Mods/ and switched on in enabled.json (sources are packed by
        tModLoader first), the mod's worlds into Worlds/; the start world is made again when the mods changed"""
        self.install()
        srcs, tmods, worlds, st = self._layout(mod_dir)
        d = self.idir(inst)
        ok, log, built = self._built(srcs, f"{d}/src")
        if not ok: raise RuntimeError("the mod does not build:\n" + log)
        self._sh(f"mkdir -p {d}/save/Mods && find {d}/save/Mods -name '*.tmod' ! -name GABState.tmod -delete")
        for b in built: self._sh(f"cp {shlex.quote(b)} {d}/save/Mods/", check=True)
        for t in tmods: self.seat.copy_in(t, f"{d}/save/Mods/{t.name}")
        names = [s.name for s in srcs] + [t.stem for t in tmods]
        self.seat.put(f"{d}/save/Mods/enabled.json", json.dumps(["GABState", *names]))
        sig = tree_hash(srcs + tmods + worlds) + json.dumps(st, sort_keys=True)
        if self._sh(f"cat {d}/mods.sig 2>/dev/null").stdout.strip() != sig:
            self._sh(f"rm -rf {d}/fresh {d}/worlds_in && mkdir -p {d}/worlds_in")
            for w in worlds: self.seat.copy_in(w, f"{d}/worlds_in/{w.name}")
            self.seat.put(f"{d}/mods.sig", sig)
        self.seat.put(f"{d}/start.json", json.dumps(st)); self.seat.put(f"{d}/mods.json", json.dumps(names))
        return {"mods": names, "worlds": [w.name for w in worlds], "start": st}

    def _start(self, inst):
        r = self._sh(f"cat {self.idir(inst)}/start.json 2>/dev/null").stdout.strip()
        try: st = json.loads(r) if r else {}
        except ValueError: st = {}
        return {"mode": st.get("mode") or "game", "world": str(st.get("world") or "World"), "player": str(st.get("player") or "Player"),
                "seed": str(st.get("seed") or "gab"), "size": SIZES.get(st.get("size") or "small", 1)}

    def _mods(self, inst):
        r = self._sh(f"cat {self.idir(inst)}/mods.json 2>/dev/null").stdout.strip()
        try: return json.loads(r) if r else []
        except ValueError: return []

    def worldgen(self, inst):
        """the start world + player with the instance's mods, kept in fresh/ -> (ok, log)"""
        st, d = self._start(inst), self.idir(inst)
        # a clean save: the mod's own worlds (worlds_in/), else -autocreate generates the start world from the seed
        self._sh(f"rm -rf {d}/fresh {d}/save/Players {d}/save/Worlds && mkdir -p {d}/save/Players {d}/save/Worlds && cp -a {d}/worlds_in/. {d}/save/Worlds/ 2>/dev/null; true")
        out = self._bg(f"bash {self.root}/bin/gtr.sh world {d} {shlex.quote(st['player'])} {shlex.quote(st['world'])} {shlex.quote(st['seed'])} {st['size']}",
                       f"{d}/world.run", r"^WORLD (OK|FAILED)", 480)
        if "WORLD OK" not in out:
            errs = error_lines(self._sh(f"cat {d}/server.log {d}/server.out 2>/dev/null").stdout)
            why = "timed out (480 s) making the world" if "timed out" in out else "the server stopped before the player was made"
            return False, gbase.gate_log(errs, f"world generation / player creation with the mods failed: {why}", out[-1500:])
        self._sh(f"mkdir -p {d}/fresh && cp -a {d}/save/Worlds {d}/save/Players {d}/fresh/", check=True)
        return True, out.strip().splitlines()[-1][:300]

    def start(self, inst):
        """fresh start: the start world + player (made once per mod set, restored from fresh/ every start), the client on the
        instance's display, ok when the player is in the world (mode game) or the title menu is up (mode menu), mods loaded"""
        self.install(); st, d = self._start(inst), self.idir(inst)
        self.stop(inst)
        if st["mode"] == "game":
            if "ok" not in self._sh(f"test -d {d}/fresh/Worlds && echo ok").stdout:
                ok, log = self.worldgen(inst)
                if not ok: return False, log
            self._sh(f"rm -rf {d}/save/Worlds {d}/save/Players && cp -a {d}/fresh/Worlds {d}/fresh/Players {d}/save/", check=True)
        self._sh(f"bash {self.root}/bin/gtr.sh start {d} {self.dn} {st['mode']} {shlex.quote(st['player'])} {shlex.quote(st['world'])}")
        want = set(self._mods(inst)); t0 = time.time(); e = {}
        while time.time() - t0 < 240:
            time.sleep(3)
            if "up" not in self._sh(f"bash {self.root}/bin/gtr.sh alive {d}").stdout:
                return False, gbase.gate_log(error_lines(self._live_log(inst, force=True)), "tModLoader exited", self._sh(f"tail -c 1500 {d}/client.out").stdout)
            log = self._live_log(inst)
            bad = [l for l in error_lines(log) if re.search(r"Disabling Mod|An error occurred while loading", l)]
            if bad: return False, gbase.gate_log(error_lines(log), "tModLoader could not load the mods (it disables them and shows its error screen)", log[-1500:])
            if re.search(r"SteamAPI_Init|Steam.*(not running|failed to init)", log, re.I) and "Distribution Platform: Steam" not in log:
                return False, gbase.gate_log(error_lines(log), "Steam is not running / not logged on in the sandbox (tModLoader needs it)", log[-800:])
            e = self._state(inst, 2.0).get("engine") or {}
            loaded = {str(x).split(" ")[0] for x in e.get("mods") or []}
            if st["mode"] == "game" and e.get("screen") in ("in_game", "paused", "dead") and isinstance(e.get("player"), dict):
                if want - loaded: return False, gbase.gate_log(error_lines(log), f"in the world, but these mods are not loaded: {sorted(want - loaded)}", log[-1500:])
                time.sleep(2); return True, f"in world {(e.get('world') or {}).get('name')} as {e['player'].get('name')}, mods {sorted(loaded)}"
            if st["mode"] == "menu" and e.get("screen") == "menu" and e.get("menu_mode") == 0 and time.time() - t0 > 8:
                if want - loaded: return False, gbase.gate_log(error_lines(log), f"title menu, but these mods are not loaded: {sorted(want - loaded)}", log[-1500:])
                return True, f"title menu, mods {sorted(loaded)}"
        log = self._live_log(inst)
        return False, gbase.gate_log(error_lines(log), f"timed out: the start target was not reached in 240 s (last screen: {e.get('screen')}, menu {e.get('menu_mode')})",
                                     "\n".join(log.splitlines()[-25:]))

    def load_check(self, inst, out_dir=None):
        """the gate's second half (after provision + install_mod): the start world made with the mods (worldgen, player creation),
        the client loads every mod and reaches the start target, still alive 5 s later -> (ok, log with the errors)"""
        ok, log = self.start(inst)
        if ok:
            time.sleep(5)
            if "up" not in self._sh(f"bash {self.root}/bin/gtr.sh alive {self.idir(inst)}").stdout:
                ok, log = False, gbase.gate_log(error_lines(self._live_log(inst, force=True)), "tModLoader exited within 5 s of reaching the start target", log)
            else:
                errs = error_lines(self._live_log(inst))
                if errs: log += "\nlog errors (not failing the gate; see the regression column):\n" + "\n".join(errs)
        self.stop(inst)
        return ok, log

    def _alive(self, inst): return "up" in self._sh(f"bash {self.root}/bin/gtr.sh alive {self.idir(inst)}").stdout

    def _live_log(self, inst, force=False):
        """this instance's client.log (the live one in the private copy while its client runs, else its kept copy)"""
        d = self.idir(inst)
        if force or self._alive(inst): return self._sh(f"cat {self.tml}/tModLoader-Logs/client.log 2>/dev/null").stdout
        return self._sh(f"cat {d}/client.log 2>/dev/null").stdout

    def stop(self, inst): self._sh(f"test -f {self.root}/bin/gtr.sh && bash {self.root}/bin/gtr.sh stop {self.idir(inst)}")

    def log_tail(self, inst, n=40): return "\n".join(self._live_log(inst).splitlines()[-n:])

    def log_errors(self, inst):
        e = error_lines(self._live_log(inst), 20)
        return {"errors": len(e), "lines": e}

    def _state(self, inst, t=2.5):
        r = self._sh(f"bash {self.root}/bin/gtr.sh state {self.idir(inst)} {t}", timeout=15).stdout.strip()
        try: raw = json.loads(r.splitlines()[-1])
        except (ValueError, IndexError): raw = {"error": r[-300:] or "no answer"}
        return shape_state(raw, running=self._alive(inst) if "error" in raw else True)

    def state(self, inst):
        """STATE_SPEC snapshot, live from the running game through the harness mod GABState (request / answer files, answered at the
        end of the next frame; menus and loading screens included):
        engine: screen (menu | loading | in_game | paused | dead), menus_open [inventory, chest, npc_chat, sign, map, options,
          chat_input, ui:<vanilla UI state>], menu_mode (title menus), world {name, seed, size, evil, difficulty, hardmode, spawn},
          time {day, clock, ticks, moon_phase, events [blood_moon, eclipse, rain, ...]}, player {name, pos [x, y] in tiles, velocity,
          on_ground, life, life_max, mana, mana_max, defense, dead, held, selected_slot, coins (copper), inventory {item: count},
          armor, accessories, buffs, zones}, progress {downed [bosses / events]}, npcs {vanilla "Name#slot": {pos, dist, life,
          life_max, town, friendly}} (nearest 12), bosses ["Name life/max"], mods ["Name version"] (loaded, harness excluded).
        mod (only what mods added; empty for vanilla): vars {"Mod.Type.field": value} (fields of the mods' ModSystem / local ModPlayer
          classes and static fields, simple values, 40 max), ui [open mod UI states], npcs / projectiles / items_dropped
          {"Mod/Name#slot": {...}} nearest, inventory {"Mod/Name": {name, count}}, buffs, biomes, tiles / walls {"Mod/Name": {count,
          nearest}} within 60 x 35 tiles, tile_entities {"Mod/Name@x,y": fields}.
        screen_text: "status: " (world generation / loading), "dialog: NPC: text", "sign: ", "combat: " (non-number combat texts),
          "popup: " (item pickups), "hover: ", "chat: " (last 8 chat lines, Main.NewText included), "ui: " (texts of open mod UIs),
          "hud: " (text drawn by mod interface layers in the last frame)."""
        return self._state(inst)

    def dev_command(self, inst, k, v, mod, wd):
        if k != "worldgen": return None
        ok, log = self.worldgen(inst)
        return {"ok": ok, "log": log[-3000:]}

    # ---- regression
    def run_probes(self, names, mod_dir=None, label="vanilla"):
        """the pool session (3 game starts, ~3.5 min) with the mod's .tmod files"""
        self.install()
        mods = []
        if mod_dir:
            srcs, tmods, worlds, st = self._layout(mod_dir)
            ok, log, mods = self._built(srcs, f"{self.root}/gab/probe-{label}-src")
            if not ok: return {"error": "the mod does not build: " + log[-600:]}
            for t in tmods:
                self.seat.copy_in(t, f"{self.root}/gab/probe-{label}-tmod/{t.name}"); mods.append(f"{self.root}/gab/probe-{label}-tmod/{t.name}")
        return P.TRProbes(self.seat, self.root).session(label, mods, only=P.groups_of(names))

    def compare(self, spec, base, got): return P.item_result(spec, base, got)

    def version(self):
        if getattr(self, "_ver", None): return self._ver
        r = self._sh(f"python3 - {self.tml}/tModLoader.dll <<'PY'\nimport re, sys\ntry:\n    b = open(sys.argv[1], 'rb').read()\n    m = re.search('ProductVersion'.encode('utf-16-le') + rb'\\x00\\x00((?:[^\\x00]\\x00)+)', b)\n    print(m.group(1).decode('utf-16-le') if m else '?')\nexcept OSError: print('?')\nPY").stdout.strip()
        m = re.match(r"([\d.]+)\+([\d.]+)", r)
        b = f"tModLoader {'.'.join(str(int(x)) for x in m.group(2).split('.'))} / Terraria {m.group(1)}" if m else f"tModLoader ? ({r[:60]})"
        self._ver = {"game": "terraria", "build": b, "mod_loader": "tModLoader (built-in mod loader)", "image": self.seat.sandbox}
        return self._ver
