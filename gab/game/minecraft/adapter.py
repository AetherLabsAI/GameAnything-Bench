"""Minecraft Java Edition 26.3 + Fabric Loader 0.19.5 + Fabric API 0.162.0+26.3 (commercial: the user brings their own copy).

Game files: the player's own .minecraft (bought on minecraft.net; the Fabric profile `fabric-26.3-0.19.5` installed by the Fabric
installer or a launcher), read only: versions/, libraries/, assets/, the launcher's Java 25 runtime (jvm/ or runtime/). The
folder comes from $GAB_MINECRAFT_DIR, else ~/.minecraft in the sandbox. Host `docker`: the image
(docker/minecraft) holds only the runtime (Temurin JDK 25 at /opt/jdk); the user's folder on the HOST (config minecraft_dir = env
GAB_MINECRAFT_DIR; docker/minecraft/fetch_game.py lays it out from Mojang's and Fabric's public endpoints, no login) is
bind-mounted read only at /mc/main (docker_mounts()); game files never go into an image. provision() checks the game
jar (sha256 of 26.3.jar). Fabric API (Apache-2.0) is downloaded from the Fabric maven (sha256 pinned). No login: the game runs
OFFLINE with a local player name ("Player") into a single-player world (no Microsoft account for play or mod
testing); network off for the game (a dead SOCKS proxy for every Mojang service, multiplayer disabled); telemetry off, no
first-run screens (options.txt preset: accessibility onboarding, tutorial toasts, realms notifications, music toasts off,
pause-on-focus-loss off). No EULA dialog exists in the client.

Instances (<root> = <seat home>/gab-minecraft): <root>/inst/<inst>/ = the game dir (mods/ = Fabric API + the harness mod
gabbridge + the deliverable's jars, config/, resourcepacks/, options.txt, saves/, logs/) + gab/ (start.json, datapacks/,
saves/, pid, launcher log). Every start is a fresh world: a new world from start.json (seed 2024, survival, normal by
default; the deliverable's start.yaml may change it or name a shipped save) with the deliverable's datapacks, created by
gabbridge from the title screen as a player does with "Create New World". The game renders on a private Xvfb (:140+seat for
sessions, :150+seat for gate / probe instances) through VirtualGL EGL on the sandbox GPU (Minecraft 26.x needs a GPU).

Deliverable mod/ (devkit/README.md): Fabric mod source projects (built by `game build` / the gate with javac against the game:
26.x is unobfuscated, no Gradle), built *.jar Fabric mods, datapacks, resource packs, saves/<world>/, config/, start.yaml.
"""
import hashlib, json, os, re, shlex, time
from pathlib import Path

from .. import base as gbase
from ..base import GameAdapter
from ...util import tree_hash
from . import mcgab as M, probes as P

HERE = Path(__file__).parent
VERSION_ID = "fabric-26.3-0.19.5"
GAME_JAR = "versions/26.3/26.3.jar"
GAME_JAR_SHA256 = "4508d006323f24fa02876310c192d739af56516eb259000ac50f0909a68c9a2d"
API_JAR = "fabric-api-0.162.0+26.3.jar"
API_SHA256 = "8626c189b702f466800b1c8ddac6203cf8a1e0bdaee75a5f9a33b18c2baa81b1"
API_URL = "https://maven.fabricmc.net/net/fabricmc/fabric-api/fabric-api/0.162.0%2B26.3/fabric-api-0.162.0%2B26.3.jar"
IMAGE_API = f"/opt/gab/minecraft/{API_JAR}"   # Fabric API inside the docker image
MOUNT = "/mc/main"                  # host `docker`: where the user's game folder is mounted (read only)
PLAYER = "Player"
# gate / `game build` error lines (gbase.error_lines): log4j ERROR / FATAL entries + their exception line, crash-report heads,
# Fabric / Mixin failures, javac errors; never the offline-network noise of an account-less game (Realms, authlib, services)
ERR_PAT = re.compile(r"/(ERROR|FATAL)\]|^Caused by: |^Description: |Could not execute entrypoint|Mixin apply .*failed|Incompatible mods? found|"
                     r"^[\w.$]*(Exception|Error)\b|: error: |^\s+symbol: |^error: ")
ERR_IGNORE = re.compile(M.OFFLINE_NOISE.pattern + r"|^\s+at |^\s*\.\.\. \d+ more|/(INFO|WARN|DEBUG)\]", re.I)
ENGINE_KEYS = ["screen", "screen_title", "world", "dimension", "pos", "yaw", "pitch", "health", "max_health", "food", "saturation", "air", "armor",
               "xp_level", "game_mode", "difficulty", "on_ground", "vehicle", "held", "hotbar_slot", "inventory", "effects", "time_of_day", "day",
               "weather", "biome", "looking_at", "entities_near"]
MOD_KEYS = {"mods": dict, "registries": dict, "datapacks": list, "gamerules": dict, "scoreboard": dict, "teams": dict, "storage": dict,
            "bossbars": dict, "player_tags": list, "advancements": dict, "entities": dict, "block_entities": dict, "blocks_near": dict}


def shape_state(raw):
    """the bridge answer -> STATE_SPEC layout: fixed engine keys (null when unknown), fixed mod keys, screen_text <= 45 lines"""
    raw = raw if isinstance(raw, dict) else {"error": f"bad answer {str(raw)[:100]}"}
    e = raw.get("engine") if isinstance(raw.get("engine"), dict) else {}
    m = raw.get("mod") if isinstance(raw.get("mod"), dict) else {}
    st = {"running": bool(raw.get("running")), "engine": {k: e.get(k) for k in ENGINE_KEYS},
          "mod": {k: (m.get(k) if isinstance(m.get(k), t) else t()) for k, t in MOD_KEYS.items()},
          "screen_text": [str(x)[:200] for x in raw.get("screen_text") or [] if isinstance(raw.get("screen_text"), list)][:45]}
    for k in ("inventory", "effects", "entities_near"):
        if not isinstance(st["engine"][k], dict): st["engine"][k] = {}
    err = raw.get("error") or e.get("error") or m.get("error")
    if err: st["error"] = str(err)[:300]
    return st


def bridge_files():
    """the harness mod's source (relative path -> text) and its hash (rebuilt in the sandbox when it changes)"""
    d = HERE / "bridge"
    files = {str(p.relative_to(d)): p.read_text() for p in sorted(d.rglob("*")) if p.is_file() and p.suffix in (".java", ".json")}
    h = hashlib.sha256(json.dumps(files, sort_keys=True).encode()).hexdigest()[:16]
    return files, h


class Adapter(GameAdapter):
    name = "minecraft"
    title = "Minecraft Java Edition 26.3 (Fabric)"
    screen = (1280, 720)            # the game window fills the private 1280x720 display
    extra_help = ("  cmd 'COMMAND'                a server command on the running world (operator level; for testing, e.g. 'time set noon',\n"
                  "                               'give @p diamond'); the evaluation's player uses real input only\n"
                  "  query KIND [k=v ...]         read the world: block x= y= z= | blocks x1= .. z2= | entities [type=] [r=] | container x= y= z=\n"
                  "                               | spawn | perf | gamerules | time\n")

    def __init__(self, seat, root=None, main=None):
        super().__init__(seat)
        self.root = root or f"{seat.home}/gab-minecraft"
        docker = getattr(getattr(seat, "backend", None), "name", "") == "docker"
        self._main = main or (MOUNT if docker else os.environ.get("GAB_MINECRAFT_DIR"))   # docker: the host path is mounted at MOUNT
        s = int(seat.seat) if str(seat.seat).isdigit() else 1
        self.dn, self.pdn = 140 + s, 150 + s          # session display, gate / probe display
        self._ready = False

    @staticmethod
    def docker_mounts():
        """host `docker`: the user's game folder (config minecraft_dir / env GAB_MINECRAFT_DIR, a path on the host) read only at MOUNT"""
        from ... import config
        d = str(config.get("minecraft_dir") or "").strip()
        if not d:
            raise RuntimeError("minecraft on host docker needs your own game files: `python3 -I docker/minecraft/fetch_game.py DIR` "
                               "(public Mojang / Fabric downloads, no login; only if you own Minecraft: Java Edition), then "
                               "`gab setup --set minecraft_dir DIR` (or env GAB_MINECRAFT_DIR)")
        d = os.path.abspath(os.path.expanduser(d))
        if not os.path.isfile(f"{d}/versions/{VERSION_ID}/{VERSION_ID}.json"):
            raise RuntimeError(f"minecraft_dir {d}: no versions/{VERSION_ID}/{VERSION_ID}.json (run docker/minecraft/fetch_game.py {d})")
        return [(d, MOUNT, "ro")]

    def requirements(self):
        return {"game": "minecraft", "gpu": True, "mem_gb": 2.6, "steam": False, "hosts": ["pool", "docker"],
                "own_copy": "Minecraft: Java Edition (bought on minecraft.net), version 26.3, with the Fabric loader 0.19.5 profile "
                            "(fabric-26.3-0.19.5) in the player's .minecraft; set GAB_MINECRAFT_DIR to that folder (mounted read only; "
                            "host docker: config minecraft_dir, docker/minecraft/fetch_game.py lays it out from the public endpoints). "
                            "Not redistributed. Played offline: no Microsoft login in the benchmark.",
                "eula": "Minecraft EULA accepted by the owner of the copy (no in-game dialog); Fabric API Apache-2.0 (downloaded)",
                "image": "the java layer (Java 25 + Xvfb + VirtualGL); no game files in any image"}

    # ---- paths
    def main(self):
        if not self._main:
            r = self.seat.exec(f"for d in /mc/main {self.seat.home}/.minecraft; do [ -f $d/versions/{VERSION_ID}/{VERSION_ID}.json ] && echo $d && break; done").stdout.split()
            self._main = r[0] if r else "/mc/main"
        return self._main

    def idir(self, inst): return f"{self.root}/inst/{inst}"
    def mdir(self, inst): return f"{self.root}/stage/{inst}"
    def display(self, inst): return f":{self.pdn if self._side(inst) else self.dn}"
    def port(self, inst): return 27000 + (self.pdn if self._side(inst) else self.dn)
    @staticmethod
    def _side(inst): return str(inst).startswith("probe-") or str(inst).endswith("-gate")
    def api(self): return f"{self.root}/base/{API_JAR}"
    def paths(self, inst):
        p = {"game_files": self.main(), "instance": self.idir(inst), "mods": f"{self.idir(inst)}/mods", "world": f"{self.idir(inst)}/saves",
             "game_log": f"{self.idir(inst)}/logs/latest.log", "client_jar": f"{self.main()}/{GAME_JAR}"}
        src = self.seat.exec("for d in /lab/mc-sources /mc/sources; do [ -d $d/net/minecraft ] && echo $d && break; done").stdout.strip()
        if src: p["decompiled_sources"] = src
        return p

    def _py(self, args, timeout=600):
        return self.seat.exec(f"python3 {self.root}/bin/mcgab.py {args}", timeout=timeout)

    def _json(self, args, timeout=600):
        r = self._py(args, timeout)
        try: return json.loads(r.stdout.strip().splitlines()[-1])
        except (ValueError, IndexError): return {"ok": False, "error": (r.stdout[-600:] + r.stderr[-600:]).strip() or f"rc {r.returncode}"}

    # ---- install / instance
    def install(self):
        """game check (own copy), Fabric API, the harness tool + mod (rebuilt when its source changes), the options preset"""
        if self._ready: return True
        main = self.main()
        r = self.seat.exec(f"sha256sum {main}/{GAME_JAR} 2>/dev/null | cut -c1-64; test -f {main}/versions/{VERSION_ID}/{VERSION_ID}.json && echo fabric").stdout.split()
        if not r or r[0] != GAME_JAR_SHA256:
            raise RuntimeError(f"Minecraft 26.3 not found in {main} (need {GAME_JAR}, sha256 {GAME_JAR_SHA256[:12]}...; the player's own copy: GAB_MINECRAFT_DIR)")
        if "fabric" not in r: raise RuntimeError(f"no Fabric profile {VERSION_ID} in {main}/versions (install Fabric loader 0.19.5 for 26.3)")
        self.seat.exec(f"mkdir -p {self.root}/bin {self.root}/base {self.root}/inst {self.root}/stage {self.root}/cache {self.root}/bridge", check=True)
        for f in ("mcgab.py", "mcprobes.py"): self.seat.put(f"{self.root}/bin/{f}", (HERE / f).read_text())
        self.seat.put(f"{self.root}/base/options.txt", (HERE / "options.txt").read_text())
        if self.seat.exec(f"sha256sum '{self.api()}' 2>/dev/null | cut -c1-64").stdout.strip() != API_SHA256:
            # the docker image ships it (/opt/gab/minecraft: the container has no network); else the Fabric maven
            self.seat.exec(f"{{ cp -f '{IMAGE_API}' '{self.api()}.tmp' 2>/dev/null || curl -sSfL --retry 3 -o '{self.api()}.tmp' '{API_URL}'; }} "
                           f"&& [ \"$(sha256sum '{self.api()}.tmp' | cut -c1-64)\" = {API_SHA256} ] && mv -f '{self.api()}.tmp' '{self.api()}'", timeout=300)
            if self.seat.exec(f"sha256sum '{self.api()}' 2>/dev/null | cut -c1-64").stdout.strip() != API_SHA256:
                raise RuntimeError(f"Fabric API download failed ({API_URL})")
        files, h = bridge_files()
        if self.seat.exec(f"cat {self.root}/base/gabbridge.hash 2>/dev/null").stdout.strip() != h or \
                self.seat.exec(f"test -f {self.root}/base/gabbridge.jar && echo y").stdout.strip() != "y":
            self.seat.exec(f"rm -rf {self.root}/bridge/src {self.root}/bridge/fabric.mod.json")
            for rel, txt in files.items(): self.seat.put(f"{self.root}/bridge/{rel}", txt)
            b = self._json(f"build {self.root}/bridge {self.root}/base/gabbridge.jar {main} {VERSION_ID} '{self.api()}'")
            if not b.get("ok"): raise RuntimeError(f"the harness mod did not build: {b.get('log') or b}")
            self.seat.put(f"{self.root}/base/gabbridge.hash", h)
        self._ready = True
        return True

    def provision(self, inst):
        self.install(); self.stop(inst)
        d = self.idir(inst)
        self.seat.exec(f"rm -rf {d} && mkdir -p {d}/mods {d}/config {d}/resourcepacks {d}/gab/datapacks {d}/gab/saves && "
                       f"cp '{self.api()}' {self.root}/base/gabbridge.jar {d}/mods/ && cp {self.root}/base/options.txt {d}/options.txt", check=True)
        self.seat.put(f"{d}/gab/start.json", json.dumps(M.DEFAULT_START))
        return {"instance": inst, "dir": d, "display": self.display(inst)}

    def _stage(self, inst, mod_dir):
        """the deliverable's mod/ into the sandbox (<root>/stage/<inst>/mod)"""
        dst = f"{self.mdir(inst)}/mod"; h = tree_hash(mod_dir)
        if self.seat.exec(f"cat {self.mdir(inst)}/mod.hash 2>/dev/null").stdout.strip() == h: return dst   # unchanged: no re-copy
        self.seat.exec(f"rm -rf {self.mdir(inst)} && mkdir -p {self.mdir(inst)}")    # (a shipped save can be large: base64 over exec)
        self.seat.copy_in(Path(mod_dir), dst)
        self.seat.put(f"{self.mdir(inst)}/mod.hash", h)
        return dst

    def install_mod(self, inst, mod_dir, probe=False):
        """a player's install: built jars -> mods/, resource packs -> resourcepacks/ (enabled), config/ -> config/; datapacks and a
        shipped save go into the world at every start. probe=True: the default world (start.yaml ignored), for the probes"""
        self.install()
        if self.seat.exec(f"test -f {self.idir(inst)}/mods/gabbridge.jar && echo y").stdout.strip() != "y": self.provision(inst)
        st = self._stage(inst, mod_dir)
        r = self._json(f"install {st} {self.idir(inst)} {self.main()} {VERSION_ID} '{self.api()}' {self.root}/cache" + (" --probe" if probe else ""), timeout=1200)
        r = json.loads(json.dumps(r).replace(st + "/", "mod/"))
        f = f"{self.idir(inst)}/gab/install_error.txt"
        if not r.get("ok"):                            # start() reports it (the agent's `game start` gets the build errors)
            msg = "\n".join(["the mod could not be installed:"] + (r.get("problems") or []) + (r.get("build") or []) + ([r["error"]] if r.get("error") else []))
            self.seat.put(f, msg[-6000:])
        else: self.seat.exec(f"rm -f {f}")
        return r

    def build(self, inst, mod_dir=None):
        """the gate's build: structure of mod/ + javac of every source project (cached by content) -> (ok, log with the errors)"""
        if mod_dir is None: return True, "no mod dir given"
        L = M.layout(mod_dir)
        if L["problems"] and not L["projects"]:
            return False, gbase.gate_log(L["problems"], "mod/ is not installable (layout problems)", "\n".join(f"warning: {w}" for w in L["warnings"]))
        self.install()
        st = self._stage(f"_build-{inst}", mod_dir)
        r = self._json(f"check {st} {self.main()} {VERSION_ID} '{self.api()}' {self.root}/cache", timeout=1200)
        self.seat.exec(f"rm -rf {self.mdir('_build-' + inst)}")
        r = json.loads(json.dumps(r).replace(st + "/", "mod/"))              # sandbox stage paths -> mod/... in the messages
        log = []
        if r.get("problems"): log += ["mod/ problems:"] + r["problems"]
        if r.get("build"): log += ["build:"] + r["build"]
        if r.get("warnings"): log += [f"warning: {w}" for w in r["warnings"]]
        if r.get("error"): log.append(r["error"])
        if r.get("ok"):
            log.append(f"ok: mods {r.get('mod_ids')}, {json.dumps(r.get('layout'))}, start {r.get('start') or 'default (fresh world)'}")
            return True, gbase.gate_log([], None, "\n".join(log))
        failed = [b.split(":", 1)[0] for b in r.get("build") or [] if "FAILED" in b.split("\n", 1)[0]]
        why = (f"javac failed for {failed}" if failed else "mod/ is not installable (layout problems)" if r.get("problems") else
               f"the build did not run: {r.get('error')}" if r.get("error") else "the build failed")
        E = (r.get("problems") or []) + gbase.error_lines("\n".join(r.get("build") or []), ERR_PAT, ERR_IGNORE)
        return False, gbase.gate_log(E[:gbase.ERR_N], why, "\n".join(log))

    def _x(self, inst, cmd, timeout=180):
        """xdotool / ffmpeg on the instance display; the game window gets the input focus first (no window manager)"""
        focus = "w=$(xdotool search --onlyvisible --name '^Minecraft' 2>/dev/null | head -1); [ -n \"$w\" ] && xdotool windowfocus $w 2>/dev/null; "
        return self.seat.exec(f"export DISPLAY={self.display(inst)}; {focus}{cmd}", timeout=timeout)

    def start(self, inst, timeout=240):
        """fresh world (or a copy of the shipped save) with the installed mods; ok when the player is in the world"""
        d = self.idir(inst); self.stop(inst)
        e = self.seat.exec(f"cat {d}/gab/install_error.txt 2>/dev/null").stdout
        if e.strip(): return False, gbase.gate_log(gbase.error_lines(e, r"."), "the mod could not be installed (build / layout errors below)")
        r = self._json(f"launch {d} {self.main()} {VERSION_ID} {self.display(inst)} {self.port(inst)} {PLAYER}", timeout=120)
        if not r.get("ok"): return False, gbase.gate_log(self.gate_errors(inst), f"the game could not be launched: {r.get('error')}")
        st = json.loads(self.seat.exec(f"cat {d}/gab/start.json").stdout or "{}")
        if st.get("mode") == "menu": time.sleep(30); return self._alive(inst), "title menu"
        w = self._json(f"wait {d} {self.port(inst)} {timeout}", timeout=timeout + 60)
        if not w.get("ok"):
            E = self.gate_errors(inst)
            why = ("the game crashed or exited before reaching the world" if w.get("error") == "the game exited" else
                   f"the start world did not load: {w['error']}" if "start world" in str(w.get("error")) or "could not open" in str(w.get("error")) else
                   f"not in the start world after {timeout} s (timed out; last bridge answer: {json.dumps(w.get('health'))[:200]})" if "not in the world" in str(w.get("error"))
                   else str(w.get("error")))
            return False, gbase.gate_log(E, why, "" if E else "game log tail:\n" + (w.get("log") or "")[-2000:])
        time.sleep(2); self._x(inst, "true")          # focus the window: mouse look needs an active window
        return True, f"in the world after {w.get('s')} s ({st.get('mode')} world {st.get('world')!r}, seed {st.get('seed')}, {st.get('game_mode')}, {st.get('difficulty')})"

    def _alive(self, inst): return bool(self._json(f"alive {self.idir(inst)}", timeout=30).get("alive"))

    def load_check(self, inst, out_dir=None):
        """the gate's load test: the real game starts with the mod and enters the start world; every mod id of the deliverable is
        loaded by Fabric. Game-log error lines are reported (MC-STABLE judges them against vanilla), not failed here. The agent's
        `game build` load-tests on <inst>-gate: its running dev game is closed first (two games on one seat: memory)."""
        if str(inst).endswith("-gate"): self.stop(str(inst)[:-5])
        ok, log = self.start(inst)
        if not ok: return False, log
        st = self.state(inst); E = self.gate_errors(inst)
        want = set(json.loads(self.seat.exec(f"cat {self.idir(inst)}/gab/install.json 2>/dev/null").stdout or "{}").get("mods") or [])
        loaded = set((st.get("mod") or {}).get("mods") or {})
        missing = sorted(m[:-4] for m in want if m[:-4] not in loaded)
        detail = f"{log}\nmods loaded: {sorted(loaded) or 'none besides the base install'}"
        if missing: return False, gbase.gate_log(E, f"mods not loaded by Fabric: {missing}", detail)
        if not st.get("running") or st.get("error"):
            return False, gbase.gate_log(E, f"the game did not answer after the start: {st.get('error') or 'not running'}", detail)
        return True, gbase.gate_log(E, None, detail)

    def stop(self, inst):
        self.seat.exec(f"python3 {self.root}/bin/mcgab.py stop {self.idir(inst)} {self.display(inst)} 2>/dev/null; true", timeout=60)

    def log_tail(self, inst, n=40):
        return self.seat.exec(f"tail -{int(n)} {self.idir(inst)}/logs/latest.log 2>/dev/null || tail -{int(n)} {self.idir(inst)}/gab/launcher.log").stdout

    def gate_errors(self, inst):
        """the error lines of the instance's crash report + game log (or launcher log), for gate / build answers"""
        d = self.idir(inst)
        t = self.seat.exec(f"cat {d}/crash-reports/*.txt 2>/dev/null | head -150; cat {d}/logs/latest.log 2>/dev/null || tail -400 {d}/gab/launcher.log 2>/dev/null",
                           timeout=60).stdout
        return gbase.error_lines(t, ERR_PAT, ERR_IGNORE, after=1)

    def log_errors(self, inst):
        r = self._json(f"errors {self.idir(inst)}", timeout=60)
        return r if "errors" in r else {"errors": None, "lines": [], "crash": False, "error": r.get("error")}

    def state(self, inst):
        """STATE_SPEC snapshot, live from the running game through gabbridge (one exec, ~0.1 s in the game):
        engine (fixed keys, null when unknown): screen (in_game | dead | title_menu | loading | the open screen's class, e.g.
          InventoryScreen, ContainerScreen, PauseScreen, ChatScreen), screen_title, world (level name), dimension, pos [x,y,z] (0.1),
          yaw, pitch, health, max_health, food, saturation, air, armor, xp_level, game_mode, difficulty, on_ground, vehicle (entity
          type), held (main-hand item), hotbar_slot (1-9), inventory {item: count}, effects {effect: level}, time_of_day (0-23999),
          day, weather (clear | rain | thunder), biome, looking_at ({block, pos} | {entity, pos} | null), entities_near {type: count}
          (radius 32).
        mod (generic, empty on a vanilla world): mods {id: version} loaded besides the base install; registries {namespace: {block |
          item | entity_type | block_entity_type | sound_event | mob_effect: {count, ids}}} entries outside minecraft / Fabric;
          datapacks [enabled packs besides vanilla / Fabric's]; gamerules {rule: {value, default}} that differ from the default;
          scoreboard {objective: {criteria, display, slots, scores {holder: value}}}; teams; storage {id: SNBT} (command storage);
          bossbars {id: {name, value, max, visible}}; player_tags; advancements {id: done | in_progress} outside minecraft;
          entities {"<type>#<id>": {type, name, pos, health, tags, data (SNBT of a non-vanilla entity minus the common keys)}}
          (nearest 20 within 64 that are non-vanilla, custom-named or tagged); block_entities {"x,y,z": {type, text | name |
          data}} within 8 (non-vanilla types, named containers, signs with text); blocks_near {block: count} of non-vanilla blocks
          within 8.
        screen_text: "title: / subtitle: / actionbar: ..." while shown, "boss: <bar name>", "sidebar: <objective>" and its lines,
          "screen: ..." the open screen's title, labels and widget texts, "chat: ..." the last 10 chat lines (oldest first).
        Values cut at 200 chars; lists capped."""
        r = self._json(f"state {self.idir(inst)} {self.port(inst)}", timeout=20)
        if "running" not in r: r = {"running": False, "error": r.get("error") or "no answer"}
        return shape_state(r)

    def dev_command(self, inst, k, v, mod, wd):
        if k == "cmd":
            r = self._json(f"call {self.port(inst)} {shlex.quote(json.dumps({'m': 'cmd', 'cmd': ' '.join(v)}))} 30", timeout=60)
            return r
        if k == "query":
            if not v: return {"ok": False, "error": "query KIND [k=v ...]"}
            args = {}
            for a in v[1:]:
                key, _, val = a.partition("=")
                try: args[key] = json.loads(val)
                except ValueError: args[key] = val
            return self._json(f"call {self.port(inst)} {shlex.quote(json.dumps({'m': 'query', 'q': v[0], 'args': args}))} 30", timeout=60)
        return None

    # ---- regression
    def run_probes(self, names, mod_dir=None, label="vanilla"):
        """a fresh instance (+ the mod, default world) -> the probes (mcprobes.py) in one sandbox run, then the game log's errors"""
        inst = f"probe-{label}"[:60]
        self.provision(inst)
        if mod_dir:
            r = self.install_mod(inst, mod_dir, probe=True)
            if not r.get("ok"): return {n: {"error": f"install failed: {json.dumps(r)[:300]}"} for n in names}
        ok, log = self.start(inst)
        if not ok: return {n: {"error": f"instance did not start: {log[-300:]}"} for n in names}
        try:
            run = [n for n in P.ORDER if n in names]
            out = f"{self.idir(inst)}/gab/probes.json"
            r = self._json(f"probes {self.idir(inst)} {self.port(inst)} {self.display(inst)} {','.join(run) or 'difficulty'} {out}", timeout=1500)
            try: res = json.loads(self.seat.exec(f"cat {out}").stdout)
            except ValueError: res = {n: {"error": f"probe run wrote no result: {json.dumps(r)[:200]}"} for n in run}
            res["log_errors"] = self.log_errors(inst)
            return res
        finally: self.stop(inst)

    def compare(self, spec, base, got): return P.item_result(spec, base, got)

    def version(self):
        return {"game": "minecraft", "build": "26.3 (Fabric 0.19.5, Fabric API 0.162.0+26.3)", "mod_loader": "fabric-loader 0.19.5 + fabric-api 0.162.0+26.3",
                "image": self.seat.sandbox}
