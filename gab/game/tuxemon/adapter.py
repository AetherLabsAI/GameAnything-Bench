"""Tuxemon 0.4.35 adapter. Install: the development-branch checkout (src/, game root with mods/tuxemon) + a venv with the pinned
Python packages (pygame-ce 2.5.7 ...), under <seat home>/oss-tuxemon (the verified reference install, python3.12
of the sandbox; `install()` rebuilds it from upstream if missing, see there). No EULA / ToS (GPL-3 / CC-BY-SA), no first-run
dialog; a fresh HOME per instance with a seeded ~/.tuxemon/tuxemon.yaml (splash off, sound off, 1280x720 window, English).
The player session runs the real game (run_tuxemon.py through the launcher gabplay.py: the multiplayer server never listens =
network off, a state reporter thread, and in dev instances a request hook for event actions) on a private Xvfb :190+seat.
A fresh start shows the title screen (NEW GAME -> campaign -> ...), as for a player. The probes run headless (SDL dummy).

Deliverable mod/ (devkit/README.md): files laid out like the game root (mods/tuxemon/maps/x.tmx + x.yaml, mods/tuxemon/db/...,
mods/tuxemon/gfx/..., mods/tuxemon/l18n/..., tuxemon/... python), or several such dirs one level down (applied in name order).
install_mod copies them over a hard-link copy of the game root: what a player does when dropping a mod into the game folder.
"""
import json, re, shlex, time
from pathlib import Path

from .. import base as gbase
from ..base import GameAdapter
from . import probes as P

UPSTREAM = "https://github.com/Tuxemon/Tuxemon.git"
PIP = ["babel==2.18.0", "cbor==1.0.0", "websockets==17.3", "pillow==13.0.0", "packaging==26.3", "pygame-ce==2.5.7", "pyscroll==2.31",
       "PyTMX==3.32", "requests==2.34.2", "natsort==8.4.0", "PyYAML==6.0.3", "prompt_toolkit==3.0.53", "pygame-menu-ce==4.5.2",
       "pydantic==2.14.1"]                      # the verified venv (pip freeze of oss-tuxemon/venv, 2026-10-30)
TOP_OK = {"mods", "tuxemon"}
# game variables a vanilla new game holds that no stock map / db line names (set by the engine code at runtime; from vanilla
# sessions 2026-10-31): never reported as mod variables
VANILLA_RUNTIME_VARS = []
CONFIG = """display:
  resolution_x: 1280
  resolution_y: 720
  splash: false
  fullscreen: false
  vsync: false
game:
  locale: en_US
  net_controller_enabled: false
  cli_enabled: false
  dev_tools: false
gameplay:
  sound_volume: 0.0
  music_volume: 0.0
"""
LAUNCHER = r'''"""gabplay.py: the gab player session of Tuxemon (runs in the sandbox). TX_SRC=<instance src> TX_STATE=<json> [TX_VANILLA=<json>]
[TX_REQ=<dir>] python3 gabplay.py  -> the real game (run_tuxemon.launch_game) with: the multiplayer websocket server never
listening (network off); a state reporter (STATE_SPEC): `TX_STATE.req` (a request id) -> TX_STATE is rewritten within a frame
by the game thread (the reporter thread answers itself if the game thread is busy > 0.4 s), and every 2 s anyway; TX_VANILLA
= the vanilla baseline (variables, NPCs / events per map, item / monster slugs) to tell mod content apart; (dev instances
only) TX_REQ/req.json {id, lines} = event action lines run on the game thread like a map event (answer in resp.json).
No gameplay change: the reporter only reads.
Import order matters: no game module that reads tuxemon.prepare.DISPLAY_CONTEXT at import time (graphics, map.view; pulled in
by network / session / client) may load before prepare.pygame_init() has made the real display -- else it keeps the 1x1
placeholder (scale 1) and every map is drawn as 16 px tiles on the scaled grid. So the patches are applied in _after_display(),
run right after the game's own pygame_init()."""
import json, os, re, sys, threading, time, traceback
SRC = os.environ["TX_SRC"]; sys.path.insert(0, SRC); os.chdir(SRC)
local_session = _cl = _orig = None                       # bound by _after_display()

try: VAN = json.load(open(os.environ["TX_VANILLA"]))
except Exception: VAN = {}
VVARS = set(VAN.get("vars") or []); VNPC = VAN.get("npcs") or {}; VEV = set(VAN.get("events") or [])
VITEMS = set(VAN.get("items") or []); VMONS = set(VAN.get("monsters") or [])
STATE = os.environ["TX_STATE"]; REQF = STATE + ".req"

def cut(s, n=200):
    s = str(s); return s if len(s) <= n else s[:n - 3] + "..."

def _label(i):
    lab = getattr(i, "label", None)
    if lab: return str(lab)
    go = getattr(i, "game_object", None)
    if go is not None:
        slug = getattr(go, "slug", None) or getattr(getattr(go, "__self__", None), "slug", None)
        if slug: return str(slug)
        f = getattr(go, "func", None)
        if f is not None and getattr(go, "args", None): return str(getattr(go.args[0], "slug", go.args[0]))
        if getattr(go, "char", None): return str(go.char)                 # InputMenu letter keys
        a = getattr(go, "action", None)
        if a is not None: return {"backspace": "<-", "confirm": "END", "pick_random": "RANDOM", "empty": ""}.get(getattr(a, "__name__", ""), getattr(a, "__name__", ""))
        return str(getattr(go, "__name__", type(go).__name__))
    return ""

def ui(st):
    """dialog text / menu items of one state (as the probes read them)"""
    d = {}
    box = getattr(st, "dialog_box", None)
    if box is not None: d["text"] = getattr(box, "_text", "")
    m = getattr(st, "menu", None)
    if m is not None and hasattr(m, "get_widgets"):
        try:
            ws = m.get_widgets(); d["items"] = [w.get_title() for w in ws]
            sw = m.get_selected_widget(); d["sel"] = ws.index(sw) if sw in ws else None
        except Exception: pass
    mi = getattr(st, "menu_items", None)
    if mi is not None and "items" not in d:
        try: d["items"] = [_label(i) for i in mi]; d["sel"] = getattr(st, "selected_index", None)
        except Exception: pass
    ic = getattr(st, "input_controller", None)                          # text input (player name ...)
    if ic is not None:
        disp = getattr(st, "input_display", None)
        pr = getattr(getattr(disp, "prompt", None), "text", None) or getattr(disp, "prompt_text", "") or ""
        d["input"] = (str(pr) + ": " if pr else "") + str(getattr(ic, "current_string", ""))
    return d

def snap(rid=None):
    c = getattr(local_session, "_client", None) or getattr(local_session, "client", None)
    s = {"t": round(time.time(), 2), "req": rid}
    if c is None: return s
    try:
        st = c.current_state; s["screen"] = type(st).__name__ if st else None; s["states"] = list(c.active_state_names)
        s["texts"] = []; s["menu"] = None
        for k, x in enumerate(c.state_manager.active_states):
            u = ui(x)
            if u.get("text") and not any(type(y).__name__ == "DialogState" for y in c.state_manager.active_states[:k]):
                s["texts"].append(cut(u["text"], 400))            # the top-most dialog box (lower ones are covered by it)
            if u.get("input") is not None: s["texts"].append("input: " + cut(u["input"], 200))
            if x is st and u.get("items"): s["menu"] = {"state": type(x).__name__, "items": [cut(i, 80) for i in u["items"]][:40], "sel": u.get("sel")}
    except Exception as e: s["ui_error"] = cut(e, 120)
    try:
        p = local_session.player
        mp = c.get_map_name()
    except Exception: return s
    try:
        s.update(map=mp, tile=[int(v) for v in p.tile_pos], facing=str(p.facing).split(".")[-1], name=p.name,
                 money=p.money_controller.money_manager.get_money(), steps=int(getattr(p, "steps", 0) or 0),
                 bag={i.slug: i.quantity for i in p.items},
                 party=[{"slug": m.slug, "level": m.level, "hp": m.current_hp, "max_hp": m.hp} for m in p.monsters])
        gv = {str(k): v for k, v in list(p.game_variables.items())}
        s["vars_total"] = len(gv)
        s["mod_vars"] = {k: cut(v) for k, v in sorted(gv.items()) if k not in VVARS}
        for k in ("hour", "daytime", "stage_of_day", "season", "weekday"):
            if k in gv: s.setdefault("time", {})[k] = cut(gv[k], 40)
        npcs = {}
        for slug, n in sorted(c.npc_manager.npcs.items()):
            if n is p or slug in (VNPC.get(mp) or ()) or slug in (VNPC.get("*") or ()): continue
            npcs[slug] = {"name": cut(getattr(n, "name", ""), 60), "x": int(n.tile_pos[0]), "y": int(n.tile_pos[1]), "facing": str(n.facing).split(".")[-1]}
        s["mod_npcs"] = npcs
        s["npcs_total"] = len(c.npc_manager.npcs) - 1
        evs = [e.name for e in list(c.map_manager.events) + list(c.map_manager.inits)]
        s["mod_events"] = sorted({e for e in evs if e not in VEV and e != "gabdev"})[:20]
        if VITEMS: s["mod_items"] = sorted(k for k in s["bag"] if k not in VITEMS)
        if VMONS: s["mod_monsters"] = sorted({m["slug"] for m in s["party"] if m["slug"] not in VMONS})
    except Exception as e: s["in_world"] = False; s["error"] = cut(e, 160)
    return s

def write(d):
    tmp = STATE + ".tmp"
    with open(tmp, "w") as f: json.dump(d, f, default=str)
    os.replace(tmp, STATE)

_want = {"id": None}; _done = threading.Event()
def answer_in_game():
    rid = _want["id"]
    if rid is None: return
    _want["id"] = None
    try: write(snap(rid))
    except Exception: pass
    _done.set()

def reporter():
    last = 0.0
    while True:
        rid = None
        try:
            if os.path.exists(REQF):
                rid = open(REQF).read().strip() or "r"; os.remove(REQF)
        except Exception: rid = None
        if rid is None and time.time() - last >= 2: rid = "tick"
        if rid is not None:
            _done.clear(); _want["id"] = rid
            if not _done.wait(0.4):                       # game thread busy (loading, a long frame): answer from here
                _want["id"] = None
                for _ in range(3):
                    try: write(snap(rid)); break
                    except Exception: time.sleep(0.02)
            last = time.time()
        time.sleep(0.03)

REQ = os.environ.get("TX_REQ"); _seen = set()
def _update(self, dt):
    _orig(self, dt)
    if _want["id"] is not None: answer_in_game()
    if not REQ: return
    f = os.path.join(REQ, "req.json")
    if not os.path.exists(f): return
    try: r = json.load(open(f))
    except Exception: return
    if r.get("id") in _seen: return
    _seen.add(r.get("id")); out = {"id": r.get("id")}
    try:
        from tuxemon.event.eventparser import EventParser
        from tuxemon.db import BoundingBox
        ev = EventParser().create_event_object({"actions": list(r["lines"])}, "gabdev", BoundingBox(x=0, y=0, width=1, height=1))
        self.event_engine.start_event(ev); out["ok"] = True
    except Exception: out["ok"] = False; out["error"] = traceback.format_exc()[-1500:]
    json.dump(out, open(os.path.join(REQ, "resp.json"), "w"))

def _after_display():
    """the network-off patch, the client update hook and the reporter, once the display (DISPLAY_CONTEXT) exists"""
    global local_session, _cl, _orig
    from tuxemon.network import websocket_server as _ws
    _ws.WebsocketServerWrapper.start_listening = lambda *a, **k: None
    from tuxemon.session import local_session
    from tuxemon import client as _cl
    _orig = _cl.LocalPygameClient.update
    _cl.LocalPygameClient.update = _update
    threading.Thread(target=reporter, daemon=True).start()

sys.argv = ["run_tuxemon.py"]
import run_tuxemon
import tuxemon.prepare as _prep                          # loads no DISPLAY_CONTEXT reader (run_tuxemon.init_display imports
_pygame_init = _prep.pygame_init                         # pygame_init from it at call time -> gets the wrapper)
def _pygame_init_then_patch(*a, **k):
    ctx = _pygame_init(*a, **k); _after_display(); return ctx
_prep.pygame_init = _pygame_init_then_patch
run_tuxemon.launch_game([])
'''
BASELINE_PY = r'''"""txbase.py SRC OUT: the vanilla baseline of a Tuxemon game root for the state reporter: variable names the stock game
can set (map / db event lines with *variable* actions and conditions, choice actions, and game_variables use in the Python
code), NPCs created per map ("*": campaign-wide event files), every stock event name, item / monster slugs"""
import json, re, sys
from pathlib import Path
src, out = Path(sys.argv[1]), sys.argv[2]
D = src / "mods" / "tuxemon"
V, NPC, EV = set(), {}, {}
ACT = re.compile(r"\b(\w*variable\w*|translated_dialog_choice|dialog_choice|random_integer|choice_\w+|get_\w+|save_timestamp|"
                 r"timestamp_cooldown|one_of|cooldown_days|variable_\w+|set_\w+|add_\w+|modify_\w+|format_\w+)[ \t]+([^\n<\"]+)")
IDENT = re.compile(r"[A-Za-z_][\w]*")
for f in list(D.rglob("*.yaml")) + list(D.rglob("*.tmx")):
    t = f.read_text(errors="replace")
    for m in ACT.finditer(t):
        if "variable" in m.group(1) or m.group(1) in ("translated_dialog_choice", "dialog_choice", "random_integer", "save_timestamp",
                                                      "timestamp_cooldown", "one_of", "cooldown_days") or m.group(1).startswith(("choice_", "get_")):
            V.update(IDENT.findall(m.group(2)))
    if f.parent.name == "maps":
        key = f.stem + ".tmx" if (f.parent / (f.stem + ".tmx")).exists() else "*"
        NPC.setdefault(key, set()).update(re.findall(r"create_npc[ \t]+([\w-]+)", t))
        if f.suffix == ".yaml":
            try: EV.setdefault(key, set()).update((__import__("yaml").safe_load(t) or {}).get("events", {}).keys())
            except Exception: pass
        else: EV.setdefault(key, set()).update(re.findall(r"<object[^>]*\bname=\"([^\"]+)\"", t))
for f in (src / "tuxemon").rglob("*.py"):
    t = f.read_text(errors="replace")
    V.update(re.findall(r"game_variables(?:\.set\(|\.get\(|\[|\.pop\(|\.setdefault\()\s*[\"'](\w+)[\"']", t))
    V.update(re.findall(r"variables?\[\s*[\"'](\w+)[\"']\s*\]", t))
V.update(sys.argv[3].split(",") if len(sys.argv) > 3 else [])
json.dump({"vars": sorted(V), "npcs": {k: sorted(v) for k, v in NPC.items()}, "events": sorted(set().union(*EV.values())),
           "items": sorted(p.stem for p in (D / "db" / "item").glob("*.yaml")),
           "monsters": sorted(p.stem for p in (D / "db" / "monster").glob("*.yaml"))}, open(out, "w"))
print("ok", len(V), len(NPC))
'''
ENGINE_KEYS = ("screen", "states", "map", "tile", "facing", "player_name", "money", "steps", "bag", "party", "time", "variables_total",
               "npcs_total")


def build_state(raw, running):
    """the reporter's raw snapshot (gabplay.py snap) -> the STATE_SPEC layout (see Adapter.state)"""
    raw = raw if isinstance(raw, dict) else {}
    e = dict.fromkeys(ENGINE_KEYS)
    t = raw.get("tile")
    e.update(screen=raw.get("screen") if running else "not_running", states=raw.get("states"), map=raw.get("map"),
             tile={"x": t[0], "y": t[1]} if isinstance(t, list) and len(t) == 2 else None,
             facing=raw.get("facing"), player_name=raw.get("name"), money=raw.get("money"), steps=raw.get("steps"), bag=raw.get("bag"),
             party=raw.get("party"), time=raw.get("time"), variables_total=raw.get("vars_total"), npcs_total=raw.get("npcs_total"))
    mv = raw.get("mod_vars") or {}
    mod = {"variables": dict(sorted(mv.items())[:30]), "npcs": dict(sorted((raw.get("mod_npcs") or {}).items())[:20]),
           "map_events": (raw.get("mod_events") or [])[:20], "items": raw.get("mod_items") or [], "monsters": raw.get("mod_monsters") or []}
    if len(mv) > 30: mod["variables_more"] = len(mv) - 30
    cut = lambda x, n=200: (x := " ".join(str(x).split())) if len(x) <= n else x[:n - 3] + "..."
    text = [cut(t) for t in raw.get("texts") or [] if str(t).strip()]
    m = raw.get("menu")
    if m and m.get("items"):
        items = [str(i) for i in m["items"]]
        sel = m.get("sel") if isinstance(m.get("sel"), int) and 0 <= m["sel"] < len(items) else None
        text.append(cut(f"menu ({m.get('state')}): " + " | ".join(f"[{x}]" if k == sel else x for k, x in enumerate(items) if x.strip() or k == sel)))
    out = {"running": running, "engine": e, "mod": mod, "screen_text": text}
    if raw.get("error") and running: out["error"] = cut(raw["error"])
    return out


SESSION_SH = r'''#!/bin/bash
# tx-session.sh INSTDIR DISPLAYNUM start|stop [dev]   (Xvfb + Tuxemon in the background, pids in INSTDIR/session)
I=$1; DN=$2; ACT=$3; DEV=$4; S=$I/session; mkdir -p $S
stop() { p=$(cat $S/tx.pid 2>/dev/null); x=$(cat $S/xvfb.pid 2>/dev/null); [ -n "$p" ] && kill -- -$p 2>/dev/null; [ -n "$x" ] && kill $x 2>/dev/null; sleep 1
         [ -n "$p" ] && kill -9 -- -$p 2>/dev/null; [ -n "$x" ] && kill -9 $x 2>/dev/null; rm -f $S/tx.pid $S/xvfb.pid; }
if [ "$ACT" = stop ]; then stop; exit 0; fi
stop; rm -f /tmp/.X$DN-lock $S/state.json $S/state.json.req $S/req/*.json
setsid -f Xvfb :$DN -screen 0 1280x720x24 -nolisten tcp > $S/xvfb.log 2>&1 < /dev/null
for i in $(seq 1 40); do [ -e /tmp/.X11-unix/X$DN ] && break; sleep 0.25; done
echo $(pgrep -n -f "^Xvfb :$DN ") > $S/xvfb.pid
export HOME=$I/HOME DISPLAY=:$DN SDL_AUDIODRIVER=dummy SDL_FRAMEBUFFER_ACCELERATION=0 TX_SRC=$I/src TX_STATE=$S/state.json TX_VANILLA=__ROOT__/bridge/vanilla.json PYTHONPATH=__ROOT__/venv/lib/python3.12/site-packages
[ "$DEV" = dev ] && { mkdir -p $S/req; export TX_REQ=$S/req; }
cd $I/src
setsid -f bash -c "echo \$\$ > $S/tx.pid; exec python3 -u $I/gabplay.py > $S/stdout.log 2>&1 < /dev/null"
for i in $(seq 1 20); do [ -s $S/tx.pid ] && break; sleep 0.1; done
echo started $(cat $S/tx.pid)
'''


class Adapter(GameAdapter):
    name = "tuxemon"
    title = "Tuxemon"
    screen = (1280, 720)
    extra_help = ("  action 'LINE' ['LINE' ...]    run event-action lines in the running dev game, as a map event would (testing only;\n"
                  "                               players cannot), e.g. action 'teleport player,spyder_paper_town.tmx,10,8'\n"
                  "                               'add_monster rockitten,10' 'wild_encounter nut,4' 'set_variable my_flag:yes'\n"
                  "  probe NAME[,NAME]            run verified regression probes (headless) on a fresh copy with deliverable/mod,\n"
                  "                               e.g. probe gate (title screen + database + your maps load), probe maps, probe new_game\n")

    def __init__(self, seat, root=None):
        super().__init__(seat)
        self.root = root or f"{seat.home}/oss-tuxemon"
        n = int(seat.seat) if str(seat.seat).isdigit() else 1
        self.dn = 190 + n                                     # session display (private Xvfb)
        self._mods = None

    def requirements(self):
        return {"game": "tuxemon", "gpu": False, "mem_gb": 0.5, "steam": False, "hosts": ["pool", "docker", "local"],
                "image": "gameanything/tuxemon:0.4.35", "eula": None}

    def idir(self, inst): return f"{self.root}/gab/{inst}"
    def display(self, inst): return f":{self.dn}"
    def paths(self, inst): return {"game_root_vanilla": f"{self.root}/src", "instance_game_root": f"{self.idir(inst)}/src",
                                   "game_data": f"{self.idir(inst)}/src/mods/tuxemon", "user_dir": f"{self.idir(inst)}/HOME/.tuxemon",
                                   "game_log": f"{self.idir(inst)}/session/stdout.log", "python": "python3 (venv site-packages: "
                                   f"{self.root}/venv/lib/python3.12/site-packages)"}

    # ---- install / instance
    def _ok(self):
        return self.seat.exec(f"test -f {self.root}/src/mods/tuxemon/mod.yaml && test -d {self.root}/venv/lib/python3.12/site-packages/pygame "
                              f"&& echo ok").stdout.strip() == "ok"

    def install(self):
        """the verified install is a copy of a 2026-10-30 development checkout (no git metadata). If missing, rebuild it from
        upstream: the development branch as of 2026-10-30 + a venv with the pinned packages (same Tuxemon 0.4.35; not
        bit-identical to the verified copy -> a new vanilla baseline is made for it, its version string differs)"""
        if not self._ok():
            r = self.seat.exec(f"set -e; mkdir -p {self.root}/dl && cd {self.root}/dl && rm -rf tx && git clone -q --branch development {UPSTREAM} tx && "
                               f"cd tx && git checkout -q $(git rev-list -1 --before=2026-10-30T23:59 HEAD) && git rev-parse HEAD > ../commit.txt && "
                               f"rm -rf .git && cd {self.root} && rm -rf src && mv dl/tx src && python3 -m venv venv && "
                               f"venv/bin/pip install -q {' '.join(PIP)}", timeout=3600)
            if not self._ok(): raise RuntimeError(f"Tuxemon install failed: {r.stdout[-400:]} {r.stderr[-400:]}")
        self.seat.put(f"{self.root}/bridge/tx-session.sh", SESSION_SH.replace("__ROOT__", self.root))
        self.seat.put(f"{self.root}/bridge/gabplay.py", LAUNCHER)
        base = BASELINE_PY + "# extra: " + ",".join(VANILLA_RUNTIME_VARS) + "\n"
        if self.seat.exec(f"cat {self.root}/bridge/txbase.py 2>/dev/null").stdout != base or \
           self.seat.exec(f"test -s {self.root}/bridge/vanilla.json && echo ok").stdout.strip() != "ok":
            self.seat.put(f"{self.root}/bridge/txbase.py", base)
            self.seat.exec(f"cd {self.root}/bridge && PYTHONPATH={self.root}/venv/lib/python3.12/site-packages python3 txbase.py {self.root}/src "
                           f"vanilla.json.tmp {','.join(VANILLA_RUNTIME_VARS)} && mv vanilla.json.tmp vanilla.json", timeout=300)
        return True

    def provision(self, inst):
        self.install(); self.stop(inst); d = self.idir(inst)
        self.seat.exec(f"rm -rf {d} && mkdir -p {d}/HOME/.tuxemon/saves {d}/session && cp -al {self.root}/src {d}/src && "
                       f"cp {self.root}/bridge/gabplay.py {d}/gabplay.py", check=True)
        self.seat.put(f"{d}/HOME/.tuxemon/tuxemon.yaml", CONFIG)
        self.seat.put(f"{d}/mods.json", "[]"); self._mods = []
        return {"instance": inst, "dir": d, "display": self.display(inst)}

    @staticmethod
    def _layout(mod_dir):
        """-> mod dirs laid out like the game root: mod/ itself, or its subdirs (name order)"""
        m = Path(mod_dir)
        if not m.exists(): return []
        if any((m / t).is_dir() for t in TOP_OK): return [m]
        return sorted(p for p in m.iterdir() if p.is_dir() and any((p / t).is_dir() for t in TOP_OK))

    @staticmethod
    def mod_maps(mods):
        """map files (.tmx) the mod adds or replaces (the gate loads each)"""
        return sorted({p.name for m in mods for p in (Path(m) / "mods" / "tuxemon" / "maps").glob("*.tmx")})

    def install_mod(self, inst, mod_dir):
        """fresh game root (hard-link copy of vanilla) with the mod's files copied over it (vanilla files stay untouched)"""
        mods = self._layout(mod_dir); d = self.idir(inst)
        self.seat.exec(f"rm -rf {d}/src {d}/mod_* && cp -al {self.root}/src {d}/src", check=True)
        for k, m in enumerate(mods):
            self.seat.copy_in(m, f"{d}/mod_{k}_{m.name}")
            self.seat.exec(f"cp -r --remove-destination {d}/mod_{k}_{m.name}/. {d}/src/", check=True)
        self._mods = [str(m) for m in mods]
        self.seat.put(f"{d}/mods.json", json.dumps({"mods": [m.name for m in mods], "maps": self.mod_maps(mods), "host": self._mods}))
        return {"mods": [m.name for m in mods], "maps": self.mod_maps(mods)}

    def attach(self, inst, mod_dir): self._mods = None

    def _installed(self, inst):
        r = self.seat.exec(f"cat {self.idir(inst)}/mods.json 2>/dev/null").stdout.strip()
        j = json.loads(r) if r else {}
        return j if isinstance(j, dict) else {}

    def build(self, inst, mod_dir=None):
        """structure check of the deliverable (the load test is load_check). -> (ok, log)"""
        L = []; mods = self._layout(mod_dir) if mod_dir else []
        if mod_dir is not None and not mods: L.append("mod/ holds nothing laid out like the game root (mods/tuxemon/... or tuxemon/...)")
        for m in mods:
            for e in m.iterdir():
                if e.name not in TOP_OK and not e.name.lower().startswith("readme"):
                    L.append(f"{m.name}/{e.name}: unexpected top-level entry (only mods/ and tuxemon/ are laid over the game root)")
            for p in (m / "mods").glob("*") if (m / "mods").exists() else []:
                if p.name != "tuxemon": L.append(f"{m.name}/mods/{p.name}: the game loads only the data mod mods/tuxemon/ (put your files there)")
            for f in m.rglob("*.yaml"):
                try: __import__("yaml").safe_load(f.read_text(errors="replace"))
                except Exception as e: L.append(f"{f.relative_to(m)}: not YAML ({str(e)[:100]})")
            for f in (m / "mods" / "tuxemon" / "maps").glob("*.tmx") if (m / "mods" / "tuxemon" / "maps").exists() else []:
                if "<map" not in f.read_text(errors="replace")[:2000]: L.append(f"{f.relative_to(m)}: not a Tiled .tmx map")
        return not L, "\n".join(L) or f"ok: {[m.name for m in mods]}, maps {self.mod_maps(mods)}"

    def load_check(self, inst, out_dir=None):
        """the gate's load test (headless, the mod laid over a fresh game root): the game reaches its title screen and keeps
        running for 10 game s, the database loads, every map the mod adds / replaces loads; no crash"""
        j = self._installed(inst); mods = [Path(m) for m in j.get("host", [])]
        pr = P.TXProbes(self.seat, self.root, mods=mods, label=f"gate-{inst}", par=1, log=lambda s: None)
        r = pr.run_all(["gate"], mod_maps=j.get("maps", []))
        self.seat.exec(f"rm -rf {pr.inst}")
        g = r.get("gate") or {}; E = gbase.error_lines("\n".join(r.get("_error_lines") or []), r".")
        if not isinstance(g, dict) or "error" in g:
            return False, gbase.gate_log(E, f"the headless load run failed: {(g.get('error') if isinstance(g, dict) else g) or 'no result'}"[-400:],
                                         json.dumps({"gate": g, "runs": r["log_errors"]["runs"]}, default=str)[-3000:])
        bad = {m: v["err"] for m, v in g.get("maps", {}).items() if not v.get("ok")}
        ok = g.get("top") is not None and not bad
        why = None if ok else "the title screen did not come up" if g.get("top") is None else \
            f"{len(bad)} map(s) of the mod did not load: " + "; ".join(f"{m}: {e}" for m, e in bad.items())[:600]
        return ok, gbase.gate_log(E, why, json.dumps({"title_state": g.get("top"), "db_tables": len(g.get("db_counts") or {}), "maps": g.get("maps"),
                                                      "map_failures": bad}, default=str))

    def start(self, inst):
        """the real game in a window on the instance's Xvfb, with the installed mod; ok at the title screen"""
        d = self.idir(inst)
        self.seat.exec(f"rm -rf {d}/HOME/.tuxemon/saves/* {d}/session/state.json")      # every start is a fresh game state
        self.seat.exec(f"bash {self.root}/bridge/tx-session.sh {d} {self.dn} start {'dev' if inst.startswith('dev') else ''}")
        t0 = time.time()
        while time.time() - t0 < 90:
            time.sleep(2)
            if not self._alive(inst): return False, "tuxemon exited\n" + self.log_tail(inst)
            sc = self.state(inst)["engine"]["screen"]
            if sc and sc not in ("SplashState", "BackgroundState", "not_running"):
                self._x(inst, "xdotool mousemove 1279 719"); time.sleep(1); return True, f"screen {sc}"
        return False, "no title screen after 90 s\n" + self.log_tail(inst)

    def _alive(self, inst):
        return self.seat.exec(f"p=$(cat {self.idir(inst)}/session/tx.pid 2>/dev/null); [ -n \"$p\" ] && "
                              f"[ \"$(ps -o stat= -p $p 2>/dev/null | cut -c1)\" != Z ] && kill -0 $p 2>/dev/null && echo up").stdout.strip() == "up"

    def stop(self, inst):
        self.seat.exec(f"test -f {self.root}/bridge/tx-session.sh && bash {self.root}/bridge/tx-session.sh {self.idir(inst)} {self.dn} stop")

    def log_tail(self, inst, n=40): return self.seat.exec(f"tail -{n} {self.idir(inst)}/session/stdout.log").stdout

    def state(self, inst):
        """STATE_SPEC snapshot, fresh: a request file -> the session's reporter (gabplay.py) answers from the game thread within
        a frame (the game is never paused or fed input).
        {"running", "engine": {screen (top game state: PressAnyKeyState / StartState / WorldState / DialogState / ...),
         states (the stack), map (.tmx), tile {x, y}, facing, player_name, money, steps, bag {slug: qty}, party [{slug, level,
         hp, max_hp}], time {hour, daytime, stage_of_day, season, weekday} (game clock variables), variables_total, npcs_total},
         "mod": {variables {name: value} (game variables no stock map / db line or engine code sets: the vanilla baseline
                 bridge/vanilla.json, 30), npcs {slug: {name, x, y, facing}} (NPCs on the current map the stock map does not
                 create, 20), map_events [event names the stock map does not have], items / monsters [bag / party slugs not in
                 the stock db]},
         "screen_text": [dialog texts shown (every DialogState in the stack), "menu (State): A | [B] | C" of the top menu
                         ([x] = selected)]}"""
        S = f"{self.idir(inst)}/session"; rid = f"r{time.time_ns()}"
        r = self.seat.exec(f"p=$(cat {S}/tx.pid 2>/dev/null); if [ -n \"$p\" ] && [ \"$(ps -o stat= -p $p 2>/dev/null | cut -c1)\" != Z ] && "
                           f"kill -0 $p 2>/dev/null; then echo UP; echo {rid} > {S}/state.json.req.tmp && mv {S}/state.json.req.tmp {S}/state.json.req; "
                           f"for i in $(seq 1 40); do grep -q '\"req\": \"{rid}\"' {S}/state.json 2>/dev/null && break; sleep 0.03; done; "
                           f"else echo DOWN; fi; cat {S}/state.json 2>/dev/null").stdout
        head, _, body = r.partition("\n")
        try: raw = json.loads(body) if body.strip() else {}
        except ValueError: raw = {}
        return build_state(raw, head.strip() == "UP")

    def log_errors(self, inst):
        sig = P.errors_in(self.seat.exec(f"cat {self.idir(inst)}/session/stdout.log 2>/dev/null").stdout, self.root)
        return {"errors": len(sig), "signatures": sorted(set(sig))[:30]}

    def input(self, inst, s):
        """a key tap is a short press (~0.12 s, like a finger): the game reads key state per frame, and an instant xdotool
        down + up inside one frame is lost"""
        if s["do"] == "key":
            return self._x(inst, " ; ".join(f"xdotool keydown {shlex.quote(x)}; sleep 0.12; xdotool keyup {shlex.quote(x)}; sleep 0.05"
                                            for x in str(s["keys"]).split()))
        return super().input(inst, s)

    # ---- dev tools beyond the common ones
    def dev_command(self, inst, k, v, mod, wd):
        if k == "action":
            if not inst.startswith("dev"): return {"error": "event actions run only in the dev instance"}
            if not v: return {"error": "usage: action 'LINE' ['LINE' ...]"}
            q = f"{self.idir(inst)}/session/req"; rid = f"r{int(time.time() * 1000)}"
            self.seat.put(f"{q}/req.json", json.dumps({"id": rid, "lines": list(v)}))
            for _ in range(20):
                time.sleep(0.5)
                r = self.seat.exec(f"cat {q}/resp.json 2>/dev/null").stdout.strip()
                if r and json.loads(r).get("id") == rid:
                    time.sleep(1.5); return {**json.loads(r), "state": self.state(inst)}
            return {"error": "no answer (is the game running? `game start`)"}
        if k == "probe":
            names = [x for x in ",".join(v).split(",") if x]
            bad = [n for n in names if n not in P.PROBES and n not in P.SUB and n != "gate"]
            if bad or not names: return {"error": f"probes: gate, {', '.join(P.PROBES)}"}
            mods = self._layout(mod) if mod and Path(mod).exists() else []
            pr = P.TXProbes(self.seat, self.root, mods=mods, label=f"devprobe-{inst}", par=2, log=lambda s: None)
            r = pr.run_all(names, mod_maps=self.mod_maps(mods)); self.seat.exec(f"rm -rf {pr.inst}")
            short = lambda x: x if len(json.dumps(x, default=str)) < 2500 else {"summary": {k2: (v2 if len(json.dumps(v2, default=str)) < 300 else "...")
                                                                                         for k2, v2 in x.items()} if isinstance(x, dict) else str(x)[:2500]}
            return {n: short(r.get(n)) for n in names} | {"errors": r["log_errors"]["signatures"][:15], "not_finished": r["log_errors"]["not_finished"]}
        return None

    # ---- regression
    def run_probes(self, names, mod_dir=None, label="vanilla"):
        """a fresh instance (+ mod), every probe one headless game process, 4 in parallel (TXProbes.run_all)"""
        mods = self._layout(mod_dir) if mod_dir else []
        pr = P.TXProbes(self.seat, self.root, mods=mods, label=re.sub(r"[^\w.-]+", "_", label), log=lambda s: None)
        return pr.run_all([n for n in names if n != "log_errors"])

    probe_rev = P.REV                          # in the vanilla baseline's cache key (regression.baseline)

    def compare(self, spec, base, got): return P.compare_item(spec, base, got)

    def version(self):
        v = self.seat.exec(f"grep -m1 '^version' {self.root}/src/pyproject.toml; cat {self.root}/dl/commit.txt 2>/dev/null; "
                           f"ls {self.root}/venv/lib/python3.12/site-packages | grep -m1 -o 'pygame_ce-[0-9.]*[0-9]'").stdout.split("\n")
        b = v[0].split("=", 1)[-1].strip().strip('"') if v and "=" in v[0] else "?"
        commit = v[1].strip()[:10] if len(v) > 2 and re.fullmatch(r"[0-9a-f]{40}", v[1].strip()) else ""
        return {"game": "tuxemon", "build": b + (f"+{commit}" if commit else " (development checkout 2026-10-30)"),
                "mod_loader": "game-root overlay (mods/tuxemon)", "image": self.seat.sandbox,
                "python": "3.12 + " + (v[-2] if len(v) > 1 else "?")}
