"""Stardew Valley regression probes (pool games/stardew_valley/pool.yaml). Vanilla is the oracle: the same probes run on a fresh
vanilla instance and on the modded one, and compare() checks the modded result against the vanilla one.

All probes run in ONE game session on a fresh probe instance (the start save: spring 1, year 1, 6:00, farmhouse), in ORDER.
Setup steps (warp the farmer, place a stone, move a villager next to the farmer, set the clock) go through the harness bridge;
the measured behaviour is driven by REAL input (xdotool keys / clicks on the game window): walking, tool use, the menu key,
talking, buying, stepping into bed and answering the sleep question. State is read from the running game (bridge `state`).
A probe that raises gives {"error": ...} (could not test); a dead game is restarted once for the remaining probes and recorded
in log_errors.crashed (SD-STABLE fails then)."""
import json, time

ENGINE_KEYS = ["screen", "menu", "location", "location_name", "tile", "facing", "health", "max_health", "stamina", "max_stamina", "money",
               "date", "time", "weather", "inventory", "held", "skills", "riding", "event", "paused", "player", "farm", "quests",
               "friendship", "npcs_here", "flags_new", "mailbox"]
ORDER = ["player_start", "menu_opens", "clock", "walk", "farm_tools", "warp_works", "npc_talk", "shop_buy", "sleep_next_day", "save_reload"]
TOOLS = {"hoe": "Hoe", "can": "Watering Can", "pickaxe": "Pickaxe", "scythe": "Scythe", "axe": "Axe"}
STONE, WEEDS, TWIG = "(O)450", "(O)784", "(O)294"


class ProbeError(Exception): pass


class SDProbes:
    ALL = ORDER

    def __init__(self, ad, inst, log=lambda s: None):
        self.ad, self.inst, self.log = ad, inst, log
        self.slept = False

    # ---- helpers
    def br(self, cmd, timeout=6.0, **kw):
        r = self.ad._br(self.inst, {"cmd": cmd, **kw}, timeout)
        if isinstance(r, dict) and r.get("no_answer"): raise ProbeError(f"bridge {cmd}: {r.get('error')}")
        return r

    def eng(self):
        st = self.br("state")
        e = st.get("engine") if isinstance(st, dict) else None
        if not isinstance(e, dict): raise ProbeError(f"no state: {str(st)[:200]}")
        return e

    def x(self, cmd): return self.ad._x(self.inst, cmd)

    def tap(self, k, hold=0.12):
        """one key press the game sees (it polls the keyboard once per frame: a press + release in the same frame is lost)"""
        self.x(f"xdotool keydown {k} sleep {hold} keyup {k}")

    def hold(self, k, s): self.x(f"xdotool keydown {k} sleep {s} keyup {k}")

    def click(self, x, y): self.x(f"xdotool mousemove {int(x)} {int(y)} sleep 0.15 mousedown 1 sleep 0.12 mouseup 1")

    def park(self): self.x("xdotool mousemove 1270 10")

    def settle(self):
        """no menu, no event, no dialogue (setup, not measured)"""
        for _ in range(4):
            m = self.br("menu")
            if not m.get("menu") and not m.get("event_up") and not m.get("dialogue_up"): break
            self.br("skip_event"); self.br("close_menu"); time.sleep(0.8)
        self.park()

    def wait_eng(self, cond, timeout=10.0, step=0.5):
        t0 = time.time(); e = {}
        while time.time() - t0 < timeout:
            try: e = self.eng()
            except ProbeError: e = {}
            if e and cond(e): return e
            time.sleep(step)
        return e

    def warp(self, loc, x, y, facing=2):
        """setup: put the farmer on a tile. Entering the farmhouse from outside lands on its door whatever the target
        (Game1.warpFarmer), so a second warp inside the location places the farmer exactly."""
        at = lambda e: e.get("location") == loc and e.get("tile") == {"x": x, "y": y}
        e = {}
        for _ in range(2):
            self.br("warp", location=loc, x=x, y=y, facing=facing)
            e = self.wait_eng(lambda e: at(e) and e.get("screen") == "in game", 12)
            if at(e): break
        if not at(e): raise ProbeError(f"warp to {loc} {x},{y} failed: at {e.get('location')} {e.get('tile')}")
        e = self.wait_eng(lambda e: e.get("paused") is False, 6)       # the fade-in is over: the game runs again
        time.sleep(0.4); self.br("face", dir=facing)
        return e

    def slot_of(self, name):
        for it in self.br("inv").get("items") or []:
            if it.get("name") == name: return it["slot"]
        raise ProbeError(f"no {name} in the inventory")

    def select(self, name):
        s = self.slot_of(name)
        if s > 11: raise ProbeError(f"{name} not on the toolbar (slot {s})")
        self.tap("0" if s == 9 else "minus" if s == 10 else "equal" if s == 11 else str(s + 1)); time.sleep(0.3)
        cur = self.br("inv").get("current")
        if cur != s: raise ProbeError(f"selecting {name}: slot {s} wanted, {cur} selected")

    def tile(self, x, y): return self.br("tile", x=x, y=y)

    # ---- probes
    def player_start(self):
        time.sleep(2)
        e = self.eng()
        return {"location": e["location"], "tile": [e["tile"]["x"], e["tile"]["y"]], "money": e["money"], "health": e["health"],
                "stamina": e["stamina"], "items": sorted([k, v] for k, v in (e.get("inventory") or {}).items()),
                "day": f"{e['date']['season']} {e['date']['day']} y{e['date']['year']}"}

    def menu_opens(self):
        self.settle()
        self.tap("e"); time.sleep(1.2); m1 = self.br("menu").get("menu")
        self.tap("Escape"); time.sleep(1.2); m2 = self.br("menu").get("menu")
        if m2: self.br("close_menu")
        return {"opens": m1, "closes": m2 is None}

    def clock(self, seconds=36):
        self.settle()
        mark = self.br("clock")["now_ms"]; time.sleep(seconds); c = self.br("clock")
        ms = [e[1] for e in c.get("log") or [] if e[1] > mark]
        iv = [b - a for a, b in zip(ms, ms[1:])]
        return {"runs": bool(ms), "steps": len(ms), "sec_per_step": round(sum(iv) / len(iv) / 1000, 2) if iv else None,
                "_dbg": {"intervals_ms": iv, "paused": c.get("paused"), "menu": c.get("menu")}}

    def walk(self):
        """hold D, then A, 1.2 s each, on a cleared strip of the farm: tiles moved"""
        self.settle(); self.warp("Farm", 60, 20, 1)
        self.br("clear", x1=52, y1=19, x2=70, y2=21); time.sleep(0.4)
        e0 = self.eng(); self.hold("d", 1.2); time.sleep(0.6); e1 = self.eng()
        self.hold("a", 1.2); time.sleep(0.6); e2 = self.eng()
        return {"east": e1["tile"]["x"] - e0["tile"]["x"], "west": e1["tile"]["x"] - e2["tile"]["x"],
                "rows": [e0["tile"]["y"], e1["tile"]["y"], e2["tile"]["y"]]}

    def farm_tools(self):
        """hoe tills, the watering can waters it, pickaxe breaks a stone, scythe cuts weeds, axe chops a twig; stamina per use
        (tools selected with the number keys, used with C; the farmer faces the target)"""
        self.settle(); self.warp("Farm", 62, 24, 3)
        self.br("clear", x1=58, y1=22, x2=66, y2=26); time.sleep(0.3)
        out, cost = {}, {}
        def use(tool, facing, tx, ty, place=None):
            if place: self.br("place", id=place, x=tx, y=ty)
            self.select(TOOLS[tool]); self.br("face", dir=facing); time.sleep(0.2)
            s0 = self.eng()["stamina"]; self.tap("c", 0.15); time.sleep(1.4)
            cost[tool] = s0 - self.eng()["stamina"]
            return self.tile(tx, ty)
        out["hoe_tills"] = bool(use("hoe", 3, 61, 24).get("hoe_dirt"))
        out["can_waters"] = bool(use("can", 3, 61, 24).get("watered"))
        out["pickaxe_stone"] = use("pickaxe", 2, 62, 25, STONE).get("object") is None
        out["scythe_weeds"] = use("scythe", 1, 63, 24, WEEDS).get("object") is None
        out["axe_twig"] = use("axe", 0, 62, 23, TWIG).get("object") is None
        out["stamina_cost"] = cost
        return out

    def warp_works(self):
        """walk off the farm's east edge into the Bus Stop and back (real keys), arrival tiles"""
        self.settle(); self.warp("Farm", 64, 15, 2)
        ws = [w for w in self.br("warps").get("warps") or [] if w["to"] == "BusStop"]
        if not ws: raise ProbeError("no Farm -> BusStop warp")
        w = sorted(ws, key=lambda w: w["y"])[len(ws) // 2]
        self.warp("Farm", w["x"] - 3, w["y"], 1); self.br("clear", x1=w["x"] - 5, y1=w["y"], x2=w["x"] - 1, y2=w["y"])
        e1 = self._walk_until("d", "BusStop")
        out = {"to_busstop": e1.get("location") == "BusStop", "busstop_tile": [e1["tile"]["x"], e1["tile"]["y"]] if e1.get("tile") else None}
        if not out["to_busstop"]: return {**out, "to_farm": None}
        time.sleep(0.8)
        bs = [w for w in self.br("warps").get("warps") or [] if w["to"] == "Farm"]
        b = sorted(bs, key=lambda w: w["y"])[len(bs) // 2]
        self.warp("BusStop", b["x"] + 3, b["y"], 3)
        e2 = self._walk_until("a", "Farm")
        out.update(to_farm=e2.get("location") == "Farm", farm_tile=[e2["tile"]["x"], e2["tile"]["y"]] if e2.get("tile") else None)
        return out

    def _walk_until(self, key, loc, tries=14):
        """short key holds until the farmer is in loc; the arrival tile comes from the bridge's warp log (the tile the game
        put the farmer on when the location changed)"""
        t0 = self.br("warplog")["now_ms"]; e = {}
        for _ in range(tries):
            self.hold(key, 0.3)
            try: e = self.eng()
            except ProbeError: e = {}
            if e.get("location") == loc: break
        if e.get("location") != loc: return e
        self.wait_eng(lambda x: x.get("location") == loc and x.get("screen") == "in game", 5)
        arr = [w for w in self.br("warplog").get("log") or [] if w["ms"] > t0 and w["to"] == loc]
        return {"location": loc, "tile": {"x": arr[0]["x"], "y": arr[0]["y"]}} if arr else e

    def npc_talk(self):
        """Lewis moved next to the farmer on the Bus Stop road; the action key (X) opens his dialogue; friendship points gained"""
        self.settle(); self.br("settime", time=900); self.warp("BusStop", 12, 23, 1)
        self.br("npc", name="Lewis", x=13, y=23, facing=3); time.sleep(1.2)
        f0 = (self.eng().get("friendship") or {}).get("Lewis", 0)
        sp = self.br("screen_of", name="Lewis")                  # a right click on him (the keyboard action key does not reach
        if not sp.get("ok"): raise ProbeError(f"Lewis not here: {sp}")   # villagers in 1.6 on this setup; players click them)
        self.x(f"xdotool mousemove {sp['x']} {sp['y']} sleep 0.2 mousedown 3 sleep 0.12 mouseup 3"); time.sleep(1.5)
        m = self.br("menu"); d = m.get("detail") or {}
        e = self.eng(); self.park()
        self.br("close_menu"); time.sleep(0.5)
        f1 = (self.eng().get("friendship") or {}).get("Lewis", 0)
        return {"dialogue": m.get("menu") == "DialogueBox", "speaker": d.get("speaker"), "friendship_gain": f1 - f0,
                "_dbg": {"text": (d.get("text") or "")[:120], "screen_text": e.get("screen")}}

    def shop_buy(self):
        """Pierre behind his counter at 10:00; X at the counter opens his shop; a left click on Parsnip Seeds buys one;
        Escape closes the shop (the bought seeds go to the inventory)"""
        self.settle(); self.br("settime", time=1000); self.warp("SeedShop", 4, 19, 0)
        self.br("npc", name="Pierre", x=4, y=17, facing=2); time.sleep(1.2)
        e0 = self.eng(); m0 = e0["money"]; s0 = (e0.get("inventory") or {}).get("Parsnip Seeds", 0)
        self.tap("x"); time.sleep(1.5)
        m = self.br("menu"); d = m.get("detail") or {}
        out = {"shop": d.get("shop") if m.get("menu") == "ShopMenu" else m.get("menu"), "n_items": len(d.get("items") or [])}
        it = next((i for i in d.get("items") or [] if i["name"] == "Parsnip Seeds"), None)
        if m.get("menu") == "ShopMenu" and it:
            k = it["i"] - d.get("first", 0); bt = (d.get("buttons") or [])[k] if 0 <= k < len(d.get("buttons") or []) else None
            if bt: self.click(bt["x"] + bt["w"] // 3, bt["y"] + bt["h"] // 2); time.sleep(1.0)
            out["listed_price"] = it.get("price")
        self.park(); self.tap("Escape"); time.sleep(1.2)
        if self.br("menu").get("menu"): self.br("close_menu"); time.sleep(0.5)
        e1 = self.eng()
        out.update(paid=m0 - e1["money"], bought=(e1.get("inventory") or {}).get("Parsnip Seeds", 0) - s0)
        return out

    def _sleep(self):
        self.settle()
        bed = self.br("bed")
        self.warp(bed["location"], bed["x"] - 1, bed["y"], 1)
        q = {}
        for _ in range(4):                     # step right into the bed (the question comes on entering it); else step out, retry
            self.hold("d", 0.3); time.sleep(0.7); q = self.br("menu")
            if q.get("menu") == "DialogueBox": break
            self.hold("a", 0.45); time.sleep(0.5)
        d = q.get("detail") or {}
        yes = next((r for r in d.get("responses") or [] if (r.get("key") or "").lower() == "yes"), None)
        if q.get("menu") != "DialogueBox" or not yes: raise ProbeError(f"no sleep question at the bed: {json.dumps(q)[:300]}")
        d0 = self.eng()["date"]["day"]
        self.click(yes["x"] + yes["w"] // 2, yes["y"] + yes["h"] // 2); self.park()
        t0 = time.time(); e = {}
        while time.time() - t0 < 180:                 # first look at the new morning (the clock runs again 7 s after waking)
            time.sleep(1)
            try: e = self.eng()
            except ProbeError: continue
            if e.get("date") and e["date"]["day"] != d0 and e.get("screen") in ("in game", "event", "dialogue", "menu"): break
        woke = dict(e); time.sleep(2)
        self.slept = True
        return d0, woke, round(time.time() - t0, 1)

    def sleep_next_day(self):
        d0, e, s = self._sleep()
        return {"next_day": e["date"]["day"] == d0 + 1, "morning": 600 <= (e.get("time") or 0) <= 650, "location": e["location"],
                "screen": e["screen"], "_dbg": {"seconds": s, "time": e.get("time"), "menu": e.get("menu")}}

    def save_reload(self):
        """the night's save, quit to the title and load it: the farmer's state comes back the same"""
        if not self.slept: self._sleep()
        self.settle(); time.sleep(1)
        keys = ("location", "tile", "money", "inventory", "date", "health", "stamina", "max_stamina", "skills", "friendship")
        a = {k: self.eng().get(k) for k in keys}
        save = self.ad._info().get("save")
        self.br("exit_title")
        w = self.ad._wait(self.inst, "screen=title", 90)
        if not w.get("ok"): raise ProbeError(f"exit to title: {w}")
        time.sleep(2)
        r = self.br("load", save=save)
        if not r.get("ok"): raise ProbeError(f"load: {r}")
        w = self.ad._wait(self.inst, "screen=in game", 150)
        if not w.get("ok"): raise ProbeError(f"reload: {w}")
        time.sleep(2)
        b = {k: self.eng().get(k) for k in keys}
        diff = {k: [a[k], b[k]] for k in keys if a[k] != b[k] and k != "tile"}
        return {"same": not diff, "diff": json.loads(json.dumps(diff, default=str)), "day": b["date"]["day"], "location": b["location"]}

    # ---- run
    def run_all(self, names):
        names = [n for n in ORDER if n in set(names)]
        want_log = "log_errors" in set(names) or True
        out, crashed = {}, []
        ok, l = self.ad.start(self.inst)
        if not ok:
            err = {"error": f"game did not start: {l[-600:]}"}
            return {**{n: err for n in names}, "log_errors": {"alive": False, "crashed": ["start"], "start_log": l[-1500:], **self.ad.log_errors(self.inst)}}
        for n in names:
            t0 = time.time()
            try: out[n] = getattr(self, n)()
            except Exception as e: out[n] = {"error": f"{type(e).__name__}: {str(e)[:300]}"}
            out[n]["_s"] = round(time.time() - t0, 1)
            if not self.ad._alive(self.inst):
                crashed.append(n); self.log(f"stardew probes: game died during {n}; restarting")
                le = self.ad.log_errors(self.inst); out.setdefault("_crash_logs", []).append({"after": n, **le})
                ok, l = self.ad.start(self.inst)
                if not ok: break
                self.slept = False
        if want_log:
            alive = self.ad._alive(self.inst)
            out["log_errors"] = {"alive": alive, "crashed": crashed, **self.ad.log_errors(self.inst)}
            for c in out.get("_crash_logs") or []:
                for k, v in (c.get("errors") or {}).items(): out["log_errors"]["errors"][k] = out["log_errors"]["errors"].get(k, 0) + v
        return out


# ---------------------------------------------------------------------------------------------------- comparison
def _norm(x): return json.loads(json.dumps(x, default=str))


def near(b, g, rel, ab):
    return b is not None and g is not None and abs(g - b) <= max(ab, rel * abs(b))


def compare(name, base, got):
    """one probe's modded result vs vanilla -> (ok | None, detail). '_'-keys are debug only."""
    if got is None or base is None: return None, "not probed"
    if isinstance(got, dict) and "error" in got: return None, f"probe error: {got['error']}"
    if isinstance(base, dict) and "error" in base: return None, f"vanilla probe error: {base['error']}"
    b = {k: v for k, v in _norm(base).items() if not k.startswith("_")}
    g = {k: v for k, v in _norm(got).items() if not k.startswith("_")}
    if name == "log_errors":
        if not g.get("alive") or g.get("crashed"): return False, f"game crashed / exited (during {g.get('crashed') or 'the run'})"
        new = sorted(set(g.get("errors") or {}) - set(b.get("errors") or {}))
        return (not new), (f"new kinds of SMAPI errors: {new[:5]}" if new else f"no new error kinds ({sum((g.get('errors') or {}).values())} error lines)")
    if name == "clock":
        if not g.get("runs"): return False, "the clock does not run"
        bs, gs = b.get("sec_per_step"), g.get("sec_per_step")
        if not bs or not gs: return None, f"too few clock steps (vanilla {bs}, got {gs})"
        return 0.75 * bs <= gs <= 1.33 * bs, f"{gs} s per 10 in-game minutes vs vanilla {bs}"
    if name == "warp_works":
        bad = {k: f"{b.get(k)} -> {g.get(k)}" for k in ("to_busstop", "to_farm") if b.get(k) != g.get(k)}
        for k in ("busstop_tile", "farm_tile"):                         # first tile seen after the warp: +-2 tiles (walk timing)
            bt, gt = b.get(k), g.get(k)
            if (bt is None) != (gt is None) or (bt and gt and abs(bt[0] - gt[0]) + abs(bt[1] - gt[1]) > 2): bad[k] = f"{bt} -> {gt}"
        return not bad, bad or f"Farm -> BusStop {g.get('busstop_tile')}, BusStop -> Farm {g.get('farm_tile')} (as vanilla)"
    if name == "walk":
        bad = {k: f"{b.get(k)} -> {g.get(k)}" for k in ("east", "west") if not near(b.get(k), g.get(k), 0.3, 2)}
        if g.get("rows") and len(set(g["rows"])) > 1: bad["rows"] = g["rows"]
        return not bad, bad or f"east {g.get('east')} / west {g.get('west')} tiles (vanilla {b.get('east')} / {b.get('west')})"
    diff = {k: f"{b.get(k)} -> {g.get(k)}" for k in sorted(set(b) | set(g)) if b.get(k) != g.get(k)}
    return not diff, diff or "same as vanilla"


def compare_item(spec, base, got):
    """pool `probe:` spec (name or name.f1+f2) -> (ok | None, detail)"""
    name, _, fields = spec.partition(".")
    b, g = (base or {}).get(name), (got or {}).get(name)
    if fields and isinstance(b, dict) and isinstance(g, dict) and "error" not in b and "error" not in g:
        fs = fields.split("+")
        b, g = {f: b.get(f) for f in fs}, {f: g.get(f) for f in fs}
    return compare(name, b, g)
