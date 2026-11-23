#!/usr/bin/env python3
"""gab Valheim state reader (runs in the Steam sandbox, python3 stdlib): one STATE_SPEC snapshot of a running Valheim through the
harness channel of ValheimBridge (JSON-RPC on 127.0.0.1:<port>). The queries are sent in parallel on separate connections, so
the bridge answers them in the same frame (its main-thread queue): ~0.1-0.3 s.

    vhstate.py <harness port> <base dir> <plugins comma list> [vanilla]
base dir: the vanilla baselines (<base>/prefabs.json: every prefab of a vanilla game; <base>/start.json: global keys / player data
keys of a vanilla start). With `vanilla` (a vanilla instance in its world) the baselines are written when missing.
Prints one JSON line {running, engine, mod, screen_text} (+ error)."""
import json, os, re, socket, sys, threading, time

PORT, BASE = int(sys.argv[1]), sys.argv[2]
PLUGINS = [p for p in (sys.argv[3] if len(sys.argv) > 3 else "").split(",") if p]
VANILLA = len(sys.argv) > 4 and sys.argv[4] == "vanilla"
# vanilla progress keys (ZoneSystem global keys the game itself sets, and world modifiers): everything else a mod set
VANILLA_KEY = re.compile(r"^(defeated_\w+|killed_\w+|Killed\w+|nomap|noportals|nobuildcost|playerdamage|enemydamage|enemyspeedsize|enemylevelupchance|"
                         r"resourcerate|staminarate|moveStaminaRate|staminaregenrate|skillgainrate|skillreductionrate|deathpenalty|eventrate|"
                         r"raidrate|nonmagic|teleportall|passivemobs|nonpassivemobs|worldlevel|deathdeleteitems|deathdeleteunequipped|"
                         r"deathskillsreset|preset|fire|dungeonbuild|noworkbench|allpiecesunlocked|activebosses|nomapdeath)\b", re.I)
CUT = 200


def call(method, params=None, timeout=3.0):
    with socket.create_connection(("127.0.0.1", PORT), 2) as s:
        s.settimeout(timeout)
        s.sendall((json.dumps({"jsonrpc": "2.0", "id": 1, "method": method, "params": params or {}}) + "\n").encode())
        line = s.makefile(encoding="utf-8").readline()
    r = json.loads(line)
    if "error" in r: raise RuntimeError(f"{method}: {r['error'].get('message')}")
    return r["result"]


def q(query, **a): return call("eval_query", {"query": query, "args": a})


def parallel(jobs):
    out = {}
    def run(k, f):
        try: out[k] = f()
        except Exception as e: out[k] = {"_error": str(e)[:200]}
    ts = [threading.Thread(target=run, args=(k, f)) for k, f in jobs.items()]
    for t in ts: t.start()
    for t in ts: t.join(5)
    return out


def load(name):
    try: return json.load(open(os.path.join(BASE, name)))
    except (OSError, ValueError): return None


def cut(s): s = str(s); return s if len(s) <= CUT else s[:CUT]


def main():
    try: h = call("health", timeout=2.0)
    except Exception as e:
        print(json.dumps({"running": None, "error": f"bridge not answering: {e}"[:300]})); return
    st = h.get("status")
    engine = {"screen": {"in_world": "in_game"}.get(st, st), "menus_open": [], "world": None, "time": None, "player": None, "inventory": {},
              "equipment": {}, "skills": {}, "status_effects": [], "nearby": {}, "progress": None, "event": None, "fps": round(h.get("fps") or 0),
              "mods": PLUGINS}
    mod, text = {}, []
    if st not in ("in_world", "dead"):
        print(json.dumps({"running": True, "engine": engine, "mod": mod, "screen_text": text})); return
    R = parallel({"status": lambda: q("status"), "inv": lambda: q("inventory"), "eq": lambda: q("equipment"), "skills": lambda: q("skills"),
                  "se": lambda: q("status_effects"), "world": lambda: q("world"), "keys": lambda: q("global_keys"), "pd": lambda: q("player_data"),
                  "obj": lambda: q("objects", r=60, limit=300), "msg": lambda: q("messages", n=8), "input": lambda: q("input_state"),
                  "ev": lambda: q("events", brief=True), **({"prefabs": lambda: q("prefabs")} if VANILLA and load("prefabs.json") is None else {})})
    s = R.get("status") or {}
    inp = R.get("input") or {}
    engine["menus_open"] = [k for k, f in (("inventory", "inventory_visible"), ("menu", "menu_visible"), ("console", "console_visible"),
                                           ("text_viewer", "text_viewer"), ("map", "minimap_open"), ("chat", "chat_focus"), ("build_menu", "piece_selection"))
                            if inp.get(f)]
    w = R.get("world") or {}
    engine["world"] = {"name": w.get("world"), "seed": w.get("seed"), "time_s": w.get("time")}
    engine["time"] = {k: s.get(k) for k in ("day", "day_fraction", "time_of_day", "is_day", "environment", "paused")}
    engine["player"] = {k: s.get(k) for k in ("name", "pos", "yaw", "health", "max_health", "stamina", "max_stamina", "eitr", "max_eitr", "dead", "biome",
                                              "on_ground", "swimming", "crouching", "attached_to", "at_helm", "riding", "in_bed", "crafting_station",
                                              "in_place_mode", "selected_piece", "weight", "max_weight", "teleporting")}
    # Player.TeleportTo: until the game finishes the teleport (2-15 s; never when the target area does not get ready) Hud's loading
    # screen covers everything (a black screen, no HUD), the player is held at the target and ignores input (2026-11-12: a mod's
    # TeleportTo that never finished gave all-black replays while the state said in_game)
    engine["player"]["takes_input"] = inp.get("take_input") if isinstance(inp.get("take_input"), bool) else None
    if s.get("teleporting"): engine["screen"] = "loading"
    if s.get("dead"): engine["screen"] = "dead"
    counts = (R.get("inv") or {}).get("counts") or {}
    engine["inventory"] = dict(list(counts.items())[:30])
    eq = R.get("eq") if isinstance(R.get("eq"), dict) else {}
    engine["equipment"] = {slot: (v.get("prefab") or v.get("name")) for slot, v in eq.items() if isinstance(v, dict)}
    sk = (R.get("skills") or {}).get("skills") or []
    engine["skills"] = {str(x.get("name") or x.get("skill")): round(float(x.get("level") or 0), 1) for x in sk if isinstance(x, dict) and float(x.get("level") or 0) > 0}
    ses = (R.get("se") or {}).get("status_effects") or []
    engine["status_effects"] = [str((x.get("label") or x.get("prefab")) if isinstance(x, dict) else x)[:60] for x in ses][:15]
    keys = (R.get("keys") or {}).get("global_keys") or []
    engine["progress"] = {"global_keys": sorted(k for k in keys if VANILLA_KEY.match(k))}
    ev = (R.get("ev") or {}).get("active")
    engine["event"] = ev.get("name") if isinstance(ev, dict) else None
    # ---- baselines (a vanilla instance writes them once)
    if VANILLA:
        os.makedirs(BASE, exist_ok=True)
        if "prefabs" in R and isinstance(R["prefabs"], dict) and R["prefabs"].get("prefabs"):
            json.dump(sorted(R["prefabs"]["prefabs"]), open(os.path.join(BASE, "prefabs.json"), "w"))
        if load("start.json") is None:
            pd = R.get("pd") or {}
            json.dump({"custom_data": sorted((pd.get("custom_data") or {}).keys()) if isinstance(pd.get("custom_data"), dict) else [],
                       "unique_keys": sorted(pd.get("unique_keys") or [])}, open(os.path.join(BASE, "start.json"), "w"))
    vanilla = set(load("prefabs.json") or [])
    start = load("start.json") or {}
    objs = (R.get("obj") or {}).get("objects") or []
    near = {}
    for o in objs:
        if o.get("kind") == "player": continue
        if vanilla and o.get("prefab") not in vanilla: continue
        if len(near) >= 12 or o.get("kind") not in ("creature", "character", "npc", "piece", "item", "container", "pickable"): continue
        near[f"{o.get('prefab')}#{str(o.get('id'))[-6:]}"] = {k: o.get(k) for k in ("kind", "pos", "dist", "health", "label") if o.get(k) is not None}
    engine["nearby"] = near
    # ---- mod: what is not vanilla
    if vanilla:
        mo = {}
        for o in objs:
            if o.get("prefab") in vanilla or o.get("kind") == "player": continue
            if len(mo) >= 20: break
            mo[f"{o.get('prefab')}#{str(o.get('id'))[-6:]}"] = {k: o.get(k) for k in ("kind", "label", "pos", "dist", "health", "max_health", "stack", "piece_name")
                                                               if o.get(k) is not None}
        if mo: mod["objects"] = mo
        mi = {k: v for k, v in counts.items() if k not in vanilla and not str(k).startswith("$")}
        if mi: mod["inventory"] = mi
    else: mod["note"] = "no vanilla prefab baseline yet (a vanilla instance records it)"
    mk = sorted(k for k in keys if not VANILLA_KEY.match(k))
    if mk: mod["global_keys"] = mk[:30]
    pd = R.get("pd") or {}
    cd = pd.get("custom_data") if isinstance(pd.get("custom_data"), dict) else {}
    cdm = {k: cut(v) for k, v in cd.items() if k not in set(start.get("custom_data") or [])}
    if cdm: mod["player_data"] = dict(list(sorted(cdm.items()))[:30])
    uk = sorted(set(pd.get("unique_keys") or []) - set(start.get("unique_keys") or []))
    if uk: mod["player_unique_keys"] = uk[:30]
    items_cd = [x for x in pd.get("items_with_custom_data") or [] if isinstance(x, dict)]
    if items_cd: mod["item_data"] = {str(x.get("prefab")): x.get("custom_data") for x in items_cd[:10]}
    ex = pd.get("extra_inventories")
    if isinstance(ex, dict) and ex: mod["extra_inventories"] = {k: (v or {}).get("counts") for k, v in list(ex.items())[:5]}
    # ---- screen text
    for m in ((R.get("msg") or {}).get("messages") or [])[-8:]: text.append("hud: " + cut(m))
    hv = s.get("hovering") if isinstance(s.get("hovering"), dict) else None
    if hv:
        if hv.get("hover_name"): text.append("hover: " + cut(hv["hover_name"]))
        if hv.get("hover_text"): text.append("hover_text: " + cut(re.sub(r"<[^>]+>", "", str(hv["hover_text"])).replace("\n", " | ")))
    errs = {k: v["_error"] for k, v in R.items() if isinstance(v, dict) and "_error" in v}
    out = {"running": True, "engine": engine, "mod": mod, "screen_text": text[:30]}
    if errs: out["error"] = "; ".join(f"{k}: {v}" for k, v in errs.items())[:300]
    print(json.dumps(out))


if __name__ == "__main__":
    t0 = time.time(); main()
