"""Xonotic 0.8.6 regression probes + in-sandbox runner. Ported from the original probes_xonotic.py (verified
in game 2026-10-30, Xonotic 0.8.6 = data 20230620; A/A + negative checks in games/xonotic/pool.yaml). Changes: the gab adapter
pushes THIS file into the sandbox (<root>/bridge/xoprobe.py) and runs it there (python3 stdlib only on that side); the GL client
is started through the gab launcher CLIENT_SH (written by the adapter to <root>/bridge/xo-client.sh; it seeds the same
first-start config as the verified client/cl.sh: XonStat ToS accepted for the benchmark runtime, statistics off) and runs with
the network off (cl_curl_enabled 0, no master servers); runner commands for the adapter (session / client / gate / serve /
state / rcon / stop); the host-side yaml compare CLI is gone (the adapter calls item_result()).

Probes: an instance is a dedicated server with its own -userdir (mods = files copied into <userdir>/data: cfg files such as
autoexec.cfg / server.cfg, or pk3 packs), driven over plain-text rcon on 127.0.0.1 (several commands per packet are
NUL-separated). Players are bots scripted with Xonotic's own `sv_cmd bot_cmd` queue (presskey / wait / selectweapon /
impulse); state is read with `prvm_edictget` / `prvm_global`, so every probe reads the server's own numbers, not pixels.
The client items (client.hud / menu / fps) run the GL client as a listen server on the seat's screen (ClientRun).

    python3 xoprobe.py session --name A --port 26421 [--mod DIR ...] [--only p1,p2] --out a.json      (inside the sandbox)
"""
import json, math, os, re, socket, subprocess, sys, time
from pathlib import Path

COLOR = re.compile(r"\^(x[0-9a-fA-F]{3}|[0-9])")
NUM = re.compile(r"-?\d+(?:\.\d+)?(?:e-?\d+)?")
XROOT = Path(os.environ.get("XON_ROOT") or Path(__file__).resolve().parent.parent)   # <root>/bridge/xoprobe.py
GAME = XROOT / "Xonotic"
VERSION = "0.8.6 (data 20230620)"

MAP = "stormkeep"                       # stock DM map used by every in-place probe
SPOT, SPOT_YAW = (-400.0, 528.0, -7.0), 0.0   # stormkeep: open floor, ~580 units free along +x
TARGET_DX = 128.0                       # weapon target distance along +x
PHYS_CVARS = ["sv_gravity", "sv_maxspeed", "sv_maxairspeed", "sv_stopspeed", "sv_accelerate", "sv_airaccelerate", "sv_friction",
              "edgefriction", "sv_stepheight", "sv_jumpvelocity", "sv_jumpvelocity_crouch", "sv_airaccel_qw", "sv_airaccel_qw_stretchfactor",
              "sv_airaccel_sideways_friction", "sv_airstopaccelerate", "sv_airstrafeaccelerate", "sv_maxairstrafespeed", "sv_airstrafeaccel_qw",
              "sv_aircontrol", "sv_aircontrol_power", "sv_aircontrol_penalty", "sv_airspeedlimit_nonqw", "sv_warsowbunny_turnaccel",
              "sv_warsowbunny_accel", "sv_warsowbunny_topspeed", "sv_warsowbunny_backtosideratio", "sv_friction_on_land", "sv_friction_slick",
              "sv_doublejump", "sv_jumpspeedcap_min", "sv_jumpspeedcap_max", "g_teleport_maxspeed", "sv_gameplayfix_stepdown_maxspeed",
              "g_physics_clientselect", "g_mod_physics", "sys_ticrate", "slowmo",
              "g_balance_falldamage_deadminspeed", "g_balance_falldamage_minspeed", "g_balance_falldamage_factor", "g_balance_falldamage_maxdamage",
              "g_balance_falldamage_onlyvertical", "g_balance_selfdamagepercent"]
START_CVARS = ["g_balance_health_start", "g_balance_armor_start", "g_balance_health_regen", "g_balance_health_rot", "g_balance_health_limit",
               "g_balance_armor_regen", "g_balance_armor_rot", "g_balance_armor_limit", "g_spawnshieldtime", "g_mod_balance"]
# weapon id (selectweapon / dumpweapons #) -> name; the stock arsenal a player can pick up on stock maps
WEAPONS = {1: "blaster", 2: "shotgun", 3: "machinegun", 4: "mortar", 6: "electro", 7: "crylink", 8: "vortex", 9: "hagar",
           10: "devastator", 19: "shockwave", 20: "arc"}
AMMO = ("ammo_shells", "ammo_nails", "ammo_rockets", "ammo_cells")
ITEM_CLASSES = ["item_health_small", "item_health_medium", "item_health_big", "item_health_mega", "item_armor_small", "item_armor_medium",
                "item_armor_big", "item_armor_mega", "item_shells", "item_bullets", "item_rockets", "item_cells", "item_plasma", "item_fuel",
                "item_strength", "item_shield", "item_invincible", "item_speed", "item_invisibility", "item_jetpack"] + \
               [f"weapon_{w}" for w in ("shotgun", "machinegun", "mortar", "grenadelauncher", "electro", "crylink", "vortex", "nex", "hagar",
                                        "devastator", "rocketlauncher", "vaporizer", "minstanex", "hook", "arc", "shockwave", "minelayer",
                                        "hlac", "rifle", "seeker", "fireball", "porto")]
MODES = [("dm", "stormkeep", []), ("ctf", "catharsis", ["item_flag_team", "ctf_captureshield"]),
         ("race", "leave_em_behind", ["trigger_race_checkpoint"])]
MAPS = ["afterslime", "solarium", "xoylent"]
SCORE_FIELDS = {"33": "score", "34": "kills", "35": "deaths", "37": "suicides"}   # scorekeeper _scores[] index in 0.8.6 DM (damage 38/39 are noisy)
ERR = re.compile(r"Host_Error|Quake Error|^ERROR|PRVM_|QC crash|SV_Error|segmentation|\berror\b", re.I)
ERR_IGNORE = re.compile(r"keygen server|The command '\w+' returned an error status|couldn't exec|rcon command from", re.I)


# ------------------------------------------------------------------ bridge ------------------------------------------------------------------
class Rcon:
    """Plain-text DarkPlaces rcon (instance started with rcon_secure 0 on 127.0.0.1). Commands in one packet are NUL-separated;
    an echo marker ends each reply. The server reads rcon once per frame (~33 ms), so batch where possible.
    Never prvm_edictget an edict number >= num_edicts: that is a Host_Error and kills the server."""
    def __init__(self, port, password, host="127.0.0.1"):
        self.addr, self.pw = (host, int(port)), password
        self.s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM); self.n = 0

    def __call__(self, cmd, timeout=3.0):
        return self.many([cmd], timeout)[0]

    def many(self, cmds, timeout=4.0):
        self.n += 1; tag = f"__XOP{os.getpid()}_{self.n}_"
        body = "\0".join(f"{c}\0echo {tag}{i}__" for i, c in enumerate(cmds))
        self.s.sendto(b"\xff\xff\xff\xffrcon " + self.pw.encode() + b" " + body.encode("latin1"), self.addr)
        buf, t0 = "", time.time(); self.s.settimeout(0.2); last = f"{tag}{len(cmds) - 1}__"
        while time.time() - t0 < timeout:
            try: d, _ = self.s.recvfrom(65536)
            except socket.timeout: continue
            if d.startswith(b"\xff\xff\xff\xffn"): buf += d[5:].decode("latin1")
            if last in buf: break
        buf = COLOR.sub("", buf)
        out = []
        for i in range(len(cmds)):
            m = f"{tag}{i}__"; a, _, buf = buf.partition(m); out.append(a)
        return out


END = "__XOPEND__"                       # last line of every LogRcon block in the client log
PAD = "__XOPPAD__" + "_" * 4200


class LogRcon(Rcon):
    """rcon to the GL client's listen server: its replies come back empty (the output goes to the client console), so the
    answer is read from the console log (stdout of the client) between the echo markers"""
    def __init__(self, port, password, logfile):
        super().__init__(port, password); self.logfile = Path(logfile)

    def many(self, cmds, timeout=4.0):
        self.n += 1; tag = f"__XOP{os.getpid()}_{self.n}_"; off = self.logfile.stat().st_size if self.logfile.exists() else 0
        # the client's stdout (console.log) is block-buffered: a 4 KB pad line pushes the answer out at once (else small answers
        # sat in the buffer for seconds, 2026-10-31); END = the block's end (screen_lines() drops the whole block), padded too
        body = "\0".join(f"{c}\0echo {tag}{i}__" for i, c in enumerate(cmds)) + f"\0echo {PAD}\0echo {END}\0echo {PAD}"
        self.s.sendto(b"\xff\xff\xff\xffrcon " + self.pw.encode() + b" " + body.encode("latin1"), self.addr)
        t0, last, buf = time.time(), f"{tag}{len(cmds) - 1}__", ""
        while time.time() - t0 < timeout:
            time.sleep(0.03)
            with open(self.logfile, "rb") as f: f.seek(off); buf = f.read().decode("latin1")
            if "\n" + last in buf: break                            # the marker as output (the echoed command line holds it too)
        buf = COLOR.sub("", buf).split("rcon command from", 1)[-1]
        buf = buf.split("\n", 1)[1] if "\n" in buf else ""        # drop the "server received rcon command" echo line
        out = []
        for i in range(len(cmds)):
            m = f"{tag}{i}__"; a, _, buf = buf.partition(m); out.append(a)
        return out


def _last(s):
    v = [l for l in s.splitlines() if l.strip() and not l.startswith(("server received", ":anticheat", "Command '"))]
    return v[-1].strip() if v else ""


class Instance:
    def __init__(self, name, port):
        self.name, self.port = name, int(port)
        self.dir = XROOT / "inst" / name

    def start(self, mods=(), mapname=MAP, fresh=True):
        if fresh and self.dir.exists(): subprocess.run(["rm", "-rf", str(self.dir)], check=True)
        (self.dir / "data").mkdir(parents=True, exist_ok=True)
        for m in mods:
            m = Path(m)
            for p in (m.iterdir() if m.is_dir() else [m]): subprocess.run(["cp", "-r", str(p), str(self.dir / "data")], check=True)
        pw = f"xop{os.getpid()}{self.port}{int(time.time()) % 100000}"; (self.dir / "rcon.pw").write_text(pw)
        cmd = (f"cd {GAME} && exec ./xonotic-linux64-dedicated -userdir {self.dir} +set rcon_password {pw} +set rcon_secure 0 "
               f"+set net_address 127.0.0.1 +port {self.port} +set sv_public 0 +set sv_eventlog 1 +set sv_eventlog_console 1 "
               f"+set sv_autopause 0 +set bot_join_empty 1 +set sv_cheats 1 +set g_debug_bot_commands 1 +map {mapname}")
        log = open(self.dir / "console.log", "w")
        p = subprocess.Popen(["bash", "-c", cmd], stdout=log, stderr=subprocess.STDOUT, stdin=subprocess.DEVNULL, start_new_session=True)
        (self.dir / "pid").write_text(str(p.pid)); self.proc = p
        self.rcon = Rcon(self.port, pw)
        t0 = time.time()
        while time.time() - t0 < 90:
            if p.poll() is not None: raise RuntimeError(f"server exited ({p.returncode}); see {self.dir}/console.log")
            if "map:" in self.rcon("status", 1.0): break
        else: raise RuntimeError("server did not answer rcon within 90 s")
        t0 = time.time()      # the match starts g_start_delay (vanilla 15 s) after the map: nobody can fire before that
        while time.time() - t0 < 60 and ":startdelay_ended" not in self.log(): time.sleep(0.5)
        time.sleep(1)
        return self

    def attach(self):
        self.rcon = Rcon(self.port, (self.dir / "rcon.pw").read_text()); return self

    def alive(self):
        try:
            p = getattr(self, "proc", None)
            if p is not None and p.poll() is not None: return False
            return "map:" in self.rcon("status", 2.0)
        except Exception: return False

    def stop(self):
        try: os.killpg(int((self.dir / "pid").read_text()), 15)
        except (ProcessLookupError, FileNotFoundError, ValueError): pass
        p = getattr(self, "proc", None)
        if p is not None:
            try: p.wait(10)
            except Exception: pass

    def log(self):
        try: return (self.dir / "console.log").read_text(errors="replace")
        except FileNotFoundError: return ""


# ------------------------------------------------------------------ probes ------------------------------------------------------------------
class XOProbes:
    def __init__(self, inst):
        self.I, self.r = inst, inst.rcon

    def get(self, e, f): return _last(self.r(f"prvm_edictget server {e} {f}"))

    def getn(self, e, f):
        m = NUM.findall(self.get(e, f)); return float(m[0]) if m else None

    def gets(self, e, fields): return [_last(o) for o in self.r.many([f"prvm_edictget server {e} {f}" for f in fields])]

    def cvars(self, names):
        out = {}
        for n, o in zip(names, self.r.many(names)):
            m = re.search(r'"%s" is "([^"]*)"' % re.escape(n), o); out[n] = m.group(1) if m else None
        return out

    def stime(self):
        m = re.search(r"time:\s*(-?[\d.]+)", self.r("prvm_global server time")); return float(m.group(1)) if m else None

    def until(self, t, step=0.03):
        while True:
            s = self.stime()
            if s is None or s >= t: return s
            time.sleep(step)

    def state(self, e):
        o = self.r.many([f"prvm_edictget server {e} origin", f"prvm_edictget server {e} velocity", "prvm_global server time"])
        try:
            a = [float(x) for x in NUM.findall(_last(o[0]))][:3]; v = [float(x) for x in NUM.findall(_last(o[1]))][:3]
            t = float(re.search(r"time:\s*(-?[\d.]+)", o[2]).group(1))
            return (t, a, v) if len(a) == 3 and len(v) == 3 else None
        except Exception:
            return None

    def maxclients(self):
        m = re.search(r"\((\d+) max\)", self.r("status")); return int(m.group(1)) if m else 16

    def bots(self, n, timeout=8):
        """Fresh bots (each with an empty bot_cmd queue): setbots 0, then n; -> [(edict, name)]."""
        self.r("sv_cmd setbots 0"); t0 = time.time()
        while time.time() - t0 < timeout and self._players(): time.sleep(0.2)
        if n == 0: return []
        self.r(f"sv_cmd setbots {n}"); t0 = time.time()
        while time.time() - t0 < timeout:
            p = self._players()
            if len(p) >= n: return p[:n]
            time.sleep(0.1)
        raise RuntimeError(f"only {len(self._players())} of {n} bots joined")

    def _players(self):
        mc = self.maxclients()
        nm = self.r.many([f"prvm_edictget server {e} netname" for e in range(1, mc + 1)])
        cl = self.r.many([f"prvm_edictget server {e} classname" for e in range(1, mc + 1)])
        return [(e, _last(a)) for e, (a, b) in enumerate(zip(nm, cl), 1) if _last(b) == "player" and _last(a).startswith("[BOT]")]

    def queue(self, name, cmds):
        self.r.many([f'sv_cmd bot_cmd "{name}" {c}' for c in cmds])

    def place(self, e, pos, yaw=None, vel=(0, 0, 0), health=None, armor=None):
        c = [f'prvm_edictset server {e} origin "{pos[0]} {pos[1]} {pos[2]}"', f'prvm_edictset server {e} velocity "{vel[0]} {vel[1]} {vel[2]}"']
        if yaw is not None: c += [f'prvm_edictset server {e} v_angle "0 {yaw} 0"', f'prvm_edictset server {e} angles "0 {yaw} 0"']
        if health is not None: c.append(f"prvm_edictset server {e} health {health}")
        if armor is not None: c.append(f"prvm_edictset server {e} armorvalue {armor}")
        self.r.many(c)

    def frags(self):
        return {m.group(1).strip(): int(m.group(2)) for m in re.finditer(r"^#\d+\s+(\S.*?)\s+(-?\d+)\s+\d+:\d\d:\d\d", self.r("status"), re.M)}

    def findent_ids(self, cls):
        return [int(x) for x in re.findall(r"^(\d+) \(%s\)" % re.escape(cls), self.r(f"sv_cmd findent {cls}"), re.M)]

    def count_ents(self, classes):
        out = {}
        for c, o in zip(classes, self.r.many([f"sv_cmd findent {c}" for c in classes])):
            m = re.search(r"Found (\d+) entit", o); out[c] = int(m.group(1)) if m else 0
        return out

    # --- settings snapshots (catch cfg / pk3 changes directly) ---
    def physics_cvars(self):
        return self.cvars(PHYS_CVARS)

    def weapon_balance(self):
        """Every weapon setting as the game itself dumps it (sv_cmd dumpweapons -> <userdir>/data/data/weapons_dump.cfg)."""
        f = self.I.dir / "data" / "data" / "weapons_dump.cfg"
        if f.exists(): f.unlink()
        self.r("sv_cmd dumpweapons"); t0 = time.time()
        while time.time() - t0 < 5 and not f.exists(): time.sleep(0.2)
        time.sleep(0.3); txt = f.read_text(errors="replace")
        s = {m.group(1): m.group(2).strip('"') for m in re.finditer(r"^set\s+(\S+)\s+(\S+)", txt, re.M)}
        return {"settings": s, "n": len(s), "weapons": re.findall(r"^// \{\{\{ #\d+: (.+)$", txt, re.M)}

    # --- player state ---
    def player_start(self):
        """A freshly joined bot, read in its first frames (before it can pick anything up): health, armor, weapons, ammo."""
        self.bots(0); self.r("sv_cmd setbots 1"); t0 = time.time(); p = []
        while time.time() - t0 < 6 and not p: p = self._players()
        if not p: return {"error": "no bot joined"}
        e = p[0][0]
        h, a, w, *am = self.gets(e, ["health", "armorvalue", "stat_WEAPONS"] + list(AMMO))
        f = lambda x: round(float(NUM.findall(x)[0])) if NUM.findall(x) else None
        out = {"health": f(h), "armor": f(a), "weapons": w, **{k: f(v) for k, v in zip(AMMO, am)}, "_cvars": self.cvars(START_CVARS)}
        self.bots(0)
        return out

    def move(self):
        """One bot on the stormkeep floor: hold forward 1.0 s, release, jump once (bot_cmd queue, server time), then launched
        straight up at 600 u/s (gravity over a long arc), then slammed into the floor at 950 u/s (fall damage). State sampled every frame."""
        [(e, name)] = self.bots(1)
        self.queue(name, ["wait 1.5", "presskey forward", "wait 1.0", "releasekey forward", "wait 1.5", "presskey jump", "wait 0.1",
                          "releasekey jump", "wait 600"])
        T0 = self.stime()
        self.until(T0 + 0.1); self.place(e, SPOT, SPOT_YAW, health=100, armor=0)
        S = []
        while True:
            st = self.state(e)
            if st: S.append(st)
            if st and st[0] > T0 + 6.0: break
        self.place(e, SPOT, SPOT_YAW, health=100, armor=0); time.sleep(0.3)
        self.r(f'prvm_edictset server {e} velocity "0 0 600"'); L = []; tl = self.stime()     # rise 225 < the 250 headroom here
        while True:
            st = self.state(e)
            if st: L.append(st)
            if st and st[0] > tl + 1.8: break
        # fall damage: slammed into the floor from 100 units up at 950 u/s (lands at ~1030, vanilla: above g_balance_falldamage_minspeed 900)
        self.place(e, (SPOT[0], SPOT[1], SPOT[2] + 100), SPOT_YAW, vel=(0, 0, -950), health=100, armor=0); time.sleep(0.6)
        hp_after = self.getn(e, "health")
        self.bots(0)
        run = [s for s in S if T0 + 1.5 <= s[0] <= T0 + 2.6]
        run_speed = max((math.hypot(v[0], v[1]) for _, _, v in run), default=None)
        pre = [s for s in S if T0 + 0.6 <= s[0] <= T0 + 1.45]
        rest = [s for s in S if T0 + 3.6 <= s[0] <= T0 + 4.0]
        travel = round(math.hypot(rest[-1][1][0] - pre[-1][1][0], rest[-1][1][1] - pre[-1][1][1]), 1) if rest and pre else None
        z0 = rest[-1][1][2] if rest else None
        jump = [s for s in S if T0 + 4.0 <= s[0] <= T0 + 6.0]
        air = [s for s in jump if z0 is not None and s[1][2] > z0 + 0.5]
        apex = round(max(s[1][2] for s in jump) - z0, 1) if jump and z0 is not None else None
        airtime = round(air[-1][0] - air[0][0], 3) if air else None
        lz0 = L[0][1][2] if L else None
        lair = [s for s in L if lz0 is not None and s[1][2] > lz0 + 2]
        return {"run_speed": round(run_speed, 1) if run_speed is not None else None, "travel": travel, "jump_height": apex,
                "jump_airtime": airtime, "gravity": _fit_gravity(air), "launch_height": round(max(s[1][2] for s in L) - lz0, 1) if L else None,
                "launch_gravity": _fit_gravity(lair), "fall_damage": round(100 - hp_after, 1) if hp_after is not None else None}

    def weapons(self):
        """Shooter bot (all weapons via cheat impulse 99) fires one primary shot of each stock weapon at a bot 128 units ahead;
        target health/armor reset to 100/0 and shooter ammo to 50 of each type before every shot."""
        (e1, n1), (e2, n2) = self.bots(2)
        q = ["wait 1.5", "impulse 99", "wait 0.5"]
        for w in WEAPONS: q += [f"selectweapon {w}", "wait 1.5", "presskey attack1", "wait 0.05", "releasekey attack1", "wait 1.45"]
        self.queue(n2, ["wait 600"]); self.queue(n1, q + ["wait 600"]); T0 = self.stime()
        tgt = (SPOT[0] + TARGET_DX, SPOT[1], SPOT[2])
        f = lambda x: float(NUM.findall(x)[0]) if NUM.findall(x) else 0.0
        out = {}
        for i, (w, wn) in enumerate(WEAPONS.items()):
            t = T0 + 2.0 + 3.0 * i
            self.until(t + 0.7)
            self.place(e1, SPOT, SPOT_YAW); self.place(e2, tgt, SPOT_YAW + 180, health=100, armor=0)
            self.r.many([f"prvm_edictset server {e1} {a} 50" for a in AMMO])
            self.until(t + 2.9)
            h, a = self.gets(e2, ["health", "armorvalue"]); am = self.gets(e1, list(AMMO))
            out[wn] = {"damage": round(100 - f(h), 1), "ammo_used": {k[5:]: round(50 - f(v), 1) for k, v in zip(AMMO, am) if abs(50 - f(v)) > 0.01}}
        self.bots(0)
        return out

    def score(self):
        """Scoreboard counting: a scripted frag (vortex shot at a frozen bot set to 1 health) and, by a third bot, a suicide
        (`kill`: the bot dies after the game's ~2.5 s countdown); each bot's scoreboard row (scorekeeper _scores: score, kills,
        deaths, suicides, damage ...). The `status` frags column is NOT used: it lags / misses changes (seen in A/A)."""
        (e1, n1), (e2, n2), (e3, n3) = self.bots(3)
        self.queue(n2, ["wait 600"]); self.queue(n3, ["cc kill", "wait 600"])
        self.queue(n1, ["wait 1.5", "impulse 99", "wait 1.5", "selectweapon 8", "wait 1.0", "presskey attack1", "wait 0.05", "releasekey attack1",
                        "wait 600"])           # selectweapon returns only once the switch is done (after impulse 99's auto-switch)
        T0 = self.stime(); f0 = self.frags(); n_log = len(self.I.log())
        self.until(T0 + 2.5); self.place(e1, SPOT, SPOT_YAW); self.place(e2, (SPOT[0] + TARGET_DX, SPOT[1], SPOT[2]), health=1, armor=0)
        fired = False
        while (self.stime() or 0) < T0 + 12 and not fired:
            fired = f"{n1}: releasekey attack1" in COLOR.sub("", self.I.log()[n_log:]); time.sleep(0.2)
        self.until(max(self.stime() + 0.7, T0 + 6.0)); time.sleep(0.5)
        raw = {role: self.scores(e) for role, e in (("shooter", e1), ("victim", e2), ("suicider", e3))}
        sc = {role: {nm: int((r or {}).get(ix, 0)) for ix, nm in SCORE_FIELDS.items()} for role, r in raw.items()}
        f1 = self.frags(); ev = COLOR.sub("", self.I.log()[n_log:])
        self.bots(0)
        return {"fired": fired, **sc, "_scores_raw": raw, "_status_frags": [f1.get(n, 0) - f0.get(n, 0) for n in (n1, n2, n3)],
                "_eventlog": {"frags": len(re.findall(r"^:kill:frag:", ev, re.M)), "suicides": len(re.findall(r"^:kill:suicide:", ev, re.M))}}

    def scores(self, e):
        """The player's scoreboard row: its scorekeeper entity's _scores[] (index -> value; only non-zero entries are printed)."""
        cs = NUM.findall(self.get(e, "_cs"))
        sk = NUM.findall(self.get(int(cs[0]), "scorekeeper")) if cs else []
        if not sk: return None
        return {m.group(1): float(m.group(2)) for m in re.finditer(r"^_scores\[(\d+)\]\s+(-?[\d.e+-]+)", self.r(f"prvm_edict server {int(sk[0])}"), re.M)}

    def items(self):
        """Item entities on the stock map by class; one medium-health pickup (amount) and its respawn time."""
        res = {"counts": {k: v for k, v in self.count_ents(ITEM_CLASSES).items() if v}}
        ids = self.findent_ids("item_health_medium")
        if not ids: return {**res, "pickup_taken": None}
        it = ids[0]; o = [float(x) for x in NUM.findall(self.get(it, "origin"))][:3]
        [(e, n)] = self.bots(1); self.queue(n, ["wait 600"]); time.sleep(0.3)
        solid0 = self.get(it, "solid")
        self.place(e, (o[0], o[1], o[2] + 25), health=50, armor=0); time.sleep(0.4)
        t0 = self.stime(); taken = self.get(it, "solid") != solid0; hp = self.getn(e, "health")
        self.place(e, SPOT); back = None
        while self.stime() - t0 < 40:
            if self.get(it, "solid") == solid0: back = round(self.stime() - t0, 1); break
            time.sleep(0.2)
        self.bots(0)
        return {**res, "pickup_taken": taken, "pickup_gain": round(hp - 50) if hp is not None else None, "respawn_s": back}

    def bots_play(self, seconds=45):
        """Four bots with their own AI: they join, move around the map and frag each other."""
        bots = self.bots(4)
        n0 = len(re.findall(r"^:kill:", self.I.log(), re.M))
        path = {e: 0.0 for e, _ in bots}; last = {}; t0 = time.time()
        while time.time() - t0 < seconds:
            for e, _ in bots:
                p = [float(x) for x in NUM.findall(self.get(e, "origin"))][:3]
                if len(p) == 3 and len(last.get(e, [])) == 3: path[e] += min(math.dist(p, last[e]), 1500)   # teleports / respawns capped
                last[e] = p
            time.sleep(1.0)
        kills = len(re.findall(r"^:kill:", self.I.log(), re.M)) - n0
        self.bots(0)
        return {"joined": len(bots), "all_moved": all(v > 500 for v in path.values()), "kills": kills, "fight": kills > 0,
                "_path": {str(k): round(v) for k, v in path.items()}}

    def modes(self):
        """Each stock mode on a stock map for it: the map loads in that mode, a bot spawns, the mode's limits and entities."""
        out = {}
        for gt, mp, ents in MODES:
            self.r(f"sv_cmd gametype {gt}"); self.r(f"map {mp}"); ok = self._wait_map(mp)
            p = self.bots(1) if ok else []
            lim = self.gets(p[0][0], ["stat_FRAGLIMIT", "stat_TIMELIMIT"]) if p else [None, None]
            out[gt] = {"loaded": ok, "gametype": self._gametype(), "fraglimit": lim[0], "timelimit": lim[1],
                       "entities": self.count_ents(ents) if ents else {}, "bot_spawned": bool(p)}
            self.bots(0)
        self.r("sv_cmd gametype dm"); self.r(f"map {MAP}"); self._wait_map(MAP)
        return out

    def maps_load(self):
        out = {}
        for mp in MAPS + [MAP]:
            self.r(f"map {mp}"); ok = self._wait_map(mp)
            p = self.bots(1) if ok else []
            out[mp] = {"loaded": ok, "bot_spawned": bool(p) and (self.getn(p[0][0], "health") or 0) > 0}
            self.bots(0)
        return out

    def _wait_map(self, mp, timeout=60):
        t0 = time.time(); time.sleep(1.5)
        while time.time() - t0 < timeout:
            try:
                if re.search(r"^map:\s+%s\s*$" % re.escape(mp), self.r("status", 1.5), re.M) and (self.stime() or 0) > 1.0:
                    time.sleep(1.5); return True
            except Exception: pass
            time.sleep(0.5)
        return False

    def _gametype(self):
        ls = re.findall(r"^:gamestart:([a-z]+)_", self.I.log(), re.M); return ls[-1] if ls else None

    def perf(self, seconds=20):
        """Server CPU load (`status` timing line) with 8 bots playing on the stock map (median of samples)."""
        self.bots(8); time.sleep(5); vals = []; t0 = time.time()
        while time.time() - t0 < seconds:
            m = re.search(r"timing:\s*([\d.]+)% CPU", self.r("status"))
            if m: vals.append(float(m.group(1)))
            time.sleep(2)
        self.bots(0)
        return {"cpu_pct": round(sorted(vals)[len(vals) // 2], 2) if vals else None, "_samples": vals}

    ORDER = ["physics_cvars", "weapon_balance", "player_start", "move", "weapons", "score", "items", "bots_play", "perf", "maps_load", "modes"]

    def run(self, names=None):
        out = {"_timing": {}}
        for n in [x for x in self.ORDER if names is None or x in names]:
            t = time.time()
            try: out[n] = getattr(self, n)()
            except Exception as ex: out[n] = {"error": f"{type(ex).__name__}: {str(ex)[:160]}"}
            out["_timing"][n] = round(time.time() - t, 1)
            if not self.I.alive(): out["_crashed_at"] = n; break
        return out


def _fit_gravity(samples):
    """Least-squares z = a + b t + c t^2 over airborne samples (server time) -> g = -2c (units/s^2)."""
    if len(samples) < 6: return None
    t0 = samples[0][0]; ts = [s[0] - t0 for s in samples]; zs = [s[1][2] for s in samples]
    S = lambda k: sum(t ** k for t in ts); Z = lambda k: sum(z * t ** k for t, z in zip(ts, zs))
    A = [[len(ts), S(1), S(2)], [S(1), S(2), S(3)], [S(2), S(3), S(4)]]
    M = [row + [b] for row, b in zip(A, [Z(0), Z(1), Z(2)])]
    for i in range(3):
        p = max(range(i, 3), key=lambda r: abs(M[r][i])); M[i], M[p] = M[p], M[i]
        if abs(M[i][i]) < 1e-12: return None
        for r in range(3):
            if r != i: k = M[r][i] / M[i][i]; M[r] = [a - k * b for a, b in zip(M[r], M[i])]
    return round(-2 * M[2][3] / M[2][2], 1)


def log_errors(text):
    return sum(1 for l in text.splitlines() if ERR.search(l) and not ERR_IGNORE.search(l))


# ------------------------------------------------------------------ compare ------------------------------------------------------------------
MOVE_TOL = {"run_speed": (0.02, 2), "travel": (0.05, 30),   # travel: A/A once 394 vs 418 (30 accepted after review)
            "jump_height": (0.05, 2), "jump_airtime": (0.1, 0.07),
            "gravity": (0.05, 20), "launch_height": (0.05, 10), "launch_gravity": (0.03, 15), "fall_damage": (0.0, 3)}   # (relative, absolute)
WEAPON_TOL = {w: 8 for w in ("shotgun", "crylink", "mortar", "electro", "hagar", "devastator")}   # spread / projectile / splash (A/A: crylink 40.5 vs 44.2); others DMG_TOL
DMG_TOL = 3


def _close(a, b, rel, ab):
    if a is None or b is None: return a == b
    return abs(a - b) <= max(ab, rel * abs(a))


def compare(name, base, got):
    """-> (ok | None, detail). Plain equality except where the game is noisy (tolerances from the A/A runs, 2026-10-30)."""
    norm = lambda x: {k: v for k, v in json.loads(json.dumps(x)).items() if not str(k).startswith("_")} if isinstance(x, dict) else x
    base, got = norm(base), norm(got)
    if not isinstance(got, dict) or not got: return None, "not probed"
    if "error" in got: return None, got["error"]
    if "error" in (base or {}): return None, f"baseline error: {base['error']}"
    if name == "physics_cvars":
        d = {k: f"{base.get(k)} -> {got.get(k)}" for k in base if base.get(k) != got.get(k)}
        return not d, d or "unchanged"
    if name == "weapon_balance":
        b, g = base["settings"], got["settings"]
        d = {k: f"{b.get(k)} -> {g.get(k)}" for k in sorted(set(b) | set(g)) if b.get(k) != g.get(k)}
        if base.get("weapons") != got.get("weapons"): d["weapon_list"] = f"{base.get('weapons')} -> {got.get('weapons')}"
        if not d: return True, f"{len(b)} settings unchanged"
        return False, dict(list(d.items())[:12], **({"more": len(d) - 12} if len(d) > 12 else {}))
    if name == "move":
        d = {k: f"{base.get(k)} -> {got.get(k)}" for k, (rel, ab) in MOVE_TOL.items() if k in base and not _close(base.get(k), got.get(k), rel, ab)}
        return not d, d or "same as vanilla"
    if name == "weapons":
        d = {}
        for w, b in base.items():
            g = got.get(w) or {}
            if not _close(b.get("damage"), g.get("damage"), 0.0, WEAPON_TOL.get(w, DMG_TOL)): d[w + ".damage"] = f"{b.get('damage')} -> {g.get('damage')}"
            if b.get("ammo_used") != g.get("ammo_used"): d[w + ".ammo_used"] = f"{b.get('ammo_used')} -> {g.get('ammo_used')}"
        return not d, d or "same as vanilla"
    if name == "items":
        d = {}
        bc, gc = base.get("counts") or {}, got.get("counts") or {}
        if bc != gc: d["counts"] = {k: f"{bc.get(k, 0)} -> {gc.get(k, 0)}" for k in sorted(set(bc) | set(gc)) if bc.get(k, 0) != gc.get(k, 0)}
        if base.get("pickup_taken") != got.get("pickup_taken"): d["pickup_taken"] = f"{base.get('pickup_taken')} -> {got.get('pickup_taken')}"
        if not _close(base.get("pickup_gain"), got.get("pickup_gain"), 0, 2): d["pickup_gain"] = f"{base.get('pickup_gain')} -> {got.get('pickup_gain')}"
        if not _close(base.get("respawn_s"), got.get("respawn_s"), 0, 1.5): d["respawn_s"] = f"{base.get('respawn_s')} -> {got.get('respawn_s')}"
        return not d, d or "same as vanilla"
    if name == "bots_play":
        d = {k: f"{base.get(k)} -> {got.get(k)}" for k in ("joined", "all_moved", "fight") if base.get(k) != got.get(k)}
        return not d, d or f"kills {got.get('kills')} (vanilla {base.get('kills')})"
    if name == "perf":
        b, g = base.get("cpu_pct"), got.get("cpu_pct")
        if b is None or g is None: return None, "no reading"
        return g <= max(2 * b, b + 3.0), f"{g}% server CPU with 8 bots vs vanilla {b}%"
    d = {k: f"{base.get(k)} -> {got.get(k)}" for k in sorted(set(base) | set(got)) if base.get(k) != got.get(k)}
    return not d, d or "unchanged"


def item_result(spec, base, got):
    """Pool item `probe:` spec (probe, or probe.f1+f2 as in g1_regress.item_result) on two session JSONs -> (ok | None, detail)."""
    if spec == "log_errors":
        if got.get("_crashed_at") or got.get("alive_at_end") is False: return False, f"server died (during {got.get('_crashed_at')})"
        b, g = base.get("log_errors"), got.get("log_errors")
        return (g is not None and b is not None and g <= b), f"{g} error lines vs vanilla {b}"
    if spec.startswith("client."):         # GL client items: a ClientRun JSON, or a session JSON with it under "client"
        f = spec.split(".", 1)[1]
        return compare_client(f, base.get("client", base).get(f), got.get("client", got).get(f))
    p = spec.split(".")[0]
    b, g = base.get(p), got.get(p)
    if "." in spec and isinstance(b, dict) and isinstance(g, dict):
        fields = spec.split(".", 1)[1].split("+")
        b = {k: v for k, v in b.items() if k in fields}; g = {k: v for k, v in g.items() if k in fields or k == "error"}
    return compare(p, b, g)


# ------------------------------------------------------------------ client (GL) ------------------------------------------------------------
# The GL client runs as a listen server on the seat's screen (Xvfb :101-:103, GPU through VirtualGL on the pod's Xorg :99), fully
# scripted by a cfg (defer + the cl_hook_gamestart_all hook), no input devices. It is always started through CLIENT_SH, which
# owns the first-start setup of a fresh userdir (as the verified client/cl.sh, set up by the main session on the
# instruction 2026-10-30: the benchmark runtime accepts the XonStat ToS, statistics stay off, a non-default name skips the
# first-run welcome dialog) + network off (no HTTP, no master servers, local address only) + rcon on 127.0.0.1 for state().
CLIENT_SH_TEXT = r'''#!/bin/bash
# xo-client.sh DISPLAY NAME PORT [CFG [GL] [extra args...]]: the GL client (listen server) on DISPLAY, userdir <root>/inst/NAME
R=$(cd "$(dirname "$0")/.." && pwd); cd $R/Xonotic
D=$1; N=$2; P=$3; CFG=${4:-xop_client.cfg}; GL=${5:-vglrun -d :99}; shift 5 2>/dev/null || shift $#
U=$R/inst/$N; mkdir -p $U/data
if ! grep -q _termsofservice_accepted "$U/data/config.cfg" 2>/dev/null; then
  cat >> "$U/data/config.cfg" <<"SEED"
seta "_termsofservice_accepted" "1"
seta "cl_allow_uid2name" "0"
seta "cl_allow_uidtracking" "0"
seta "_cl_name" "GameAnything"
SEED
fi
[ -f $U/rcon.pw ] || echo "xoc$$$RANDOM" > $U/rcon.pw
echo $$ > $U.pid                       # setsid'd by the caller: pid = process group of the whole client
NET="+set cl_curl_enabled 0 +set sv_public -1 +set sv_master1 \"\" +set sv_master2 \"\" +set sv_master3 \"\" +set sv_master4 \"\" +set sv_masterextra1 \"\" +set sv_masterextra2 \"\" +set g_playerstats_gamereport_uri \"\""
DISPLAY=$D exec $GL ./xonotic-linux64-glx -userdir $U -nosound +vid_fullscreen 0 +vid_width 1280 +vid_height 720 \
  +set net_address 127.0.0.1 +port $P $NET +set rcon_password $(cat $U/rcon.pw) +set rcon_secure 0 "$@" +exec $CFG > $U/console.log 2>&1 < /dev/null
'''
CLIENT_SH = XROOT / "bridge" / "xo-client.sh"
CLIENT_CFG = r"""// GameAnything probe script for the Xonotic GL client (probes_xonotic.py ClientRun)
set scr_screenshot_jpeg 0
set scr_screenshot_png 1
set cl_welcome 0                 // server welcome dialog only on keypress (it pauses the listen server)
set g_playerstats_gamereport_uri ""   // never send game reports to the XonStat service from probe runs
alias xop_place "prvm_edictset server 1 origin \"-400 528 -7\"; prvm_edictset server 1 angles \"0 0 0\"; prvm_edictset server 1 v_angle \"0 0 0\"; prvm_edictset server 1 fixangle 1"
alias xop_state "echo XOP_STATE_BEGIN; prvm_edictget server 1 classname; prvm_edictget server 1 origin; prvm_edictget server 1 health; echo XOP_STATE_END"
alias xop_ingame "defer 3 \"cmd join\"; defer 6 \"cmd join\"; defer 9 \"cmd join\"; defer 10 \"togglemenu 0\"; defer 11 xop_place; defer 15 xop_place; defer 18 \"xop_state; echo XOP_SHOT hud; screenshot\"; defer 19 \"echo XOP_CVARS_BEGIN; cvarlist hud_; cvarlist crosshair; echo XOP_CVARS_END\"; defer 21 \"echo XOP_TIMEREFRESH; timerefresh; quit\""
alias cl_hook_gamestart_all "xop_ingame"
defer 40 "echo XOP_SHOT menu_a; screenshot"
defer 70 "echo XOP_SHOT menu_b; screenshot"
defer 100 "echo XOP_SHOT menu_c; screenshot"
defer 102 "togglemenu 0; map stormkeep"
defer 500 "echo XOP_TIMEOUT; quit"
"""


class ClientRun:
    def __init__(self, name, port, display):
        self.name, self.port, self.display = name, int(port), display
        self.dir = XROOT / "inst" / name

    def prep(self, mods=()):
        """Fresh userdir with the probe script and the mod files (CLIENT_SH adds its own first-start setup when it starts)."""
        if self.dir.exists(): subprocess.run(["rm", "-rf", str(self.dir)], check=True)
        d = self.dir / "data"; d.mkdir(parents=True)
        (d / "xop_client.cfg").write_text(CLIENT_CFG)
        for m in mods:
            m = Path(m)
            for p in (m.iterdir() if m.is_dir() else [m]): subprocess.run(["cp", "-r", str(p), str(d)], check=True)

    def run(self, mods=(), timeout=200):
        """prep + start through CLIENT_SH + result"""
        self.prep(mods)
        p = subprocess.Popen(["bash", str(CLIENT_SH), self.display, self.name, str(self.port), "xop_client.cfg", os.environ.get("XON_GL", "vglrun -d :99")], stdin=subprocess.DEVNULL,
                             stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, start_new_session=True)
        try: p.wait(timeout)
        except subprocess.TimeoutExpired:
            os.killpg(p.pid, 9); p.wait(10)
        return self.result()

    def result(self):
        lg = COLOR.sub("", (self.dir / "console.log").read_text(errors="replace"))
        shots, cur = {}, None
        for l in lg.splitlines():
            m = re.match(r"XOP_SHOT (\w+)", l)
            if m: cur = m.group(1); continue
            m = re.match(r"Wrote (screenshots/\S+\.png)", l)
            if m and cur: shots[cur] = str(self.dir / "data" / m.group(1)); cur = None
        fps = [float(x) for x in re.findall(r"seconds \(([\d.]+) fps\)", lg)]
        cv = lg.split("XOP_CVARS_BEGIN")[-1].split("XOP_CVARS_END")[0] if "XOP_CVARS_END" in lg else ""
        cvars = {m.group(1): m.group(2) for m in re.finditer(r'^(\S+) is "([^"]*)"', cv, re.M)}
        hc = {k: v for k, v in cvars.items() if k.startswith(("hud_panel_", "crosshair"))}
        stt = lg.split("XOP_STATE_BEGIN")[-1].split("XOP_STATE_END")[0].split("\n") if "XOP_STATE_END" in lg else []
        stt = [l.strip() for l in stt if l.strip() and not l.startswith("XOP")]
        org = [float(x) for x in NUM.findall(stt[1])][:3] if len(stt) > 1 else []
        placed = bool(stt) and stt[0] == "player" and len(org) == 3 and abs(org[0] - SPOT[0]) < 40 and abs(org[1] - SPOT[1]) < 40
        if "hud" in shots and not placed: shots.pop("hud"); hud_err = f"player not in game at the probe spot (state {stt}): machine too loaded?"
        else: hud_err = "no in-game screenshot"
        drawn = [shots[k] for k in ("menu_a", "menu_b", "menu_c") if k in shots and _img(shots[k]).mean() >= 0.02]
        if drawn: shots["menu"] = drawn[-1]          # the menu takes 20-90 s to appear on a loaded pod: the last drawn shot
        menu_err = "main menu not drawn (black screenshots at 40 / 70 / 100 s)" if any(k in shots for k in ("menu_a", "menu_b", "menu_c")) else "no main-menu screenshot"
        return {"menu": {"shot": shots["menu"]} if "menu" in shots else {"error": menu_err},
                "hud": {"shot": shots["hud"], "cvars": hc, "n_cvars": len(hc)} if "hud" in shots else {"error": hud_err},
                "fps": {"timerefresh_fps": round(max(fps), 1) if fps else None, "_all": fps},
                "timeout": "XOP_TIMEOUT" in lg, "log_errors": log_errors(lg)}


def _img(path, size=(320, 180)):
    from PIL import Image
    import numpy as np
    return np.asarray(Image.open(path).convert("L").resize(size), dtype=float) / 255.0


def image_diff(a, b, box=None):
    """Mean absolute grey-level difference (0..1) of two screenshots, optionally inside a relative box (x0, y0, x1, y1)."""
    A, B = _img(a), _img(b)
    if box:
        h, w = A.shape; x0, y0, x1, y1 = box
        A, B = A[int(y0 * h):int(y1 * h), int(x0 * w):int(x1 * w)], B[int(y0 * h):int(y1 * h), int(x0 * w):int(x1 * w)]
    return round(float(abs(A - B).mean()), 4)


HUD_BOXES = {"bottom_strip": (0.0, 0.80, 1.0, 1.0), "crosshair": (0.45, 0.43, 0.55, 0.57), "top_strip": (0.0, 0.0, 1.0, 0.08)}
MENU_DIFF_MAX, HUD_DIFF_MAX, FPS_MIN_RATIO = 0.03, 0.05, 0.5     # menu / fps: NOT calibrated (blocked on the shared pod, see the pool)


def compare_client(name, base, got):
    if not isinstance(got, dict) or "error" in got: return None, (got or {}).get("error", "not probed")
    if not isinstance(base, dict) or "error" in base: return None, f"baseline: {(base or {}).get('error')}"
    if name == "menu":
        dd = image_diff(base["shot"], got["shot"]); return dd <= MENU_DIFF_MAX, f"main menu screenshot differs by {dd} (max {MENU_DIFF_MAX})"
    if name == "hud":
        d = {k: f"{base['cvars'].get(k)} -> {got['cvars'].get(k)}" for k in sorted(set(base["cvars"]) | set(got["cvars"]))
             if base["cvars"].get(k) != got["cvars"].get(k)}
        try: boxes = {k: image_diff(base["shot"], got["shot"], b) for k, b in HUD_BOXES.items()}   # reported only (see below)
        except Exception as ex: boxes = {"error": str(ex)[:80]}
        det = {"_regions_info_only": boxes, "n_settings": len(got["cvars"])}
        if d: det.update(cvars=dict(list(d.items())[:10]), n_cvar_diffs=len(d))
        # Pixel regions are NOT part of the verdict: on the shared pod two vanilla runs differed by 0.003-0.006 when quiet but
        # 0.10-0.15 under load (textures still streaming), as much as a hidden health panel (0.06-0.16). Settings are exact.
        return (not d) and len(got["cvars"]) > 100, det
    if name == "fps":
        b, g = base.get("timerefresh_fps"), got.get("timerefresh_fps")
        if not b or not g: return None, "no reading"
        return g >= FPS_MIN_RATIO * b, f"{g} fps vs vanilla {b} (min {FPS_MIN_RATIO}x)"


# ------------------------------------------------------------------ runner (inside the sandbox) ------------------------------------------
def session(name, port, mods, only, out):
    inst = Instance(name, port)
    res = {"version": VERSION, "mods": [str(m) for m in mods], "started": time.strftime("%Y-%m-%d %H:%M:%S")}
    try:
        inst.start(mods)
        res.update(XOProbes(inst).run(only))
        res["alive_at_end"] = inst.alive()
    except Exception as ex:
        res["error"] = f"{type(ex).__name__}: {str(ex)[:200]}"; res["alive_at_end"] = False
    finally:
        lg = inst.log()
        res["log_errors"] = log_errors(lg); res["_error_lines"] = [l for l in lg.splitlines() if ERR.search(l) and not ERR_IGNORE.search(l)][:20]
        inst.stop()
    Path(out).write_text(json.dumps(res, indent=1))
    return res


def gate(name, port, mods, mapname, gametype, out):
    """the eval gate's load test: a dedicated server with the mod on the start map (+ gametype) comes up, the match starts,
    a bot spawns alive, the server survives 10 s with 2 bots; error lines counted"""
    inst = Instance(name, port); res = {"map": mapname, "gametype": gametype}
    try:
        inst.start(mods, mapname)
        P = XOProbes(inst)
        if gametype and gametype != "dm":
            P.r(f"sv_cmd gametype {gametype}"); P.r(f"map {mapname}"); res["map_loaded"] = P._wait_map(mapname)
        b = P.bots(2); res["bot_alive"] = (P.getn(b[0][0], "health") or 0) > 0
        time.sleep(10); res["alive"] = inst.alive(); res["gametype_running"] = P._gametype()
        P.bots(0)
    except Exception as ex: res["error"] = f"{type(ex).__name__}: {str(ex)[:300]}"; res["alive"] = False
    finally:
        lg = inst.log(); res["log_errors"] = log_errors(lg)
        res["_error_lines"] = [l for l in lg.splitlines() if ERR.search(l) and not ERR_IGNORE.search(l)][:300]   # deduplicated on the host
        inst.stop()
    res["ok"] = bool(res.get("alive") and res.get("bot_alive") and res.get("map_loaded", True))
    Path(out).write_text(json.dumps(res, indent=1)); return res


# ------------------------------------------------------------------ state (STATE_SPEC) ---------------------------------------------------
# Live state of the GL client's listen server, all through rcon (LogRcon: the answers are read from the client's console.log).
# Vanilla baselines (made by the harness, once per install): <root>/gab/state_base/cvars.json = cvars + aliases of a vanilla GL
# client in game with the harness start cfg; ents_<map>_<gametype>.json = entity count per classname on a vanilla server.
STATE_BASE = XROOT / "gab" / "state_base"
ANSI = re.compile(r"\x1b\[[0-9;]*m")
# cvars that differ between two vanilla starts (diff of two vanilla GL clients in game, 2026-10-31: _cl_color, cmdline, qport)
# or that the harness sets per start (rcon_password, bot_number from start.yaml): never "mod"
VOLATILE_CVARS = re.compile(r"^(_cl_color|cmdline|qport|rcon_password|bot_number)$")
STATE_WEAPONS = {1: "blaster", 2: "shotgun", 3: "machinegun", 4: "mortar", 5: "minelayer", 6: "electro", 7: "crylink", 8: "vortex",
                 9: "hagar", 10: "devastator", 11: "porto", 12: "vaporizer", 13: "hook", 14: "hlac", 15: "tuba", 16: "rifle",
                 17: "fireball", 18: "seeker", 19: "shockwave", 20: "arc"}
STATE_AMMO = ("ammo_shells", "ammo_nails", "ammo_rockets", "ammo_cells", "ammo_plasma", "ammo_fuel")


def cut(s, n=200):
    s = str(s); return s if len(s) <= n else s[:n - 3] + "..."


def parse_edict(txt):
    """`prvm_edict server N` output -> {field: value text}"""
    out = {}
    for l in txt.splitlines():
        m = re.match(r"^\s*([A-Za-z_][\w\[\]\.]*)\s+(.*\S)\s*$", l)
        if m and "EDICT " not in l: out[m.group(1)] = m.group(2)
    return out


def parse_cvarlist(txt):
    """`cvarlist` output -> {name: value}"""
    return {m.group(1): m.group(2) for m in re.finditer(r'^(\S+) is "(.*?)" \[', txt, re.M)}


def parse_aliases(txt):
    """`alias` (no arguments) output -> {name: commands}"""
    return {m.group(1): m.group(2).strip() for m in re.finditer(r"^(\S+) : (.*)$", txt, re.M)}


def cvar_diff(now, base, cap=30):
    """cvars that are new or differ from the vanilla baseline (volatile ones left out) -> {name: {value, vanilla}}"""
    out = {}
    for k in sorted(now):
        if k in base and base[k] == now[k] or VOLATILE_CVARS.match(k): continue
        out[k] = {"value": cut(now[k]), "vanilla": None if k not in base else cut(base[k])}
        if len(out) >= cap: break
    return out


LOG_NOISE = re.compile(r"^(Sound \S+ not found|curl |curl support|url_fclose|Receiving player stats|execing |couldn't exec|Wrote |"
                       r"server received rcon|Loaded |Authenticated connection|Connection accepted|<-- server|Server: |Client using|"
                       r"Client opened|Loading |NOTE: |GL_|Video Mode|CDAudio|Can't get initial CD|CD Audio|Server using port|"
                       r"Server listening|Generated private ID|Saved unsigned key|unconnected changed name|Xlib|vid\.support|Skeletal|"
                       r"DPSOFTRAST|Current nice|Game is Xonotic|gamename for|Xonotic Linux|\"\S+\" set to|<=+>|Received signal|"
                       r"Client \".*\" dropped|WARNING|Warning|restarting renderer|client: program|Shader |Unknown command|"
                       r"\S+ is \"|mapname: |time: |\s*$|map: |.* \(\d+ max\)$|Host_|CL_|S_|Mod_|R_|Draw_|.*\.(tga|png|jpg|wav|ogg|md3|iqm|dpm|bsp|cfg|dat)\b)")


def screen_lines(log, n=10):
    """the console lines a player saw on screen since the current map started (chat, notifications, centerprints, echo / print
    of cfg / QC): the rcon blocks of the harness (from 'server received rcon command' to the end marker) and engine noise left
    out -> last n lines"""
    t = COLOR.sub("", ANSI.sub("", log)).replace("\r", "")
    i = t.rfind("\n<=====")
    if i >= 0: t = t[i:]
    j, k = t.find(END), t.find("server received rcon command")
    if j >= 0 and (k < 0 or j < k): t = t[j:]                     # the text starts inside an rcon block: skip to its end
    t = re.sub(r"server received rcon command from[^\n]*\n.*?" + END + r"[^\n]*\n", "", t, flags=re.S)
    t = re.sub(r"server received rcon command from.*\Z", "", t, flags=re.S)       # a block whose end is not in the log yet
    keep = []
    for l in t.splitlines():
        l = l.strip().lstrip("\x01\x02").strip()
        if l and not LOG_NOISE.match(l) and "__XOP" not in l and l not in keep[-1:]: keep.append(cut(l))
    return keep[-n:]


def map_classnames(udir, mapname):
    """classnames the map's entity source can place: the stock bsp lump, a .ent override or a map in the mod's files (pk3 /
    pk3dir / loose maps/), read from the instance's data/ and the stock data -> set"""
    import zipfile
    names = set()
    def from_text(t): names.update(re.findall(r'"classname"\s+"([^"]+)"', t))
    def from_bsp(b):
        try:
            o, n = int.from_bytes(b[8:12], "little"), int.from_bytes(b[12:16], "little"); from_text(b[o:o + n].decode("latin1"))
        except Exception: pass
    want = (f"maps/{mapname}.ent", f"maps/{mapname}.bsp")
    for d in [Path(udir) / "data", GAME / "data"]:
        if not d.exists(): continue
        for f in sorted(d.iterdir()):
            try:
                if f.suffix == ".pk3" and f.is_file():
                    with zipfile.ZipFile(f) as z:
                        for w in want:
                            if w in z.namelist(): (from_text if w.endswith(".ent") else from_bsp)(z.read(w).decode("latin1") if w.endswith(".ent") else z.read(w))
                elif f.is_dir():
                    for w in want:
                        q = f / w if f.suffix == ".pk3dir" else (d / w if f.name == "maps" else None)
                        if q is not None and q.exists(): (from_text if w.endswith(".ent") else from_bsp)(q.read_text(errors="replace") if w.endswith(".ent") else q.read_bytes())
            except Exception: continue
    return names


def _findent_counts(r, classes):
    out = {}
    cl = sorted(classes)
    for i in range(0, len(cl), 40):
        for c, o in zip(cl[i:i + 40], r.many([f"sv_cmd findent {c}" for c in cl[i:i + 40]], 4.0)):
            m = re.search(r"Found (\d+) entit", o); out[c] = int(m.group(1)) if m else 0
    return out


def _alive_pid(d):
    """the client launcher records its process group in <inst>.pid; running = that process exists and is not a zombie"""
    try:
        pid = int(Path(str(d) + ".pid").read_text().strip())
        return Path(f"/proc/{pid}/stat").read_text().rsplit(")", 1)[1].split()[0] != "Z"
    except (OSError, ValueError, IndexError): return False


def _log_tail(f, n=3_000_000):
    try:
        with open(f, "rb") as h:
            h.seek(0, 2); size = h.tell(); h.seek(max(0, size - n)); return h.read().decode("latin1")
    except OSError: return ""


CP_MAX = 10                  # CSQC centerprint slots (centerprint_messages[0..9], hud/panel/centerprint.qc)


def _gval(out, name):
    """`prvm_global VM name` answer -> the value text (all lines: a string global may hold newlines)"""
    t = out.split(f"{name}:", 1)
    return t[1].strip("\n").lstrip(" ").rstrip() if len(t) > 1 else None


CVAR_SCAN_S = 20.0           # full cvarlist + alias scan at most every 20 s (≈ 0.7 MB of console output); in between only the
                             # cvars already found are re-read


def client_state(name, port, light=False):
    """STATE_SPEC snapshot of a running GL client (the adapter's state() docstring has the layout); light: engine only, no
    cvars / aliases / entities / screen text (start() polls with it)"""
    d = XROOT / "inst" / name
    if not _alive_pid(d): return {"running": False}
    try: r = LogRcon(port, (d / "rcon.pw").read_text().strip(), d / "console.log")
    except FileNotFoundError: return {"running": True, "error": "no rcon password (the client is starting)"}
    num = lambda v: (lambda m: float(m[0]) if m else None)(NUM.findall(str(v or "")))
    cache_f = d / "gab_state_cache.json"
    try: cache = json.loads(cache_f.read_text())
    except (OSError, ValueError): cache = {}
    full = not light and time.time() - float(cache.get("t") or 0) > CVAR_SCAN_S
    known = [] if light or full else sorted(cache.get("cvars") or {})
    G = ["mapname", "time", "intermission_running", "game_stopped", "warmup_stage", "game_starttime", "maxclients", "MapInfo_LoadedGametype"]
    C = ["scoreboard_active", "intermission"]
    CP = [] if light else (["time", "centerprint_title"] + [f"centerprint_messages{f'[{i}]' if i else ''}" for i in range(CP_MAX)]
                           + [f"centerprint_expire_time{f'[{i}]' if i else ''}" for i in range(CP_MAX)])
    cmds = ([f"prvm_global server {g}" for g in G] + [f"prvm_global client {g}" for g in C] + ["prvm_global menu Menu_Active", "prvm_edict server 1"]
            + [f"prvm_global client {g}" for g in CP] + ["prvm_edictcount server"] + (["cvarlist", "alias"] if full else known))
    o = r.many(cmds, 4.0)
    if not "".join(o).strip(): return {"running": True, "error": "the game does not answer (loading?)"}
    gv = {}
    for g, x in zip(G + C + ["Menu_Active"], o):
        m = re.search(r":\s*(.*?)\s*$", _last(x)); gv[g] = m.group(1) if m else None
    k = len(G) + len(C) + 1
    ent = parse_edict(o[k])
    cpv = {g: _gval(x, g) for g, x in zip(CP, o[k + 1:k + 1 + len(CP)])}
    k += len(CP) + 1
    m = re.search(r"num_edicts:\s*(\d+)", o[k]); n_ed = int(m.group(1)) if m else 0
    mapname = gv.get("mapname") if gv.get("mapname") not in (None, "", "0") else None
    # 2nd batch: the other clients (edicts 2..maxclients always exist: safe to read), the gametype, the current weapon, the
    # scorekeeper of the local player
    mc = 0 if light else min(int(num(gv.get("maxclients")) or 0), 64)
    # (an edict number >= num_edicts is a Host_Error: read the player's weapon entity / scorekeeper only when the map is the one
    # the numbers were seen on before, it is not ending, and they are below num_edicts)
    safe = bool(mapname) and mapname == cache.get("map") and not num(gv.get("game_stopped")) and not num(gv.get("intermission_running"))
    ok_e = lambda v, need_safe=True: (lambda e: e if (safe or not need_safe) and 0 < e < n_ed else 0)(int(num(v) or 0))
    gt_e = ok_e(gv.get("MapInfo_LoadedGametype"), False)     # a registry entity: made at progs start, before the map's
    we, cs = ok_e(ent.get("weaponentities")), ok_e(ent.get("_cs"))
    F = ["classname", "netname", "frags", "deadflag", "health"]
    c2 = [f"prvm_edictget server {e} {f}" for e in range(2, mc + 1) for f in F]
    c2 += [f"prvm_edictget server {gt_e} mdl" if gt_e else "echo", f"prvm_edictget server {we} weaponname" if we else "echo",
           f"prvm_edictget server {cs} scorekeeper" if cs and not light else "echo"]
    o2 = r.many(c2, 4.0)
    others = {}
    for i, e in enumerate(range(2, mc + 1)):
        v = [_last(x) for x in o2[i * len(F):(i + 1) * len(F)]]
        if len(v) == len(F) and v[0] in ("player", "spectator", "observer") and v[1]:
            others[COLOR.sub("", v[1])] = {"class": v[0], "frags": round(num(v[2]) or 0), "alive": v[0] == "player" and not num(v[3]) and (num(v[4]) or 0) > 0}
    gametype = _last(o2[-3]) or None; weapon = _last(o2[-2]) or None
    sk = int(num(_last(o2[-1])) or 0)
    deaths = None
    if sk:
        sc = {m.group(1): float(m.group(2)) for m in re.finditer(r"^_scores\[(\d+)\]\s+(-?[\d.e+-]+)", r(f"prvm_edict server {sk}"), re.M)}
        deaths = round(sc.get("35", 0.0))
    # local player
    org = [round(float(x), 1) for x in NUM.findall(ent.get("origin", ""))][:3]
    held = []
    bits = [int(float(x)) for x in NUM.findall(ent.get("stat_WEAPONS", ""))][:3]
    for wi in range(1, 24 * len(bits) + 1):
        if bits[(wi - 1) // 24] & (1 << ((wi - 1) % 24)): held.append(STATE_WEAPONS.get(wi, f"weapon{wi}"))
    pl = ent.get("classname") == "player"
    alive = pl and not num(ent.get("deadflag")) and (num(ent.get("health")) or 0) > 0
    t = num(gv.get("time")); st0 = num(gv.get("game_starttime"))
    status = None
    if mapname:
        status = ("ended" if num(gv.get("game_stopped")) or num(gv.get("intermission_running")) else "warmup" if num(gv.get("warmup_stage"))
                  else "countdown" if t is not None and st0 and t < st0 else "running")
    screen = ("menu" if num(gv.get("Menu_Active")) or not mapname else
              "scoreboard" if num(gv.get("scoreboard_active")) or num(gv.get("intermission")) else "in_game")
    z = lambda f: round(num(ent.get(f)) or 0) if ent else None             # an edict prints only its non-zero fields; whole numbers
                                                                            # as the HUD shows them (health rot: no 0.01 noise)
    engine = {"map": mapname, "gametype": gametype, "time_s": round(t, 1) if t is not None else None, "match": status, "screen": screen,
              "player": {"name": COLOR.sub("", ent.get("netname", "")) or None, "class": ent.get("classname"), "pos": org or None,
                         "health": z("health"), "armor": z("armorvalue"), "alive": alive, "frags": z("frags"), "deaths": deaths,
                         "weapons": held if ent else None, "weapon": weapon if pl else None,
                         "ammo": {a[5:]: round(num(ent.get(a))) for a in STATE_AMMO if num(ent.get(a))}},
              "others": others}
    def save(**kw):
        cache.update(kw, map=mapname)
        try: cache_f.write_text(json.dumps(cache))
        except OSError: pass
    if light: save(); return {"running": True, "engine": engine}
    # mod: cvars / aliases vs the vanilla baseline (full scan every CVAR_SCAN_S, else the known ones re-read); map entities vs
    # the vanilla map
    mod = {}
    try: base = json.loads((STATE_BASE / "cvars.json").read_text())
    except (OSError, ValueError): base = {}; mod["baseline"] = "missing: cvars"
    bc, ba = base.get("cvars") or {}, base.get("aliases") or {}
    if full:
        cv, al = parse_cvarlist(o[-2]), parse_aliases(o[-1])
        mod["cvars"] = cvar_diff(cv, bc) if bc else {}
        mod["aliases"] = dict([(a, cut(v)) for a, v in sorted(al.items()) if ba and ba.get(a) != v and not a.startswith("_")][:30])
        save(t=time.time(), cvars=mod["cvars"], aliases=mod["aliases"])
    else:
        now = {}
        for n, x in zip(known, o[k + 1:]):
            m = re.search(r'"%s" is "(.*?)" \[' % re.escape(n), x); now[n] = m.group(1) if m else None
        mod["cvars"] = {n: {"value": cut(v), "vanilla": cache["cvars"][n].get("vanilla")} for n, v in now.items()
                        if v is not None and (n not in bc or bc[n] != v)}
        mod["aliases"] = cache.get("aliases") or {}
        save()
    mod["entities"] = {}
    if mapname:
        try: eb = json.loads((STATE_BASE / f"ents_{mapname}_{gametype or 'dm'}.json").read_text())
        except (OSError, ValueError): eb = None; mod["baseline_entities"] = f"missing: {mapname} {gametype}"
        mc_f = d / f"gab_classes_{mapname}.json"                  # the map's entity classnames, read once per instance + map
        try: cls = set(json.loads(mc_f.read_text()))
        except (OSError, ValueError):
            cls = map_classnames(d, mapname)
            try: mc_f.write_text(json.dumps(sorted(cls)))
            except OSError: pass
        cand = cls | set(eb or {})
        cnt = _findent_counts(r, cand) if cand else {}
        for c in sorted(cnt):
            v0 = (eb or {}).get(c, 0)
            if cnt[c] != v0 and len(mod["entities"]) < 20: mod["entities"][c] = {"count": cnt[c], "vanilla": v0 if eb is not None else None}
    # screen_text: the CSQC centerprints still on screen (title + messages not expired), then the console lines
    ct = num(cpv.get("time")) or 0
    cps = [cpv.get("centerprint_title") or ""]
    for i in range(CP_MAX):
        m, x = cpv.get(f"centerprint_messages{f'[{i}]' if i else ''}") or "", num(cpv.get(f"centerprint_expire_time{f'[{i}]' if i else ''}")) or 0
        if m.strip() and (x == 0 or x > ct): cps.append(m)
    cps = [cut("centerprint: " + re.sub(r"\s+", " ", COLOR.sub("", c)).strip()) for c in cps if c.strip()]
    return {"running": True, "engine": engine, "mod": mod, "screen_text": cps + screen_lines(_log_tail(d / "console.log"))}


def entity_baseline(name, port, mapname, gametype):
    """the vanilla entity census (findent count per classname of the map's entity source) of mapname + gametype on a vanilla
    dedicated server -> {file} (STATE_BASE/ents_<map>_<gametype>.json)"""
    inst = Instance(name, port); res = {}
    try:
        inst.start([], mapname)
        if gametype and gametype != "dm":
            P = XOProbes(inst); P.r(f"sv_cmd gametype {gametype}"); P.r(f"map {mapname}"); P._wait_map(mapname)
        STATE_BASE.mkdir(parents=True, exist_ok=True)
        cnt = _findent_counts(inst.rcon, map_classnames(inst.dir, mapname))
        if not cnt or not any(cnt.values()): raise RuntimeError("no entities counted")
        f = STATE_BASE / f"ents_{mapname}_{gametype or 'dm'}.json"; tmp = f.with_suffix(".tmp")
        tmp.write_text(json.dumps(cnt, indent=0, sort_keys=True)); tmp.rename(f); res["file"] = str(f)
    except Exception as ex: res["error"] = f"{type(ex).__name__}: {str(ex)[:200]}"
    finally: inst.stop()
    return res


def capture_baseline(name, port, what):
    """the vanilla cvar / alias baseline from a running vanilla GL client (what = cvars) -> the file written"""
    d = XROOT / "inst" / name; STATE_BASE.mkdir(parents=True, exist_ok=True)
    pw = (d / "rcon.pw").read_text().strip()
    if what == "cvars":
        r = LogRcon(port, pw, d / "console.log"); cv, al = r.many(["cvarlist", "alias"], 6.0)
        f = STATE_BASE / "cvars.json"; data = {"cvars": parse_cvarlist(cv), "aliases": parse_aliases(al)}
        if len(data["cvars"]) < 1000: raise RuntimeError(f"cvarlist too short ({len(data['cvars'])})")
    else: raise ValueError(what)
    tmp = f.with_suffix(".tmp"); tmp.write_text(json.dumps(data, indent=0, sort_keys=True)); tmp.rename(f)
    return str(f)


def main():
    a = sys.argv[1:]; cmd = a[0]
    kv = lambda k, d=None: a[a.index(k) + 1] if k in a else d
    mods = [a[i + 1] for i, x in enumerate(a) if x == "--mod"]
    if cmd == "session":
        only = kv("--only"); r = session(kv("--name"), int(kv("--port")), mods, only.split(",") if only else None, kv("--out"))
        print(json.dumps({"timing": r.get("_timing"), "error": r.get("error"), "log_errors": r.get("log_errors"), "alive_at_end": r.get("alive_at_end")}))
    elif cmd == "client":
        r = ClientRun(kv("--name"), int(kv("--port")), kv("--display")).run(mods); Path(kv("--out")).write_text(json.dumps(r, indent=1))
        print(json.dumps({k: v for k, v in r.items() if k != "hud"}))
    elif cmd == "gate": print(json.dumps(gate(kv("--name"), int(kv("--port")), mods, kv("--map", MAP), kv("--gametype", "dm"), kv("--out"))))
    elif cmd == "state": print(json.dumps(client_state(a[1], int(a[2]), light="light" in a[3:])))
    elif cmd == "entbase": print(json.dumps(entity_baseline(kv("--name"), int(kv("--port")), kv("--map", MAP), kv("--gametype", "dm"))))
    elif cmd == "baseline": print(json.dumps({"file": capture_baseline(a[1], int(a[2]), a[3])}))
    elif cmd == "rcon":
        d = XROOT / "inst" / a[1]; print(LogRcon(int(a[2]), (d / "rcon.pw").read_text().strip(), d / "console.log")(a[3], float(kv("--timeout", 3))))
    elif cmd == "stop": Instance(a[1], 0).stop()


if __name__ == "__main__":
    main()
