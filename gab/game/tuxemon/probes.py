"""Tuxemon regression probes. Ported from the original probes_tuxemon.py (verified in game 2026-10-30,
Tuxemon 0.4.35 development checkout, pygame-ce 2.5.7, Python 3.12; A/A + negative checks in games/tuxemon/pool.yaml).
Changes: the host side runs through a gab Seat (any host backend) instead of docker exec / docker cp on a fixed container;
install root is an argument; mods are copied in with seat.copy_in; the instance's src/, homes and shots are deleted after a
suite (out/ with the JSON results and logs is kept); one added runner probe `gate` (the eval gate's load test: startup to the
title screen, the database, the mod's own maps; not a pool probe); no CLI. Probe logic and compare rules are unchanged.
2026-11-06: a mod that changes the new-game opening no longer makes every probe could-not-test: start_game / stock_start (below
new_game), walk / warp on the stock maps the mod leaves alone, not-applicable items, TX-STABLE counts only real crashes; probe
outputs changed, so REV is in the vanilla baseline's cache key.

Bridge (no game patch): the in-sandbox runner RUNNER (written to <instance>/txrun.py) imports the game from an instance's
src/, builds the real LocalPygameClient (SDL dummy video, no window), runs the real startup state machine and steps the
client's own fixed-timestep update (1/60 s per frame, as fast as the CPU allows). Input is real: pygame KEYDOWN / KEYUP events
through the game's own input manager (arrows, return = A, escape = menu/back). State is read from the session (map, tile,
party, bag, money, variables), the state stack, the open menu's widgets and every text shown (AlertManager.alert wrapped).
Setup shortcuts use the game's own event-script actions (teleport, add_monster, wild_encounter, set_money ...) through a
temporary EventObject. Determinism: random.seed(1) at start and re-seeded before each measured fight / encounter walk; a
frame clock replaces the wall clock (always June 15 2026, 12:00 at start); the network server never listens.
One probe = one fresh process with its own HOME (~/.tuxemon); save_load runs a second process in the same HOME that loads
the save through the real title screen.

Mods: a directory whose layout mirrors the game root (mods/tuxemon/maps/x.tmx, mods/tuxemon/db/monster/x.yaml, tuxemon/...
python). An instance is <root>/runs/<label>-<ts>/src, a hard-link copy of the vanilla src/ (cp -al) with each mod copied over
it (cp --remove-destination: the vanilla files stay untouched). Vanilla = the same with no mod.
"""
import json, os, re, time
from pathlib import Path

ROOT = "/home/player/oss-tuxemon"                       # default install: ROOT/{src, venv}
VERSION = "Tuxemon 0.4.35 (development branch checkout, pygame-ce 2.5.7, Python 3.12.3)"
REV = "2026-11-06"      # probe output revision (stock start, walk alt routes, re-seed after the start): a new vanilla baseline
PROBES = ["new_game", "campaigns", "walk", "warp", "talk", "story", "battle", "capture", "items", "shop", "menu", "save_load",
          "encounter", "db", "maps"]
GATE_FRAMES = 600                                        # gate: 10 game s on the title screen
SUB = {"campaigns": ["campaign_xero", "campaign_waves"], "save_load": ["save", "load"]}   # composite probes

RUNNER = r'''GATE_FRAMES = __GATE_FRAMES__
"""txrun.py: Tuxemon probe runner (runs INSIDE the sandbox, one probe per process).
   TX_SRC=<instance src> HOME=<fresh home> python3 txrun.py <probe> <out.json> [shot_dir]"""
import os, re, sys, random, time, json, datetime as _dt, traceback
ROOT = os.environ["TX_SRC"]
os.environ.setdefault("SDL_VIDEODRIVER", "dummy"); os.environ.setdefault("SDL_AUDIODRIVER", "dummy")
sys.path.insert(0, ROOT); os.chdir(ROOT)
DT = 1.0 / 60
# ---- virtual clock: game time follows simulated frames, not the wall clock (deterministic day/night, key repeat)
_real_time = time.time
_V = {"t": 1780000000.0, "frames": 0}          # 2026-05-28 ~
time.time = lambda: _V["t"] + _V["frames"] * DT
FIXED_NOW = _dt.datetime(2026, 6, 15, 12, 0, 0)

def mon_info(m):
    return {"slug": m.slug, "level": m.level, "hp": m.current_hp, "max_hp": m.hp, "exp": m.total_experience,
            "stats": [m.melee, m.armour, m.ranged, m.dodge, m.speed], "moves": [t.slug for t in m.moves.moves]}

def _label(i):
    lab = getattr(i, "label", None)
    if lab: return str(lab)
    go = getattr(i, "game_object", None)
    if go is not None:
        slug = getattr(go, "slug", None) or getattr(getattr(go, "__self__", None), "slug", None)
        if slug: return str(slug)
        f = getattr(go, "func", None)
        if f is not None and getattr(go, "args", None): return str(getattr(go.args[0], "slug", go.args[0]))
        return str(getattr(go, "__name__", type(go).__name__))
    return ""

MESSAGES = []          # every text the game shows in a dialog / combat text box (AlertManager.alert), in order

class Game:
    def __init__(self, seed=1, title=False):
        random.seed(seed)
        from tuxemon.platform import platform
        platform.init()
        from tuxemon.prepare import pygame_init
        self.ctx = pygame_init()
        import tuxemon.time_handler as th
        for name in dir(th):
            obj = getattr(th, name)
            if isinstance(obj, type) and hasattr(obj, "get_current_time"):
                obj.get_current_time = lambda self_: FIXED_NOW + _dt.timedelta(seconds=_V["frames"] * DT)
        from tuxemon.user_config import CONFIG
        cfg = CONFIG.copy(); cfg.logging.configure()
        cfg.config_model.display.splash = False
        cfg.config_model.game.skip_titlescreen = not title
        self.cfg = cfg
        from tuxemon.client import LocalPygameClient
        from tuxemon.session import local_session
        from tuxemon.startup_state_machine import StartupStateMachine
        import pygame
        self.pg = pygame
        from tuxemon.network import websocket_server as _ws   # no network server: parallel probe runs would fight over its port
        _ws.WebsocketServerWrapper.start_listening = lambda *a, **k: None
        from tuxemon.menu import alert as _al
        _orig = _al.AlertManager.alert
        def _rec(am, message, *a, **k):
            MESSAGES.append(str(message)); return _orig(am, message, *a, **k)
        _al.AlertManager.alert = _rec
        self.c = LocalPygameClient.create(cfg, self.ctx); local_session.set_client(self.c)
        self.s = local_session
        StartupStateMachine(self.c, cfg, None).run()
        self.frames = 0
    @property
    def player(self): return self.s.player
    def step(self, n=1):
        for _ in range(n):
            self.c.update(DT); self.frames += 1; _V["frames"] += 1
    def shot(self, path):
        self.c.draw(); self.pg.image.save(self.c.screen, path)
    def _ev(self, typ, name):
        k = self.pg.key.key_code(name)
        self.pg.event.post(self.pg.event.Event(typ, key=k, mod=0, unicode="", scancode=0))
    def key(self, name, hold=3, after=10):
        self._ev(self.pg.KEYDOWN, name); self.step(hold); self._ev(self.pg.KEYUP, name); self.step(after)
    def states(self): return list(self.c.active_state_names)
    def top(self): st = self.c.current_state; return type(st).__name__ if st else None
    def act(self, name, *params): return self.c.event_engine.execute_action(name, [str(p) for p in params])
    def script(self, *lines, wait=True, max_frames=600):
        """run event-script action lines (same syntax as map events, e.g. 'wild_encounter nut,4') as a running
        event, non-blocking: the game loop keeps stepping while it runs"""
        from tuxemon.event.eventparser import EventParser
        from tuxemon.db import BoundingBox
        ev = EventParser().create_event_object({"actions": list(lines)}, "txprobe", BoundingBox(x=0, y=0, width=1, height=1))
        self.c.event_engine.start_event(ev)
        if wait:
            f = 0
            while ev.id in self.c.event_engine.running_events and f < max_frames: self.step(1); f += 1
        return ev
    def tile(self): return [int(v) for v in self.player.tile_pos]
    def mapname(self): return self.c.get_map_name()
    def money(self): return self.player.money_controller.money_manager.get_money()
    def bag(self): return {i.slug: i.quantity for i in self.player.items}
    def party(self, npc=None): return [mon_info(m) for m in (npc or self.player).monsters]
    def vars(self):   # values that are fresh uuids (e.g. add_monster's instance id) differ every run: masked
        return {k: ("<uuid>" if re.fullmatch(r"[0-9a-f]{32}", str(v)) else str(v)) for k, v in self.player.game_variables.items()}
    def info(self):
        return {"map": self.mapname(), "tile": self.tile(), "facing": str(self.player.facing).split(".")[-1],
                "states": self.states(), "money": self.money(), "party": self.party(), "bag": self.bag()}
    def ui(self):
        st = self.c.current_state
        if st is None: return {}
        d = {"state": type(st).__name__}
        box = getattr(st, "dialog_box", None)
        if box is not None: d["text"] = getattr(box, "_text", "")
        m = getattr(st, "menu", None)
        if m is not None and hasattr(m, "get_widgets"):
            try:
                ws = m.get_widgets(); d["items"] = [w.get_title() for w in ws]
                sw = m.get_selected_widget(); d["sel"] = ws.index(sw) if sw in ws else None
            except Exception as e: d["menu_err"] = str(e)[:80]
        mi = getattr(st, "menu_items", None)
        if mi is not None and "items" not in d:
            try:
                d["items"] = [_label(i) for i in mi]; d["sel"] = getattr(st, "selected_index", None)
            except Exception: pass
        return d
    def choose(self, label, max_moves=40, key="down"):
        for _ in range(max_moves):
            u = self.ui(); items = u.get("items") or []
            if u.get("sel") is not None and u["sel"] < len(items) and str(items[u["sel"]]).upper() == label.upper():
                self.key("return", after=30); return True
            self.key(key, after=6)
        return False
    def advance(self, answers=None, max_steps=200, log=None, stop=None):
        """press return through dialogs; answer menus from answers {label in menu: label to pick}; stop at WorldState"""
        answers = answers or {}
        for i in range(max_steps):
            u = self.ui()
            if stop and stop(u): return u
            if u.get("state") == "WorldState": return u
            if log is not None and (not log or log[-1] != u): log.append(u)
            items = [str(x).upper() for x in (u.get("items") or [])]
            picked = next((v for k, v in answers.items() if k.upper() in items), None)
            if picked and u.get("state") != "DialogState": self.choose(picked)
            else: self.key("return", after=30)
        return self.ui()
    def walk(self, d, n=1, max_frames=60):
        """n single-tile steps with real arrow-key taps; returns the tile (or [map, x, y] after a map change) after each"""
        out = []
        for _ in range(n):
            t0 = self.tile(); m0 = self.mapname()
            self._ev(self.pg.KEYDOWN, d)
            f = 0
            while f < 40 and self.tile() == t0 and self.mapname() == m0: self.step(1); f += 1
            self._ev(self.pg.KEYUP, d)
            f = 0
            while f < max_frames and (self.player.moving or f < 3): self.step(1); f += 1
            self.step(2)
            out.append(self.tile() if self.mapname() == m0 else [self.mapname()] + self.tile())
        return out
    def settle(self, frames=60): self.step(frames)

class OpeningChanged(RuntimeError):
    """the stock opening did not run as in vanilla (raised by new_game only, never by the game)"""

def type_name(g, check=False):
    """in the name InputMenu: letter A, then END. check: press A again while the field is still empty (the InputMenu drops the
    first letter it gets when nothing pressed a key since it opened, e.g. when an event script opens it)"""
    g.key("return", after=10)                               # letter A
    ic = getattr(g.c.current_state, "input_controller", None)
    for _ in range(3 if check else 0):
        if ic is None or ic.current_string: break
        g.key("return", after=10)
    for k in ["down"] * 5 + ["right"]: g.key(k, after=6)  # END
    g.key("return", after=30)

def new_game(g, log=None, campaign="TUXEMON: SPYDER AND THE CATHEDRAL"):
    """real title flow: campaign menu -> pronoun -> skip intro (YES) -> name 'A' -> starter form (Budaye) -> bedroom"""
    g.step(30)
    g.choose(campaign)
    g.step(60)
    g.advance({"NO": "YES", "HE/HIM": "HE/HIM"}, max_steps=80, log=log, stop=lambda u: u.get("state") == "InputMenu")
    if g.top() != "InputMenu": raise OpeningChanged("no name input: " + str(g.ui()))
    type_name(g)
    g.advance({"PICK": "PICK"}, max_steps=150, log=log)
    g.settle(120)

# ---- start of the probes that need a game in progress (2026-11-06). The devkit lets a mod change the new-game opening; those
# probes measure walking, warps, battles, menus, saving ... and not the opening (TX-NEWGAME does), so when the stock opening
# does not end where vanilla's does, they start from a stock state built with the game's own actions (stock_start) instead of
# failing as could-not-test. Vanilla (and any mod that keeps the opening) always takes the new_game path.
TOUCHED = {x for x in os.environ.get("TX_MOD_TOUCHED", "").split(",") if x}     # files the mod puts in mods/tuxemon/maps/
def touched(mapname):
    """the mod adds or replaces this map's .tmx or its .yaml events"""
    stem = mapname.rsplit(".", 1)[0]
    return f"{stem}.tmx" in TOUCHED or f"{stem}.yaml" in TOUCHED

# vanilla right after new_game (measured 2026-11-06): bedroom (3,4) facing down, name A, $500, no monster, empty bag, 9 steps
STOCK = {"map": "spyder_bedroom.tmx", "tile": [3, 4], "facing": "DOWN", "money": 500, "party": [], "bag": {}}
STOCK_NAME, STOCK_STEPS = "A", 9.0
STOCK_VARS = {"scenario_choice": "spyder_campaign", "race_choice": "white_male", "pronoun_choice": "pronoun_he",
              "gender_choice": "gender_male", "method_money": "conserved", "cathedral_share": "0.5", "cathedral_interest": "0.1",
              "cathedral_fee": "100", "scoop_coeff": "50", "question_intro": "yes", "spyder_intro": "yes", "choice_phase": "progress",
              "myintrochoice": "budaye", "billie_choice": "budaye", "areyousure": "yes", "intro_scoop": "done"}
HOMES = [("spyder_bedroom.tmx", 3, 4), ("spyder_downstairs.tmx", 2, 4), ("spyder_paper_town.tmx", 10, 8)]
START_SEED = 1004

def off_stock(g):
    """None if the game is where vanilla's new_game leaves it, else what differs"""
    if g.top() != "WorldState" or g.c.event_engine.running_events: return f"no control after the opening: {g.ui()}"
    i = g.info(); d = {k: i.get(k) for k in STOCK if i.get(k) != STOCK[k]}
    if g.player.name != STOCK_NAME: d["name"] = g.player.name
    if g.player.steps != STOCK_STEPS: d["steps"] = g.player.steps
    return f"opening ends off the vanilla state: {d}" if d else None

OPENING_ANSWERS = {"NO": "YES", "HE/HIM": "HE/HIM", "PICK": "PICK"}
def stock_start(g, why, log=None):
    """play the mod's opening to the first moment the player has control (real keys: return through dialogs, the vanilla
    answers in menus, FIGHT in battles, name A in a name input), then set the vanilla post-opening state with the game's own
    actions / player API: the name through the real rename_player input, party, bag, money, tuxepedia, steps, playtime, the
    vanilla opening's variables (the mod's own stay), and teleport to the first stock home map the mod does not change"""
    log = [] if log is None else log
    calm = same = 0; prev = None
    for i in range(400):
        u = g.ui(); st = u.get("state")
        same = same + 1 if u == prev else 0; prev = u
        if same >= 12 and st != "WorldState": break           # a screen of the mod that keys do not leave (its own game state)
        if st == "WorldState":
            g.step(30)
            if g.top() != "WorldState": continue
            if not g.c.event_engine.running_events: break
            calm += 1
            if calm >= 20: break                              # an event of the mod that never ends: control is there anyway
            continue
        if not log or log[-1] != u: log.append(u)
        if st == "InputMenu": type_name(g, check=True); continue
        if st == "MainCombatMenuState": g.choose("FIGHT"); continue
        items = [str(x).upper() for x in (u.get("items") or [])]
        picked = next((v for k, v in OPENING_ANSWERS.items() if k in items), None)
        if picked and st != "DialogState": g.choose(picked); continue
        g.key("return", after=30)
    removed = []
    for _ in range(10):                                       # still on such a screen above the world: the game's own remove_state
        if g.top() == "WorldState" or "WorldState" not in g.states(): break
        removed.append(g.top()); g.act("remove_state"); g.step(30)
    if g.top() != "WorldState": raise RuntimeError(f"stock start: no control of the player after the mod's opening: {g.ui()}")
    if g.player.name != STOCK_NAME:
        g.script("rename_player player", wait=False); g.step(20)
        if g.top() != "InputMenu": raise RuntimeError(f"stock start: rename_player shows no name input: {g.ui()}")
        type_name(g, check=True); g.step(30)
        if g.player.name != STOCK_NAME: raise RuntimeError(f"stock start: name input gave {g.player.name!r}")
    g.player.party.clear_party(); g.player.bag.clear_items()
    g.act("clear_tuxepedia"); g.script("set_money player,500")
    for k, v in STOCK_VARS.items(): g.act("set_variable", f"{k}:{v}")
    g.player.steps = STOCK_STEPS; g.s.reset_time()
    home = next((h for h in HOMES if not touched(h[0])), HOMES[0])
    g.act("teleport", "player", *home); g.act("char_face", "player", "down"); g.settle(120)
    if g.top() != "WorldState": raise RuntimeError(f"stock start: no control at {home}: {g.ui()}")
    g.start = {"mode": "stock", "why": why[:300], "home": home[0], "opening": trace_of(log)[:12], **({"removed": removed} if removed else {})}

def start_game(g):
    """new_game; if the mod changed the opening (no name input, or it ends off the vanilla state) stock_start. Both end with the
    RNG re-seeded, so what follows does not depend on how many random draws the opening made"""
    log = []
    try:
        new_game(g, log); why = off_stock(g)
    except OpeningChanged as e: why = str(e)
    if why: stock_start(g, why, log)
    else: g.start = {"mode": "new_game"}
    random.seed(START_SEED)

# ------------------------------------------------------------------------------------------------ helpers
def npcs(g): return {k: [int(v.tile_pos[0]), int(v.tile_pos[1])] for k, v in sorted(g.c.npc_manager.npcs.items())}

def trace_of(log):
    out = []
    for u in log:
        k = u.get("state", "") + ":" + (u.get("text") or "|".join(str(x) for x in (u.get("items") or [])))
        if not out or out[-1] != k: out.append(k)
    return out

def walk_route(g, route, retries=12):
    """route = [(dir, n)]; each step retried while blocked (wandering NPCs), stops on a map change"""
    arrivals = []
    for d, n in route:
        for _ in range(n):
            m0, t0 = g.mapname(), g.tile()
            for _r in range(retries):
                g.walk(d, 1)
                if g.mapname() != m0 or g.tile() != t0: break
                g.step(30)
            if g.mapname() != m0:
                g.settle(90); arrivals.append([g.mapname()] + g.tile() + [g.info()["facing"]]); break
    return arrivals

def goto(g, dest, max_tries=4):
    """walk to tile dest with real arrow keys; the route comes from the game's own pathfinder (Pathfinder.pathfind)"""
    dest = tuple(dest); m0 = g.mapname()
    for _ in range(max_tries):
        cur = tuple(g.tile())
        if cur == dest: return True
        path = g.c.pathfinder.pathfind(cur, dest, g.player.facing)
        if not path: g.step(30); continue
        path = [tuple(int(v) for v in p) for p in path]
        if path[0] == cur: path = path[1:]
        if path and abs(path[0][0] - cur[0]) + abs(path[0][1] - cur[1]) != 1: path = path[::-1]
        if path and path[0] == cur: path = path[1:]
        for nxt in path:
            cur = tuple(g.tile())
            dx, dy = nxt[0] - cur[0], nxt[1] - cur[1]
            d = {(1, 0): "right", (-1, 0): "left", (0, 1): "down", (0, -1): "up"}.get((dx, dy))
            if d is None: break
            for _r in range(8):
                g.walk(d, 1)
                if tuple(g.tile()) != cur or g.mapname() != m0: break
                g.step(30)
            if g.mapname() != m0: return False
            if tuple(g.tile()) != nxt: break
    return tuple(g.tile()) == dest

def turn(g, d): g.key(d, hold=2, after=12)

def autoplay(g, max_steps=300, log=None, fight=True, answers=None):
    """play through dialogs / menus / battles with real keys: FIGHT + first technique in combat, YES in yes/no menus"""
    answers = answers or {"YES": "YES"}
    for i in range(max_steps):
        u = g.ui(); st = u.get("state")
        if st == "WorldState":
            g.step(20)
            if g.top() == "WorldState": return i
            continue
        if log is not None and (not log or log[-1] != u): log.append(u)
        items = [str(x).upper() for x in (u.get("items") or [])]
        if st == "MainCombatMenuState" and fight: g.choose("FIGHT"); continue
        picked = next((v for k, v in answers.items() if k.upper() in items), None)
        if picked and st not in ("DialogState",): g.choose(picked); continue
        g.key("return", after=30)
    return None

def to_world(g, n=8):
    for _ in range(n):
        if g.top() == "WorldState": return
        g.key("escape", after=30)

def setup_town(g, x=10, y=8, mon=("rockitten", 20)):
    if mon: g.act("add_monster", mon[0], mon[1])
    g.act("teleport", "player", "spyder_paper_town.tmx", x, y); g.settle(90)

# ------------------------------------------------------------------------------------------------ probes
def p_new_game(g, shots):
    log = []; t = _real_time()
    try: new_game(g, log)
    except OpeningChanged as e:      # the opening differs from vanilla: a result (TX-NEWGAME fails), not a could-not-test
        return {"trace": trace_of(log), "end": g.info(), "name": g.player.name, "opening_changed": str(e)[:300]}
    if shots: g.shot(shots + "/new_game.png")
    return {"trace": trace_of(log), "end": g.info(), "name": g.player.name, "vars": g.vars(), "npcs": npcs(g),
            "wall_s": round(_real_time() - t, 1)}

def _campaign(g, shots, label, tag):
    log = []
    g.step(30); g.choose(label); g.step(60)
    for i in range(14):
        u = g.ui()
        if not log or log[-1] != u: log.append(u)
        if u.get("state") == "InputMenu": break
        if u.get("state") == "ChoiceState" and u.get("items") and "NO" in [str(x).upper() for x in u["items"]]: g.choose("NO"); continue
        g.key("return", after=40)
    if shots: g.shot(shots + f"/campaign_{tag}.png")
    return {"trace": trace_of(log), "map": g.mapname(), "tile": g.tile(), "vars": g.vars()}
def p_campaign_xero(g, shots): return _campaign(g, shots, "TUXEMON: XERO", "xero")
def p_campaign_waves(g, shots): return _campaign(g, shots, "TUXEMON: WAVES OF CHAOS", "waves")

WALK = [("up", 3), ("right", 6), ("down", 3), ("left", 6), ("down", 3), ("right", 8), ("up", 1)]
# two more stock maps, measured in every run (2026-11-06): with no wandering NPC on the route (the downstairs mom wanders, so
# no downstairs route), each walked from a teleport with the RNG re-seeded
WALK_ALT = {"spyder_paper_town.tmx": ((10, 8), [("left", 4), ("down", 4), ("right", 9), ("up", 4), ("right", 3)]),
            "spyder_paper_rival_bedroom.tmx": ((3, 5), [("left", 4), ("up", 3), ("right", 5), ("down", 3), ("left", 2)])}
def _route(g, route): return [[d] + g.walk(d, n) for d, n in route]
def p_walk(g, shots):
    """the bedroom route right after the opening (stock start: only if the mod leaves the bedroom alone), then the WALK_ALT
    routes on the maps the mod does not change"""
    start_game(g); stock = g.start["mode"] == "stock"
    r = {}
    if not stock or not touched("spyder_bedroom.tmx"):
        out = _route(g, WALK)
        if shots: g.shot(shots + "/walk.png")
        r.update({"start": [3, 4], "steps": out, "end": g.tile(), "facing": g.info()["facing"], "map": g.mapname()})
    alt = {}
    for m, ((x, y), route) in WALK_ALT.items():
        if touched(m): alt[m] = "skipped: the mod changes this map"; continue
        g.act("teleport", "player", m, x, y); g.act("char_face", "player", "down"); g.settle(90); random.seed(7)
        alt[m] = {"start": [x, y], "steps": _route(g, route), "end": g.tile(), "facing": g.info()["facing"], "map": g.mapname()}
    r["alt"] = alt
    if stock and "steps" not in r and not any(isinstance(v, dict) for v in alt.values()):
        return {"not_applicable": "the mod changes the opening and every map of the walk routes: " + ", ".join(["spyder_bedroom.tmx", *WALK_ALT])}
    return r

# warp legs: (map, tile next to the door / stairs, the step onto it, map it leads to)
LEGS = [("spyder_bedroom.tmx", (8, 2), "left", "spyder_downstairs.tmx"),        # bedroom: stairs tile (7,2)
        ("spyder_downstairs.tmx", (4, 5), "down", "spyder_paper_town.tmx"),     # downstairs: front door (4,6)
        ("spyder_paper_town.tmx", (19, 13), "up", "spyder_paper_scoop.tmx"),    # paper town: mart door (19,12)
        ("spyder_paper_scoop.tmx", (6, 9), "down", "spyder_paper_town.tmx"),    # mart: exit (6,10)
        ("spyder_paper_town.tmx", (10, 7), "up", "spyder_downstairs.tmx"),      # paper town: house door (10,6)
        ("spyder_downstairs.tmx", (0, 2), "up", "spyder_bedroom.tmx")]          # downstairs: stairs (0,1) -> bedroom
def p_warp(g, shots):
    """new_game: one walk through all legs from the bedroom (as since 2026-10-30). Stock start: each leg on its own (teleport
    next to the door, face, step), only the legs whose two maps the mod does not change"""
    start_game(g)
    arr = []
    def arrive(m0, dest, last):
        if g.mapname() != m0: arr.append([m0, list(dest), last, "->", g.mapname()] + g.tile() + [g.info()["facing"]])
        else: arr.append([m0, list(dest), last, "-> no warp", g.tile()])
    if g.start["mode"] == "stock":
        for k, (m0, dest, last, m1) in enumerate(LEGS):
            if touched(m0) or touched(m1): arr.append(["skipped", m0, m1, "the mod changes this map"]); continue
            g.act("teleport", "player", m0, *dest); g.act("char_face", "player", last); g.settle(90)
            g.walk(last, 1); g.settle(90); arrive(m0, dest, last)
            if shots and k == 1: g.shot(shots + "/warp_town.png")
        if not any(a[0] != "skipped" for a in arr):
            return {"not_applicable": "the mod changes the opening and a map of every warp leg"}
        return {"arrivals": arr, "end": g.info()["map"]}
    for k, (m0, dest, last, m1) in enumerate(LEGS):
        goto(g, dest); m = g.mapname(); g.walk(last, 1); g.settle(90); arrive(m, dest, last)
        if shots and k == 1: g.shot(shots + "/warp_town.png")
    return {"arrivals": arr, "end": g.info()["map"]}

def p_talk(g, shots):
    start_game(g)
    res = {}
    def talk(tag, mapname, x, y, face):
        if mapname: g.act("teleport", "player", mapname, x, y); g.settle(90)
        turn(g, face); n0 = len(MESSAGES)
        g.key("return", after=40)
        res[tag + "_state"] = g.top()
        if shots: g.shot(shots + f"/talk_{tag}.png")
        g.advance(max_steps=20)
        res[tag] = MESSAGES[n0:]
    talk("tv", "spyder_downstairs.tmx", 1, 5, "up")                  # object event (TV)
    talk("sign", "spyder_paper_town.tmx", 12, 2, "up")               # town sign
    talk("homemaker", "spyder_paper_town.tmx", 10, 13, "down")      # static NPC (Silver)
    res["npcs_town"] = npcs(g)
    return res

def p_story(g, shots):
    """paper town: walk into Dante's trigger row, pick Rockitten at the pen, first trainer fight with Billie"""
    start_game(g)
    g.act("teleport", "player", "spyder_paper_town.tmx", 24, 16); g.settle(90)
    log = []
    n0 = len(MESSAGES)
    goto(g, (24, 14)); walk_route(g, [("up", 1)])
    autoplay(g, 200, log)
    r = {"dante": MESSAGES[n0:], "after_dante": {"tile": g.tile(), "vars": {k: v for k, v in g.vars().items() if "dante" in k}}}
    g.act("teleport", "player", "spyder_paper_town.tmx", 23, 9); g.settle(30)
    random.seed(1003)
    n0 = len(MESSAGES); log2 = []
    turn(g, "left"); g.key("return", after=40)
    autoplay(g, 400, log2)
    for _ in range(3):                      # the first fight starts by itself once the pick sets firstfightdue
        g.step(120)
        if g.top() == "WorldState" and "CombatState" not in g.states(): 
            if autoplay(g, 400, log2) == 0: break
    r["pick_and_fight"] = MESSAGES[n0:]
    r["trace"] = trace_of(log2)
    r["party"] = g.party(); r["bag"] = g.bag(); r["money"] = g.money()
    r["vars"] = {k: v for k, v in g.vars().items() if k in ("mymonchoice", "firstfightdue", "firstfightend", "battle_last_result",
                                                            "rockittenchosen", "dantebin", "dantefirst")}
    r["battles"] = [str(b) for b in getattr(g.player.battle_handler, "battles", [])][:5]
    if shots: g.shot(shots + "/story_end.png")
    return r

def _battle(g, encounter, use_items=(), max_steps=400):
    random.seed(1001)      # re-seed right before the measured fight: earlier random draws (e.g. a mod's extra NPC) do not shift it
    n0 = len(MESSAGES)
    g.script(encounter, wait=False); g.step(30)
    turns = []; items = list(use_items)
    for i in range(max_steps):
        u = g.ui(); st = u.get("state")
        if st == "WorldState":
            g.step(20)
            if g.top() == "WorldState": break
            continue
        if st == "MainCombatMenuState":
            turns.append([m["hp"] for m in g.party()])
            if items:
                it = items.pop(0); g.choose("ITEM"); g.step(10)
                if not g.choose(it): to_world(g, 1)
                g.step(10)
                if g.top() == "MonsterMenuState": g.key("return", after=30)
            else:
                g.choose("FIGHT"); g.step(10)
            continue
        g.key("return", after=30)
    return {"messages": MESSAGES[n0:], "turns": len(turns), "hp_per_turn": turns, "party": g.party(), "bag": g.bag(),
            "money": g.money(), "state": g.top()}

def p_battle(g, shots):
    start_game(g); setup_town(g)
    r = _battle(g, "wild_encounter nut,4")
    if shots: g.shot(shots + "/battle_end.png")
    return r

def p_capture(g, shots):
    start_game(g); setup_town(g); g.act("add_item", "tuxeball", 6)
    r = _battle(g, "wild_encounter nut,4", use_items=["Tuxeball"] * 6)
    r["captured"] = [m["slug"] for m in r["party"][1:]]
    return r

def p_items(g, shots):
    start_game(g); g.act("add_monster", "rockitten", 10); g.act("add_item", "potion", 2); g.script("set_monster_health ,0.3")
    hp0 = g.party()[0]["hp"]
    g.key("escape", after=40); g.choose("BAG"); g.step(20)
    bag_items = g.ui().get("items")
    g.key("return", after=40); choice = g.ui().get("items")
    g.key("return", after=40); g.key("return", after=40)
    n0 = len(MESSAGES); g.step(20)
    msgs = MESSAGES[-2:]
    to_world(g)
    return {"bag_menu": bag_items, "item_choice": choice, "hp_before": hp0, "hp_after": g.party()[0]["hp"],
            "bag_after": g.bag(), "messages": msgs}

def p_shop(g, shots):
    start_game(g); g.script("set_money player,5000"); g.act("add_item", "potion", 3)
    g.act("teleport", "player", "spyder_candy_scoop.tmx", 3, 6); g.settle(90)
    turn(g, "left"); n0 = len(MESSAGES); g.key("return", after=40)
    r = {"npc_state": g.top()}
    g.advance(max_steps=5, stop=lambda u: u.get("state") == "ShopChoiceState")
    r["choice"] = g.ui().get("items"); m0 = g.money()
    g.choose("BUY"); g.step(20); r["buy_list"] = g.ui().get("items")
    g.choose("Mega Potion"); g.step(10)
    g.key("right", after=8); g.key("right", after=8)
    r["picker"] = g.ui().get("items")
    if shots: g.shot(shots + "/shop_picker.png")
    g.key("return", after=40); g.advance(max_steps=4, stop=lambda u: u.get("state") in ("ShopItemBuyMenuState", "ShopChoiceState"))
    r["money_after_buy"] = g.money(); r["bag_after_buy"] = g.bag()
    g.key("escape", after=30)
    if g.top() == "ShopChoiceState" and "SELL" in [str(x).upper() for x in g.ui().get("items") or []]:
        g.choose("SELL"); g.step(20); r["sell_list"] = g.ui().get("items")
        g.choose("Potion"); g.step(10); g.key("return", after=40)
        g.advance(max_steps=4, stop=lambda u: u.get("state") in ("ShopItemSellMenuState", "ShopChoiceState"))
    r["money_after_sell"] = g.money(); r["bag_end"] = g.bag(); r["money_start"] = m0
    r["messages"] = MESSAGES[n0:]
    to_world(g)
    return r

def p_menu(g, shots):
    start_game(g); g.act("add_monster", "rockitten", 10); g.act("add_item", "potion", 1)
    g.key("escape", after=40)
    r = {"world_menu": [x for x in g.ui().get("items", []) if x]}
    if shots: g.shot(shots + "/menu.png")
    for lab in ["TUXEMON", "BAG", "PLAYER", "OPTIONS"]:
        g.choose(lab); g.step(30)
        u = g.ui(); r[lab] = {"state": u.get("state"), "items": [x for x in (u.get("items") or []) if x][:20]}
        for _ in range(4):
            if g.top() == "WorldMenuState": break
            g.key("escape", after=30)
        r[lab]["back"] = g.top()
    g.key("escape", after=30); r["closed"] = g.top()
    return r

def p_save(g, shots):
    start_game(g); setup_town(g, 12, 8); g.act("add_item", "potion", 3); g.script("set_money player,1234")
    g.script("set_variable txprobe:saved")
    snap = {"info": g.info(), "vars": g.vars()}
    g.key("escape", after=40); g.choose("SAVE"); g.step(30)
    g.key("return", after=60); saved_msg = g.ui().get("text")
    to_world(g)
    return {"saved": snap, "save_msg": saved_msg, "files": sorted(os.listdir(os.path.expanduser("~/.tuxemon/saves")))}

def p_load(g, shots):
    """second process in the same HOME as p_save: real title screen (intro -> start menu LOAD -> slot 1)"""
    g.step(60); log = []
    for _ in range(12):
        u = g.ui()
        if u.get("state") == "WorldState": break
        if not log or log[-1] != u: log.append(u)
        items = [str(x).upper() for x in (u.get("items") or [])]
        if "LOAD" in items: g.choose("LOAD"); g.step(30); continue
        g.key("return", after=60)
    g.settle(120)
    if shots: g.shot(shots + "/loaded.png")
    return {"trace": trace_of(log), "info": g.info(), "vars": g.vars()}

def p_encounter(g, shots):
    start_game(g); g.act("add_monster", "rockitten", 10)
    g.act("teleport", "player", "spyder_route1.tmx", 22, 12); g.settle(90)
    from tuxemon.database.runtime import db
    table = sorted({str(getattr(e, "monster", None) or (e.get("monster") if isinstance(e, dict) else e))
                    for e in (db.lookup("spyder_route1", table="encounter").monsters or [])})
    random.seed(1002)
    steps = 0; found = None
    for i in range(60):
        d = "right" if (i // 3) % 2 == 0 else "left"
        g.walk(d, 1); steps += 1; g.step(20)
        if "CombatState" in g.states():
            g.step(60)
            cs = g.c.get_state_by_name("CombatState")
            opp = [m.slug for m in g.c.combat_session.players[1].monsters] if hasattr(g.c, "combat_session") else None
            found = {"steps": steps, "opponent": opp, "messages": [m for m in MESSAGES if "wild" in m.lower()][-1:]}
            break
    if shots: g.shot(shots + "/encounter.png")
    return {"found": found, "steps": steps, "table": table}

def p_db(g, shots):
    import hashlib
    from tuxemon.database.runtime import db
    out = {}
    for table, entries in sorted(db.database.items()):
        t = {}
        for slug, model in entries.items():
            try: blob = json.dumps(model.model_dump(mode="json"), sort_keys=True, default=str)
            except Exception: blob = repr(model)
            t[slug] = hashlib.sha1(blob.encode()).hexdigest()[:12]
        out[table] = t
    mons = db.database.get("monster", {})
    stats = {}
    for slug, m in sorted(mons.items()):
        d = m.model_dump(mode="json")
        stats[slug] = {k: d.get(k) for k in ("types", "shape", "catch_rate", "moveset") if k in d}
    return {"hash": out, "counts": {k: len(v) for k, v in out.items()}, "monster": stats}

def p_maps(g, shots):
    from tuxemon.constants.paths import mods_folder
    mdir = mods_folder / "tuxemon" / "maps"
    res = {}
    for p in sorted(mdir.glob("*.tmx")):
        try:
            md = g.c.map_loader.load_map_data(str(p))
            ev = getattr(md, "events", None); ini = getattr(md, "inits", None)
            res[p.name] = {"ok": True, "events": len(ev) if ev is not None else None, "inits": len(ini) if ini is not None else None,
                           "size": list(getattr(md, "size", []) or [])}
        except Exception as e:
            res[p.name] = {"ok": False, "err": f"{type(e).__name__}: {str(e)[:120]}"}
        g.c.map_loader.clear_cache()
    return {"maps": res, "n": len(res), "n_ok": sum(1 for v in res.values() if v["ok"])}

def p_gate(g, shots):
    """gab gate (not a pool probe): the game reached its title screen and keeps running; the database loads; every map the
    mod adds or replaces (TX_MOD_MAPS, comma list) loads"""
    g.step(GATE_FRAMES)
    from tuxemon.database.runtime import db
    from tuxemon.constants.paths import mods_folder
    res = {"top": g.top(), "states": g.states(), "db_counts": {t: len(e) for t, e in sorted(db.database.items())}, "maps": {}}
    for m in [x for x in os.environ.get("TX_MOD_MAPS", "").split(",") if x]:
        try:
            md = g.c.map_loader.load_map_data(str(mods_folder / "tuxemon" / "maps" / m))
            res["maps"][m] = {"ok": True, "events": len(getattr(md, "events", None) or [])}
        except Exception as e: res["maps"][m] = {"ok": False, "err": f"{type(e).__name__}: {str(e)[:200]}"}
        g.c.map_loader.clear_cache()
    if shots: g.shot(shots + "/gate.png")
    return res

PROBES = {k[2:]: v for k, v in globals().items() if k.startswith("p_")}

if __name__ == "__main__":
    name, out = sys.argv[1], sys.argv[2]
    shots = sys.argv[3] if len(sys.argv) > 3 else None
    res = {"probe": name}
    try:
        t0 = _real_time()
        g = Game(seed=int(os.environ.get("TX_SEED", "1")), title=(name in ("load", "gate")))
        res["init_s"] = round(_real_time() - t0, 1)
        res["data"] = PROBES[name](g, shots)
        if isinstance(res["data"], dict) and getattr(g, "start", None): res["data"]["_start"] = g.start   # how the game was started
        res["ok"] = True
    except Exception as e:
        res["ok"] = False; res["error"] = traceback.format_exc()[-2000:]
        # who raised it (2026-11-06): "game" = the game's own code (or a library it called) -> a crash a player would see;
        # "probe" = this script (a setup step that found the game not as expected) -> the probe could not test, not a crash
        tb = traceback.extract_tb(e.__traceback__); me = os.path.abspath(__file__)
        last = max([k for k, f in enumerate(tb) if os.path.abspath(f.filename) == me] or [-1])
        res["raised_by"] = "game" if last < len(tb) - 1 else "probe"
    json.dump(res, open(out, "w"), indent=1, default=str)
    print("TXPROBE_DONE", name, res["ok"])
'''

SUITE_SH = r'''#!/bin/bash
# suite.sh INST PAR job...   job = probe name, or "save+load" (sequential, same HOME). Per job: INST/out/<p>.json + .log
I=$1; P=$2; shift 2; mkdir -p $I/out $I/shots
run1() { j=$1; first=${j%%+*}; rm -rf $I/home_$first; mkdir -p $I/home_$first
  for p in ${j//+/ }; do
    HOME=$I/home_$first TX_SRC=$I/src TX_MOD_MAPS="$(cat $I/mod_maps.txt 2>/dev/null)" TX_MOD_TOUCHED="$(cat $I/mod_touched.txt 2>/dev/null)" PYTHONPATH=__ROOT__/venv/lib/python3.12/site-packages SDL_VIDEODRIVER=dummy SDL_AUDIODRIVER=dummy \
      timeout -s KILL 480 python3 -u $I/txrun.py $p $I/out/$p.json $I/shots > $I/out/$p.log 2>&1
    echo "$? $p" >> $I/out/rc.txt
  done; }
export -f run1; export I
printf "%s\n" "$@" | xargs -P $P -I{} bash -c 'run1 {}'
echo SUITE_DONE
'''

ERR = re.compile(r" - (ERROR|CRITICAL) - |^Traceback|Segmentation fault|Fatal Python error")


def error_text(logtxt):
    """the error lines of a run log as written (for the gate log): ERROR / CRITICAL lines, and of a Python traceback its last
    frame and the exception line"""
    L, out, i = logtxt.splitlines(), [], 0
    while i < len(L):
        if L[i].startswith("Traceback"):
            j = i + 1
            while j < len(L) and L[j].startswith((" ", "\t")): j += 1
            fr = [x.strip() for x in L[i + 1:j] if x.strip().startswith("File ")][-1:]
            out.append("Traceback: " + " / ".join(fr + ([L[j].strip()] if j < len(L) else []))); i = j + 1
        elif ERR.search(L[i]): out.append(L[i].strip()); i += 1
        else: i += 1
    return out


def errors_in(logtxt, root=ROOT):
    """-> list of normalized error signatures (numbers / hex / paths masked)"""
    out = []
    for line in logtxt.splitlines():
        if ERR.search(line):
            s = re.sub(r"^\[[^\]]*\]\s*", "", line)
            s = re.sub(r"0x[0-9a-f]+|[0-9a-f]{32}|\d+", "#", s)
            s = re.sub(re.escape(root) + r"/runs/[^/ ]+/", "<inst>/", s)
            out.append(s[:200])
    return out


def touched_maps(mods):
    """every file the mods put in mods/tuxemon/maps/ (map .tmx, event .yaml, ...): the runner's TX_MOD_TOUCHED"""
    return sorted({p.name for m in mods for p in (Path(m) / "mods" / "tuxemon" / "maps").glob("*") if p.is_file()})


def crashed(r):
    """a probe run that crashed or hung: no result (killed by the timeout, segfault ...) or an exception raised in the game's
    own code. An exception the probe script raised itself (a setup step found the game not as expected) is not a crash: the
    probe could not test (2026-11-06; until then every failed run counted as a crash for TX-STABLE)"""
    return not r.get("ok") and r.get("raised_by") != "probe"


class TXProbes:
    def __init__(self, seat, root=ROOT, mods=(), label="vanilla", par=4, log=print, keep=False):
        self.seat, self.root, self.mods, self.label, self.par, self.log, self.keep = seat, root, [Path(m) for m in mods], label, par, log, keep
        self.inst = f"{root}/runs/{label}-{time.strftime('%m%d-%H%M%S')}"

    # -- sandbox plumbing (gab Seat)
    def sh(self, cmd, timeout=900): return self.seat.exec(cmd, timeout=timeout).stdout

    def put(self, remote, text): self.seat.put(remote, text)

    def setup(self, mod_maps=()):
        """bridge files + a fresh instance (hard-link copy of vanilla src/, mods copied over it)"""
        self.sh(f"mkdir -p {self.inst} && cp -al {self.root}/src {self.inst}/src")
        self.put(f"{self.inst}/txrun.py", RUNNER.replace("__GATE_FRAMES__", str(GATE_FRAMES)))   # per instance: parallel suites never share a runner
        self.put(f"{self.inst}/suite.sh", SUITE_SH.replace("__ROOT__", self.root))
        self.put(f"{self.inst}/mod_maps.txt", ",".join(mod_maps))
        self.put(f"{self.inst}/mod_touched.txt", ",".join(touched_maps(self.mods)))
        if self.sh(f"test -f {self.inst}/src/mods/tuxemon/mod.yaml && echo ok").strip() != "ok":
            raise RuntimeError(f"instance copy failed: {self.inst}")
        for k, m in enumerate(self.mods):
            if not m.is_dir(): raise RuntimeError(f"mod {m} is not a directory")
            d = f"{self.inst}/mod_{k}_{m.name}"
            self.seat.copy_in(m, d)
            out = self.sh(f"cp -r --remove-destination {d}/. {self.inst}/src/ && echo ok")
            if "ok" not in out: raise RuntimeError(f"mod {m} was not installed: {out[-300:]}")
        self.log(f"[tx] instance {self.inst} ({len(self.mods)} mod(s))")

    def cleanup(self):
        if not self.keep: self.sh(f"rm -rf {self.inst}/src {self.inst}/home_* {self.inst}/shots {self.inst}/mod_*")

    def run_all(self, names=None, mod_maps=()):
        names = list(names or PROBES)
        try: return self._run_all(names, mod_maps)
        finally: self.cleanup()

    def _run_all(self, names, mod_maps):
        self.setup(mod_maps)
        jobs = []
        for n in names:
            if n == "log_errors": continue
            subs = SUB.get(n, [n])
            if n == "save_load": jobs.append("save+load")
            else: jobs += subs
        t0 = time.time()
        out = self.sh(f"bash {self.inst}/suite.sh {self.inst} {self.par} {' '.join(jobs)}", timeout=3600)
        if "SUITE_DONE" not in out: self.log(f"[tx] suite did not finish: {out[-300:]}")
        raw, runs, errs, lines = {}, [], [], []
        for j in jobs:
            for p in j.split("+"):
                txt = self.sh(f"cat {self.inst}/out/{p}.json 2>/dev/null; echo; echo =====; cat {self.inst}/out/{p}.log 2>/dev/null")
                js, _, logtxt = txt.partition("\n=====\n")
                try: r = json.loads(js.strip())
                except Exception: r = {"ok": False, "error": "no result (crash or timeout)"}
                e = errors_in(logtxt, self.root)
                errs += [f"{p}: {x}" for x in e]; lines += [f"{p}: {x}" for x in error_text(logtxt)]
                runs.append({"probe": p, "ok": bool(r.get("ok")), "errors": len(e), "init_s": r.get("init_s"),
                             **({} if r.get("ok") else {"raised_by": r.get("raised_by", "no result"), "crashed": crashed(r)})})
                raw[p] = r.get("data") if r.get("ok") else {"error": (r.get("error") or "failed")[-600:]}
                if not r.get("ok"): self.log(f"[tx] {p}: FAILED {raw[p]['error'][-200:]}")
        res = {}
        for n in names:
            if n == "log_errors": continue
            subs = SUB.get(n)
            if subs:
                bad = [s for s in subs if isinstance(raw.get(s), dict) and "error" in raw[s] and len(raw[s]) == 1]
                res[n] = {"error": f"{bad[0]}: {raw[bad[0]]['error']}"} if bad else {s: raw.get(s) for s in subs}
            else: res[n] = raw.get(n)
        res["log_errors"] = {"errors": len(errs), "signatures": sorted(set(errs)), "runs": runs,
                             "not_finished": [r["probe"] for r in runs if crashed(r)],
                             "probe_failed": [r["probe"] for r in runs if not r["ok"] and not crashed(r)], "wall_s": round(time.time() - t0)}
        res["_instance"] = self.inst; res["_error_lines"] = lines         # raw, for people (gate log); not compared
        return res


# --------------------------------------------------------------------------------------------- compare
def _strip(x):
    if isinstance(x, dict): return {k: _strip(v) for k, v in x.items() if not str(k).startswith("_") and k not in ("wall_s",)}
    if isinstance(x, list): return [_strip(v) for v in x]
    return x

def _diff(b, g, path="", out=None, limit=12):
    """first differences between two JSON values, as {path: 'b -> g'}"""
    out = {} if out is None else out
    if len(out) >= limit: return out
    if isinstance(b, dict) and isinstance(g, dict):
        for k in sorted(set(b) | set(g), key=str):
            if k not in g: out[f"{path}.{k}"] = "missing"
            elif k not in b: out[f"{path}.{k}"] = f"new: {str(g[k])[:80]}"
            else: _diff(b[k], g[k], f"{path}.{k}", out, limit)
            if len(out) >= limit: break
    elif isinstance(b, list) and isinstance(g, list) and len(b) == len(g):
        for i, (x, y) in enumerate(zip(b, g)): _diff(x, y, f"{path}[{i}]", out, limit)
    elif b != g:
        out[path or "."] = f"{str(b)[:100]} -> {str(g)[:100]}"
    return out

WALK_KEYS = ("start", "steps", "end", "facing", "map")

def _battle_view(d):
    """what a fight is compared on: who used what, faint / win lines, captures, final party and bag"""
    keep = [m for m in d.get("messages", []) if re.search(r"used|fainted|won|Gotcha|capture|joined|xp|defeated|wild", m)]
    return {"lines": keep, "party": [(m["slug"], m["level"], m["hp"]) for m in d.get("party", [])], "bag": d.get("bag"),
            "turns": d.get("turns"), "end": d.get("state")}

def compare(name, base, got):
    """-> (ok | None, detail). None = could not test (probe error), or not applicable (detail {"not_applicable": why}: the mod
    changes every stock map the probe could use; regression.py excludes the item)."""
    src = (got.get("save") if name == "save_load" and isinstance(got, dict) else got)
    start = (src.get("_start") or {}) if isinstance(src, dict) else {}
    stock = start.get("mode") == "stock"              # the mod changed the opening: the probe ran from stock_start
    note = f" (stock start: {start.get('why', '')[:120]})" if stock else ""
    base, got = _strip(json.loads(json.dumps(base))), _strip(json.loads(json.dumps(got)))
    if isinstance(got, dict) and "not_applicable" in got: return None, {"not_applicable": got["not_applicable"]}
    if isinstance(got, dict) and "error" in got and len(got) == 1: return None, got["error"]
    if isinstance(base, dict) and "error" in base and len(base) == 1: return None, f"vanilla baseline broken: {base['error']}"
    if got is None: return None, "not probed"
    if name == "log_errors":
        nf = [r for r in got.get("not_finished", []) if r not in base.get("not_finished", [])]
        new = sorted(set(got.get("signatures", [])) - set(base.get("signatures", [])))
        ok = not nf and not new and got.get("errors", 0) <= base.get("errors", 0)
        return ok, {"crashed_or_hung": nf, "errors": f"{base.get('errors')} -> {got.get('errors')}", "new_errors": new[:8],
                    **({"probe_failed": got["probe_failed"]} if got.get("probe_failed") else {})}
    if name == "db":                  # every vanilla entry unchanged; new entries allowed
        diff, added = {}, 0
        for t, entries in base["hash"].items():
            ge = got["hash"].get(t, {})
            for slug, h in entries.items():
                if ge.get(slug) != h: diff[f"{t}.{slug}"] = "missing" if slug not in ge else "changed"
            added += len(set(ge) - set(entries))
        for slug, b in base.get("monster", {}).items():
            gm = got.get("monster", {}).get(slug)
            if gm and gm != b: diff[f"monster.{slug}"] = _diff(b, gm, limit=3)
        return not diff, (dict(list(diff.items())[:15]) | {"_n_changed": len(diff)} if diff else f"unchanged ({added} entries added)")
    if name == "maps":                # every vanilla map that loads in vanilla still loads with the same number of events
        diff = {}
        for m, b in base["maps"].items():
            g = got["maps"].get(m)
            if not b.get("ok"): continue
            if g is None: diff[m] = "missing"
            elif not g.get("ok"): diff[m] = g.get("err")
            elif (g.get("events"), g.get("inits"), g.get("size")) != (b.get("events"), b.get("inits"), b.get("size")):
                diff[m] = f"events/inits/size {b.get('events')}/{b.get('inits')}/{b.get('size')} -> {g.get('events')}/{g.get('inits')}/{g.get('size')}"
        return not diff, (dict(list(diff.items())[:12]) if diff else f"{base['n_ok']} vanilla maps load ({got['n']} maps)")
    if name in ("battle", "capture"):
        b, g = _battle_view(base), _battle_view(got)
        if name == "capture": b["captured"], g["captured"] = base.get("captured"), got.get("captured")
        d = _diff(b, g)
        return not d, (d or f"same fight: {g['turns']} turns, {g['lines'][-1] if g['lines'] else ''}{note}")
    if name == "story":
        keys = ["dante", "after_dante", "trace", "party", "bag", "money", "vars"]
        b = {k: base.get(k) for k in keys}; g = {k: got.get(k) for k in keys}
        b["fight"] = _battle_view({"messages": base.get("pick_and_fight", [])})["lines"]
        g["fight"] = _battle_view({"messages": got.get("pick_and_fight", [])})["lines"]
        d = _diff(b, g)
        return not d, (d or "same cutscene, pick and first trainer fight")
    if name == "encounter":
        f = got.get("found")
        if not f: return False, f"no wild encounter in {got.get('steps')} steps in route 1 grass (vanilla: after {(base.get('found') or {}).get('steps')})"
        opp = (f.get("opponent") or [None])[0]
        ok = opp in base.get("table", []) and f["steps"] <= max(30, 3 * (base.get("found") or {}).get("steps", 10))
        return ok, f"{opp} after {f['steps']} steps (vanilla {(base.get('found') or {}).get('opponent')} after {(base.get('found') or {}).get('steps')}; table {base.get('table')})"
    if name == "save_load":
        g_s, g_l = (got.get("save") or {}), (got.get("load") or {})
        if not g_l or "error" in g_l: return False, f"load failed: {str(g_l.get('error', 'no data'))[-200:]}"
        si, li = g_s.get("saved", {}).get("info", {}), g_l.get("info", {})
        inner = _diff({k: si.get(k) for k in ("map", "tile", "money", "party", "bag")}, {k: li.get(k) for k in ("map", "tile", "money", "party", "bag")})
        sv, lv = g_s.get("saved", {}).get("vars", {}), g_l.get("vars", {})
        lost = [k for k in sv if lv.get(k) != sv[k]]
        if stock:                     # the mod's opening sets its own variables: vs vanilla only on vanilla's variables
            for part, bpart in ((got.get("save", {}).get("saved", {}), ((base.get("save") or {}).get("saved") or {})),
                                (got.get("load", {}), (base.get("load") or {}))):
                bv = set(bpart.get("vars") or {})
                if isinstance(part.get("vars"), dict): part["vars"] = {k: v for k, v in part["vars"].items() if k in bv}
        d = _diff(base, got)
        ok = not inner and not lost and not d
        return ok, (f"saved == loaded == vanilla ({li.get('map')} {li.get('tile')}){note}" if ok else
                    {"saved_vs_loaded": inner, "vars_lost": lost[:8], "vs_vanilla": d})
    if name == "walk":                # every route that ran in both: the bedroom route and the WALK_ALT routes (exact)
        d, ran = {}, []
        if "steps" in got or not stock:
            _diff({k: base.get(k) for k in WALK_KEYS}, {k: got.get(k) for k in WALK_KEYS}, ".spyder_bedroom.tmx", d); ran.append("spyder_bedroom.tmx")
        for m, gv in sorted((got.get("alt") or {}).items()):
            bv = (base.get("alt") or {}).get(m)
            if isinstance(gv, dict) and isinstance(bv, dict): _diff(bv, gv, f".alt.{m}", d); ran.append(m)
        if not ran: return None, "no walk route both in the vanilla baseline and in this run" + note
        return not d, (d or f"same steps as vanilla on {', '.join(ran)}{note}")
    if name == "warp" and stock:      # each leg on its own: the legs that ran vs the same legs of vanilla's walk through all
        d, ran = {}, 0
        for k, a in enumerate(got.get("arrivals") or []):
            if a and a[0] == "skipped": continue
            b = (base.get("arrivals") or [])[k] if k < len(base.get("arrivals") or []) else None
            _diff(b, a, f".arrivals[{k}]", d); ran += 1
        if not ran: return None, {"not_applicable": "no warp leg on maps the mod leaves alone"}
        return not d, (d or f"same arrivals as vanilla on {ran} of {len(got.get('arrivals') or [])} legs{note}")
    d = _diff(base, got)              # exact (deterministic) for everything else
    return not d, (d or ("same as vanilla" + note))

def item_result(spec, base, got):
    """pool `probe:` spec ("x" or "x+y" for several probes, "x.f1+f2" for fields of x) -> (ok | None, detail)"""
    if "." in spec:
        p, fields = spec.split(".", 1)
        b = {f: (base.get(p) or {}).get(f) for f in fields.split("+")}; g = {f: (got.get(p) or {}).get(f) for f in fields.split("+")}
        if isinstance(got.get(p), dict) and "error" in got[p]: return None, got[p]["error"]
        d = _diff(_strip(b), _strip(g)); return not d, (d or "same as vanilla")
    res = [compare(p, base.get(p), got.get(p)) for p in spec.split("+")]
    if any(r[0] is False for r in res): return False, [r[1] for r in res if r[0] is False]
    if any(r[0] is None for r in res):
        cnt = [r[1] for r in res if r[0] is None]
        if all(r[0] is None and isinstance(r[1], dict) and "not_applicable" in r[1] for r in res):   # nothing left to test
            return None, {"not_applicable": "; ".join(x["not_applicable"] for x in cnt)}
        return None, cnt
    return True, [r[1] for r in res] if len(res) > 1 else res[0][1]

def compare_item(spec, base, got):
    """gab contract: pool `probe:` spec + the two run_all() results -> (ok | None, detail)"""
    if "." not in spec and "+" not in spec and spec not in base: return None, "not in the vanilla baseline"
    return item_result(spec, base, got)
