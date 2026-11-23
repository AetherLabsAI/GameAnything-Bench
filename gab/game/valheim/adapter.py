"""Valheim with BepInEx (commercial: the user brings Valheim, Steam app 892970, Linux build). Runs in a Steam-enabled trial container
(the machine's Steam hub, Steam logged on with the user's account; gab/host/steam.py). Pool verified in game 2026-10-30 (Valheim 1.0.21, BepInExPack_Valheim
5.4.2351, ValheimBridge 0.2.0).

Install (`install()`): BepInExPack_Valheim 5.4.2351 from Thunderstore (pinned sha256, unpacked into <root>/bepinexpack, never into the
Steam folder); a private game template <root>/game-tmpl (launcher + UnityPlayer.so copied from the Steam install, valheim_Data linked
read only, BepInEx core + doorstop from the pack; root = <seat home>/pool-probes/valheim); the harness plugin ValheimBridge (the labs'
bridge, gamebench bridges/valheim, built here against the template); the runners bin/vhp.sh + bin/driver.py (probes), bin/gvh.sh +
bin/vhstate.py (player sessions, state).

An instance (<root>/gab/<inst>) = game/ (a copy of the template; BepInEx/plugins = ValheimBridge + the mod's plugins), save/ (the
bridge's VHBRIDGE_SAVEDIR: local worlds and characters only, never Steam Cloud, never the user's saves), its own Unity / BepInEx logs,
bridge ports 6540+/6640+, a private Xvfb (:155+seat, 1280x720) through VirtualGL. No dialog: the bridge starts the world without
menus (no account, no server browser: a closed local server). A fresh start: the start world (start.yaml seed, or the mod's world
files) with a new character (vanilla start: rag tunic, torch), made once per mod set and kept as the bridge's template "gab_start";
every start / reset restores it (no world generation). Mode menu: the title screen. Devcommands / the F5 console only on dev-*
instances.

Deliverable mod/ (devkit/README.md): one folder per BepInEx plugin: a C# project (mod/<Name>/<Name>.csproj, built here: net462 against
the game's own assemblies) or a built plugin folder (DLLs + assets), or loose DLLs; + optional worlds/<name>.fwl + .db and start.yaml
{mode: game | menu, seed, world, player}.
"""
import hashlib, json, re, shlex, time
from pathlib import Path

import yaml

from .. import base as gbase
from ..base import GameAdapter
from . import probes as P

HERE = Path(__file__).parent
STEAM_DIR = ".steam/debian-installation/steamapps/common/Valheim"
BEP_VER = "5.4.2351"
BEP_SHA256 = "bce631497976a93977ceb08e166712e6c31d15244956f89f17df092a9b62e29f"
BEP_URL = f"https://thunderstore.io/package/download/denikson/BepInExPack_Valheim/{BEP_VER}/"
NAME_RE = re.compile(r"^[A-Za-z][A-Za-z0-9_.-]*$")
RESERVED = {"ValheimBridge", "BepInEx", "core", "patchers", "config"}
START_KEYS = {"mode", "seed", "world", "player"}
ENGINE_KEYS = ["screen", "menus_open", "world", "time", "player", "inventory", "equipment", "skills", "status_effects", "nearby", "progress",
               "event", "fps", "mods"]
# vanilla BepInEx noise of every run (pool A/A 2026-10-30): the Steam party library is not shipped for Linux
VANILLA_NOISE = re.compile(r"libParty\.so|DllNotFoundException: Party", re.I)
ERR = re.compile(r"^\[(Error|Fatal)\s*:|Exception|Could not load|error CS\d+|Failed to load|Skipping", re.I)


def shape_state(raw, running=None):
    raw = raw if isinstance(raw, dict) else {}
    e = raw.get("engine") if isinstance(raw.get("engine"), dict) else {}
    out = {"running": bool(raw.get("running") if raw.get("running") is not None else running), "engine": {k: e.get(k) for k in ENGINE_KEYS},
           "mod": raw.get("mod") if isinstance(raw.get("mod"), dict) else {},
           "screen_text": [str(x)[:200] for x in (raw.get("screen_text") or []) if isinstance(x, str)][:30]}
    for k, empty in (("menus_open", []), ("inventory", {}), ("equipment", {}), ("skills", {}), ("status_effects", []), ("nearby", {}), ("mods", [])):
        if not isinstance(out["engine"][k], type(empty)): out["engine"][k] = empty
    if raw.get("error"):
        out["error"] = str(raw["error"])[:300]
        if out["running"] and not e: out["engine"]["screen"] = "starting"
    return out


def error_lines(text, limit=gbase.ERR_N):
    """BepInEx / Unity errors for the gate's and `game build`'s answer: [Error] / [Fatal] entries, exceptions, plugins BepInEx could not
    load, minus the vanilla noise (gab.game.base.error_lines: deduplicated with counts, first `limit`)"""
    return gbase.error_lines(text, ERR, VANILLA_NOISE, n=limit)


def tree_hash(paths):
    h = hashlib.sha256()
    for root in paths:
        root = Path(root)
        files = [root] if root.is_file() else sorted(p for p in root.rglob("*") if p.is_file() and not {"bin", "obj"} & set(p.relative_to(root).parts))
        for f in files:
            h.update(str(f.relative_to(root.parent)).encode()); h.update(f.read_bytes())
    return h.hexdigest()[:16]


class Adapter(GameAdapter):
    name = "valheim"
    title = "Valheim (BepInEx)"
    screen = (1280, 720)
    extra_help = ("  console 'COMMAND'            a Valheim console command in the running dev game (devcommands on), e.g. console 'spawn Boar 3'\n")

    def __init__(self, seat, root=None):
        super().__init__(seat)
        self.root = root or f"{seat.home}/pool-probes/valheim"
        self.steam = f"{seat.home}/{STEAM_DIR}"
        self.bep = f"{self.root}/bepinexpack/BepInExPack_Valheim"
        n = int(seat.seat) if str(seat.seat).isdigit() else 1
        self.dn, self.aport, self.hport = f":{155 + n}", 6540 + n, 6640 + n
        self._ready = False

    def requirements(self):
        return {"game": "valheim", "gpu": True, "mem_gb": 4.0, "steam": True, "hosts": ["pool", "docker"],
                "own_copy": "Valheim (Steam app 892970), Linux build, in the Steam sandbox's library (docker: the Steam hub's, gab/host/steam.py)",
                "mod_loader": f"BepInExPack_Valheim {BEP_VER} (Thunderstore, downloaded by install())", "eula": None}

    # ---- paths
    def idir(self, inst): return f"{self.root}/gab/{inst}"
    def display(self, inst): return self.dn
    def paths(self, inst):
        d = self.idir(inst)
        return {"game_template": f"{self.root}/game-tmpl", "managed_assemblies": f"{self.root}/game-tmpl/valheim_Data/Managed",
                "bepinex_core": f"{self.root}/game-tmpl/BepInEx/core", "instance": d, "plugins": f"{d}/game/BepInEx/plugins",
                "saves": f"{d}/save (worlds_local/, characters_local/)", "bepinex_log": f"{d}/game/BepInEx/LogOutput.log", "unity_log": f"{d}/player.log"}

    def _env(self):   # XDG_RUNTIME_DIR: .NET 8 keeps its named mutexes there (without it `dotnet build` failed as player on the test sandbox)
        return (f"export VHP_ROOT={self.root} VHP_STEAM={shlex.quote(self.steam)} VHP_BEPINEX={shlex.quote(self.bep)} "
                f"XDG_RUNTIME_DIR=${{XDG_RUNTIME_DIR:-/tmp/xdg-runtime}}; mkdir -p -m 700 $XDG_RUNTIME_DIR 2>/dev/null; ")

    def _sh(self, cmd, timeout=110, check=False):
        r = self.seat.exec(self._env() + cmd, timeout=timeout)
        if check and r.returncode: raise RuntimeError(f"sandbox command failed ({r.returncode}): {cmd[:160]}\n{r.stdout[-1500:]}\n{r.stderr[-800:]}")
        return r

    def _bg(self, cmd, out, marker, timeout):
        self._sh(f"mkdir -p $(dirname {out}) && rm -f {out}; setsid nohup bash -c {shlex.quote(self._env() + cmd + f' > {out} 2>&1')} > /dev/null 2>&1 < /dev/null &")
        t0 = time.time()
        while time.time() - t0 < timeout:
            time.sleep(3)
            t = self._sh(f"cat {out} 2>/dev/null").stdout
            if re.search(marker, t, re.M): return t
        return self._sh(f"cat {out} 2>/dev/null").stdout + f"\n(timed out after {timeout} s)"

    def rpc(self, method, params=None, port=None, timeout=30):
        r = self._sh(f"bash {self.root}/bin/gvh.sh rpc {port or self.hport} {method} {shlex.quote(json.dumps(params or {}))} {timeout}", timeout=timeout + 15).stdout.strip()
        try: a = json.loads(r.splitlines()[-1])
        except (ValueError, IndexError): return {"_error": r[-300:] or "no answer"}
        return a.get("result") if "result" in a else {"_error": (a.get("error") or {}).get("message", "error")}

    # ---- install
    def install(self):
        if self._ready: return True
        if "ok" not in self._sh(f"test -x {self.steam}/valheim.x86_64 && test -d {self.steam}/valheim_Data && echo ok").stdout:
            raise RuntimeError(f"Valheim is not installed in this Steam sandbox ({self.steam}): the user installs their own copy with their Steam account")
        if "ok" not in self._sh(f"test -f {self.bep}/BepInEx/core/BepInEx.dll && echo ok").stdout:
            # the docker image gameanything-bench-steam carries the same pinned pack ($GAB_BEPINEX_PACK): a trial has no network
            self._sh(f'P=${{GAB_BEPINEX_PACK:-}}; if [ -n "$P" ] && echo "{BEP_SHA256}  $P/BepInExPack_Valheim-{BEP_VER}.zip" | sha256sum -c - >/dev/null 2>&1; '
                     f'then mkdir -p {self.root}/bepinexpack && cp -a "$P"/BepInExPack_Valheim {self.root}/bepinexpack/; fi')
        if "ok" not in self._sh(f"test -f {self.bep}/BepInEx/core/BepInEx.dll && echo ok").stdout:
            out = self._bg(f"mkdir -p {self.root}/dl && cd {self.root}/dl && curl -fsSL --max-time 300 -o bep.zip.part {BEP_URL} && "
                           f"echo '{BEP_SHA256}  bep.zip.part' | sha256sum -c - && mv -f bep.zip.part BepInExPack_Valheim-{BEP_VER}.zip && "
                           f"python3 -I - BepInExPack_Valheim-{BEP_VER}.zip {self.root}/bepinexpack <<'PY'\nimport sys, zipfile, os\n"
                           "z = zipfile.ZipFile(sys.argv[1]); dst = os.path.realpath(sys.argv[2]); os.makedirs(dst, exist_ok=True)\n"
                           "for i in z.infolist():\n    t = os.path.realpath(os.path.join(dst, i.filename))\n"
                           "    if not (t == dst or t.startswith(dst + os.sep)): sys.exit('unsafe path in zip: ' + i.filename)\n"
                           "z.extractall(dst)\nPY\necho BEP-OK", f"{self.root}/dl/bep.out", r"^BEP-OK|FAILED|rror|unsafe", 400)
            if "BEP-OK" not in out: raise RuntimeError("BepInExPack_Valheim download / check failed: " + out[-600:])
        for f in ("vhp.sh", "gvh.sh", "driver.py", "vhstate.py"):
            self._put_if_changed(f"{self.root}/bin/{f}", (HERE / "bridge" / f).read_text())
        sig = self._sh(f"stat -c '%s %Y' {self.steam}/valheim.x86_64; cat {self.bep}/../manifest.json 2>/dev/null | md5sum").stdout.strip().replace("\n", " ")
        if sig != self._sh(f"cat {self.root}/game-tmpl.sig 2>/dev/null").stdout.strip() or "ok" not in self._sh(f"test -f {self.root}/game-tmpl/BepInEx/core/BepInEx.dll && echo ok").stdout:
            out = self._sh(f"bash {self.root}/bin/vhp.sh template && echo {shlex.quote(sig)} > {self.root}/game-tmpl.sig && echo TMPL-OK", timeout=110).stdout
            if "TMPL-OK" not in out: raise RuntimeError("could not make the game template: " + out[-600:])
        self._bridge()
        self._ready = True
        return True

    def _put_if_changed(self, path, text):
        if hashlib.md5(text.encode()).hexdigest() not in self._sh(f"md5sum {path} 2>/dev/null").stdout: self.seat.put(path, text)

    def _bridge(self):
        src = HERE / "bridge" / "ValheimBridge"; sig = tree_hash([src])
        if sig in self._sh(f"cat {self.root}/ValheimBridge.dll.src 2>/dev/null").stdout and "ok" in self._sh(f"test -f {self.root}/ValheimBridge.dll && echo ok").stdout: return
        d = f"{self.root}/gab/_harness/src/ValheimBridge"
        self._sh(f"rm -rf {d} && mkdir -p {d}")
        for f in sorted(src.iterdir()):
            if f.is_file(): self.seat.put(f"{d}/{f.name}", f.read_bytes())
        ok, log, out = self._build_one(d, "ValheimBridge")
        if not ok: raise RuntimeError("building the harness plugin ValheimBridge failed:\n" + log)
        self._sh(f"cp {out}/ValheimBridge.dll {self.root}/ValheimBridge.dll && echo {sig} > {self.root}/ValheimBridge.dll.src", check=True)

    def _build_one(self, d, name):
        """dotnet build of a plugin project in the sandbox against the template -> (ok, error text, output folder)"""
        T = f"{self.root}/game-tmpl"
        out = self._bg(f"cd {d} && DOTNET_CLI_TELEMETRY_OPTOUT=1 DOTNET_NOLOGO=1 dotnet build -c Release -nologo -clp:NoSummary -p:GameDir={T} -p:BepInExDir={T}/BepInEx "
                       f"> build.log 2>&1; echo BUILD-RC=$?", f"{d}/build.run", r"^BUILD-RC=", 900)
        log = self._sh(f"cat {d}/build.log 2>/dev/null").stdout
        if "BUILD-RC=0" not in out:
            clean = "\n".join(re.sub(r"\s+\[[^\]]*\.csproj\]$", "", l).replace(d + "/", "") for l in log.splitlines())
            errs = gbase.error_lines(clean, r"error CS\d+|: error |\berror\b", r"warning|0 Error")
            why = "timed out (900 s)" if "timed out" in out else "the plugin does not compile"
            return False, gbase.gate_log(errs, f"{name}: {why}", log[-3000:]), None
        dll = self._sh(f"cd {d} && f=$(find bin/Release -name '{name}.dll' | head -1); [ -z \"$f\" ] && f=$(find bin/Release -name '*.dll' ! -name 'System.*' ! -name 'netstandard.dll' | head -1); dirname \"$f\"").stdout.strip()
        if not dll or dll == ".": return False, gbase.gate_log([], f"{name}: built, but no {name}.dll under bin/Release/", log[-1500:]), None
        return True, f"built {name}", f"{d}/{dll}"

    # ---- the deliverable
    @staticmethod
    def _layout(mod_dir):
        """-> (project folders, plugin folders, loose dlls, world files, start dict)"""
        m = Path(mod_dir) if mod_dir else None
        if not m or not m.exists(): return [], [], [], [], {}
        dirs = sorted(p for p in m.iterdir() if p.is_dir() and p.name != "worlds")
        projs = [p for p in dirs if any(p.glob("*.csproj"))]
        plugs = [p for p in dirs if p not in projs]
        dlls = sorted(m.glob("*.dll"))
        worlds = sorted(f for f in (m / "worlds").iterdir() if f.suffix in (".fwl", ".db")) if (m / "worlds").is_dir() else []
        st = yaml.safe_load((m / "start.yaml").read_text()) if (m / "start.yaml").exists() else {}
        return projs, plugs, dlls, worlds, st if isinstance(st, dict) else {"_bad": st}

    def check_layout(self, mod_dir):
        projs, plugs, dlls, worlds, st = self._layout(mod_dir); L = []
        if not projs and not plugs and not dlls:
            L.append("mod/ holds no BepInEx plugin: a C# project mod/<Name>/<Name>.csproj, a built plugin folder mod/<Name>/ with its DLL(s), or mod/<Name>.dll")
        for p in projs + plugs + dlls:
            n = p.stem if p.suffix == ".dll" else p.name
            if not NAME_RE.match(n): L.append(f"{n}: a plugin name is letters, digits, _ . -")
            if n in RESERVED: L.append(f"{n}: name taken by BepInEx / the harness")
        for p in projs:
            if len(list(p.glob("*.csproj"))) > 1: L.append(f"{p.name}/: more than one .csproj")
            if not any(p.rglob("*.cs")): L.append(f"{p.name}/: no .cs file")
            src = "\n".join(c.read_text(errors="replace") for c in p.rglob("*.cs"))
            if "BepInPlugin" not in src: L.append(f"{p.name}/: no [BepInPlugin(...)] class: BepInEx would not load it")
        for p in plugs:
            if not any(p.rglob("*.dll")): L.append(f"{p.name}/: neither a .csproj nor a built .dll")
        names = [w.stem for w in worlds]
        for w in {w.stem for w in worlds}:
            if names.count(w) != 2: L.append(f"worlds/{w}: a world is two files, {w}.fwl and {w}.db")
        if st.get("_bad") is not None: L.append("start.yaml must be a mapping")
        for k in set(st) - START_KEYS - {"_bad"}: L.append(f"start.yaml: unknown key {k} (have: {', '.join(sorted(START_KEYS))})")
        if st.get("mode", "game") not in ("game", "menu"): L.append("start.yaml: mode must be game or menu")
        if st.get("world") and st["world"] not in names: L.append(f"start.yaml: world {st['world']} is not mod/worlds/{st['world']}.fwl + .db")
        for k in ("player", "world", "seed"):
            if st.get(k) is not None and not re.match(r"^[A-Za-z0-9_]{1,20}$", str(st[k])): L.append(f"start.yaml: {k} must be 1-20 letters / digits / _")
        return L

    def _built(self, projs, tmp):
        """projects built (cached by content hash) -> (ok, log, [(name, output folder in the sandbox)])"""
        out, logs = [], []
        for p in projs:
            sig = tree_hash([p]); d = f"{self.root}/gab/_built/{sig}/{p.name}"
            have = self._sh(f"cat {d}.out 2>/dev/null").stdout.strip()
            if not have:
                self._sh(f"rm -rf {d} && mkdir -p {Path(d).parent}")
                self.seat.copy_in(p, d)
                csproj = next(p.glob("*.csproj")).stem
                ok, log, o = self._build_one(d, csproj)
                logs.append(log)
                if not ok: return False, log, out
                self._sh(f"echo {shlex.quote(o)} > {d}.out"); have = o
            else: logs.append(f"{p.name}: built before (same sources)")
            out.append((p.name, have))
        return True, "\n".join(logs), out

    def build(self, inst, mod_dir=None):
        """the gate's first half: structure check + every plugin project compiled -> (ok, log with the compiler errors)"""
        if mod_dir is None: return True, "no mod dir given"
        L = self.check_layout(mod_dir)
        if L: return False, gbase.gate_log(L, "the hand-in mod/ is malformed", "devkit/README.md: What you hand in")
        self.install()
        projs, plugs, dlls, worlds, st = self._layout(mod_dir)
        ok, log, _ = self._built(projs, None)
        return ok, log + (f"\nready plugins: {[p.name for p in plugs] + [d.name for d in dlls]}" if plugs or dlls else "")

    def provision(self, inst):
        self.install(); self.stop(inst)
        d = self.idir(inst)
        self._sh(f"rm -rf {d} && mkdir -p {d}/save && cp -a {self.root}/game-tmpl {d}/game && cp {self.root}/ValheimBridge.dll {d}/game/BepInEx/plugins/", check=True)
        self.seat.put(f"{d}/start.json", "{}"); self.seat.put(f"{d}/mods.json", "[]")
        return {"instance": inst, "dir": d, "display": self.dn}

    def install_mod(self, inst, mod_dir):
        """what a player does with a mod manager: each plugin into BepInEx/plugins/<Name>/ (projects are built first), world files into
        the local worlds folder; the start template is made again when the mod changed"""
        self.install()
        projs, plugs, dlls, worlds, st = self._layout(mod_dir)
        d = self.idir(inst); pl = f"{d}/game/BepInEx/plugins"
        ok, log, built = self._built(projs, None)
        if not ok: raise RuntimeError("the mod does not build:\n" + log)
        self._sh(f"find {pl} -mindepth 1 -maxdepth 1 ! -name ValheimBridge.dll -exec rm -rf {{}} +")
        for name, o in built: self._sh(f"mkdir -p {pl}/{name} && cp -r {shlex.quote(o)}/. {pl}/{name}/ && rm -f {pl}/{name}/*.pdb", check=True)
        for p in plugs: self.seat.copy_in(p, f"{pl}/{p.name}")
        for f in dlls: self.seat.copy_in(f, f"{pl}/{f.name}")
        names = [n for n, _ in built] + [p.name for p in plugs] + [f.stem for f in dlls]
        sig = tree_hash(projs + plugs + dlls + worlds) + json.dumps(st, sort_keys=True)
        if self._sh(f"cat {d}/mods.sig 2>/dev/null").stdout.strip() != sig:
            self._sh(f"rm -rf {d}/save && mkdir -p {d}/save/worlds_local")
            for w in worlds: self.seat.copy_in(w, f"{d}/save/worlds_local/{w.name}")
            self.seat.put(f"{d}/mods.sig", sig)
        self.seat.put(f"{d}/start.json", json.dumps(st)); self.seat.put(f"{d}/mods.json", json.dumps(names))
        return {"plugins": names, "worlds": [w.name for w in worlds], "start": st}

    def _start_cfg(self, inst):
        r = self._sh(f"cat {self.idir(inst)}/start.json 2>/dev/null").stdout.strip()
        try: st = json.loads(r) if r else {}
        except ValueError: st = {}
        return {"mode": st.get("mode") or "game", "world": str(st.get("world") or "gab"), "seed": str(st.get("seed") or "gabworld01"),
                "player": str(st.get("player") or "player").lower(), "shipped": bool(st.get("world"))}

    def _mods(self, inst):
        r = self._sh(f"cat {self.idir(inst)}/mods.json 2>/dev/null").stdout.strip()
        try: return json.loads(r) if r else []
        except ValueError: return []

    def _alive(self, inst): return "up" in self._sh(f"bash {self.root}/bin/gvh.sh alive {self.idir(inst)}").stdout

    def _launch(self, inst):
        """start the game, wait until the bridge answers (a boot hang is retried, as the pool runner does) -> (ok, why)"""
        d = self.idir(inst)
        for attempt in (1, 2, 3):
            self._sh(f"bash {self.root}/bin/gvh.sh start {d} {self.dn} {self.hport} {self.aport} {'console' if inst.startswith('dev-') else ''}")
            t0 = time.time()
            while time.time() - t0 < 150:
                time.sleep(3)
                if not self._alive(inst): break
                h = self.rpc("health", timeout=8)
                if isinstance(h, dict) and h.get("status") in ("menu", "in_world", "dead", "loading"): return True, h.get("status")
            self.stop(inst)
        return False, "timed out: the game did not boot (the bridge never answered in 3 tries of 150 s)"

    def start(self, inst):
        """fresh start: the start world + character (the bridge's template gab_start, made once per mod set: a new world from the seed or
        the mod's world, a new character), or the title screen (mode menu). ok when there, with the mod's plugins loaded."""
        self.install(); st, d = self._start_cfg(inst), self.idir(inst)
        self.stop(inst)
        if self._mods(inst) and "ok" not in self._sh(f"test -f {self.root}/gab/state_base/prefabs.json && echo ok").stdout and not inst.startswith("statebase"):
            self._ensure_base()
        ok, why = self._launch(inst)
        if not ok: return False, gbase.gate_log(self._errors(inst), why, self.log_tail(inst, 30))
        want, t0 = self._mods(inst), time.time()
        while time.time() - t0 < 120:                                  # BepInEx loads the plugins before the title screen
            h = self.rpc("health", timeout=8)
            if isinstance(h, dict) and h.get("status") in ("menu", "in_world"): break
            time.sleep(3)
        bad = self._plugins_missing(inst, want)
        if bad: return False, gbase.gate_log(self._errors(inst), f"BepInEx did not load: {bad}", self.log_tail(inst, 30))
        if st["mode"] == "menu": return True, f"title screen, plugins {want}"
        cheats = inst.startswith("dev-")
        has_tmpl = "ok" in self._sh(f"ls {d}/save/wc_templates 2>/dev/null | grep -q gab_start && echo ok").stdout
        sc = {"template": "gab_start"} if has_tmpl else {"world": st["world"], "seed": st["seed"], "character": st["player"], "fresh": not st["shipped"]}
        r = self.rpc("reset", {"scenario": {**sc, "cheats": cheats, "wait": False}}, timeout=30)
        t0 = time.time(); h = {}
        while time.time() - t0 < 900:
            time.sleep(4)
            if not self._alive(inst): return False, gbase.gate_log(self._errors(inst), "Valheim exited while starting the world", self.log_tail(inst, 30))
            h = self.rpc("health", timeout=10)
            if isinstance(h, dict) and h.get("status") == "in_world": break
            if isinstance(h, dict) and h.get("session") == "failed": return False, gbase.gate_log(self._errors(inst), f"the world did not start: {h.get('error')}", self.log_tail(inst, 30))
        else: return False, gbase.gate_log(self._errors(inst), f"timed out: not in the world after 900 s (last bridge health: {h})", self.log_tail(inst, 30))
        time.sleep(3)
        if not has_tmpl:
            t = self.rpc("save_template", {"name": "gab_start"}, timeout=60)
            if isinstance(t, dict) and t.get("_error"): return False, gbase.gate_log(self._errors(inst), f"could not keep the start state: {t['_error']}", "")
        if not self._mods(inst): self._state(inst, vanilla=True)          # a vanilla instance records the baselines once
        return True, f"in the world {st['world']} as {st['player']}" + (f", plugins {want}" if want else "")

    def _ensure_base(self):
        """the vanilla baselines of state()'s mod section (every vanilla prefab, the keys of a vanilla start): a vanilla instance, once"""
        self.provision("statebase")
        try: self.start("statebase")
        finally: self.stop("statebase")

    def _plugins_missing(self, inst, want):
        """-> [] when BepInEx finished its chainloader and loaded at least one plugin of the mod (BepInPlugin names differ from the
        folder names), else what is missing"""
        if not want: return []
        log = self._sh(f"cat {self.idir(inst)}/game/BepInEx/LogOutput.log 2>/dev/null").stdout
        loaded = [n for n in re.findall(r"Loading \[([^\]]+?) [\d.]+\]", log) if n != "ValheimBridge"]
        if "Chainloader startup complete" not in log: return ["(BepInEx did not finish loading the plugins)"]
        return [] if loaded else [f"(none of the mod's plugins: {', '.join(want)})"]

    def _errors(self, inst):
        d = self.idir(inst)
        return error_lines(self._sh(f"cat {d}/game/BepInEx/LogOutput.log {d}/player.log 2>/dev/null | tail -c 400000").stdout)

    def load_check(self, inst, out_dir=None):
        """the gate's second half: the plugins load (BepInEx), the start world comes up with them, the game is alive 8 s later"""
        ok, log = self.start(inst)
        if ok:
            time.sleep(8)
            if not self._alive(inst): ok, log = False, gbase.gate_log(self._errors(inst), "Valheim exited within 8 s of reaching the start target", log)
            else:
                e = self._errors(inst)
                if e: log += "\nlog errors (not failing the gate; see the regression column):\n" + "\n".join(e)
        self.stop(inst)
        return ok, log

    def stop(self, inst): self._sh(f"test -f {self.root}/bin/gvh.sh && bash {self.root}/bin/gvh.sh stop {self.idir(inst)}")

    def log_tail(self, inst, n=40): return self._sh(f"tail -{n} {self.idir(inst)}/game/BepInEx/LogOutput.log 2>/dev/null").stdout

    def log_errors(self, inst):
        e = self._errors(inst)
        return {"errors": len(e), "lines": e[:20]}

    def _state(self, inst, vanilla=False):
        plugs = ",".join(self._mods(inst))
        r = self._sh(f"python3 {self.root}/bin/vhstate.py {self.hport} {self.root}/gab/state_base {shlex.quote(plugs) or '-'} {'vanilla' if vanilla else ''}", timeout=20).stdout.strip()
        try: raw = json.loads(r.splitlines()[-1])
        except (ValueError, IndexError): raw = {"error": r[-300:] or "no answer"}
        if raw.get("running") is None: raw["running"] = self._alive(inst)
        return shape_state(raw)

    def state(self, inst):
        """STATE_SPEC snapshot, live through ValheimBridge's harness channel (vhstate.py: ~12 queries answered in one frame):
        engine: screen (menu | loading | in_game | dead | booting; loading also while the player teleports: the game then shows its black
          loading screen and ignores input), menus_open [inventory, menu, console, text_viewer, map, chat,
          build_menu], world {name, seed, time_s}, time {day, day_fraction, time_of_day, is_day, environment, paused}, player {name,
          pos [x, y, z], yaw, health, stamina, eitr (+ max), dead, biome, on_ground, swimming, crouching, attached_to, at_helm, riding,
          in_bed, crafting_station, in_place_mode, selected_piece, weight, teleporting, takes_input}, inventory {prefab: count}, equipment {slot: prefab}, skills
          {skill: level > 0}, status_effects, nearby {vanilla "prefab#id": {kind, pos, dist, health, label}} (12 nearest creatures /
          pieces / items within 60 m), progress {global_keys the game itself sets}, event (active raid), fps, mods [plugins].
        mod: objects {"prefab#id": {...}} prefabs no vanilla game has (20 nearest within 60 m), inventory {prefab: count} of non-vanilla
          items, global_keys a mod set, player_data (Player.m_customData keys a vanilla start lacks), player_unique_keys, item_data
          (items' custom data), extra_inventories (Inventory fields of mod components on the player). Vanilla = the prefab list and start
          keys of a vanilla instance, recorded once per install.
        screen_text: "hud: " the last 8 HUD messages (MessageHud: centre / top-left, what ShowMessage shows), "hover: " / "hover_text: "
          the hovered object's name and prompt."""
        return self._state(inst)

    def dev_command(self, inst, k, v, mod, wd):
        if k != "console": return None
        if not inst.startswith("dev-"): return {"error": "the console is on only in the dev instance"}
        if not self._alive(inst): return {"ok": False, "error": "the game is not running (game start)"}
        r = self.rpc("eval_query", {"query": "command", "args": {"cmd": " ".join(v)}}, timeout=30)
        return {"ok": isinstance(r, dict) and r.get("success", False), "answer": r}

    # ---- regression
    def run_probes(self, names, mod_dir=None, label="vanilla"):
        """the pool session (2 game starts, ~7 min) with the mod's plugins"""
        self.install()
        mods = []
        if mod_dir:
            projs, plugs, dlls, worlds, st = self._layout(mod_dir)
            ok, log, built = self._built(projs, None)
            if not ok: return {"error": "the mod does not build: " + log[-600:]}
            t = f"{self.root}/gab/probe-{label}-plugins"
            self._sh(f"rm -rf {t} && mkdir -p {t}")
            for name, o in built: self._sh(f"mkdir -p {t}/{name} && cp -r {shlex.quote(o)}/. {t}/{name}/ && rm -f {t}/{name}/*.pdb"); mods.append(f"{t}/{name}")
            for p in plugs: self.seat.copy_in(p, f"{t}/{p.name}"); mods.append(f"{t}/{p.name}")
            for f in dlls: self.seat.copy_in(f, f"{t}/{f.name}"); mods.append(f"{t}/{f.name}")
        return P.VHProbes(self.seat, self.root, self.steam, self.bep).session(label, mods, only=P.groups_of(names))

    def compare(self, spec, base, got): return P.item_result(spec, base, got)

    def version(self):
        if getattr(self, "_ver", None): return self._ver
        b = self._sh(f"grep -m1 buildid {Path(self.steam).parent.parent}/appmanifest_892970.acf 2>/dev/null | grep -o '[0-9][0-9]*'").stdout.strip()
        self._ver = {"game": "valheim", "build": f"Valheim (Steam build {b or '?'})", "mod_loader": f"BepInExPack_Valheim {BEP_VER} + ValheimBridge 0.2.0",
                     "image": self.seat.sandbox}
        return self._ver
