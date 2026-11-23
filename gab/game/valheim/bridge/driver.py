#!/usr/bin/env python3
"""Sandbox-side driver for probes_valheim.py (copied into the sandbox by the host module; python3 stdlib only).
Talks to ValheimBridge (gamebench/bridges/valheim, WCP JSON-RPC on 127.0.0.1:$VHBRIDGE_HARNESS_PORT) in a running Valheim:
enters the probe world without menus, runs the probe groups through the bridge's actions / fixtures / queries, prints
`VHPROBE {json}` lines to $VHPROBE_OUT.
    probes_valheim_driver.py run1 <groups,...>     fresh world "gaprobe" (seed gaprobe01), character "gaprobe"; ends with save
    probes_valheim_driver.py run2                  the same world again (no fresh): reads back what run1's save step wrote
"""
import hashlib, json, math, os, socket, sys, time

PORT = int(os.environ.get("VHBRIDGE_HARNESS_PORT", "6634"))
OUT = os.environ.get("VHPROBE_OUT", "/tmp/vhprobe.jsonl")
WORLD, SEED, CHAR = "gaprobe", "gaprobe01", "gaprobe"
_id = 0


def emit(probe, d):
    d = dict(d); d["probe"] = probe
    with open(OUT, "a") as f: f.write("VHPROBE " + json.dumps(d) + "\n")
    print(probe, json.dumps(d)[:300], flush=True)


def call(method, timeout=120, **params):
    global _id; _id += 1
    with socket.create_connection(("127.0.0.1", PORT), 5) as s:
        s.settimeout(timeout)
        s.sendall((json.dumps({"jsonrpc": "2.0", "id": _id, "method": method, "params": params}) + "\n").encode())
        line = s.makefile(encoding="utf-8").readline()
    if not line: raise RuntimeError("connection closed without a reply")
    r = json.loads(line)
    if "error" in r: raise RuntimeError(f"{method}: {r['error'].get('code')} {r['error'].get('message')}")
    return r["result"]


def q(query, **a): return call("eval_query", query=query, args=a)
def act(name, timeout=120, **a): return call("act", timeout=timeout, name=name, args=a)
def cmd(c): return q("command", cmd=c)
def st(): return q("status")
def h(s): return hashlib.md5(s.encode()).hexdigest()[:12]
def r2(x, n=2): return round(float(x), n) if x is not None else None


def wait(sec):
    t = time.time()
    while time.time() - t < sec: time.sleep(min(1.0, sec))


def wait_health(want, secs):
    t, hh = time.time(), {}
    while time.time() - t < secs:
        try:
            hh = call("health", timeout=15)
            if hh.get("status") in want: return hh
            if hh.get("session") == "failed": raise RuntimeError("session failed: " + str(hh.get("error")))
        except (OSError, RuntimeError) as e:
            if "session failed" in str(e): raise
        time.sleep(2)
    raise RuntimeError(f"timeout waiting for {want}: {hh}")


def me_pos(): return st()["pos"]


def ground(x, z):
    g = q("ground", x=x, z=z, r=0.1)
    return g["ground_height"]


def tp(x, z, y=None, yaw=0):
    a = dict(x=x, z=z, yaw=yaw)
    if y is not None: a["y"] = y
    r = q("teleport", **a)
    if r.get("distant"):
        for _ in range(60):
            time.sleep(1)
            s = st()
            if not s.get("teleporting"): break
    wait(1.5)
    return r


def objects(**a): return q("objects", **a)["objects"]


def spawn(prefab, x, z, y=None, **kw):
    a = dict(prefab=prefab, x=x, z=z, **kw)
    if y is not None: a["y"] = y
    r = q("spawn", **a)
    return (r.get("ids") or [None])[0]


def health_of(oid):
    o = q("object", id=oid)
    return o.get("object") or {}


def find_key(o, k):
    """first value of key k anywhere in a nested JSON value"""
    if isinstance(o, dict):
        if k in o: return o[k]
        for v in o.values():
            r = find_key(v, k)
            if r is not None: return r
    if isinstance(o, list):
        for v in o:
            r = find_key(v, k)
            if r is not None: return r
    return None


def inv_counts(): return q("inventory").get("counts", {})


# ------------------------------------------------------------------ probes
def p_content():
    pre = q("prefabs")["prefabs"]
    rec = q("recipes")["recipes"]
    rec_sig = sorted(json.dumps({k: r.get(k) for k in ("item", "amount", "station", "station_level", "resources", "min_station_level") if k in r}, sort_keys=True) for r in rec)
    items = {}
    for it in ["Club", "AxeStone", "AxeFlint", "Hammer", "Hoe", "Cultivator", "ShieldWood", "Bow", "ArrowWood", "KnifeFlint", "SpearFlint", "PickaxeAntler",
               "Torch", "Wood", "Stone", "Resin", "Flint", "LeatherScraps", "DeerHide", "RawMeat", "CookedMeat", "Raspberry", "ArmorLeatherChest", "ArmorRagsChest",
               "HelmetLeather", "CapeDeerHide", "AxeBronze", "SwordBronze", "MaceBronze", "Bronze", "Copper", "Tin", "Coal", "TrophyDeer", "TrophyBoar"]:
        s = q("item_stats", item=it, brief=True)
        if s.get("error"): items[it] = "missing"; continue
        s.pop("recipe", None); s.pop("label", None)
        items[it] = h(json.dumps(s, sort_keys=True))
    creatures = {}
    for c in ["Boar", "Deer", "Neck", "Greyling", "Greydwarf", "Troll", "Skeleton", "Eikthyr", "gd_king", "Wolf", "Serpent", "Leech", "Draugr"]:
        pi = q("prefab_info", prefab=c, depth=1)
        comp = pi.get("components") or {}
        ch = next((v for k, v in comp.items() if k in ("Humanoid", "Character")), None) if isinstance(comp, dict) else None
        if isinstance(comp, list):
            ch = next((x for x in comp if isinstance(x, dict) and x.get("type") in ("Humanoid", "Character")), None)
        keep = {}
        if isinstance(ch, dict):
            src = ch.get("fields", ch)
            for k in ("m_health", "m_walkSpeed", "m_runSpeed", "m_speed", "m_turnSpeed", "m_flying", "m_faction", "m_boss", "m_tolerateWater", "m_damageModifiers", "m_jumpForce"):
                if k in src: keep[k] = src[k]
        creatures[c] = keep if keep else ("missing" if pi.get("error") else h(json.dumps(comp, sort_keys=True, default=str)[:20000]))
    pieces = q("build_pieces", tool="Hammer")
    piece_sig = sorted(json.dumps({k: p.get(k) for k in ("prefab", "resources", "station", "category") if k in p}, sort_keys=True) for p in pieces.get("pieces", []))
    return {"n_prefabs": len(pre), "prefabs": sorted(pre), "n_recipes": len(rec), "recipes": rec_sig, "items": items, "creatures": creatures,
            "n_hammer_pieces": len(piece_sig), "hammer_pieces": piece_sig}


def p_worldgen():
    locs = q("locations", limit=5000, x=0, z=0)
    inst = locs.get("instances") or []
    names = {}
    for li in inst: names[li["name"]] = names.get(li["name"], 0) + 1
    pos_hash = h(";".join(sorted(f"{li['name']}@{round(li['pos'][0])},{round(li['pos'][2])}" for li in inst)))
    start = next((li for li in inst if li["name"] == "StartTemple"), None)
    biomes = {}
    for b in ["Meadows", "BlackForest", "Swamp", "Mountain", "Plains", "Ocean"]:
        r = q("biome_finder", biome=b, x=0, z=0, n=3, max_r=6000, step=64)
        biomes[b] = [[round(p["pos"][0]), round(p["pos"][2])] for p in (r.get("found") or r.get("points") or r.get("spots") or [])][:3] if isinstance(r, dict) else r
    s = st(); p0 = s["pos"]
    grid = []
    for dx in range(-48, 49, 16):
        for dz in range(-48, 49, 16):
            g = q("ground", x=p0[0] + dx, z=p0[2] + dz, r=0.1)
            grid.append([dx, dz, r2(g["ground_height"], 1), g["biome"]])
    return {"n_locations": locs.get("count"), "location_counts": names, "locations_hash": pos_hash,
            "start_temple": [round(start["pos"][0]), round(start["pos"][2])] if start else None, "biome_spots": biomes,
            "spawn_biome": s["biome"], "spawn_ground_grid_hash": h(json.dumps(grid)), "spawn_ground": grid[len(grid) // 2]}


def p_inventory():
    inv = q("inventory"); eq = q("equipment"); s = st()
    return {"counts": inv.get("counts"), "equipment": eq if isinstance(eq, (list, dict)) else str(eq), "max_health": s["max_health"], "max_stamina": s["max_stamina"],
            "max_weight": s["max_weight"], "skills_count": len((q("skills").get("skills") or []))}


def full_stamina(): q("set_stats", stamina=st()["max_stamina"])


def setup_flat():
    """A spot near spawn: the start temple's paved circle is flat; move 6 m east of the centre."""
    s = st(); return s["pos"]


def p_move(home):
    d = {}
    tp(home[0], home[2], yaw=0); wait(2)
    full_stamina()
    w = act("speed_sample", seconds=4, move="walk", yaw=90)
    d["walk_speed"] = r2(w.get("avg_speed")); d["walk_max"] = r2(w.get("max_speed"))
    d["base_walk"] = w.get("base", {}).get("walk"); d["base_run"] = w.get("base", {}).get("run"); d["base_swim"] = w.get("base", {}).get("swim")
    tp(home[0], home[2], yaw=0); wait(1.5); full_stamina()
    stam0 = st()["stamina"]
    r = act("speed_sample", seconds=3, move="run", yaw=90)
    stam1 = st()["stamina"]
    d["run_speed"] = r2(r.get("avg_speed")); d["run_max"] = r2(r.get("max_speed")); d["run_stamina_used_3s"] = r2(stam0 - stam1, 1)
    # jump: apex height and stamina (retried up to 3 times when the press did not register: the player still settling after a teleport)
    import threading
    for attempt in range(3):
        tp(home[0], home[2], yaw=0); wait(2); full_stamina()
        y0 = st()["pos"][1]; sta0 = st()["stamina"]; smin = sta0
        t0 = time.time(); res = {}
        th = threading.Thread(target=lambda: res.setdefault("r", act("jump")))
        th.start(); ymax = y0
        while time.time() - t0 < 1.6:
            try: s_ = st(); ymax = max(ymax, s_["pos"][1]); smin = min(smin, s_["stamina"])
            except Exception: pass
        th.join(5)
        wait(1)
        if ymax - y0 > 0.3: break
    d["jump_height"] = r2(ymax - y0, 2); d["jump_stamina"] = r2(sta0 - smin, 1)
    # fall damage: 6 m drop onto the ground (god off; a new character has 25 health, more than ~8 m kills it)
    tp(home[0], home[2], yaw=0); wait(1.5)
    q("set_stats", health=st()["max_health"])
    hp0 = st()["health"]; g = ground(home[0], home[2])
    q("teleport", x=home[0], z=home[2], y=g + 6)
    for _ in range(40):
        time.sleep(0.25); s = st()
        if s.get("on_ground") and abs(s["pos"][1] - g) < 1.5: break
    wait(1.5)
    d["fall_damage_6m"] = r2(hp0 - st()["health"], 1)
    q("set_stats", health=st()["max_health"])
    return d


def p_swim(ocean):
    d = {}
    if not ocean: return {"error": "no ocean spot"}
    x, z = ocean
    tp(x, z, y=30.3, yaw=0); wait(5)
    s = st(); d["swimming"] = s.get("swimming")
    full_stamina(); sta0 = st()["stamina"]
    r = act("speed_sample", seconds=3, move="walk", yaw=0)
    d["swim_speed"] = r2(r.get("avg_speed")); d["swim_stamina_used_3s"] = r2(sta0 - st()["stamina"], 1)
    return d


def p_combat(home):
    d = {}
    tp(home[0], home[2], yaw=0); wait(1.5)
    cmd("nospawn"); q("give", item="Club", amount=1); act("equip", item="Club")
    x, z = home[0], home[2] + 2.2
    cmd("god")   # the target fights back
    oid = spawn("Greydwarf", x, z)
    wait(1.0)
    o = health_of(oid); hp0 = o.get("health"); d["target_max_health"] = o.get("max_health")
    hits = []
    for i in range(3):
        q("teleport", id=oid) if False else None
        st_ = st()
        full_stamina()
        o = health_of(oid); before = o.get("health")
        if before is None: break
        bp = o.get("pos") or [x, 0, z]
        act("attack", x=bp[0], z=bp[2], y=bp[1] + 0.5)
        wait(1.2)
        after = health_of(oid).get("health")
        hits.append(r2((before or 0) - (after or 0), 1) if after is not None else "dead")
    d["club_hits"] = hits
    num = [x for x in hits if isinstance(x, (int, float))]
    d["club_hit_mean"] = r2(sum(num) / len(num), 1) if num else 0
    d["club_hits_landed"] = sum(1 for x in num if x > 0)
    try: q("remove", id=oid)
    except Exception: pass
    cmd("god")
    # damage taken: 20 blunt, unblocked, no armor
    q("set_stats", health=st()["max_health"]); hp0 = st()["health"]
    r = q("apply_damage", target="player", blunt=20)
    wait(0.5)
    d["blunt20_taken"] = r2(hp0 - st()["health"], 1)
    # blocking: wood shield + Block held, 20 blunt from a creature in front
    q("give", item="ShieldWood", amount=1); act("equip", item="ShieldWood")
    q("set_stats", health=st()["max_health"], stamina=st()["max_stamina"])
    p = st()["pos"]; att = spawn("Greyling", p[0], p[2] + 2.0)
    wait(0.5)
    import threading
    th = threading.Thread(target=lambda: act("block", seconds=2.0)); th.start(); wait(0.6)
    hp0 = st()["health"]; sta0 = st()["stamina"]
    q("apply_damage", target="player", blunt=20, attacker=att, blockable=True)
    wait(0.4)
    d["blocked_blunt20_taken"] = r2(hp0 - st()["health"], 1); d["block_stamina"] = r2(sta0 - st()["stamina"], 1)
    th.join(5)
    try: q("remove", id=att)
    except Exception: pass
    act("unequip", item="ShieldWood"); act("unequip", item="Club")
    q("set_stats", health=st()["max_health"])
    return d


def p_creatures(home):
    d = {}
    tab = q("spawn_data", biome="Meadows", local=False)
    sp = tab.get("spawners") or tab.get("spawn_data") or tab.get("list") or []
    rows = sorted(json.dumps({k: v for k, v in s.items() if k in ("m_prefab", "m_biome", "m_spawnAtDay", "m_spawnAtNight", "m_maxSpawned", "m_spawnInterval",
                                                                  "m_spawnChance", "m_minLevel", "m_maxLevel", "m_groupSizeMin", "m_groupSizeMax", "m_enabled")}, sort_keys=True, default=str)
                  for s in sp if isinstance(s, dict))
    d["meadows_spawners"] = rows; d["n_meadows_spawners"] = len(rows)
    tab2 = q("spawn_data", local=False)
    allsp = tab2.get("spawners") or tab2.get("spawn_data") or tab2.get("list") or []
    d["all_spawn_prefabs"] = sorted({str(s.get("m_prefab")) for s in allsp if isinstance(s, dict)})
    # behaviour: a Greyling 12 m away comes at the player; a Deer 6 m away runs off
    tp(home[0], home[2], yaw=0); wait(1.5); cmd("god")
    p = st()["pos"]
    g = spawn("Greyling", p[0] + 12, p[2]); dr = spawn("Deer", p[0] - 6, p[2])
    wait(1)
    def dist(oid):
        o = health_of(oid); op = o.get("pos")
        if not op: return None
        pp = st()["pos"]; return math.hypot(op[0] - pp[0], op[2] - pp[2])
    g0, d0 = dist(g), dist(dr)
    wait(8)
    g1, d1 = dist(g), dist(dr)
    d["greyling_approaches"] = g0 is not None and g1 is not None and g1 < g0 - 3
    d["deer_flees"] = d0 is not None and d1 is not None and d1 > d0 + 3
    for oid in (g, dr):
        try: q("remove", id=oid)
        except Exception: pass
    cmd("god")
    return d


def p_build_craft(home):
    d, c = {}, {}
    q("give", item="Hammer", amount=1); q("give", item="Wood", amount=60); q("give", item="Stone", amount=20)
    act("equip", item="Hammer")
    # a fixed list of spots around spawn; the first where the hammer's placement is Valid (same terrain in every instance)
    p = None; tried = 0
    for dx, dz in [(-20, -20), (-24, -16), (-16, -24), (-28, -28), (20, 20), (-30, 10), (10, -30), (30, -30), (-40, -40), (40, 40)]:
        x, z = home[0] + dx, home[2] + dz
        tp(x, z - 3, yaw=0); wait(1.5); tried += 1
        w0 = inv_counts().get("Wood", 0)
        r = act("place", piece="piece_workbench", x=x, z=z, yaw=180)
        if r.get("ok"): p = [x, 0, z - 3]; break
    d["workbench_placed"] = p is not None; d["spots_tried"] = tried
    if p is None: return d, {"error": "no valid workbench spot"}
    w1 = inv_counts().get("Wood", 0); d["workbench_wood"] = w0 - w1
    p = st()["pos"]
    benches = objects(x=p[0], z=p[2], r=10, prefab="piece_workbench")
    # structural support: 10 wood poles (2 m) stacked from the ground; vanilla wood holds a limited height
    sx, sz = p[0] + 8, p[2] - 6; g = ground(sx, sz)
    ids = []
    for i in range(14):
        ids.append(spawn("wood_pole2", sx, sz, y=g + 1.0 + 2.0 * i))
        time.sleep(0.15)
    wait(8)
    left = objects(x=sx, z=sz, r=25, prefab="wood_pole2", detail=True)
    d["poles_standing"] = len(left)
    d["pole_top_height"] = r2(max([o["pos"][1] - g for o in left], default=0), 1)
    # crafting at the workbench: a Club (wood) through the crafting screen's routine
    if benches:
        act("use", id=benches[0]["id"]); wait(1)
        wb = inv_counts().get("Wood", 0)
        r = act("craft", recipe="Club")
        c["club_crafted"] = r.get("crafted"); c["club_wood"] = wb - inv_counts().get("Wood", 0)
        r = act("craft", recipe="ArmorRagsChest")
        c["rags_without_scraps"] = bool(r.get("ok"))
        act("close_inventory")
    else:
        c["error"] = "no workbench"
    # by hand: a Torch (wood + resin)
    q("give", item="Resin", amount=2)
    r = act("craft", recipe="Torch"); c["torch_by_hand"] = r.get("crafted")
    act("close_inventory")
    return d, c


def p_sail(ocean):
    if not ocean: return {"error": "no ocean spot"}
    x, z = ocean
    tp(x, z, y=30.3, yaw=0); wait(3)
    cmd("wind 0 1")
    oid = spawn("Raft", x + 3, z, y=30.5)
    wait(3)
    d = {"mounted": False}
    for _ in range(3):
        o = health_of(oid); op = o.get("pos")
        if op: q("teleport", x=op[0], z=op[2] - 1.2, y=op[1] + 1.0); wait(1.5)
        r = act("mount", id=oid)
        if r.get("ok"): d["mounted"] = True; break
    if d["mounted"]:
        s = act("sail", speed="half", seconds=8)
        d["half_ok"] = bool(s.get("ok")); d["half_moved_m"] = r2(s.get("moved_m"), 1); d["half_speed"] = r2(s.get("velocity"), 2)
        act("sail", speed="stop", seconds=2)
        act("dismount")
    cmd("resetwind")
    return d


def p_portal(home):
    """Two portals with the same tag connect (Game.ConnectPortals); a third with another tag stays unconnected.
    (Walking into the trigger did not fire on 2026-10-30: the bridge's walk stops at the frame; not probed.)"""
    d = {}
    tp(home[0] - 30, home[2] + 20, yaw=0); wait(2)
    p = st()["pos"]
    a = spawn("portal_wood", p[0] + 4, p[2]); b = spawn("portal_wood", p[0] + 150, p[2] + 40); c = spawn("portal_wood", p[0] - 6, p[2] + 6)
    q("portal_tag", id=a, tag="gaprobe"); q("portal_tag", id=b, tag="gaprobe"); q("portal_tag", id=c, tag="lonely")
    wait(10)
    oa, oc = q("object", id=a), q("object", id=c)
    d["same_tag_connected"] = bool(find_key(oa, "connected")); d["other_tag_connected"] = bool(find_key(oc, "connected"))
    d["tag_kept"] = find_key(oa, "tag")
    return d


def p_weather():
    d = {}
    s = st(); d["day_length_s"] = None
    t0 = q("world")["time"]
    wait(5)
    t1 = q("world")["time"]
    d["world_time_rate"] = r2((t1 - t0) / 5.0, 1)
    envs = []
    # skip whole days so the environment roll is at fixed world times relative to the start
    for i in range(4):
        cmd("skiptime 300"); wait(3)
        s = st(); envs.append([s.get("day"), s.get("time_of_day")[:2], s.get("is_day"), s.get("environment")])
    d["envs"] = envs
    return d


def p_skills():
    sk = q("skills").get("skills") or []
    keep = {}
    for s in sk:
        n = s.get("name") or s.get("skill")
        if n in ("Run", "Jump", "Clubs", "Swim", "Blocking", "WoodCutting"): keep[n] = [r2(s.get("level"), 2), r2(s.get("accumulator", s.get("progress", 0)), 2)]
    return {"after_probes": keep, "n_skills": len(sk)}


def p_perf():
    f = []
    for _ in range(10):
        f.append(call("health")["fps"]); time.sleep(1)
    f.sort()
    return {"fps_median": r2(f[5], 1)}


def p_save(home):
    tp(home[0] + 20, home[2] - 20, yaw=0); wait(2)
    p = st()["pos"]
    mk = spawn("piece_chest_wood", p[0] + 3, p[2] + 3)
    q("clear_inventory"); q("give", item="Resin", amount=17); q("give", item="Flint", amount=3)
    wait(2)
    call("save"); wait(4)
    with open(os.path.join(os.path.dirname(OUT), "marker.json"), "w") as f: json.dump({"x": p[0] + 3, "z": p[2] + 3}, f)
    emit("save", {"marker": [round(p[0] + 3, 1), round(p[2] + 3, 1)]})
    call("leave"); wait(8)


def p_saveload():
    m = json.load(open(os.path.join(os.path.dirname(OUT), "marker.json")))
    tp(m["x"] - 3, m["z"] - 3); wait(4)
    chests = objects(x=m["x"], z=m["z"], r=3, prefab="piece_chest_wood")
    c = inv_counts()
    return {"marker_chest": len(chests), "resin": c.get("Resin", 0), "flint": c.get("Flint", 0), "world": q("world").get("world")}


def main():
    mode = sys.argv[1]; groups = sys.argv[2].split(",") if len(sys.argv) > 2 else []
    wait_health({"menu", "in_world"}, 300)
    call("reset", scenario={"world": WORLD, "seed": SEED, "character": CHAR, "fresh": mode == "run1", "wait": False, "cheats": True})
    wait_health({"in_world"}, 900)
    wait(5)
    hh = call("health"); emit("_entered", {"load_seconds": hh.get("last_load_seconds"), "world": q("world")})
    s = st(); home = s["pos"]
    if mode == "run2":
        try: emit("saveload", p_saveload())
        except Exception as e: emit("saveload", {"error": repr(e)[:400]})
        emit("_done", {}); return
    try:
        cmd("nospawn")
    except Exception: pass
    ocean = None
    try:
        r = q("biome_finder", biome="Ocean", x=home[0], z=home[2], n=1, max_r=3000, step=32, max_above_water=-3)
        pts = r.get("found") or r.get("points") or r.get("spots") or []
        if pts: ocean = (pts[0]["pos"][0], pts[0]["pos"][2])
    except Exception: pass
    plan = [("inventory", lambda: p_inventory()), ("content", p_content), ("worldgen", p_worldgen), ("perf", p_perf), ("move", lambda: p_move(home)),
            ("swim", lambda: p_swim(ocean)), ("combat", lambda: p_combat(home)), ("creatures", lambda: p_creatures(home)),
            ("buildcraft", lambda: p_build_craft(home)), ("sail", lambda: p_sail(ocean)), ("portal", lambda: p_portal(home)), ("weather", p_weather), ("skills", p_skills)]
    for name, f in plan:
        if name not in groups: continue
        try:
            tp(home[0], home[2]); r = f()
            if name == "buildcraft": emit("build", r[0]); emit("craft", r[1])
            else: emit(name, r)
        except Exception as e:
            if name == "buildcraft": emit("build", {"error": repr(e)[:400]}); emit("craft", {"error": repr(e)[:400]})
            else: emit(name, {"error": repr(e)[:400]})
    if "save" in groups:
        try: p_save(home)
        except Exception as e: emit("save", {"error": repr(e)[:400]})
    emit("_done", {})


if __name__ == "__main__":
    try: main()
    except Exception as e:
        emit("_fatal", {"error": repr(e)[:600]}); sys.exit(1)
