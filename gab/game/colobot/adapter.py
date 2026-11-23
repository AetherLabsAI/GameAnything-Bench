"""Colobot: Gold Edition 0.2.1-alpha adapter. Install: the Ubuntu noble distro packages (colobot 0.2.1-1build3 + data + the five
libs the image lacks + fonts-dejavu-extra) unpacked with dpkg-deb -x under <seat home>/oss-colobot/root (the verified reference install
on a test sandbox; upstream publishes no Linux binaries; `install()` reproduces it on another seat sandbox, no root rights needed).
GPL game: no EULA / ToS, no network use, no telemetry. A fresh save dir per instance with a seeded colobot.ini (player "Player",
English, sound off, no movies / tooltips / autosave, 800x600 windowed, shadows off); the player-select screen the game opens
on is answered in start() (OK for the preset player), so a fresh start shows the main menu. Rendering: Mesa llvmpipe on the seat's
display (NOT vglrun: on the shared H100 Colobot's physics broke at a few fps); the window is moved to 0,0 (no window manager).

Deliverable mod/ (devkit/README.md): a Colobot data mod = files laid out like the game's data dir (levels/, ai/, help/, models/,
textures/, sounds/, music/, icons/), or several such dirs one level down; + optional start.yaml {scene: custom101 | missions101 |
..., or menu}. install_mod copies each into <savedir>/mods/<name> and enables it in colobot.ini: what the game's Mods screen does.

state() (docs/STATE_SPEC.md), all live from the running game. Channel: gabcb.so (bridge/gabcb.cpp), LD_PRELOADed into the player
session only (never into the probes): it interposes libCBot entry points the game already calls and every 0.25 s, on the game's
main thread, writes what the engine holds -- every object through the game's own CBOT `object` view (the update function the game
registered, as a CBOT program reading radar(...) gets it), the CBOT programs that are running with the variables of their stack
(like the game's program debugger) and the texts of CBOT message(). Colobot has no other channel (no console output, no IPC;
saves need a key press and draw a "saved" message); nothing is written into the game. The game log gives level / mission state.
  engine:  screen (menu | level | won | lost | exited), level (level file), level_from_mod, scene (start.yaml), time_s (level time
           = the astronaut's lifetime), me {pos [x, y, z] (CBOT axes: z = height), orientation, shield, energy, altitude,
           carrying}, object_counts {type: n}, mission_time, snapshot_age_s, error_lines
  mod:     objects (nearest 20 to the astronaut: id, type, pos, energy, shield, cell, load, team; only in a level that comes
           from the mod), programs (running CBOT programs: owner object id, function, variables), messages_total
  screen_text: the last 6 CBOT message() texts with their age in s (the game shows one ~10 s), + "mission won / lost" lines
"""
import json, re, shlex, time
from pathlib import Path

import yaml

from .. import base as gbase
from ..base import GameAdapter
from . import probes as P

DEBS = ["pool/universe/c/colobot/colobot_0.2.1-1build3_amd64.deb", "pool/universe/c/colobot/colobot-common_0.2.1-1build3_all.deb",
        "pool/universe/c/colobot/colobot-common-sounds_0.2.1-1build3_all.deb", "pool/universe/c/colobot/colobot-common-textures_0.2.1-1build3_all.deb",
        "pool/main/b/boost1.83/libboost-filesystem1.83.0_1.83.0-2.1ubuntu3_amd64.deb", "pool/universe/g/glew/libglew2.2_2.2.0-4build1_amd64.deb",
        "pool/universe/libp/libphysfs/libphysfs1_3.0.2-6build2_amd64.deb", "pool/universe/libs/libsdl2-image/libsdl2-image-2.0-0_2.8.2+dfsg-1build2_amd64.deb",
        "pool/universe/libs/libsdl2-ttf/libsdl2-ttf-2.0-0_2.22.0+dfsg-1_amd64.deb", "pool/main/f/fonts-dejavu/fonts-dejavu-extra_2.37-8_all.deb"]
MIRROR = "http://archive.ubuntu.com/ubuntu/"
DATA_DIRS = {"levels", "ai", "help", "models", "textures", "sounds", "music", "icons", "fonts", "shaders"}
CATS = ("missions", "freemissions", "exercises", "challenges", "free", "battles", "plus", "custom")
SCENE_RE = re.compile(rf"^({'|'.join(CATS)})(\d)(\d\d)$")
SESSION_SH = r'''#!/bin/bash
# cb-session.sh INSTDIR DISPLAY start|stop [colobot args...]   (the player session: Colobot + the gabcb.so state reader)
I=$1; DP=$2; ACT=$3; shift 3; S=$I/session; D=__ROOT__; mkdir -p $S
stop() { p=$(cat $S/cb.pid 2>/dev/null); [ -n "$p" ] && kill -TERM -- -$p 2>/dev/null
         for i in 1 2 3 4 5 6; do [ -n "$p" ] && kill -0 $p 2>/dev/null || break; sleep 0.5; done
         [ -n "$p" ] && kill -KILL -- -$p 2>/dev/null; rm -f $S/cb.pid; }
if [ "$ACT" = stop ]; then stop; exit 0; fi
stop; rm -f $S/state.json $S/game.log $S/win.txt
export LD_LIBRARY_PATH=$D/root/usr/lib/x86_64-linux-gnu:$D/root/usr/lib/colobot DISPLAY=$DP SDL_AUDIODRIVER=dummy HOME=$I/HOME
mkdir -p $HOME; cd $I
A=$(printf '%q ' "$@")
setsid -f bash -c "echo \$\$ > $S/cb.pid; exec env LD_PRELOAD=$D/bridge/gabcb.so GAB_CB_STATE=$S/state.json $D/root/usr/games/colobot -datadir $D/root/usr/share/games/colobot -langdir $D/root/usr/share/locale -savedir $I/save -resolution 800x600 $A > $S/game.log 2>&1 < /dev/null"
for i in $(seq 1 20); do [ -s $S/cb.pid ] && break; sleep 0.1; done
echo started $(cat $S/cb.pid)
'''


class Adapter(GameAdapter):
    name = "colobot"
    title = "Colobot: Gold Edition"
    screen = (800, 600)             # the game window, moved to 0,0 of the 1280x720 seat screen (no window manager)
    extra_help = ("  probe NAME[,NAME]            run verified regression probes on a fresh copy with deliverable/mod (a few minutes),\n"
                  "                               e.g. probe stock_levels, probe motion, probe cbot_lang\n"
                  "  levels                       the levels of deliverable/mod with their -runscene names (start.yaml `scene:`)\n")

    def __init__(self, seat, root=None):
        super().__init__(seat)
        self.root = root or f"{seat.home}/oss-colobot"

    def requirements(self):
        return {"game": "colobot", "gpu": False, "mem_gb": 1.0, "steam": False, "hosts": ["pool", "docker", "local"],
                "image": "gameanything/colobot:0.2.1", "eula": None, "note": "software GL (llvmpipe) on an X display"}

    def idir(self, inst): return f"{self.root}/gab/{inst}"
    def paths(self, inst): return {"data_dir": f"{self.root}/root/usr/share/games/colobot", "stock_programs": f"{self.root}/root/usr/share/games/colobot/ai",
                                   "stock_levels": f"{self.root}/root/usr/share/games/colobot/levels", "save_dir": f"{self.idir(inst)}/save",
                                   "installed_mods": f"{self.idir(inst)}/save/mods", "game_log": f"{self.idir(inst)}/session/game.log"}

    # ---- install / instance
    def _ok(self): return self.seat.exec(f"test -x {self.root}/root/usr/games/colobot && test -d {self.root}/root/usr/share/games/colobot/levels && echo ok").stdout.strip() == "ok"

    def install(self):
        """the distro packages unpacked into root/ (as reproduce.sh of the pool verification) + the bridge files"""
        if not self._ok():
            r = self.seat.exec(f"set -e; mkdir -p {self.root}/debs {self.root}/root && cd {self.root}/debs && for u in {' '.join(MIRROR + d for d in DEBS)}; do "
                               f"[ -f $(basename $u) ] || curl -sfLO $u; done && for d in *.deb; do dpkg-deb -x $d ../root; done && "
                               f"F={self.root}/root/usr/share/fonts/truetype/dejavu && mkdir -p $F && for f in /usr/share/fonts/truetype/dejavu/*.ttf; do "
                               f"[ -e $F/$(basename $f) ] || ln -s $f $F/; done", timeout=1800)
            if not self._ok(): raise RuntimeError(f"Colobot install failed: {r.stdout[-400:]} {r.stderr[-400:]}")
        self.seat.exec(f"mkdir -p {self.root}/bridge {self.root}/gab {self.root}/inst")
        self.seat.copy_in(Path(__file__).parent / "bridge" / "gabcb.so", f"{self.root}/bridge/gabcb.so")
        self.seat.put(f"{self.root}/bridge/cb-session.sh", SESSION_SH.replace("__ROOT__", self.root))
        return True

    def provision(self, inst):
        self.install(); self.stop(inst); d = self.idir(inst)
        self.seat.exec(f"rm -rf {d} && mkdir -p {d}/save/mods {d}/save/savegame/Player {d}/session {d}/HOME", check=True)
        self.seat.put(f"{d}/save/colobot.ini", P.INI.format(names="", enabled=""))
        self.seat.put(f"{d}/mod.json", json.dumps({"mods": [], "start": {}}))
        return {"instance": inst, "dir": d, "display": self.display(inst)}

    @staticmethod
    def _layout(mod_dir):
        """-> (mod dirs laid out like the data dir: mod/ itself or its subdirs, start.yaml dict)"""
        m = Path(mod_dir) if mod_dir else None
        if not m or not m.exists(): return [], {}
        st = yaml.safe_load((m / "start.yaml").read_text()) if (m / "start.yaml").exists() else {}
        if any((m / t).is_dir() for t in DATA_DIRS): return [m], st or {}
        return sorted(p for p in m.iterdir() if p.is_dir() and any((p / t).is_dir() for t in DATA_DIRS)), st or {}

    @staticmethod
    def mod_levels(mods):
        """{runscene name: level path} of the levels the mod adds or replaces (custom chapters numbered in name order)"""
        out = {}
        cust = sorted({c.name for m in mods for c in (Path(m) / "levels" / "custom").glob("*") if c.is_dir()})
        for m in mods:
            for f in sorted(Path(m).glob("levels/*/*/level*/scene.txt")):
                cat, chap, lev = f.parts[-4], f.parts[-3], f.parts[-2]
                if cat == "custom": c = cust.index(chap) + 1
                else:
                    mc = re.fullmatch(r"chapter(\d+)", chap); c = int(mc.group(1)) if mc else 0
                ml = re.fullmatch(r"level(\d+)", lev)
                if ml and 1 <= c <= 9 and int(ml.group(1)) < 100: out[f"{cat}{c}{int(ml.group(1)):02d}"] = str(f.relative_to(m))
        return out

    def install_mod(self, inst, mod_dir):
        """each mod dir -> <savedir>/mods/<name>, enabled in colobot.ini (in name order), start.yaml kept"""
        mods, st = self._layout(mod_dir); d = self.idir(inst); names = []
        self.seat.exec(f"rm -rf {d}/save/mods && mkdir -p {d}/save/mods")
        for m in mods:
            n = re.sub(r"[^\w.-]+", "_", m.name if m != Path(mod_dir) else "gabmod"); self.seat.copy_in(m, f"{d}/save/mods/{n}"); names.append(n)
        self.seat.put(f"{d}/save/colobot.ini", P.INI.format(names=",".join(names), enabled=",".join("1" for _ in names)))
        lv = self.mod_levels(mods)
        self.seat.put(f"{d}/mod.json", json.dumps({"mods": names, "start": st, "levels": lv, "host": [str(m) for m in mods]}))
        return {"mods": names, "start": st, "levels": lv}

    def _installed(self, inst):
        r = self.seat.exec(f"cat {self.idir(inst)}/mod.json 2>/dev/null").stdout.strip()
        try: return json.loads(r) if r else {}
        except ValueError: return {}

    def build(self, inst, mod_dir=None):
        """structure check of the deliverable (the load test is load_check). -> (ok, log)"""
        mods, st = self._layout(mod_dir) if mod_dir else ([], {}); L = []
        if mod_dir is not None and not mods: L.append(f"mod/ holds nothing laid out like the data dir ({', '.join(sorted(DATA_DIRS))})")
        for m in mods:
            for e in m.iterdir():
                if e.name not in DATA_DIRS and e.name != "start.yaml" and not e.name.lower().startswith("readme"):
                    L.append(f"{m.name}/{e.name}: not a data-dir entry (only {', '.join(sorted(DATA_DIRS))} are loaded)")
            for f in m.glob("levels/challenges/chapter009/**/*"): L.append(f"{f.relative_to(m)}: challenges chapter 9 is reserved (probe levels); use levels/custom/<name>/"); break
            for f in m.glob("levels/**/scene.txt"):
                t = f.read_text(errors="replace")
                if not re.search(r"^\s*Title", t, re.M): L.append(f"{f.relative_to(m)}: no Title line")
                if "type=Me" not in t.replace(" ", ""): L.append(f"{f.relative_to(m)}: no astronaut (CreateObject ... type=Me)")
            for c in (m / "levels" / "custom").glob("*") if (m / "levels" / "custom").exists() else []:
                if c.is_dir() and not (c / "chaptertitle.txt").exists(): L.append(f"{c.relative_to(m)}: no chaptertitle.txt")
        sc = (st or {}).get("scene", "menu")
        if sc != "menu" and not SCENE_RE.match(str(sc)): L.append(f"start.yaml: scene {sc!r} is not menu or <category><chapter><NN> (e.g. custom101)")
        if set(st or {}) - {"scene"}: L.append(f"start.yaml: unknown keys {sorted(set(st) - {'scene'})}")
        return not L, "\n".join(L) or f"ok: mods {[m.name for m in mods]}, levels {self.mod_levels(mods)}, start {sc}"

    def load_check(self, inst, out_dir=None):
        """the gate's load test: the start scene (else every level the mod adds, max 6, else missions101) runs 14 s with the mod
        installed: the level loads without a level error and the game keeps running (the probes' stock_levels check)"""
        j = self._installed(inst); mods = [Path(m) for m in j.get("host", [])]
        sc = (j.get("start") or {}).get("scene") or "menu"
        scenes = [sc] if sc != "menu" else (sorted(j.get("levels") or {})[:6] or ["missions101"])
        pr = P.CBProbes(self.seat, self.root, f"gate-{inst}", mods, display=self.display(inst))
        try:
            P.build_probe_mod(self.seat, self.root, pr.display, pr.pm, f"{pr.inst}/run_cb.sh"); pr._upload_mods()
            res = pr.stock_levels(tuple(scenes))
            E = gbase.error_lines("\n".join(l for r in pr.runs for l in r["_lines"]), r".")
        finally: self.seat.exec(f"rm -rf {pr.inst}")
        bad = {sc: v for sc, v in res.items() if not (v["loaded"] and v["alive_after_14s"])}
        why = "; ".join(f"{sc}: " + ("the level did not load (level error)" if not v["loaded"] else "the game did not keep running for 14 s")
                        for sc, v in bad.items()) or None
        return not bad, gbase.gate_log(E, why, json.dumps({"scenes": res}, default=str)[-3000:])

    def start(self, inst):
        """the real game on the seat display with the installed mods: the start.yaml scene (-runscene) or the main menu"""
        d = self.idir(inst); sc = (self._installed(inst).get("start") or {}).get("scene") or "menu"
        self.seat.exec(f"rm -rf {d}/save/savegame/Player/* {d}/save/files")          # every start is a fresh game state
        args = "" if sc == "menu" else f"-runscene {shlex.quote(str(sc))}"
        self.seat.exec(f"bash {self.root}/bridge/cb-session.sh {d} {self.display(inst)} start {args}", timeout=60)
        t0 = time.time(); win = ""
        while time.time() - t0 < 120:
            time.sleep(2)
            if not self._alive(inst): return False, "colobot exited\n" + self.log_tail(inst)
            if not win:
                win = self._x(inst, f"xdotool search --pid $(cat {d}/session/cb.pid) 2>/dev/null | tail -1").stdout.strip()
                if win: self._x(inst, f"xdotool windowmove {win} 0 0; xdotool windowfocus {win}; echo {win} > {d}/session/win.txt")
            if not win: continue
            lg = self.seat.exec(f"cat {d}/session/game.log 2>/dev/null").stdout
            st = self._snap(inst)
            if sc == "menu" and st.get("frames", 0) > 30:
                # the game opens on its player-select screen (name "Player" preset in colobot.ini): answered in setup with OK
                # (the game rewrites colobot.ini when the player is confirmed: that tells the click was taken; the screen ignores
                # input for its first seconds)
                ini = lambda: self.seat.exec(f"stat -c %Y.%y {d}/save/colobot.ini").stdout.strip()
                time.sleep(3)
                for k in range(5):
                    m0 = ini(); self.input(inst, {"do": "click", "x": 537, "y": 181}); self._x(inst, "xdotool mousemove 799 599"); time.sleep(2)
                    if ini() != m0: time.sleep(2); return True, f"main menu (player-select screen answered: OK, try {k + 1})"
                return False, "the player-select screen did not take the OK click\n" + self.log_tail(inst)
            if sc != "menu" and re.search(r"Loading level:", lg) and any(o.get("type") == "Me" for o in st.get("objects", [])):
                time.sleep(2); self._x(inst, f"xdotool mousemove 799 599"); return True, f"in level {sc}"
            if "error occurred while trying to load a level" in lg: return False, "level load error\n" + self.log_tail(inst)
        return False, "no level / menu after 120 s\n" + self.log_tail(inst)

    def _alive(self, inst):
        return self.seat.exec(f"p=$(cat {self.idir(inst)}/session/cb.pid 2>/dev/null); [ -n \"$p\" ] && "
                              f"[ \"$(ps -o stat= -p $p 2>/dev/null | cut -c1)\" != Z ] && kill -0 $p 2>/dev/null && echo up").stdout.strip() == "up"

    def stop(self, inst):
        self.seat.exec(f"test -f {self.root}/bridge/cb-session.sh && bash {self.root}/bridge/cb-session.sh {self.idir(inst)} {self.display(inst)} stop")

    def log_tail(self, inst, n=40): return self.seat.exec(f"tail -{n} {self.idir(inst)}/session/game.log 2>/dev/null").stdout

    def input(self, inst, s):
        """like a player's hand: the game window gets the focus first (no window manager); a click is a move, a short pause and
        a 0.15 s press, a key a 0.12 s press (Colobot drops an instant xdotool click / tap on its menus)"""
        q = shlex.quote; f = f"w=$(cat {self.idir(inst)}/session/win.txt 2>/dev/null); [ -n \"$w\" ] && xdotool windowfocus $w; "
        if s["do"] == "click":
            b = gbase._button(s.get("button", 1)); x, y = int(s["x"]), int(s["y"])
            return self._x(inst, f + f"xdotool mousemove {x - 3} {y - 3}; sleep 0.2; xdotool mousemove {x} {y}; sleep 0.2; xdotool mousedown {b}; sleep 0.15; xdotool mouseup {b}")
        if s["do"] == "key":
            return self._x(inst, f + " ; ".join(f"xdotool keydown {q(k)}; sleep 0.12; xdotool keyup {q(k)}; sleep 0.05" for k in str(s["keys"]).split()))
        if s["do"] in ("hold", "type", "drag"): self._x(inst, f)
        return super().input(inst, s)

    # ---- state (STATE_SPEC)
    def _snap(self, inst):
        r = self.seat.exec(f"cd {self.idir(inst)}/session && cat state.json 2>/dev/null; echo; echo AGE $(( $(date +%s) - $(stat -c %Y state.json 2>/dev/null || date +%s) ))")
        txt, _, age = r.stdout.rpartition("AGE ")
        try: st = json.loads(txt.strip()) if txt.strip() else {}
        except ValueError: st = {}
        st["_age"] = int(age.strip() or 0) if age.strip().lstrip("-").isdigit() else None
        return st

    @staticmethod
    def compose(snap, log, alive, installed, mod_files=()):
        """shim snapshot + game log -> the STATE_SPEC dict (pure: unit-tested)"""
        objs = snap.get("objects") or []
        me = next((o for o in objs if o.get("type") == "Me"), None)
        lv = re.findall(r"Loading level: (\S+)", log); level = lv[-1] if lv else None
        won = bool(re.search(r"Team \d+ won|Mission finished|mission ended", log)) and bool(lv)
        lost = bool(re.search(r"Team \d+ lost|All teams died", log))
        mt = re.findall(r"Mission time: (\S+)", log)
        screen = "exited" if not alive else "won" if won else "lost" if lost else "level" if me else "menu"
        from_mod = bool(level) and any(level.endswith(f) or f.endswith(level.replace("levels/", "", 1)) for f in mod_files)
        counts = {}
        for o in objs: counts[o.get("type", "?")] = counts.get(o.get("type", "?"), 0) + 1
        def d2(o):
            if not me or not o.get("pos") or not me.get("pos") or None in o["pos"][:2] or None in me["pos"][:2]: return 1e9
            return ((o["pos"][0] - me["pos"][0]) ** 2 + (o["pos"][1] - me["pos"][1]) ** 2) ** 0.5
        near = sorted((o for o in objs if o.get("type") != "Me"), key=d2)[:20] if from_mod else []
        keep = ("id", "type", "pos", "energy", "shield", "cell", "load", "team")
        t = snap.get("t") or 0
        msgs = (snap.get("messages") or [])[-6:]
        txt = [f"{m.get('text', '')[:200]} ({round(t - (m.get('t') or 0))} s ago)" for m in msgs]
        if won: txt.append("mission won")
        if lost: txt.append("mission lost")
        err = len([l for l in log.splitlines() if P.ERR.search(l) and not P.ERR_IGNORE.search(l)])
        return {"running": alive,
                "engine": {"screen": screen, "level": level, "level_from_mod": from_mod, "scene": (installed.get("start") or {}).get("scene") or "menu",
                           "time_s": round(me["lifetime"], 1) if me and me.get("lifetime") is not None else None,
                           "me": {k: me.get(k) for k in ("pos", "orientation", "shield", "energy", "altitude", "load")} if me else None,
                           "object_counts": dict(sorted(counts.items())), "mission_time": mt[-1] if mt else None,
                           "snapshot_age_s": snap.get("_age"), "error_lines": err},
                "mod": {"objects": [{k: o.get(k) for k in keep} for o in near],
                        "programs": [{"owner": p.get("owner"), "function": p.get("function"), "vars": dict(list((p.get("vars") or {}).items())[:30])}
                                     for p in (snap.get("programs") or [])[:20]],
                        "messages_total": len(snap.get("messages") or [])},
                "screen_text": txt}

    def state(self, inst):
        d = self.idir(inst)
        r = self.seat.exec(f"cd {d} && cat session/state.json 2>/dev/null; echo; echo '@@AGE' $(( $(date +%s) - $(stat -c %Y session/state.json 2>/dev/null || date +%s) )); "
                           f"echo '@@LOG'; grep -aE 'Loading level|Team [0-9]+ (won|lost)|Mission finished|mission ended|All teams died|Mission time|ERROR|rror|CBot|SIGSEGV|xception' session/game.log 2>/dev/null | tail -200; "
                           f"echo '@@MOD'; cat mod.json 2>/dev/null; echo; echo '@@ALIVE'; p=$(cat session/cb.pid 2>/dev/null); [ -n \"$p\" ] && "
                           f"[ \"$(ps -o stat= -p $p 2>/dev/null | cut -c1)\" != Z ] && kill -0 $p 2>/dev/null && echo up", timeout=10).stdout
        js, _, rest = r.partition("@@AGE"); age, _, rest = rest.partition("@@LOG"); log, _, rest = rest.partition("@@MOD"); mj, _, alive = rest.partition("@@ALIVE")
        try: snap = json.loads(js.strip()) if js.strip() else {}
        except ValueError: snap = {}
        snap["_age"] = int(age.strip()) if age.strip().lstrip("-").isdigit() else None
        try: inst_ = json.loads(mj.strip()) if mj.strip() else {}
        except ValueError: inst_ = {}
        return self.compose(snap, log, alive.strip() == "up", inst_, list((inst_.get("levels") or {}).values()))

    def log_errors(self, inst):
        lg = self.seat.exec(f"cat {self.idir(inst)}/session/game.log 2>/dev/null").stdout
        errs = [l for l in lg.splitlines() if P.ERR.search(l) and not P.ERR_IGNORE.search(l)]
        return {"errors": len(errs), "lines": errs[:20], "crashed": bool(re.search(r"Unhandled exception|SIGSEGV", lg))}

    # ---- dev tools
    def dev_command(self, inst, k, v, mod, wd):
        if k == "levels":
            mods, st = self._layout(mod) if mod and Path(mod).exists() else ([], {})
            return {"levels": self.mod_levels(mods), "start": st, "note": "start.yaml `scene: <name>` starts there; custom chapters are numbered in name order"}
        if k == "probe":
            names = [x for x in ",".join(v).split(",") if x]
            if not names or any(n not in P.ALL for n in names): return {"error": f"probes: {', '.join(P.ALL)}"}
            mods, _ = self._layout(mod) if mod and Path(mod).exists() else ([], {})
            r = P.CBProbes(self.seat, self.root, f"devprobe-{inst}", mods, display=self.display(inst)).run(names)
            return {n: r.get(n) for n in names} | {"log_errors": {x: y for x, y in r["log_errors"].items() if x != "_per_run"}}
        return None

    # ---- regression
    def run_probes(self, names, mod_dir=None, label="vanilla"):
        """a fresh probe instance (+ the mod's data dirs), every probe one short game run on the seat display (CBProbes.run)"""
        mods, _ = self._layout(mod_dir) if mod_dir else ([], {})
        nm = re.sub(r"[^\w.-]+", "_", f"{label}-{time.strftime('%m%d-%H%M%S')}")
        return P.CBProbes(self.seat, self.root, nm, mods, display=self.display(None)).run([n for n in names if n != "log_errors"])

    def compare(self, spec, base, got): return P.item_result(spec, base, got)

    def version(self):
        ok = self.seat.exec(f"test -x {self.root}/root/usr/games/colobot && echo ok").stdout.strip() == "ok"
        return {"game": "colobot", "build": "0.2.1-alpha (noble 0.2.1-1build3)" if ok else "?", "mod_loader": "data-dir mods (<savedir>/mods, colobot.ini)",
                "image": self.seat.sandbox, "state_reader": "gabcb.so (LD_PRELOAD, player session only)"}
