"""Cities: Skylines 1 adapter (commercial: the user brings Cities: Skylines, Steam app 255710, Linux build).

Status: the gate items and the default picks were verified in game with `gab selfcheck cities_skylines` (status `ready` in
benchmark/pools/cities_skylines/pool.yaml; noisy items stay `unverified` and are not used). History: the probe module and the runners were
ported from the 2026-10-30 pool draft (verified in game then: the private user folder + CSProbe load, CSProbe stops at the
Paradox Legal Disclaimer, with the private game folder the disclaimer no longer shows and a new city starts loading; the pod
restarted during that first city load, so no probe result exists). CSProbe / GABState / the negative mods compiled against the
game's assemblies on 2026-10-31; the 2026-11-05 additions (CSProbe daynight + transport, GABState time_of_day + transport, the
negative mods NegNight / NegBus, the statecheck test mod) have not been compiled.

Commercial: the user installs Cities: Skylines 1 in Steam (Paradox User Agreement: accepted by the user in Steam's install flow) and
accepts Paradox's in-game Legal Disclaimer once herself (the game saves it in the game folder's colossal.cgs; install() refuses to run
without it and never writes it). Runs in a Steam-enabled trial container (one container per trial, the machine's Steam hub); Steam logged on in it (the game's SteamAPI_Init needs the running client; no DRM bypass).

Install (`install()`): finds the Steam install (STEAM_DIRS under the seat's home), checks the legal acceptance, puts the runners
(bridge/csp.sh probes, bridge/gcs.sh player sessions, bridge/cs-start.sh = the lab's start-game.sh, bridge/cgs.py the settings files)
into <root>/bin (root = <seat home>/pool-probes/cities) and builds the harness mods CSProbe (regression probes) and GABState (live
state + the menu-less start) with `dotnet build` against the game's Cities_Data/Managed.

An instance (<root>/gab/<inst>) = a private user folder (data/ = XDG_DATA_HOME: Colossal Order/Cities_Skylines/{Addons/Mods,
Addons/Assets, Addons/Maps, Saves}, userGameState.cgs with the mods switched on -- new mods start switched off in Cities, the harness
does what a player does in Content Manager; config/ = XDG_CONFIG_HOME: Player.log; tmp/ = TMPDIR), a private game folder (Cities.x64
copied, the rest linked; colossal.cgs copied from the Steam folder + TelemetryConsent false), the harness mod GABState, a display
(pool: a private Xvfb :158+seat; docker: the container's own screen, sized 1280x800 by the docker backend) through VirtualGL,
-noWorkshop. A fresh start: a new city on start.yaml's map (default Green Plains) or the mod's save, loaded by GABState from the main
menu; mode menu: the main menu. The `mod` section of state() needs the stock prefab list: recorded by the first vanilla start
(<root>/stock_prefabs.txt; without it only custom-content .crp prefabs are listed as the mod's).

Deliverable mod/ (devkit/README.md): one folder per Cities mod (mod/<Name>/: a C# project <Name>.csproj built here (net35 against
Cities_Data/Managed), or a folder the game loads as is: a DLL, or Source/*.cs that the game compiles itself), + optional assets/*.crp,
maps/*.crp, saves/*.crp, start.yaml {mode: game | menu, map, save, city}.
"""
import hashlib, json, re, shlex, time
from pathlib import Path

import yaml

from ... import config
from .. import base as gbase
from ..base import GameAdapter
from . import probes as P

HERE = Path(__file__).parent
STEAM_DIRS = (".steam/debian-installation/steamapps/common/Cities_Skylines", ".local/share/Steam/steamapps/common/Cities_Skylines",
              ".steam/steam/steamapps/common/Cities_Skylines")
LEGAL = ("legal.privacy.", "legal.terms-of-use.")
NAME_RE = re.compile(r"^[A-Za-z][A-Za-z0-9_.-]*$")
RESERVED = {"GABState", "CSProbe", "CitiesBridge"}
START_KEYS = {"mode", "map", "save", "city"}
ENGINE_KEYS = ["status", "city", "map", "date", "paused", "speed", "money", "population", "happiness", "demand", "counts", "camera", "tool",
               "mods", "time_of_day", "transport"]
ERR = re.compile(r"Exception|\[Error\]|error CS\d+|Compilation failed|Error building", re.I)
ERR_IGNORE = P.ERR_IGNORE
BRIDGE_FILES = ("csp.sh", "gcs.sh", "cs-start.sh", "cgs.py")


def shape_state(raw, running=None):
    """GABState's answer -> {running, engine (fixed keys), mod, screen_text} (+ error, blocked)"""
    raw = raw if isinstance(raw, dict) else {}
    e = raw.get("engine") if isinstance(raw.get("engine"), dict) else {}
    out = {"running": bool(raw.get("running") if raw.get("running") is not None else running), "engine": {k: e.get(k) for k in ENGINE_KEYS},
           "mod": raw.get("mod") if isinstance(raw.get("mod"), dict) else {},
           "screen_text": [str(x)[:200] for x in (raw.get("screen_text") or []) if isinstance(x, str)][:30]}
    if not isinstance(out["engine"]["mods"], list): out["engine"]["mods"] = []
    if raw.get("error"):
        out["error"] = str(raw["error"])[:300]
        if out["running"] and not e: out["engine"]["status"] = "starting"
    if raw.get("blocked"): out["blocked"] = str(raw["blocked"])[:600]
    return out


def error_lines(text, limit=gbase.ERR_N):
    """Player.log errors for the gate's / `game build`'s answer (exceptions, [Error], the game's own compiler errors of Source/*.cs
    mods) minus the vanilla noise (Steam / Paradox services, the harness mods)"""
    return gbase.error_lines(text, ERR, ERR_IGNORE, n=limit)


def tree_hash(paths):
    h = hashlib.sha256()
    for root in paths:
        root = Path(root)
        files = [root] if root.is_file() else sorted(p for p in root.rglob("*") if p.is_file() and not {"bin", "obj"} & set(p.relative_to(root).parts))
        for f in files: h.update(str(f.relative_to(root.parent)).encode()); h.update(f.read_bytes())
    return h.hexdigest()[:16]


def start_cfg_line(st):
    """start.yaml -> GABState's start.cfg line ("new:<map>|<city>" | "load:<save>"), None for mode menu"""
    if (st.get("mode") or "game") != "game": return None
    if st.get("save"): return f"load:{st['save']}"
    return f"new:{st.get('map') or 'Green Plains'}|{st.get('city') or 'GAB City'}"


class Adapter(GameAdapter):
    name = "cities_skylines"
    probe_rev = P.REV                          # in the vanilla baseline's cache key (regression.baseline)
    title = "Cities: Skylines"
    screen = (1280, 800)

    def __init__(self, seat, root=None):
        super().__init__(seat)
        self.root = root or f"{seat.home}/pool-probes/cities"
        self.gd = f"{seat.home}/{STEAM_DIRS[0]}"          # install() finds the real one
        n = int(seat.seat) if str(seat.seat).isdigit() else 1
        docker = getattr(getattr(seat, "backend", None), "name", "") == "docker"
        # docker: one container per trial, its own screen (gab-seat, 1280x800); pool: seats share the sandbox -> a display per seat,
        # and another one per seat for the probe sessions (a probe run may overlap the seat's player instance in an evaluation)
        self.dn = seat.display if docker else f":{158 + n}"
        self.pdn = seat.display if docker else f":{172 + n}"
        self._ready = False

    def requirements(self):
        return {"game": "cities_skylines", "gpu": True, "mem_gb": 9.0, "steam": True, "hosts": ["pool", "docker"],
                "own_copy": "Cities: Skylines (Steam app 255710), Linux build, in the Steam sandbox's library",
                "eula": "Paradox User Agreement (Steam install flow) and the in-game Legal Disclaimer: accepted by the user herself"}

    # ---- paths
    def idir(self, inst): return f"{self.root}/gab/{inst}"
    def udir(self, inst): return f"{self.idir(inst)}/data/Colossal Order/Cities_Skylines"
    def sdir(self, inst): return f"/tmp/gabstate/{inst}"         # GABState's request / answer folder (local disk; data/gabstate links it)
    def display(self, inst): return self.dn
    def log_path(self, inst): return f"{self.idir(inst)}/config/unity3d/Colossal Order/Cities_ Skylines/Player.log"
    def paths(self, inst):
        return {"game": self.gd, "managed_assemblies": f"{self.gd}/Cities_Data/Managed", "instance": self.idir(inst), "mods": f"{self.udir(inst)}/Addons/Mods",
                "assets": f"{self.udir(inst)}/Addons/Assets", "maps": f"{self.udir(inst)}/Addons/Maps", "saves": f"{self.udir(inst)}/Saves",
                "game_log": self.log_path(inst)}

    # XDG_RUNTIME_DIR must EXIST: with it pointing at a missing folder, `dotnet build` (the harness mods, the agent's mods) fails at
    # its first-run setup with "IOException: The system cannot open the device or file specified. : 'NuGet-Migrations'" (the named
    # mutex .NET keeps under it; reproduced 2026-11-05 in a plain container of the Steam image, with and without the hub's IPC)
    def _env(self): return (f"export CSP_ROOT={self.root} CSP_GAME={shlex.quote(self.gd)} CSP_DISPLAY={self.dn} XDG_RUNTIME_DIR=${{XDG_RUNTIME_DIR:-/tmp/xdg-runtime}}; "
                            "mkdir -p -m 700 \"$XDG_RUNTIME_DIR\" 2>/dev/null; ")
    def _sh(self, cmd, timeout=110, check=False):
        r = self.seat.exec(self._env() + cmd, timeout=timeout)
        if check and r.returncode: raise RuntimeError(f"sandbox command failed ({r.returncode}): {cmd[:160]}\n{r.stdout[-1500:]}\n{r.stderr[-800:]}")
        return r

    def _bg(self, cmd, out, marker, timeout):
        """cmd detached in the sandbox (docker exec calls are cut after ~2 min), poll `out` until a line matches marker -> text"""
        self._sh(f"mkdir -p $(dirname {out}) && rm -f {out}; setsid nohup bash -c {shlex.quote(self._env() + cmd + f' > {out} 2>&1')} > /dev/null 2>&1 < /dev/null &")
        t0 = time.time()
        while time.time() - t0 < timeout:
            time.sleep(3)
            t = self._sh(f"cat {out} 2>/dev/null").stdout
            if re.search(marker, t, re.M): return t
        return self._sh(f"cat {out} 2>/dev/null").stdout + f"\n(timed out after {timeout} s)"

    # ---- install
    def _find_game(self):
        """the Steam install of the game in the sandbox (first of STEAM_DIRS with Cities.x64 + Cities_Data), else None"""
        cands = " ".join(shlex.quote(f"{self.seat.home}/{d}") for d in STEAM_DIRS)
        r = self._sh(f"for d in {cands}; do [ -x \"$d/Cities.x64\" ] && [ -d \"$d/Cities_Data\" ] && {{ echo \"GAME=$d\"; break; }}; done").stdout
        m = re.search(r"^GAME=(.+)$", r, re.M)
        return m.group(1).strip() if m else None

    def _legal_ok(self):
        keys = self._sh(f"python3 -c \"import sys; sys.path.insert(0, '{self.root}/bin'); import cgs; from pathlib import Path; "
                        f"p = Path('{self.gd}/colossal.cgs'); d = cgs.read(p) if p.exists() else {{}}; "
                        f"print(' '.join(k for k, v in (d.get('bool') or {{}}).items() if k.startswith('legal.') and v))\"").stdout.split()
        return all(any(k.startswith(p) for k in keys) for p in LEGAL)

    def install(self):
        """runners + harness mods (once per adapter object; cheap when nothing changed)"""
        if self._ready: return True
        gd = self._find_game()
        if not gd:
            raise RuntimeError(f"Cities: Skylines is not installed in this Steam sandbox (looked in ~/{', ~/'.join(STEAM_DIRS)}): "
                               "the user installs their own copy with Steam")
        self.gd = gd
        for f in BRIDGE_FILES:
            t = (HERE / "bridge" / f).read_text()
            if hashlib.md5(t.encode()).hexdigest() not in self._sh(f"md5sum {self.root}/bin/{f} 2>/dev/null").stdout:
                self.seat.put(f"{self.root}/bin/{f}", t)
                if f.endswith(".sh"): self._sh(f"chmod +x {self.root}/bin/{f}", check=True)   # put() loses the exec bit; cs-start.sh execs itself under vglrun
        if not self._legal_ok():
            raise RuntimeError("Paradox's in-game Legal Disclaimer (privacy policy + terms of use) is not accepted in this Steam sandbox yet: "
                               "the user runs `gab get-game cities_skylines` (it shows the game in her browser) and clicks Agree herself (gab never accepts it)")
        for name, out in (("CSProbe", f"{self.root}/CSProbe.dll"), ("GABState", f"{self.root}/gab/_harness/GABState.dll")):
            src = HERE / "bridge" / name; sig = tree_hash([src])
            if sig in self._sh(f"cat {out}.src 2>/dev/null").stdout and "ok" in self._sh(f"test -f {out} && echo ok").stdout: continue
            d = f"{self.root}/gab/_harness/src/{name}"
            self._sh(f"rm -rf {d} && mkdir -p {d}")
            for f in sorted(src.iterdir()):
                if f.is_file(): self.seat.put(f"{d}/{f.name}", f.read_bytes())
            ok, log, o = self._build_one(d, name)
            if not ok: raise RuntimeError(f"building the harness mod {name} failed:\n{log}")
            self._sh(f"mkdir -p $(dirname {out}) && cp {o}/{name}.dll {out} && echo {sig} > {out}.src", check=True)
        self._ready = True
        return True

    def _build_one(self, d, name):
        """dotnet build of the project <d>/<name>.csproj against the game's assemblies -> (ok, gate log, output folder)"""
        out = self._bg(f"cd {d} && DOTNET_CLI_TELEMETRY_OPTOUT=1 DOTNET_NOLOGO=1 dotnet build -c Release -nologo -clp:NoSummary -p:GameDir={shlex.quote(self.gd)} "
                       f"> build.log 2>&1; echo BUILD-RC=$?", f"{d}/build.run", r"^BUILD-RC=", 600)
        log = self._sh(f"cat {d}/build.log 2>/dev/null").stdout
        if "BUILD-RC=0" not in out:
            clean = "\n".join(re.sub(r"\s+\[[^\]]*\.csproj\]$", "", l).replace(d + "/", "") for l in log.splitlines())
            return False, gbase.gate_log(gbase.error_lines(clean, r"error CS\d+|: error |\berror\b", r"warning|0 Error"),
                                         f"{name}: " + ("timed out (600 s)" if "timed out" in out else "the mod does not compile"), log[-3000:]), None
        o = self._sh(f"cd {d} && dirname $(find bin/Release -name '{name}.dll' | head -1)").stdout.strip()
        return (True, f"built {name}", f"{d}/{o}") if o and o != "." else (False, gbase.gate_log([], f"{name}: built, but no {name}.dll", log[-1500:]), None)

    # ---- the deliverable
    @staticmethod
    def _layout(mod_dir):
        """-> (project folders, ready mod folders, assets, maps, saves, start)"""
        m = Path(mod_dir) if mod_dir else None
        if not m or not m.exists(): return [], [], [], [], [], {}
        dirs = sorted(p for p in m.iterdir() if p.is_dir() and p.name not in ("assets", "maps", "saves"))
        projs = [p for p in dirs if any(p.glob("*.csproj"))]
        ready = [p for p in dirs if p not in projs]
        g = lambda s: sorted((m / s).glob("*.crp")) if (m / s).is_dir() else []
        st = yaml.safe_load((m / "start.yaml").read_text()) if (m / "start.yaml").exists() else {}
        return projs, ready, g("assets"), g("maps"), g("saves"), st if isinstance(st, dict) else {"_bad": st}

    def check_layout(self, mod_dir):
        """host-side structure check -> list of problems"""
        projs, ready, assets, maps, saves, st = self._layout(mod_dir); L = []
        if not projs and not ready and not assets and not maps:
            L.append("mod/ holds no Cities mod: mod/<Name>/<Name>.csproj, or mod/<Name>/ with a DLL or Source/*.cs, or assets/ / maps/ *.crp")
        for p in projs + ready:
            if not NAME_RE.match(p.name): L.append(f"{p.name}: a mod folder name is letters, digits, _ . -")
            if p.name in RESERVED: L.append(f"{p.name}: name taken by the harness")
            src = "\n".join(c.read_text(errors="replace") for c in p.rglob("*.cs"))
            if p in ready and not any(p.glob("*.dll")) and not any((p / "Source").glob("*.cs")): L.append(f"{p.name}/: no DLL and no Source/*.cs")
            if (p in projs or not any(p.glob("*.dll"))) and "IUserMod" not in src: L.append(f"{p.name}/: no IUserMod class: the game would not list the mod")
            if p in projs and not (p / f"{p.name}.csproj").exists():
                L.append(f"{p.name}/: the project must be named {p.name}.csproj (folder name = assembly name)")
        if st.get("_bad") is not None: L.append("start.yaml must be a mapping")
        for k in set(st) - START_KEYS - {"_bad"}: L.append(f"start.yaml: unknown key {k} (have: {', '.join(sorted(START_KEYS))})")
        if st.get("mode", "game") not in ("game", "menu"): L.append("start.yaml: mode must be game or menu")
        if st.get("save") and st.get("map"): L.append("start.yaml: give a map (new city) or a save, not both")
        if st.get("save") and str(st["save"]) not in {s.stem for s in saves}: L.append(f"start.yaml: save {st['save']} is not mod/saves/{st['save']}.crp")
        for k in ("map", "save", "city"):
            if st.get(k) is not None and not re.match(r"^[^\n|:]{1,60}$", str(st[k])): L.append(f"start.yaml: {k} must be 1-60 characters without | : or newlines")
        return L

    def _built(self, projs):
        """each project built (cached by its content hash) -> (ok, log, [(name, output folder in the sandbox)])"""
        out, logs = [], []
        for p in projs:
            sig = tree_hash([p]); d = f"{self.root}/gab/_built/{sig}/{p.name}"
            have = self._sh(f"cat {d}.out 2>/dev/null").stdout.strip()
            if not have:
                self._sh(f"rm -rf {d} && mkdir -p $(dirname {d})"); self.seat.copy_in(p, d)
                ok, log, o = self._build_one(d, p.name)
                if not ok: return False, log, out
                self._sh(f"echo {shlex.quote(o)} > {d}.out"); have = o; logs.append(log)
            else: logs.append(f"{p.name}: built before (same sources)")
            out.append((p.name, have))
        return True, "\n".join(logs), out

    def build(self, inst, mod_dir=None):
        """the gate's first half: structure check + every project compiled against the game's assemblies -> (ok, log with the errors)"""
        if mod_dir is None: return True, "no mod dir given"
        L = self.check_layout(mod_dir)
        if L: return False, gbase.gate_log(L, "the hand-in mod/ is malformed", "devkit/README.md: What you hand in")
        self.install()
        ok, log, _ = self._built(self._layout(mod_dir)[0])
        return ok, log

    def provision(self, inst):
        self.install(); self.stop(inst)
        d, U, gd = self.idir(inst), self.udir(inst), shlex.quote(self.gd)
        self._sh(f"rm -rf {d} && mkdir -p {shlex.quote(U)}/Addons/Mods/GABState {shlex.quote(U)}/Saves {d}/config {d}/tmp {d}/game && "
                 f"cp {self.root}/gab/_harness/GABState.dll {shlex.quote(U)}/Addons/Mods/GABState/ && mkdir -p {self.sdir(inst)} && ln -sfn {self.sdir(inst)} {d}/data/gabstate && "
                 f"cp -f {gd}/Cities.x64 {d}/game/ && for e in Cities_Data Files Mono LauncherAssets Tools steamapps launcher-settings.json; do "
                 f"[ -e {gd}/$e ] && ln -sfn {gd}/$e {d}/game/$e; done; cp {gd}/colossal.cgs {d}/game/ && "
                 f"python3 -c \"import sys; sys.path.insert(0, '{self.root}/bin'); import cgs; from pathlib import Path; p = Path('{d}/game/colossal.cgs'); "
                 f"x = cgs.read(p); x['bool']['TelemetryConsent'] = False; cgs.write(p, x)\"", check=True)
        self.seat.put(f"{d}/start.json", "{}"); self.seat.put(f"{d}/mods.json", "[]"); self._enable(inst, [])
        return {"instance": inst, "dir": d, "display": self.dn}

    def _enable(self, inst, names):
        """switch GABState + the named mods on in the instance's userGameState.cgs (the Content Manager switch)"""
        U = self.udir(inst)
        dirs = " ".join(shlex.quote(f"{U}/Addons/Mods/{n}") for n in ["GABState", *names])
        self._sh(f"python3 {self.root}/bin/cgs.py {shlex.quote(U + '/userGameState.cgs')} --enable {dirs} > /dev/null", check=True)

    def install_mod(self, inst, mod_dir):
        """what a player does: each mod folder into Addons/Mods/ and switched on, assets / maps / saves into their folders"""
        self.install()
        projs, ready, assets, maps, saves, st = self._layout(mod_dir)
        U = self.udir(inst); M = f"{U}/Addons/Mods"
        ok, log, built = self._built(projs)
        if not ok: raise RuntimeError("the mod does not build:\n" + log)
        self._sh(f"find {shlex.quote(M)} -mindepth 1 -maxdepth 1 ! -name GABState -exec rm -rf {{}} + ; rm -rf {shlex.quote(U)}/Addons/Assets {shlex.quote(U)}/Addons/Maps; "
                 f"mkdir -p {shlex.quote(U)}/Addons/Assets {shlex.quote(U)}/Addons/Maps {shlex.quote(U)}/Saves")
        for name, o in built: self._sh(f"mkdir -p {shlex.quote(M)}/{name} && cp {shlex.quote(o)}/*.dll {shlex.quote(M)}/{name}/", check=True)
        for p in ready: self.seat.copy_in(p, f"{M}/{p.name}")
        for a in assets: self.seat.copy_in(a, f"{U}/Addons/Assets/{a.name}")
        for a in maps: self.seat.copy_in(a, f"{U}/Addons/Maps/{a.name}")
        for a in saves: self.seat.copy_in(a, f"{U}/Saves/{a.name}")
        names = [n for n, _ in built] + [p.name for p in ready]
        self._enable(inst, names)
        self.seat.put(f"{self.idir(inst)}/start.json", json.dumps(st)); self.seat.put(f"{self.idir(inst)}/mods.json", json.dumps(names))
        return {"mods": names, "assets": [a.name for a in assets], "maps": [a.name for a in maps], "saves": [a.name for a in saves], "start": st}

    def _jfile(self, path, empty):
        r = self._sh(f"cat {path} 2>/dev/null").stdout.strip()
        try: return json.loads(r) if r else empty
        except ValueError: return empty

    def _start_cfg(self, inst): return self._jfile(f"{self.idir(inst)}/start.json", {})
    def _mods(self, inst): return self._jfile(f"{self.idir(inst)}/mods.json", [])

    def _alive(self, inst): return "up" in self._sh(f"bash {self.root}/bin/gcs.sh alive {self.idir(inst)}").stdout

    def _errors(self, inst):
        return error_lines(self._sh(f"tail -c 400000 {shlex.quote(self.log_path(inst))} 2>/dev/null").stdout)

    def _stock_dump(self, inst):
        """the stock prefab list from a VANILLA in-game instance (GABState request "<id>:dump"), kept as <root>/stock_prefabs.txt"""
        if "ok" in self._sh(f"test -s {self.root}/stock_prefabs.txt && echo ok").stdout: return True
        self._sh(f"bash {self.root}/bin/gcs.sh state {self.idir(inst)} 8 dump", timeout=30)
        return "ok" in self._sh(f"test -s /tmp/gabstate/stock_prefabs.txt && cp /tmp/gabstate/stock_prefabs.txt {self.root}/stock_prefabs.txt && echo ok").stdout

    def start(self, inst):
        """fresh start: the game on the instance's display, GABState loads the start city (new city on the start map, or the mod's
        save) without menus; ok when the city is loaded (mode game) or the main menu is up (mode menu) and every mod is enabled"""
        self.install(); st = self._start_cfg(inst); d = self.idir(inst); S = self.sdir(inst)
        self.stop(inst)
        mode = st.get("mode") or "game"; cfg = start_cfg_line(st)
        self._sh(f"mkdir -p {S} && rm -f {S}/start.cfg {S}/blocked" + (f" && echo {shlex.quote(cfg)} > {S}/start.cfg" if cfg else "")
                 + f"; [ -s {self.root}/stock_prefabs.txt ] && cp {self.root}/stock_prefabs.txt /tmp/gabstate/stock_prefabs.txt; true")
        self._sh(f"ln -sfn {S} {d}/data/gabstate; bash {self.root}/bin/gcs.sh start {d} {self.dn}")
        want, t0, e = set(self._mods(inst)), time.time(), {}
        while time.time() - t0 < 600:
            time.sleep(5)
            s = self._state(inst, 2.0)
            if s.get("blocked"): return False, gbase.gate_log(self._errors(inst), f"not started: {s['blocked']}", "")
            if not s.get("running") and time.time() - t0 > 90:
                return False, gbase.gate_log(self._errors(inst), "Cities: Skylines exited", self._sh(f"tail -c 1500 {d}/game.out").stdout)
            e = s.get("engine") or {}
            loaded = set(e.get("mods") or [])
            if (mode == "game" and e.get("status") == "in_game") or (mode == "menu" and e.get("status") == "menu" and time.time() - t0 > 20):
                miss = sorted(want - loaded)
                if miss: return False, gbase.gate_log(self._errors(inst), f"the game did not load / enable these mods: {miss}", "")
                if mode == "game" and not want: self._stock_dump(inst)
                return True, f"{'in the city ' + str(e.get('city')) + ' on ' + str(e.get('map')) if mode == 'game' else 'main menu'}, mods {sorted(loaded)}"
        return False, gbase.gate_log(self._errors(inst), f"timed out: the start target was not reached in 600 s (last status {e.get('status')})", "")

    def load_check(self, inst, out_dir=None):
        """the gate's second half (after provision + install_mod): the game enables every mod, reaches the start target and is still
        alive 10 s later -> (ok, log with the errors)"""
        ok, log = self.start(inst)
        if ok:
            time.sleep(10)
            if not self._alive(inst): ok, log = False, gbase.gate_log(self._errors(inst), "the game exited within 10 s of reaching the start target", log)
            else:
                err = self._errors(inst)
                if err: log += "\nlog errors (not failing the gate; see the regression column):\n" + "\n".join(err)
        self.stop(inst)
        return ok, log

    def stop(self, inst): self._sh(f"test -f {self.root}/bin/gcs.sh && bash {self.root}/bin/gcs.sh stop {self.idir(inst)}")
    def log_tail(self, inst, n=40): return self._sh(f"tail -{int(n)} {shlex.quote(self.log_path(inst))} 2>/dev/null").stdout
    def log_errors(self, inst): e = self._errors(inst); return {"errors": len(e), "lines": e[:20]}

    def _state(self, inst, t=2.5):
        r = self._sh(f"bash {self.root}/bin/gcs.sh state {self.idir(inst)} {t}", timeout=20).stdout.strip()
        try: raw = json.loads(r.splitlines()[-1])
        except (ValueError, IndexError): raw = {"error": r[-300:] or "no answer"}
        return shape_state(raw, running=self._alive(inst) if "error" in raw else True)

    def state(self, inst):
        """STATE_SPEC snapshot, live through the harness mod GABState (bridge/GABState/GABState.cs: request / answer files on local disk,
        answered at the end of the next frame on Unity's main thread; menus and loading included):
        engine: status (menu | loading | in_game | other | starting), city, map, date (game date), paused, speed (1-3), money, population,
          happiness, demand {residential, commercial, workplace}, counts {buildings, segments, vehicles, citizens}, camera {x, z, height,
          size, angle}, tool (current tool class), mods (enabled, harness excluded), time_of_day {hour, night, day_night_cycle},
          transport {lines, by_type, vehicles, list [{name, type, stops, vehicles, complete}]}.
        mod: buildings / networks / props of prefabs that are custom content or not in the stock list ({prefab: {count, pos}} /
          {prefab: count}, 20 each), districts {name: {population}}, vars (static fields of the mods' assemblies, simple values, 40 max).
        screen_text: "chirp: sender: text" (last 6 Chirper messages), "ui: " (visible labels of mod-made UI components)."""
        return self._state(inst)

    # ---- regression
    def run_probes(self, names, mod_dir=None, label="vanilla"):
        """the pool session (run1: a new city + the probe groups the names need; run2: the autosave read back) with the mod's mods"""
        self.install()
        mods = []
        if mod_dir:
            projs, ready, assets, maps, saves, st = self._layout(mod_dir)
            ok, log, built = self._built(projs)
            if not ok: return {"error": "the mod does not build: " + log[-600:]}
            t = f"{self.root}/gab/probe-{label}-mods"; self._sh(f"rm -rf {t} && mkdir -p {t}")
            for name, o in built: self._sh(f"mkdir -p {t}/{name} && cp {shlex.quote(o)}/*.dll {t}/{name}/"); mods.append(f"{t}/{name}")
            for p in ready: self.seat.copy_in(p, f"{t}/{p.name}"); mods.append(f"{t}/{p.name}")
        groups = P.groups_of(names)
        fx = self.fixture() if set(groups) & set(P.FIXTURE_GROUPS) and "growth" not in groups else None
        if isinstance(fx, dict):                     # could not make the fixture: the fixture groups answer its error
            r = P.CSProbes(self.seat, self.root, self.gd, display=self.pdn).session(label, mods, only=[g for g in groups if g not in P.FIXTURE_GROUPS])
            return {**r, **{g: {"error": fx["error"]} for g in groups if g in P.FIXTURE_GROUPS}}
        return P.CSProbes(self.seat, self.root, self.gd, display=self.pdn).session(label, mods, only=groups, fixture=fx)

    def fixture_key(self):
        """the probe-city fixture's identity: game build + CSProbe sources (a new build or probe code grows a new city)"""
        b = self.version()["build"]; m = re.search(r"build (\d+)", b)
        return f"{m.group(1) if m else 'unknown'}-{tree_hash([HERE / 'bridge' / 'CSProbe'])}"

    def fixture(self):
        """sandbox folder of the probe-city fixture (P.CSProbes.make_fixture: a vanilla city grown once and saved), from the sandbox
        cache <root>/fixtures/<key>, else the host cache <games_dir>/cities_skylines/fixtures/<key> (docker: every trial is a new
        container), else made now (~10-15 min, estimated) and kept in both -> path, or {"error"} when it could not be made.
        Vanilla baseline and modded runs of one game build get the same fixture (same key); the session records its hash."""
        key = self.fixture_key(); d = f"{self.root}/fixtures/{key}"
        if "ok" in self._sh(f"test -s {d}/fixture.json && ls {d}/gafixture*.crp >/dev/null 2>&1 && echo ok").stdout: return d
        host = config.path("games_dir") / self.name / "fixtures" / key
        if (host / "fixture.json").exists() and list(host.glob("gafixture*.crp")):
            self._sh(f"rm -rf {d} && mkdir -p $(dirname {d})"); self.seat.copy_in(host, d); return d
        info = P.CSProbes(self.seat, self.root, self.gd, display=self.pdn).make_fixture(d, label=f"fixture-{key[:24]}")
        if "error" in info: return info
        tmp = host.with_name(host.name + ".part"); host.parent.mkdir(parents=True, exist_ok=True)
        if self.seat.copy_out(d, tmp):
            import shutil
            shutil.rmtree(host, ignore_errors=True); tmp.rename(host)
        return d

    def compare(self, spec, base, got): return P.item_result(spec, base, got)

    def version(self):
        if getattr(self, "_ver", None): return self._ver
        try: self.install()
        except RuntimeError: pass
        b = self._sh(f"grep -m1 buildid {shlex.quote(str(Path(self.gd).parent.parent))}/appmanifest_255710.acf 2>/dev/null | grep -o '[0-9][0-9]*'").stdout.strip()
        self._ver = {"game": "cities_skylines", "build": f"Cities: Skylines 1 (Steam build {b or '?'})", "mod_loader": "the game's own (ICities)",
                     "image": self.seat.sandbox}
        return self._ver
