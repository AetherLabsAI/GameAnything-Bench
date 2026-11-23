"""Flare 1.15 (empyrean campaign) adapter. Install: the upstream AppImage flare-linux64-v1.15 extracted (no root) to
<seat home>/oss-flare/squashfs-root (the verified reference install; `install()` reproduces it). No EULA /
ToS dialog (GPL / CC-BY-SA), no network, no telemetry. A fresh HOME per instance with a seeded settings.txt that answers the
two first-run questions (language, mouse movement), sound off, 1280x720 window; the game runs on a private Xvfb :180+seat
(probes :160+seat), software rendering. A fresh start shows the title menu (Play Game -> class -> Create), as for a player.

Deliverable mod/ (devkit/README.md): one or more Flare mods (dirs with settings.txt + data), or a single mod at mod/ itself;
installed into <HOME>/.local/share/flare/mods and enabled in mods.txt after empyrean_campaign (what the in-game Mods menu
writes), in name order unless mod/mods.txt lists the order.
"""
import json, re, shlex, time
from pathlib import Path

from ... import config
from .. import base as gbase
from ..base import GameAdapter
from . import flmon as FM
from . import probes as P

APPIMAGE = "https://github.com/flareteam/flare-game/releases/download/v1.15/flare-linux64-v1.15.AppImage"
RESERVED = set(P.BASE_MODS) | {"default", "flareprobe", "centered_statbars", "gcw0_defaults"}
NAME_RE = re.compile(r"^[A-Za-z0-9_.-]+$")
SESSION_SH = r'''#!/bin/bash
# fl-session.sh INSTDIR DISPLAYNUM start|stop   (Xvfb + Flare in the background, pids in INSTDIR/session)
I=$1; DN=$2; ACT=$3; S=$I/session; mkdir -p $S
stop() { p=$(cat $S/fl.pid 2>/dev/null); [ -n "$p" ] && kill -- -$p 2>/dev/null; x=$(cat $S/xvfb.pid 2>/dev/null); [ -n "$x" ] && kill $x 2>/dev/null; sleep 1
         [ -n "$p" ] && kill -9 -- -$p 2>/dev/null; [ -n "$x" ] && kill -9 $x 2>/dev/null; rm -f $S/fl.pid $S/xvfb.pid $S/game.pid $S/state.sock; }
if [ "$ACT" = stop ]; then stop; exit 0; fi
stop; rm -f /tmp/.X$DN-lock
Xvfb :$DN -screen 0 1280x720x24 -nolisten tcp > $S/xvfb.log 2>&1 < /dev/null & echo $! > $S/xvfb.pid
for i in $(seq 1 40); do [ -e /tmp/.X11-unix/X$DN ] && break; sleep 0.25; done
export HOME=$I/HOME XDG_CONFIG_HOME=$I/HOME/.config XDG_DATA_HOME=$I/HOME/.local/share DISPLAY=:$DN SDL_AUDIODRIVER=dummy
cd $I
# flmon.py = the state reader: it starts Flare as its child (only an ancestor may read the game's memory) and serves state.sock
setsid nohup python3 -u __FL_ROOT__/bridge/flmon.py run --sock $S/state.sock --pidfile $S/game.pid --stock __FL_ROOT__/squashfs-root/usr/share/flare/mods \
  --user $I/HOME/.local/share/flare/mods --modstxt $I/HOME/.config/flare/mods.txt -- __FL_BIN__ --no-audio > $S/stdout.log 2>&1 < /dev/null & echo $! > $S/fl.pid
echo started $(cat $S/fl.pid)
'''


class Adapter(GameAdapter):
    name = "flare"
    title = "Flare (empyrean campaign)"
    screen = (1280, 720)
    extra_help = ("  console 'EVENT COMPONENTS'     run an event in the running dev game through Flare's developer console (F5 `exec`),\n"
                  "                               e.g. console 'intermap=maps/perdition_harbor.txt,10,15' (testing only; players cannot)\n")

    def __init__(self, seat, root=None):
        super().__init__(seat)
        self.root = root or f"{seat.home}/oss-flare"
        self.bin = f"{self.root}/squashfs-root/AppRun"
        n = int(seat.seat) if str(seat.seat).isdigit() else 1
        self.dn, self.probe_dn = 180 + n, 160 + n          # session display, probe runs (private Xvfb each)
        self._mods = None

    def requirements(self):
        return {"game": "flare", "gpu": False, "mem_gb": 0.5, "steam": False, "hosts": ["pool", "docker", "local"],
                "image": "gameanything/flare:1.15", "eula": None}

    def idir(self, inst): return f"{self.root}/gab/{inst}"
    def udir(self, inst): return f"{self.idir(inst)}/HOME/.local/share/flare"
    def display(self, inst): return f":{self.dn}"
    def paths(self, inst): return {"data_dir": f"{self.root}/squashfs-root/usr/share/flare/mods", "instance_dir": self.idir(inst),
                                   "user_mods": f"{self.udir(inst)}/mods", "mods_txt": f"{self.idir(inst)}/HOME/.config/flare/mods.txt",
                                   "saves": f"{self.udir(inst)}/saves/empyrean", "game_log": f"{self.idir(inst)}/session/stdout.log"}

    # ---- install / instance
    def install(self):
        """extract the upstream AppImage into root (once per sandbox; no root rights needed)"""
        ok = lambda: self.seat.exec(f"test -x {self.bin} && test -f {self.root}/squashfs-root/usr/share/flare/mods/empyrean_campaign/settings.txt && echo ok").stdout.strip() == "ok"
        if not ok():
            self.seat.exec(f"mkdir -p {self.root}/dl && cd {self.root} && curl -sSL -o dl/flare.AppImage {APPIMAGE} && chmod +x dl/flare.AppImage && "
                           f"dl/flare.AppImage --appimage-extract > /dev/null && rm -f dl/flare.AppImage", timeout=1800, check=True)
        self._bridge(force=True)
        return ok()

    def _session_sh(self): return SESSION_SH.replace("__FL_BIN__", self.bin).replace("__FL_ROOT__", self.root)

    def _bridge(self, force=False):
        """session script + the state reader (flmon.py) in <root>/bridge; rewritten when missing or outdated"""
        import hashlib
        files = {f"{self.root}/bridge/fl-session.sh": self._session_sh(), f"{self.root}/bridge/flmon.py": Path(FM.__file__).read_text()}
        if not force:
            have = self.seat.exec("md5sum " + " ".join(files) + " 2>/dev/null").stdout
            files = {p: t for p, t in files.items() if hashlib.md5(t.encode()).hexdigest() not in have}
        for p, t in files.items(): self.seat.put(p, t)

    def provision(self, inst):
        if not self.install(): raise RuntimeError("Flare install missing and could not be installed")
        d = self.idir(inst); self.stop(inst)
        self.seat.exec(f"rm -rf {d} && mkdir -p {d}/HOME/.config/flare {self.udir(inst)}/mods {d}/session", check=True)
        st = P.SETTINGS if inst.startswith("dev-") else P.SETTINGS.replace("dev_mode=1", "dev_mode=0")   # console only for the developer
        self.seat.put(f"{d}/HOME/.config/flare/settings.txt", st)
        self.seat.put(f"{d}/HOME/.config/flare/mods.txt", "\n".join(P.BASE_MODS) + "\n")
        self.seat.put(f"{d}/mods.json", "[]"); self._mods = []
        return {"instance": inst, "dir": d, "display": self.display(inst)}

    @staticmethod
    def _layout(mod_dir):
        m = Path(mod_dir)
        if not m.exists(): return []
        if (m / "settings.txt").exists(): return [m]
        mods = {p.name: p for p in m.iterdir() if p.is_dir() and (p / "settings.txt").exists()}
        order = [l.strip() for l in (m / "mods.txt").read_text().splitlines() if l.strip() and not l.startswith("#")] if (m / "mods.txt").exists() else []
        return [mods[n] for n in order if n in mods] + [mods[n] for n in sorted(mods) if n not in order]

    def install_mod(self, inst, mod_dir):
        """copy each mod into <user>/mods and enable it in mods.txt after the campaign -- what a player does by hand"""
        mods = self._layout(mod_dir); u = self.udir(inst)
        self.seat.exec(f"rm -rf {u}/mods && mkdir -p {u}/mods")
        for m in mods: self.seat.copy_in(m, f"{u}/mods/{m.name}")
        self._mods = [m.name for m in mods]
        self.seat.put(f"{self.idir(inst)}/HOME/.config/flare/mods.txt", "\n".join(P.BASE_MODS + self._mods) + "\n")
        self.seat.put(f"{self.idir(inst)}/mods.json", json.dumps(self._mods))
        return {"mods": self._mods}

    def attach(self, inst, mod_dir): self._mods = None                 # read back from mods.json when needed

    def _installed(self, inst):
        if self._mods is None:
            r = self.seat.exec(f"cat {self.idir(inst)}/mods.json 2>/dev/null").stdout.strip()
            self._mods = json.loads(r) if r else []
        return self._mods

    def build(self, inst, mod_dir=None):
        """structure check of the deliverable (the load test is load_check). -> (ok, log)"""
        mods = self._layout(mod_dir) if mod_dir else []
        L = []
        if mod_dir is not None and not mods: L.append("mod/ holds no Flare mod (a dir with settings.txt, or settings.txt at mod/ itself)")
        for m in mods:
            if not NAME_RE.match(m.name): L.append(f"{m.name}: mod dir name must match [A-Za-z0-9_.-]+")
            if m.name in RESERVED: L.append(f"{m.name}: name taken by a stock / harness mod")
            s = P.parse_save((m / "settings.txt").read_text(errors="replace"))
            if s.get("game", "flare-game") != "flare-game": L.append(f"{m.name}/settings.txt: game={s['game']} (must be flare-game)")
            if not any(p.is_file() for p in m.rglob("*") if p.name != "settings.txt"): L.append(f"{m.name}: no data files")
        return not L, "\n".join(L) or f"ok: mods {[m.name for m in mods]}"

    def load_check(self, inst, out_dir=None):
        """the gate's load test: two short runs with the mod stack: the title menu comes up (10 s, no crash), and a crafted
        level-1 save loads into Arrival (map loaded, alive after 6 s). New ERROR lines are reported, not failed (FL-STABLE)."""
        u = self.udir(inst)
        pr = P.FLProbes(self.seat, self.root, mods=[f"{u}/mods/{m}" for m in self._installed(inst)], label=f"gate-{inst}",
                        display=self.probe_dn, log=lambda s: None, shots_dir=config.path("runs_dir") / "_tmp_flare")
        try:
            pr.setup()
            a = pr.game("title", [["sleep", 10]], timeout=40)
            b = pr.game("load", [["wait_log", "Loading map", 40], ["sleep", 6]], save=P.make_save(), timeout=70)
        finally: self.seat.exec(f"rm -rf {pr.base}")
        ok_a = bool(a.get("alive_at_end")) and not a.get("crash")
        ok_b = bool(b.get("alive_at_end")) and not b.get("crash") and bool(b.get("maps"))
        E = gbase.error_lines("\n".join(P._err_key(e) for r in (a, b) for e in r.get("errors", [])), r"^ERROR")   # in log order, deduplicated
        why = None if ok_a and ok_b else (f"the title menu run failed ({'crashed' if a.get('crash') else 'not running after 10 s'}, rc {a.get('rc')})"
                                          if not ok_a else f"loading a level-1 save into Arrival failed (maps loaded: {b.get('maps') or 'none'}, "
                                          f"{'crashed' if b.get('crash') else 'running' if b.get('alive_at_end') else 'not running'} after 6 s, rc {b.get('rc')})")
        log = json.dumps({"title": {"ok": ok_a, "rc": a.get("rc"), "notes": a.get("notes")},
                          "load_save": {"ok": ok_b, "maps": b.get("maps"), "rc": b.get("rc"), "notes": b.get("notes")}})
        return ok_a and ok_b, gbase.gate_log(E, why, log)

    def start(self, inst):
        """fresh title menu with the installed mods; ok when the window is up and the game is still running after a few s"""
        d = self.idir(inst)
        self.seat.exec(f"rm -rf {self.udir(inst)}/saves")                 # every start is a fresh game state
        self._bridge()
        self.seat.exec(f"bash {self.root}/bridge/fl-session.sh {d} {self.dn} start")
        t0 = time.time()
        while time.time() - t0 < 60:
            time.sleep(2)
            if not self._alive(inst): return False, "flare exited\n" + self.log_tail(inst)
            if self._x(inst, "xdotool search --name Flare").stdout.strip() and "cutscene" in self.seat.exec(f"cat {d}/session/stdout.log").stdout.lower():
                time.sleep(3); return True, "title menu"
        return (True, "window up") if self._x(inst, "xdotool search --name Flare").stdout.strip() else (False, "no window after 60 s\n" + self.log_tail(inst))

    def _alive(self, inst):
        return self.seat.exec(f"p=$(cat {self.idir(inst)}/session/fl.pid 2>/dev/null); [ -n \"$p\" ] && kill -0 $p && echo up").stdout.strip() == "up"

    def stop(self, inst):
        self.seat.exec(f"test -f {self.root}/bridge/fl-session.sh && bash {self.root}/bridge/fl-session.sh {self.idir(inst)} {self.dn} stop")

    def log_tail(self, inst, n=40): return self.seat.exec(f"tail -{n} {self.idir(inst)}/session/stdout.log").stdout

    def state(self, inst):
        """STATE_SPEC snapshot, live from the running game's memory (flmon.py; see its docstring for the channel):
        {"running", "engine", "mod", "screen_text"}.
        engine (fixed keys, null when unknown / not in game):
          source       "memory" (live) | "save" (fallback: newest save slot, only when the live read fails)
          screen       starting | title | new game | load game | config | cutscene | in game
          map, map_title   current map file (maps/arrival.txt) and its title;  pos {x, y} hero position in map tiles (0.1)
          hp, hp_max, mp, mp_max, level, xp, name, class, alive
          gold, inventory {item name: count} (carried), equipped [item names]
          statuses     campaign statuses that are set (sorted, first 40)
          menus_open   [inventory, powers, character, log, book, vendor, talker, pause menu, stash, game_over, devconsole]
          talking_to   NPC name of the open dialog;  enemies  hostile living creatures on the map;  npcs  NPC names on the map
        mod (only what is not vanilla; stock = fantasycore + empyrean_campaign data in the install):
          statuses     set campaign statuses no stock data file mentions
          entities     {name: {type|npc, pos, hp, alive, ...}} nearest 20 creatures / NPCs from mod files, or placed by a mod on
                       its map (a map a mod adds or APPENDs to, compared with the stock map file)
          events       {"event@x,y": {activate, size, do: [component=value]}} events of a mod-touched map the stock map lacks
          map          {file, title, from: [mods], new} when the current map comes from / is changed by a mod
        screen_text: "hud: ..." notifications on screen, "dialog: who: text" (open NPC dialog), "region: ..." map-title banner,
          "book: ...", "quest: ..." quest log entries (the log's quest tab), "log: ..." last messages while the log is open."""
        d = self.idir(inst)
        r = self.seat.exec(f"python3 {self.root}/bridge/flmon.py query {d}/session/state.sock 2>&1", timeout=15)
        try: q = json.loads((r.stdout.strip().splitlines() or ["{}"])[-1])
        except ValueError: q = {"error": f"flmon: {r.stdout.strip()[-200:]}"}
        save = None
        if q.get("running") is None: q["running"] = self._alive(inst)
        if q["running"] and not isinstance(q.get("engine"), dict):         # no live read: newest save slot, marked
            save = self.seat.exec(f"f=$(ls -t {self.udir(inst)}/saves/empyrean/*/avatar.txt 2>/dev/null | head -1); [ -n \"$f\" ] && cat $f").stdout
        return compose_state(q, save)

    def log_errors(self, inst):
        log = self.seat.exec(f"cat {self.idir(inst)}/session/stdout.log 2>/dev/null").stdout
        cnt = {}
        for l in log.splitlines():
            if l.startswith("ERROR"): k = P._err_key(l); cnt[k] = cnt.get(k, 0) + 1
        return {"errors": cnt}

    # ---- dev tools beyond the common ones
    def dev_command(self, inst, k, v, mod, wd):
        if k != "console": return None
        if not inst.startswith("dev-"): return {"error": "the console is on only in the dev instance"}
        cmd = " ".join(v); cmd = cmd if cmd.startswith("exec ") else "exec " + cmd
        self._x(inst, f"xdotool key F5; sleep 0.5; xdotool type --delay 40 {shlex.quote(cmd)}; sleep 0.2; xdotool key Return; sleep 0.5; xdotool key F5")
        return {"ok": True, "sent": cmd, "log_tail": self.log_tail(inst, 8)}

    # ---- regression
    def run_probes(self, names, mod_dir=None, label="vanilla"):
        """every probe = its own short game run with the mod stack (FLProbes.run_all); screenshots kept on the host next to
        the baseline / evaluation (compare() reads them)"""
        mods = self._layout(mod_dir) if mod_dir else []
        pr = P.FLProbes(self.seat, self.root, mods=mods, label=label, display=self.probe_dn, log=lambda s: None,
                        shots_dir=config.path("games_dir") / "flare" / "probe_shots")
        try: res = pr.run_all([n for n in names if n != "log_errors"])
        finally: self.seat.exec(f"rm -rf {pr.base}/*/HOME {pr.base}/mods {pr.base}/*/*.png")   # keep logs / results (shots are on the host)
        res["_run_dir"] = pr.base
        return res

    def compare(self, spec, base, got): return P.compare_item(spec, base, got)

    def version(self):
        v = self.seat.exec(f"{self.root}/squashfs-root/usr/bin/flare --version 2>&1 | grep -m1 -o 'Flare [0-9.]*'; "
                           f"grep -h ^version {self.root}/squashfs-root/usr/share/flare/mods/{{fantasycore,empyrean_campaign}}/settings.txt").stdout.split("\n")
        return {"game": "flare", "build": (v[0].replace("Flare", "").strip() or "?") + (f" (fantasycore {v[1][8:]}, empyrean {v[2][8:]})" if len(v) > 2 else ""),
                "mod_loader": "mods.txt (built in)", "image": self.seat.sandbox}


def engine_from_save(text):
    """engine fields from a save slot (avatar.txt) -- the fallback when the live read is unavailable"""
    s = P.parse_save(text or "")
    E = dict.fromkeys(FM.ENGINE_KEYS); E["source"] = "save"
    if not s: return E
    m, x, y = P.pos(s); hp, mp = P.hpmp(s); car = P.items_of(s)
    E.update(screen="in game", map=m, pos={"x": float(x), "y": float(y)} if m else None, hp=int(hp) if hp is not None else None,
             mp=int(mp) if mp is not None else None, xp=int(s["xp"]) if (s.get("xp") or "").isdigit() else None, name=s.get("name"),
             gold=car.pop(1, 0), inventory={f"item {i}": q for i, q in sorted(car.items())},
             statuses=sorted(x for x in (s.get("campaign") or "").split(",") if x)[:40], **{"class": (s.get("class") or "").split(",")[0] or None})
    return E


def compose_state(q, save_text=None):
    """flmon answer (+ save text when the live read failed) -> the STATE_SPEC dict with a fixed engine key set"""
    out = {"running": bool(q.get("running"))}
    if isinstance(q.get("engine"), dict):
        out["engine"] = {"source": "memory", **{k: q["engine"].get(k) for k in FM.ENGINE_KEYS}}
    elif save_text:
        out["engine"] = engine_from_save(save_text)
    else:
        out["engine"] = {"source": None, **dict.fromkeys(FM.ENGINE_KEYS)}
        if out["running"]: out["engine"]["screen"] = "starting"
    out["mod"] = q.get("mod") if isinstance(q.get("mod"), dict) else {}
    out["screen_text"] = list(q.get("screen_text") or [])
    if q.get("error") and out["running"]: out["error"] = str(q["error"])[:300]
    return out
