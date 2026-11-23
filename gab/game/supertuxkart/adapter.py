"""SuperTuxKart 1.5 adapter. Install: the upstream Linux x86_64 release tarball unpacked into <seat home>/oss-supertuxkart (the
verified reference reference install; `install()` reproduces it on another seat sandbox). GPL game, no EULA / ToS. The one first-start
dialog asks whether the game may connect to the internet (add-on / news server, hardware reports): the benchmark runtime answers
"No" in setup (seeded config.xml enable_internet = 2, hw reports off; the benchmark's runtime policy) and the default local player is
seeded with use-frequency 1 (no tutorial question) -> a fresh instance starts straight into the main menu, network off.
A fresh instance per start (hard-link copy of the game data + the mod laid over it, own HOME); the game runs windowed 1280x720
at 0,0 (= the seat screen, no window manager) on the seat display through VirtualGL.

Deliverable mod/ (devkit/README.md): data/ (files laid over the game data: karts/<k>/kart.xml, tracks/<t>/..., stk_config.xml,
kart_characteristics.xml, gui/...), addons/{karts,tracks}/<id>/ (add-ons, as the in-game add-on manager installs them), optional
start.yaml {mode: menu | race, track, kart, numkarts, laps, race_mode: normal | time_trial | follow_leader | battle, difficulty,
reverse}. Several mods may sit side by side as mod/<name>/{data,addons}/.

state() (docs/STATE_SPEC.md; one sandbox call):
  engine: screen (loading | menu | race = the bridge reports live | track = a track world is loaded but not reporting | closed),
          start (start.yaml target), track (start / last loaded track id), tracks_loaded, log_age_s,
          race {karts (all karts in the race), minor_mode, mode} and karts [{kart (index), pos [x, y, z], speed m/s, max_speed}]
          for the local player's kart (the track triggers fire for player karts only) -- read live
          inside the running race by the gab track-script bridge (an invisible re-arming point trigger on every track of the
          player instance calls a logging function appended to the track's scripting.as: Kart::getLocation / getVelocity /
          getMaxSpeed, Track::getNumberOfKarts / getMinorRaceMode; probe instances never get it), error_lines, not_found, source.
          STK's script API has no getter for laps, rank, race time or items, and no console: those are not in the state.
  mod:    what the mods loaded into the game, from the instance build (mod_static.json): karts_added / tracks_added (game ids),
          objects_added (scene.xml objects that are not stock: track, tag, id, type, model, xyz; max 20), settings_changed
          (tuning values vs stock: [stock, mod], max 30), files (overlaid / add-on files, max 20) + counts.
  screen_text: the last 10 texts track scripts sent to the game's message display (GUI::displayMessage / ModalMessage /
          OverlayMessage / StaticMessage are routed through logging wrappers by the bridge); STK has no getter for HUD text,
          so lap / rank / timer are not in it.
"""
import base64, io, json, math, os, re, shlex, tarfile, time
import xml.etree.ElementTree as ET
from pathlib import Path

import yaml

from .. import base as gbase
from ..base import GameAdapter
from . import probes as P
from gab.game.base import vglrun

TARBALL = "https://github.com/supertuxkart/stk-code/releases/download/1.5/SuperTuxKart-1.5-linux-x86_64.tar.gz"
GAME_DIR = "SuperTuxKart-1.5-linux-x86_64"
ARENAS = {"alien_signal", "ancient_colosseum_labyrinth", "arena_candela_city", "battleisland", "cave", "hole_drop", "lasdunasarena",
          "oasis", "pumpkin_park", "stadium", "temple"}
START_KEYS = {"mode", "track", "kart", "numkarts", "laps", "race_mode", "difficulty", "reverse"}
SEC_PROBE = 5400                    # a whole probe session (headless + graphics on a loaded pod) must end within 90 min


def parse_state(t, running, start):
    """the state() shell output -> STATE_SPEC dict (also used by the unit tests)"""
    g = lambda k: int((re.findall(rf"^{k} (-?\d+)", t, re.M) or ["0"])[-1])
    age, loads, gp = g("AGE"), g("LOADS"), g("GP")
    head, _, ms = t.partition("MODSTATIC")
    kz = {}
    for m in re.finditer(r"GABST (\d+) (\d+) (-?\d+) (\S+) (\S+) (\S+) (\S+) (\S+) (\S+) (\S+)", head):
        k, n, mode = int(m.group(1)), int(m.group(2)), int(m.group(3)); x = [float(v) for v in m.groups()[3:]]
        kz[k] = {"kart": k, "pos": [round(v, 1) for v in x[:3]], "speed": round(math.hypot(x[3], x[5]), 2), "max_speed": round(x[6], 2), "_n": n, "_mode": mode}
    live = bool(kz) and age <= 3 and running
    screen = "race" if live else "track" if loads and age > 3 and running and not kz else "menu" if gp and not loads and age > 20 else "loading" if running else "closed"
    tr = re.findall(r"You chose to start in track '([^']*)'", head) or re.findall(r"cached-textures/hd/tracks/([a-z0-9_]+)", head)
    any_k = next(iter(kz.values()), {})
    eng = {"screen": screen, "start": start, "track": tr[-1] if tr else None, "tracks_loaded": loads, "log_age_s": age,
           "race": {"karts": any_k.get("_n"), "minor_mode": any_k.get("_mode"), "mode": MINOR.get(any_k.get("_mode"), any_k.get("_mode"))} if kz else None,
           "karts": [{k: v for k, v in kz[i].items() if not k.startswith("_")} for i in sorted(kz)][:10] if live else [],
           "error_lines": g("ERR"), "not_found": re.findall(r"(?:Kart|Track) .[^ ]+. not found", head)[:3], "source": "game log + track-script bridge (live)"}
    try: mod = json.loads(ms.strip()) if ms.strip() else {}
    except ValueError: mod = {"error": "mod_static.json unreadable"}
    txt = [l.split("GABMSG ", 1)[1][:200] for l in head.splitlines() if "GABMSG " in l][-10:]
    return {"running": running, "engine": eng, "mod": mod, "screen_text": txt}


MINOR = {1100: "normal race", 1101: "time trial", 1002: "follow the leader", 2000: "3 strikes battle", 3000: "easter egg hunt"}
# RaceManager minor modes as Track::getMinorRaceMode() returns them (1100 / 2000 seen in game; LINEAR_RACE(id, laps) =
# 1000 + id + 100 * laps, BATTLE_ARENA(id) = 2000 + id, EASTER_EGG(id) = 3000 + id); others are given as the number


class Adapter(GameAdapter):
    name = "supertuxkart"
    title = "SuperTuxKart"
    screen = (1280, 720)            # the game window at 0,0 fills the 1280x720 seat screen (no window manager)
    extra_help = ("  profile TRACK [KART]         a headless 8 s AI race with deliverable/mod on TRACK (your add-on or stock) and\n"
                  "                               KART (default tux): karts driving, crashes and error lines come back\n")

    def __init__(self, seat, root=None):
        super().__init__(seat)
        self.root = root or f"{seat.home}/oss-supertuxkart"
        self.gl = vglrun()

    def requirements(self):
        return {"game": "supertuxkart", "gpu": True, "mem_gb": 1.5, "steam": False, "hosts": ["pool", "docker", "local"],
                "image": "gameanything/supertuxkart:1.5", "eula": None}

    def iname(self, inst): return f"gab-{inst}"                         # instance <root>/inst/gab-<inst>
    def idir(self, inst): return f"{self.root}/inst/{self.iname(inst)}"
    def mdir(self, inst): return f"{self.root}/gab/{inst}"              # staged mods (mods/) + start.json
    def inst_dir(self, inst): return self.mdir(inst)
    def paths(self, inst): return {"game_data": f"{self.root}/{GAME_DIR}/data", "instance_data": f"{self.idir(inst)}/data",
                                   "addons": f"{self.idir(inst)}/home/data/supertuxkart/addons", "config": f"{self.idir(inst)}/home/cfg/supertuxkart/config-0.10",
                                   "game_log": f"{self.idir(inst)}/game.log"}
    def _py(self, args, timeout=900):
        return self.seat.exec(f"STK_ROOT={self.root} STK_GL={shlex.quote(self.gl)} python3 {self.root}/bridge/stkprobe.py " + args, timeout=timeout)

    # ---- install / instance
    def install(self):
        ok = lambda: self.seat.exec(f"test -x {self.root}/{GAME_DIR}/bin/supertuxkart && test -f {self.root}/{GAME_DIR}/data/stk_config.xml && echo ok").stdout.strip() == "ok"
        if not ok():
            self.seat.exec(f"mkdir -p {self.root} && cd {self.root} && curl -sSL -o stk.tar.gz {TARBALL} && tar xzf stk.tar.gz && rm -f stk.tar.gz", timeout=3600)
            if not ok(): raise RuntimeError("SuperTuxKart install failed (download / unpack of the 1.5 release)")
        self._put_big(f"{self.root}/bridge/stkprobe.py", Path(P.__file__).read_text())      # always refresh the bridge
        r = self._py("seed", timeout=300)                  # seeded first-start config (internet "No", default player), once
        if r.returncode: raise RuntimeError(f"SuperTuxKart config seed failed: {r.stdout[-300:]} {r.stderr[-500:]}")
        return True

    def _put_big(self, path, data):
        """seat.put in parts: one remote docker exec request is limited to 64 KB (Seat.put sends 100 KB of base64 per call)"""
        b = base64.b64encode(data.encode() if isinstance(data, str) else data).decode()
        self.seat.exec(f"mkdir -p \"$(dirname '{path}')\" && : > '{path}.b64'", check=True)
        for i in range(0, len(b), 60_000): self.seat.exec(f"echo {b[i:i + 60_000]} >> '{path}.b64'", check=True)
        self.seat.exec(f"base64 -d '{path}.b64' > '{path}' && rm -f '{path}.b64'", check=True)

    def _copy_in(self, src, dst):
        """Seat.copy_in in 60 KB parts (same reason): host dir / file -> dst in the sandbox"""
        buf = io.BytesIO()
        with tarfile.open(fileobj=buf, mode="w:gz") as t: t.add(str(src), arcname=Path(dst).name)
        tmp = f"{self.root}/gab/.in_{os.getpid()}_{int(time.time() * 1000)}.tgz"
        self._put_big(tmp, buf.getvalue())
        self.seat.exec(f"mkdir -p \"$(dirname '{dst}')\" && rm -rf '{dst}' && tar -xzf '{tmp}' -C \"$(dirname '{dst}')\" && rm -f '{tmp}'", check=True)

    def provision(self, inst):
        self.install(); self.stop(inst)
        self.seat.exec(f"rm -rf {self.idir(inst)} {self.mdir(inst)} && mkdir -p {self.mdir(inst)}/mods", check=True)
        self.seat.put(f"{self.mdir(inst)}/start.json", "{}")
        return {"instance": inst, "dir": self.idir(inst), "display": self.seat.display}

    @staticmethod
    def _layout(mod_dir):
        """-> ([mod dirs, each with data/ and/or addons/], start dict): mod/ itself, or mod/<name>/ side by side"""
        m = Path(mod_dir) if mod_dir else None
        if not m or not m.exists(): return [], {}
        isMod = lambda d: (d / "data").is_dir() or (d / "addons").is_dir()
        mods = [m] if isMod(m) else sorted(d for d in m.iterdir() if d.is_dir() and isMod(d))
        st = yaml.safe_load((m / "start.yaml").read_text()) if (m / "start.yaml").exists() else {}
        return mods, st or {}

    def install_mod(self, inst, mod_dir):
        """each mod's data/ + addons/ into the instance; start() lays data/ over a fresh copy of the game data and puts the
        add-ons where the in-game add-on manager installs them (<home>/data/supertuxkart/addons/{karts,tracks})"""
        mods, st = self._layout(mod_dir); d = self.mdir(inst)
        self.seat.exec(f"rm -rf {d}/mods && mkdir -p {d}/mods")
        for i, m in enumerate(mods):
            for sub in ("data", "addons"):
                if (m / sub).is_dir(): self._copy_in(m / sub, f"{d}/mods/{i:02d}_{m.name}/{sub}")
        self.seat.put(f"{d}/start.json", json.dumps(st))
        return {"mods": [m.name for m in mods], "start": st}

    def _start(self, inst):
        r = self.seat.exec(f"cat {self.mdir(inst)}/start.json 2>/dev/null").stdout.strip()
        return json.loads(r) if r else {}

    def _modargs(self, d):
        ms = self.seat.exec(f"ls -d {d}/mods/*/ 2>/dev/null").stdout.split()
        return " ".join(f"--mod {m.rstrip('/')}" for m in ms)

    def build(self, inst, mod_dir=None):
        """structure check of the deliverable -> (ok, log); the load test is load_check()"""
        if mod_dir is None: return True, "no mod dir given"
        mods, st = self._layout(mod_dir); L = []; karts, tracks = set(), set()
        if not mods: L.append("mod/ holds no data/ or addons/ folder (nor mod/<name>/data|addons)")
        for m in mods:
            for x in m.iterdir():
                if x.name not in ("data", "addons", "start.yaml") and m != Path(mod_dir): L.append(f"{m.name}/{x.name}: only data/ and addons/ are installed")
            a = m / "addons"
            if a.is_dir():
                for x in a.iterdir():
                    if x.name not in ("karts", "tracks"): L.append(f"{m.name}/addons/{x.name}: add-ons are karts/<id>/ or tracks/<id>/")
                for k in (a / "karts").glob("*"):
                    karts.add(k.name)
                    if not (k / "kart.xml").exists(): L.append(f"{m.name}/addons/karts/{k.name}: no kart.xml")
                for t in (a / "tracks").glob("*"):
                    tracks.add(t.name)
                    if not (t / "track.xml").exists(): L.append(f"{m.name}/addons/tracks/{t.name}: no track.xml")
                    elif not (t / "scene.xml").exists(): L.append(f"{m.name}/addons/tracks/{t.name}: no scene.xml")
            dd = m / "data"
            if dd.is_dir():
                karts |= {k.name for k in (dd / "karts").glob("*") if (k / "kart.xml").exists()}
                tracks |= {t.name for t in (dd / "tracks").glob("*") if (t / "track.xml").exists()}
            for f in list(m.rglob("*.xml")) + list(m.rglob("*.stkgui")):
                try: ET.parse(f)
                except ET.ParseError as e: L.append(f"{f.relative_to(mod_dir)}: XML error {e}")
            for f in m.rglob("*"):
                if " " in f.name: L.append(f"{f.relative_to(mod_dir)}: no spaces in file names")
        for k in set(st) - START_KEYS: L.append(f"start.yaml: unknown key {k} (have: {', '.join(sorted(START_KEYS))})")
        if st.get("mode", "menu") not in ("menu", "race"): L.append(f"start.yaml: mode must be menu or race, not {st.get('mode')}")
        rm = st.get("race_mode", "normal")
        if rm not in P.MODES: L.append(f"start.yaml: race_mode must be one of {', '.join(P.MODES)} (soccer / CTF cannot start from the command line in 1.5)")
        stock_t = set(P.RACE_TRACKS) | ARENAS
        if st.get("track") and st["track"] not in stock_t | tracks: L.append(f"start.yaml: track {st['track']} is neither stock nor in the mod")
        if rm == "battle" and st.get("track") and st["track"] in P.RACE_TRACKS: L.append(f"start.yaml: battle needs an arena, {st['track']} is a race track")
        if st.get("kart") and st["kart"] not in set(P.STOCK_KARTS) | karts: L.append(f"start.yaml: kart {st['kart']} is neither stock nor in the mod")
        for k, lo, hi in (("numkarts", 1, 10), ("laps", 1, 20), ("difficulty", 0, 3)):
            if k in st and (not isinstance(st[k], int) or not lo <= st[k] <= hi): L.append(f"start.yaml: {k} must be an integer {lo}-{hi}")
        return not L, "\n".join(L) or f"ok: mods {[m.name for m in mods]}, karts {sorted(karts)}, tracks {sorted(tracks)}, start {st or 'main menu'}"

    def _gate(self, inst, st, tag="gate"):
        out = f"{self.mdir(inst)}/{tag}.json"
        self._py(f"gate {self.iname(inst)}-{tag} {self._modargs(self.mdir(inst))} --start {shlex.quote(json.dumps(st))} --out {out}", timeout=1500)
        r = self.seat.exec(f"cat {out} 2>/dev/null; rm -rf {self.idir(inst)}-{tag}").stdout
        return json.loads(r) if r.strip() else {"ok": False, "error": "gate run wrote no result"}

    def load_check(self, inst, out_dir=None):
        """the gate: headless 8 s AI races on every added race track / with every added kart (or the start race), then the real
        game reaches the start target (main menu, or the race / battle) on the seat display"""
        g = self._gate(inst, self._start(inst)); runs = g.get("runs") or []
        E = gbase.error_lines("\n".join(l for r in runs for l in r.pop("error_lines", None) or []), r".")
        if not g.get("ok"):
            why = g.get("error") or "; ".join(f"{r['track']} + {r['kart']}: " + ", ".join(x for x in (
                "crashed" if r.get("crashed") else "", f"not found {r['not_found']}" if r.get("not_found") else "",
                f"{r.get('karts_driving')}/2 karts driving after 8 s" if r.get("karts_driving") != 2 else "") if x) for r in runs
                if r.get("crashed") or r.get("not_found") or r.get("karts_driving") != 2)
            return False, gbase.gate_log(E, f"headless load test failed: {why or 'no run'}"[:600], json.dumps(g)[:2500])
        ok, log = self.start(inst)
        E += [x for x in gbase.error_lines(self.log_errors_text(inst), r".") if x not in E]; self.stop(inst)
        why = None if ok else "the game did not reach the start target: " + (log.splitlines() or ["?"])[0][:300]
        return ok, gbase.gate_log(E[:gbase.ERR_N], why, json.dumps(g)[:1500] + "\ngame: " + log[-1500:])

    def start(self, inst):
        """fresh instance with the mod -> the game on the seat display; ok when the start target is reached (main menu: start-up
        loading done, the log quiet and the menu drawn; race: the race world is up). A game that dies during start-up is started
        once more (vanilla did so now and then on a test sandbox while the pod's GPU was busy, 2026-10-31)."""
        ok, log = self._start_once(inst)
        if not ok and log.startswith("the game exited"): ok, log = self._start_once(inst); log = "(second start) " + log
        return ok, log

    def _start_once(self, inst):
        st = self._start(inst); d = self.mdir(inst); self.stop(inst)
        r = self._py(f"instance {self.iname(inst)} {self._modargs(d)}", timeout=600)
        if r.returncode: return False, "instance build failed: " + r.stdout[-500:] + r.stderr[-800:]
        # setsid -f: the game must not hold the exec session's pipes
        self.seat.exec(f"setsid -f env STK_ROOT={self.root} STK_GL={shlex.quote(self.gl)} python3 {self.root}/bridge/stkprobe.py launch "
                       f"{self.iname(inst)} --display {self.seat.display} --start {shlex.quote(json.dumps(st))} > /dev/null 2>&1 < /dev/null")
        t0 = time.time(); moved = False; race = st.get("mode") == "race"; broken = None
        while time.time() - t0 < 900:                     # start-up loads every kart: 20 s on a quiet pod, minutes on a loaded one
            time.sleep(4)
            if not self._alive(inst): return False, "the game exited\n" + self.log_tail(inst)
            # the track's scripts do not compile: STK builds them together with gab's tracker (gabst_tick), so the race screen is
            # never reported -- fail now instead of waiting the 900 s out (such gates hung 15 min each)
            if race and broken is None and self.seat.exec(f"grep -ac 'Scripting: Build() failed' {self.idir(inst)}/game.log 2>/dev/null").stdout.strip() not in ("", "0"):
                broken = time.time()
            if broken and time.time() - broken > 30:
                return False, "the track's scripts do not compile (Scripting: Build() failed)\n" + self.log_tail(inst)
            if not moved:
                moved = bool(self._x(inst, "for w in $(xdotool search --name '^SuperTuxKart' 2>/dev/null); do xdotool windowmove $w 0 0; done; "
                                           "xdotool search --name '^SuperTuxKart'").stdout.strip())
                continue
            s = self.state(inst)
            e = s["engine"]
            if race and e.get("not_found"): return False, f"start target not found: {e['not_found']}"
            if race and e.get("screen") in ("race", "track"): time.sleep(5); return True, f"in race: {json.dumps(e)[:400]}"
            if not race and e.get("screen") == "menu" and self._menu_on_screen(inst): return True, "main menu"
        return False, "start target not reached in 900 s\n" + self.log_tail(inst)

    def _menu_on_screen(self, inst):
        """the main menu is drawn (its bottom bar or its logo; on a busy GPU the loading screen stays up after the log went quiet)"""
        f = f"{self.idir(inst)}/menu_check.png"; w, h = self.screen
        self._x(inst, f"ffmpeg -loglevel error -y -f x11grab -video_size {w}x{h} -i $DISPLAY -frames:v 1 {f}", timeout=60)
        return self._py(f"ismenu --loose {f}", timeout=60).stdout.strip().endswith("menu")

    def _pid(self, inst): return self.seat.exec(f"cat {self.idir(inst)}/game.pid 2>/dev/null").stdout.strip()

    def _alive(self, inst):
        """running and not a zombie (the sandbox's PID 1 does not reap exited games)"""
        p = self._pid(inst)
        return bool(p) and self.seat.exec(f"ps -o stat= -p {p} | grep -qv Z && echo up").stdout.strip() == "up"

    def stop(self, inst):
        """kill the game's own process group (setsid: pgid = the recorded pid)"""
        self.seat.exec(f"p=$(cat {self.idir(inst)}/game.pid 2>/dev/null); [ -n \"$p\" ] && {{ kill -- -$p 2>/dev/null; sleep 1; kill -9 -- -$p 2>/dev/null; }}; "
                       f"rm -f {self.idir(inst)}/game.pid; true")

    def log_tail(self, inst, n=40): return P.ANSI.sub("", self.seat.exec(f"tail -{n} {self.idir(inst)}/game.log 2>/dev/null").stdout)

    def state(self, inst):
        """STATE_SPEC state in ONE sandbox call: engine = log-derived screen + the bridge's live kart values; mod = the instance's
        mod_static.json (what the mods loaded into the game); screen_text = the bridge's message lines"""
        d = self.idir(inst)
        t = self.seat.exec(f"f={d}/game.log; echo AGE $(( $(date +%s) - $(stat -c %Y $f 2>/dev/null || date +%s) )); "
                           f"echo LOADS $(grep -ac 'Track: Overall scene complexity' $f 2>/dev/null); echo GP $(grep -ac 'Loading Grand Prix' $f 2>/dev/null); "
                           f"grep -a -m1 -o \"You chose to start in track '[^']*'\" $f 2>/dev/null; grep -a -oE 'cached-textures/hd/tracks/[a-z0-9_]+' $f 2>/dev/null | tail -1; "
                           f"grep -a -E '(Kart|Track) .[^ ]+. not found' $f 2>/dev/null | tail -3; echo ERR $(grep -acE '\\[(error|fatal) *\\]|Segmentation fault' $f 2>/dev/null); "
                           f"tail -c 60000 $f 2>/dev/null | grep -a 'GABST' | tail -20; grep -a 'GABMSG' $f 2>/dev/null | tail -10; "
                           f"p=$(cat {d}/game.pid 2>/dev/null); [ -n \"$p\" ] && ps -o stat= -p $p | grep -qv Z && echo ALIVE; "
                           f"echo START $(cat {self.mdir(inst)}/start.json 2>/dev/null); echo MODSTATIC; cat {d}/mod_static.json 2>/dev/null", timeout=30).stdout
        t = P.ANSI.sub("", t); m = re.search(r"^START (.*)$", t, re.M)
        try: start = json.loads(m.group(1)) if m and m.group(1).strip() else {}
        except ValueError: start = {}
        return parse_state(t, bool(re.search(r"^ALIVE$", t, re.M)), start or {"mode": "menu"})

    def log_errors(self, inst):
        lines = self.log_errors_text(inst).splitlines()
        return {"errors": len(lines), "lines": lines[:20]}

    def log_errors_text(self, inst):
        t = P.ANSI.sub("", self.seat.exec(f"cat {self.idir(inst)}/game.log 2>/dev/null").stdout)
        return "\n".join(l for l in t.splitlines() if P.ERR.search(l) and not P.ERR_IGNORE.search(l))

    def dev_command(self, inst, k, v, mod, wd):
        if k != "profile": return None
        if not v: return {"error": "profile TRACK [KART]"}
        self.install_mod(inst, mod)
        return self._gate(inst, {"mode": "race", "track": v[0], "kart": v[1] if len(v) > 1 else "tux"}, tag="profile")

    # ---- regression
    def run_probes(self, names, mod_dir=None, label="vanilla"):
        """one fresh probe instance (+ mod) through all asked probes (STKProbes session, detached; graphics probes on the seat
        display), as probes_supertuxkart run"""
        d = f"{self.root}/gab/probe-{label}"; n = f"gab-p-{label}"
        self.seat.exec(f"rm -rf {d} {self.root}/inst/{n} && mkdir -p {d}/mods")
        for i, m in enumerate(self._layout(mod_dir)[0] if mod_dir else []):
            for sub in ("data", "addons"):
                if (m / sub).is_dir(): self._copy_in(m / sub, f"{d}/mods/{i:02d}_{m.name}/{sub}")
        only = ",".join(x for x in names if x != "log_errors") or "settings"
        cmd = (f"exec env STK_ROOT={self.root} STK_GL={shlex.quote(self.gl)} python3 {self.root}/bridge/stkprobe.py session --name {n} "
               f"{self._modargs(d)} --only {only} --display {self.seat.display} --out {d}/session.json")
        self.seat.exec(f"cd {d} && setsid -f bash -c {shlex.quote('echo $$ > session.pid; ' + cmd)} > {d}/session.log 2>&1 < /dev/null")
        t0 = time.time(); res = None
        while time.time() - t0 < SEC_PROBE:
            time.sleep(20)
            r = self.seat.exec(f"cat {d}/session.json 2>/dev/null").stdout
            if r.strip(): res = json.loads(r); break
            if not self.seat.exec(f"p=$(cat {d}/session.pid 2>/dev/null); [ -n \"$p\" ] && ps -o stat= -p $p | grep -qv Z && echo up").stdout.strip():
                time.sleep(3); r = self.seat.exec(f"cat {d}/session.json 2>/dev/null").stdout
                if r.strip(): res = json.loads(r)
                break
        if res is None:
            tail = self.seat.exec(f"tail -5 {d}/session.log").stdout
            self.seat.exec(f"p=$(cat {d}/session.pid 2>/dev/null); [ -n \"$p\" ] && kill -- -$p 2>/dev/null; true")   # its own process group
            res = {"error": f"probe session ended without a result: {tail[-300:]}", **{x: {"error": "no session"} for x in names if x != "log_errors"}}
        self.seat.exec(f"rm -rf {self.root}/inst/{n} {d}")  # keep the sandbox small; the result is on the host
        return res

    def compare(self, spec, base, got): return P.item_result(spec, base, got)

    def version(self):
        ok = self.seat.exec(f"test -x {self.root}/{GAME_DIR}/bin/supertuxkart && echo ok").stdout.strip() == "ok"
        # the sandbox is part of the build: SK-PERF / SK-FRAMETIME are machine-bound (headless sim 12.9k frames/s on a test sandbox vs
        # 3.4k on a loaded sandbox), so the cached vanilla baseline must come from the same sandbox as the modded run
        return {"game": "supertuxkart", "build": f"1.5 on {self.seat.sandbox}" if ok else "?", "mod_loader": "data overlay + add-ons dir (built in)",
                "image": self.seat.sandbox}
