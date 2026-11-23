"""Xonotic 0.8.6 adapter. Install: the upstream release zip (dl.xonotic.org/xonotic-0.8.6.zip, data 20230620) unpacked into
<seat home>/oss-xonotic/Xonotic (the verified reference install; `install()` reproduces it on another seat sandbox).
GPL game; the one consent screen is the XonStat Terms of Service of the GL client's menu: the benchmark runtime accepts it in
setup, with statistics off (cl_allow_uid2name / uidtracking 0) and a non-default player name (no first-run
welcome dialog) -- seeded into every fresh userdir by the launcher (probes.CLIENT_SH_TEXT). Network off: no HTTP (cl_curl),
no master servers, local address only. A fresh userdir per start; the GL client runs as a listen server on the seat's display
through VirtualGL, window 1280x720 at 0,0 (= the seat screen), rcon on 127.0.0.1 for state().

Deliverable mod/ (devkit/README.md): files a player drops into the userdir's data/ (*.pk3 packs, *.pk3dir/ unpacked packs,
*.cfg e.g. autoexec.cfg, loose maps/ ...) + optional start.yaml {mode: game | menu, map, gametype, bots}.
"""
import json, re, shlex, time, zipfile
from pathlib import Path

import yaml

from .. import base as gbase
from ..base import GameAdapter
from . import probes as P
from gab.game.base import vglrun

ZIP = "https://dl.xonotic.org/xonotic-0.8.6.zip"
STOCK_MAPS = {"afterslime", "atelier", "boil", "bromine", "catharsis", "courtfun", "dance", "darkzone", "erbium", "finalrage", "fuse",
              "geoplanetary", "glowplant", "go", "implosion", "leave_em_behind", "nexballarena", "opium", "runningman", "runningmanctf",
              "silentsiege", "solarium", "stormkeep", "techassault", "trident", "vorix", "warfare", "xoylent"}
GAMETYPES = {"dm", "tdm", "ctf", "ca", "dom", "ft", "ka", "kh", "lms", "race", "cts", "ons", "as", "inv", "nb", "duel", "mayhem", "tmayhem"}
START_KEYS = {"mode", "map", "gametype", "bots"}
START_CFG = '''// gab start script (fresh start of the instance)
set cl_welcome 0
set g_playerstats_gamereport_uri ""
set bot_number {bots}
set rcon_secure 0
set rcon_password "{pw}"
alias cl_hook_gamestart_all "defer 2 \\"cmd join\\"; defer 3 \\"togglemenu 0\\""
{map}
'''


ENGINE_KEYS = ["map", "gametype", "time_s", "match", "screen", "player", "others"]
PLAYER_KEYS = ["name", "class", "pos", "health", "armor", "alive", "frags", "deaths", "weapons", "weapon", "ammo"]


def shape_state(raw):
    """xoprobe.py state answer -> {running, engine (fixed keys), mod, screen_text} (+ error)"""
    raw = raw if isinstance(raw, dict) else {}
    mod = raw.get("mod") if isinstance(raw.get("mod"), dict) else {}
    out = {"running": bool(raw.get("running")), "engine": shape_engine(raw.get("engine") or {}),
           "mod": {"cvars": {}, "aliases": {}, "entities": {}, **mod},
           "screen_text": [str(x)[:200] for x in raw.get("screen_text") or [] if isinstance(raw.get("screen_text"), list)][:15]}
    if raw.get("error"): out["error"] = str(raw["error"])[:300]
    return out


def shape_engine(e):
    """the fixed engine key set (null when unknown)"""
    e = e if isinstance(e, dict) else {}
    pl = e.get("player") if isinstance(e.get("player"), dict) else {}
    out = {k: e.get(k) for k in ENGINE_KEYS}
    out["player"] = {k: pl.get(k) for k in PLAYER_KEYS}
    if not isinstance(out["others"], dict): out["others"] = {}
    if not isinstance(out["player"]["ammo"], dict): out["player"]["ammo"] = {}
    return out


class Adapter(GameAdapter):
    name = "xonotic"
    title = "Xonotic"
    screen = (1280, 720)            # the client window at 0,0 fills the 1280x720 seat screen (no window manager)
    extra_help = "  console 'CMD'                a console command on the running game (rcon; for testing, e.g. 'sv_cmd setbots 2')\n"

    def __init__(self, seat, root=None):
        super().__init__(seat)
        self.root = root or f"{seat.home}/oss-xonotic"
        self.gl = vglrun()
        s = int(seat.seat) if str(seat.seat).isdigit() else 1
        self.port = 26400 + 20 * s          # +0 player client, +1 probe session, +2 probe client, +3 gate

    def requirements(self):
        return {"game": "xonotic", "gpu": True, "mem_gb": 1.0, "steam": False, "hosts": ["pool", "docker", "local"],
                "image": "gameanything/xonotic:0.8.6", "eula": "XonStat ToS (client menu): accepted in setup, statistics off"}

    def uname(self, inst): return f"gab-{inst}"                         # userdir <root>/inst/gab-<inst>
    def udir(self, inst): return f"{self.root}/inst/{self.uname(inst)}"
    def mdir(self, inst): return f"{self.root}/gab/{inst}"              # staged mod (mod/) + start.json
    def paths(self, inst): return {"game_dir": f"{self.root}/Xonotic", "stock_data": f"{self.root}/Xonotic/data", "userdir": self.udir(inst),
                                   "mod_files_in": f"{self.udir(inst)}/data", "game_log": f"{self.udir(inst)}/console.log"}
    def _py(self, args, timeout=900):
        return self.seat.exec(f"XON_ROOT={self.root} XON_GL={shlex.quote(self.gl)} python3 {self.root}/bridge/xoprobe.py " + args, timeout=timeout)

    # ---- install / instance
    def install(self):
        ok = lambda: self.seat.exec(f"test -x {self.root}/Xonotic/xonotic-linux64-dedicated && test -f {self.root}/Xonotic/data/xonotic-20230620-maps.pk3 && echo ok").stdout.strip() == "ok"
        if not ok():
            self.seat.exec(f"mkdir -p {self.root} && cd {self.root} && curl -sSL -o x.zip {ZIP} && unzip -q -o x.zip 'Xonotic/xonotic-linux64-*' "
                           "'Xonotic/data/*' 'Xonotic/server/*' 'Xonotic/misc/*' 'Xonotic/key_0.d0pk' 'Xonotic/COPYING' -x '*music*' '*nexcompat*'; rm -f x.zip",
                           timeout=3600)
            if not ok(): raise RuntimeError("Xonotic install failed (download / unzip of the 0.8.6 release)")
        self.seat.put(f"{self.root}/bridge/xoprobe.py", Path(P.__file__).read_text())      # always refresh the bridge
        self.seat.put(f"{self.root}/bridge/xo-client.sh", P.CLIENT_SH_TEXT)
        return True

    def provision(self, inst):
        self.install(); self.stop(inst)
        self.seat.exec(f"rm -rf {self.udir(inst)} {self.mdir(inst)} && mkdir -p {self.mdir(inst)}/mod", check=True)
        self.seat.put(f"{self.mdir(inst)}/start.json", "{}")
        return {"instance": inst, "dir": self.udir(inst), "display": self.seat.display}

    @staticmethod
    def _layout(mod_dir):
        m = Path(mod_dir) if mod_dir else None
        files = sorted(p for p in m.iterdir() if p.name != "start.yaml") if m and m.exists() else []
        st = yaml.safe_load((m / "start.yaml").read_text()) if m and (m / "start.yaml").exists() else {}
        return files, st or {}

    def install_mod(self, inst, mod_dir):
        """the mod's files into the instance; start() copies them into a fresh userdir's data/ (a player's install)"""
        files, st = self._layout(mod_dir); d = self.mdir(inst)
        self.seat.exec(f"rm -rf {d}/mod && mkdir -p {d}/mod")
        for f in files: self.seat.copy_in(f, f"{d}/mod/{f.name}")
        self.seat.put(f"{d}/start.json", json.dumps(st))
        return {"files": [f.name for f in files], "start": st}

    def _start(self, inst):
        r = self.seat.exec(f"cat {self.mdir(inst)}/start.json 2>/dev/null").stdout.strip()
        return json.loads(r) if r else {}

    def build(self, inst, mod_dir=None):
        """structure check of the deliverable -> (ok, log); the load test is load_check()"""
        if mod_dir is None: return True, "no mod dir given"
        files, st = self._layout(mod_dir); L = []
        if not files: L.append("mod/ holds nothing besides start.yaml (expected *.pk3, *.pk3dir/, *.cfg or data folders)")
        maps = set()
        for f in files:
            if f.suffix == ".pk3":
                try:
                    with zipfile.ZipFile(f) as z:
                        n = z.namelist(); maps |= {Path(x).stem for x in n if re.match(r"maps/[^/]+\.bsp$", x)}
                        if not n: L.append(f"{f.name}: empty pk3")
                except zipfile.BadZipFile: L.append(f"{f.name}: not a zip file (a pk3 is a zip)")
            elif f.suffix == ".pk3dir" and not f.is_dir(): L.append(f"{f.name}: a .pk3dir must be a directory")
            elif f.is_dir(): maps |= {p.stem for p in f.glob("maps/*.bsp")} | {p.stem for p in f.glob("**/maps/*.bsp")}
            if " " in f.name: L.append(f"{f.name}: no spaces in file names")
        for k in set(st) - START_KEYS: L.append(f"start.yaml: unknown key {k} (have: {', '.join(sorted(START_KEYS))})")
        if st.get("mode", "game") not in ("game", "menu"): L.append(f"start.yaml: mode must be game or menu, not {st.get('mode')}")
        if st.get("gametype", "dm") not in GAMETYPES: L.append(f"start.yaml: unknown gametype {st.get('gametype')}")
        if st.get("map") and st["map"] not in STOCK_MAPS | maps: L.append(f"start.yaml: map {st['map']} is neither stock nor maps/{st['map']}.bsp in the mod")
        if not isinstance(st.get("bots", 0), int) or not 0 <= st.get("bots", 0) <= 15: L.append("start.yaml: bots must be an integer 0-15")
        return not L, "\n".join(L) or f"ok: {[f.name for f in files]}, start {st or 'default (stormkeep, dm)'}"

    def load_check(self, inst, out_dir=None):
        """the gate: a dedicated server with the mod on the start map + gametype (a bot spawns, 10 s alive), then the real client
        reaches the start target"""
        st = self._start(inst); out = f"{self.mdir(inst)}/gate.json"
        self._py(f"gate --name {self.uname(inst)}-gate --port {self.port + 3} --mod {self.mdir(inst)}/mod --map {st.get('map') or P.MAP} "
                 f"--gametype {st.get('gametype') or 'dm'} --out {out}", timeout=300)
        r = self.seat.exec(f"cat {out} 2>/dev/null").stdout
        g = json.loads(r) if r.strip() else {"ok": False, "error": "gate run wrote no result"}
        E = gbase.error_lines("\n".join(g.pop("_error_lines", None) or []), P.ERR, P.ERR_IGNORE)
        if not g.get("ok"):
            why = g.get("error") or (f"map {g.get('map')} did not load" if g.get("map_loaded") is False else "no bot spawned alive"
                                     if not g.get("bot_alive") else "the server did not survive 10 s")
            return False, gbase.gate_log(E, f"server load test failed: {why}", json.dumps(g)[:2500])
        ok, log = self.start(inst)
        E += [x for x in gbase.error_lines(self.seat.exec(f"cat {self.udir(inst)}/console.log 2>/dev/null").stdout, P.ERR, P.ERR_IGNORE) if x not in E]
        self.stop(inst)
        why = None if ok else "the client did not reach the start target: " + (log.splitlines() or ["?"])[0][:200]
        return ok, gbase.gate_log(E[:gbase.ERR_N], why, json.dumps(g)[:1500] + "\nclient: " + log[-1500:])

    def start(self, inst):
        """fresh userdir + the mod's files in data/ -> the GL client on the seat display; ok when the start target is reached
        (in game: the local player exists on the map; menu: the window is up)"""
        st = self._start(inst); u = self.udir(inst); mode = st.get("mode", "game")
        self.stop(inst)
        mp = st.get("map") or P.MAP
        vanilla = not self.seat.exec(f"ls -A {self.mdir(inst)}/mod 2>/dev/null | head -1").stdout.strip() and mode == "game" and not st.get("gametype")
        if mode == "game": self._ensure_bases(inst, mp, st.get("gametype") or "dm", vanilla)
        pw = f"gab{int(time.time() * 1000) % 10**9}"
        cfg = START_CFG.format(bots=int(st.get("bots", 0) or 0), pw=pw, map="" if mode == "menu" else f"map {mp}")
        self.seat.exec(f"rm -rf {u} {u}.pid && mkdir -p {u}/data && cp -r {self.mdir(inst)}/mod/. {u}/data/ 2>/dev/null; true")
        self.seat.put(f"{u}/data/gab_start.cfg", cfg); self.seat.put(f"{u}/rcon.pw", pw)
        # setsid -f: the client must not hold the exec session's pipes (an exec that never returns, then a SIGABRT on hangup)
        self.seat.exec(f"cd {self.root} && setsid -f bash bridge/xo-client.sh {self.seat.display} {self.uname(inst)} {self.port} gab_start.cfg "
                       f"{shlex.quote(self.gl)} +maxplayers 16 > /dev/null 2>&1 < /dev/null")
        time.sleep(2); t0 = time.time(); moved = False; gt_done = st.get("gametype", "dm") == "dm"
        while time.time() - t0 < 150:
            time.sleep(3)
            if not self._alive(inst): return False, "the game exited\n" + self.log_tail(inst)
            if not moved:
                moved = bool(self._x(inst, "for w in $(xdotool search --name '^Xonotic' 2>/dev/null); do xdotool windowmove $w 0 0; done; xdotool search --name '^Xonotic'").stdout.strip())
                if moved and mode == "menu": time.sleep(15); return True, "main menu"
                continue
            e = (self._state(inst, light=True).get("engine") or {})
            if e.get("map") == mp and (e.get("player") or {}).get("class") == "player" and (e.get("time_s") or 0) > 1:
                if not gt_done:
                    self.console(inst, f"sv_cmd gametype {st['gametype']}"); self.console(inst, f"map {mp}"); gt_done = True; time.sleep(5); continue
                time.sleep(2)
                if vanilla and "cvars.json" not in self._bases():           # the vanilla cvar / alias baseline (once per install)
                    self._py(f"baseline {self.uname(inst)} {self.port} cvars", timeout=60)
                return True, f"in game: {json.dumps(e)[:400]}"
        return False, "start target not reached in 150 s\n" + self.log_tail(inst)

    def _alive(self, inst):
        """running and not a zombie (the sandbox's PID 1 does not reap exited games)"""
        return self.seat.exec(f"p=$(cat {self.udir(inst)}.pid 2>/dev/null); [ -n \"$p\" ] && ps -o stat= -p $p | grep -qv Z && echo up").stdout.strip() == "up"

    def stop(self, inst):
        """kill the client's own process group (setsid: pgid = the recorded pid)"""
        self.seat.exec(f"p=$(cat {self.udir(inst)}.pid 2>/dev/null); [ -n \"$p\" ] && {{ kill -- -$p 2>/dev/null; sleep 1; kill -9 -- -$p 2>/dev/null; }}; rm -f {self.udir(inst)}.pid; true")

    def log_tail(self, inst, n=40): return self.seat.exec(f"tail -{n} {self.udir(inst)}/console.log 2>/dev/null").stdout

    def _bases(self): return set(self.seat.exec(f"ls {self.root}/gab/state_base 2>/dev/null").stdout.split())

    def _ensure_bases(self, inst, mp, gt, vanilla):
        """vanilla baselines for state()'s mod section, made once per install (<root>/gab/state_base): the entities of the start
        map + gametype on a vanilla dedicated server; the cvars / aliases of a vanilla GL client with this start cfg (a vanilla
        instance records them itself when it reaches the game; before a modded instance a vanilla one is started for it)"""
        have = self._bases()
        if f"ents_{mp}_{gt}.json" not in have:
            self._py(f"entbase --name gab-entbase --port {self.port + 3} --map {mp} --gametype {gt}", timeout=300)
        if "cvars.json" not in have and not vanilla and inst != "statebase":
            self.provision("statebase")
            try: self.start("statebase")
            finally: self.stop("statebase")

    def state(self, inst):
        """STATE_SPEC snapshot, live through rcon on the GL client's listen server (one exec of xoprobe.py state, ~1 s):
        engine: map, gametype, time_s (server time, 0.1), match (countdown | warmup | running | ended), screen (menu | in_game |
          scoreboard), player {name, class (player | spectator | observer), pos [x,y,z] (0.1), health, armor, alive, frags,
          deaths (scoreboard), weapons [held], weapon (current), ammo {type: n}}, others {name: {class, frags, alive}} (bots and
          other clients).
        mod: cvars {name: {value, vanilla}} new or changed against the vanilla baseline (volatile ones left out; a full cvarlist
          every 20 s, the cvars found re-read on every call), aliases {name:
          commands} new or changed, entities {classname: {count, vanilla}} map entities whose count differs from the vanilla
          map + gametype (classnames of the map's entity source: stock bsp, a .ent override or a map of the mod); baseline notes
          when a baseline file is missing.
        screen_text (max 15): "centerprint: ..." the CSQC centerprints on screen (title + messages not expired; the engine's own
          `cprint` is not readable), then the last 10 console lines since the map started (chat and notifications, which the chat
          area shows; also plain echo / print output, which only the console shows), from the client's console log; the
          harness's rcon blocks and engine noise left out.
        """
        return self._state(inst)

    def _state(self, inst, light=False):
        """state(); light=True: engine only (start() polls with it; selfcheck times state() itself)"""
        r = self._py(f"state {self.uname(inst)} {self.port}" + (" light" if light else ""), timeout=30).stdout.strip()
        try: raw = json.loads(r.splitlines()[-1])
        except (ValueError, IndexError): raw = {"running": self._alive(inst), "error": r[-300:] or "no answer"}
        return shape_state(raw)

    def console(self, inst, cmd):
        return self._py(f"rcon {self.uname(inst)} {self.port} {shlex.quote(cmd)}", timeout=30).stdout

    def log_errors(self, inst):
        t = self.seat.exec(f"cat {self.udir(inst)}/console.log 2>/dev/null").stdout
        lines = [l for l in t.splitlines() if P.ERR.search(l) and not P.ERR_IGNORE.search(l)]
        return {"errors": len(lines), "lines": lines[:20]}

    def dev_command(self, inst, k, v, mod, wd):
        if k != "console": return None
        if not self._alive(inst): return {"ok": False, "error": "the game is not running (game start)"}
        return {"ok": True, "output": P.COLOR.sub("", self.console(inst, " ".join(v)))[-4000:]}

    # ---- regression
    def run_probes(self, names, mod_dir=None, label="vanilla"):
        """server probes in one fresh dedicated-server session (+ the GL client run for `client`), as probes_xonotic session"""
        d = f"{self.root}/gab/probe-{label}"; n = f"gab-p-{label}"
        self.seat.exec(f"rm -rf {d} && mkdir -p {d}/mod")
        for f in (self._layout(mod_dir)[0] if mod_dir else []): self.seat.copy_in(f, f"{d}/mod/{f.name}")
        srv = [x for x in names if x not in ("client", "log_errors")]
        self._py(f"session --name {n} --port {self.port + 1} --mod {d}/mod --only {','.join(srv) or 'physics_cvars'} --out {d}/session.json", timeout=1200)
        r = self.seat.exec(f"cat {d}/session.json 2>/dev/null").stdout
        res = json.loads(r) if r.strip() else {"error": "probe session wrote no result", **{x: {"error": "no session"} for x in srv}}
        if "client" in names:
            for k in range(2):                # 1 in 5 vanilla client runs aborted on map load under pod load (pool note): one retry
                self._py(f"client --name {n}-cl --port {self.port + 2} --display {self.seat.display} --mod {d}/mod --out {d}/client.json", timeout=420)
                c = self.seat.exec(f"cat {d}/client.json 2>/dev/null").stdout
                res["client"] = json.loads(c) if c.strip() else {"hud": {"error": "client run wrote no result"}}
                if "error" not in res["client"].get("hud", {}): break
        return res

    def compare(self, spec, base, got): return P.item_result(spec, base, got)

    def version(self):
        m = re.search(r"xonotic-(\d{8})-data\.pk3", self.seat.exec(f"ls {self.root}/Xonotic/data/ 2>/dev/null").stdout)
        return {"game": "xonotic", "build": "0.8.6" + (f" (data {m.group(1)})" if m else ""), "mod_loader": "userdir data/ (pk3 / cfg)", "image": self.seat.sandbox}
