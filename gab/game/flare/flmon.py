"""Flare 1.15 live state reader (sandbox side, stdlib only). Copied to <root>/bridge/flmon.py by the adapter.

Flare has no scripting / IPC, so the state is read from the running process's memory: `flmon.py run` starts the game as its
own child (the sandbox has kernel.yama.ptrace_scope=1: only an ancestor may read /proc/<pid>/mem; nothing is attached or
stopped, the game is never paused) and answers `flmon.py query SOCK` over a unix socket with one JSON snapshot.
Addresses: the pinned upstream binary (flare-linux64-v1.15 AppImage, usr/bin/flare, sha256 below) is PIE with a full .symtab;
global pointers (pc, mapr, camp, menu, entitym, npcs, items, gswitch) + vtables come from it, member offsets from compiling the
1.15 headers on the host (memlayout/offsets.cpp, `offsetof`). Unknown binary -> {"error": "unknown flare binary"}.

  flmon.py run --sock S --pidfile P --stock DATA_MODS_DIR --user USER_MODS_DIR --modstxt MODS_TXT -- CMD ARGS...
  flmon.py query SOCK
"""
import json, os, re, socket, struct, subprocess, sys, threading, time

BIN_SHA256 = "0dba345d3aa47078d5bd75fd4b98ca3b60944d5795cc20ce17eb5ce0b33b18c5"
SYM = {"pc": 0x1d4338, "camp": 0x1d4328, "entitym": 0x1d4318, "mapr": 0x1d42f0, "menu": 0x1d4330, "npcs": 0x1d42d8,
       "items": 0x1d4300, "gswitch": 0x1d52b8}
VTABLES = {0x1d2008: "title", 0x1d20e0: "new game", 0x1d2060: "load game", 0x1d15f0: "in game", 0x1d1f58: "config",
           0x1d1fb0: "cutscene"}
# member offsets (memlayout/offsets.json, g++ -fno-access-control offsetof over the flare-engine v1.15 headers)
O = {"Entity.stats": 216, "Entity.type_filename": 2248, "StatBlock.alive": 1, "StatBlock.corpse": 2, "StatBlock.hero_ally": 13,
     "StatBlock.enemy_ally": 14, "StatBlock.summoned": 22, "StatBlock.name": 112, "StatBlock.level": 144, "StatBlock.xp": 152,
     "StatBlock.character_class": 376, "StatBlock.hp": 440, "StatBlock.mp": 444, "StatBlock.current": 280, "StatBlock.pos": 780,
     "StatBlock.in_combat": 1092, "EntityManager.entities": 24, "NPCManager.npcs": 56, "NPC.name": 2472, "NPC.filename": 2504,
     "NPC.dialog": 2632, "NPC.talker": 2584, "NPC.vendor": 2585, "CampaignManager.status": 88, "Map.filename": 24, "Map.title": 544,
     "Map.events": 360, "Event.type": 0, "Event.activate_type": 32, "Event.components": 40, "Event.location": 64, "Event.size": 144,
     "EventComponent.type": 0, "EventComponent.s": 8, "EventComponent.size": 104, "MenuManager.inv": 152, "MenuManager.pow": 160,
     "MenuManager.chr": 168, "MenuManager.questlog": 176, "MenuManager.hudlog": 184, "MenuManager.book": 200,
     "MenuManager.vendor": 280, "MenuManager.talker": 288, "MenuManager.exit": 296, "MenuManager.stash": 312,
     "MenuManager.game_over": 320, "MenuManager.region_title": 336, "MenuManager.devconsole": 344, "Menu.visible": 8,
     "MenuHUDLog.log_msg": 144, "MenuLog.log": 368, "WidgetLog.messages": 88, "MenuTalker.npc": 432, "MenuTalker.dialog_node": 216,
     "MenuTalker.event_cursor": 220, "MenuInventory.inventory": 784, "MenuInventory.currency": 1032, "MenuItemStorage.size": 120,
     "ItemStorage.slot_number": 0, "ItemStorage.storage": 8, "ItemStack.quantity": 8, "ItemStack.size": 16,
     "ItemManager.items": 24, "Item.name": 0, "GameSwitcher.currentState": 0, "MenuRegionTitle.title": 144,
     "MenuBook.book_name": 144, "GameStateCutscene.scene_index": 144, "GameStateCutscene.scenes": 152, "Scene.caption": 32,
     "StatusMap.header_left": 24, "StatusMap.count": 40, "RbNode.left": 16, "RbNode.right": 24,
     "RbNode.parent": 8, "RbNode.value": 32}
EC_NAMES = ("none tooltip intermap intermap_id intramap mapmod mapmod_toggle soundfx loot loot_count msg shakycam requires_status "
            "requires_not_status requires_level requires_not_level requires_currency requires_not_currency requires_item "
            "requires_not_item requires_class requires_not_class requires_tile requires_not_tile set_status unset_status "
            "remove_currency remove_item reward_xp reward_currency reward_item reward_loot reward_loot_count restore power "
            "power_path power_damage power_stats power_level spawn spawn_level stash npc music cutscene repeat save_game book script "
            "chance_exec respec show_on_minimap parallax_layers random_status procgen_filename procgen_link procgen_door_level npc_id "
            "npc_hotspot npc_dialog_them npc_dialog_you npc_voice npc_dialog_topic npc_dialog_group npc_dialog_id "
            "npc_dialog_response npc_dialog_response_only npc_allow_movement npc_portrait_them npc_portrait_you quest_text "
            "was_inside_event_area npc_take_a_party").split()
ACTIVATE = ["on_trigger", "on_interact", "on_mapexit", "on_leave", "on_load", "on_clear", "static"]
BASE_MODS = ("fantasycore", "empyrean_campaign")
CUT = 200


def cut(s, n=CUT): return s if len(s) <= n else s[:n - 3] + "..."


def r1(v): return round(float(v), 1)


# ------------------------------------------------------------------ process memory
class Mem:
    def __init__(self, pid):
        self.pid = pid; self.fd = os.open(f"/proc/{pid}/mem", os.O_RDONLY); self.base = None
        for line in open(f"/proc/{pid}/maps"):
            p = line.split()
            if len(p) >= 6 and p[5].endswith("/usr/bin/flare") and int(p[2], 16) == 0:
                self.base = int(p[0].split("-")[0], 16); self.path = p[5]; break

    def read(self, a, n):
        if not a or a < 0x1000 or a > 0x7fffffffffff: raise ValueError(f"bad address {a:#x}")
        b = os.pread(self.fd, n, a)
        if len(b) != n: raise ValueError("short read")
        return b

    def u64(self, a): return struct.unpack("<Q", self.read(a, 8))[0]
    def i32(self, a): return struct.unpack("<i", self.read(a, 4))[0]
    def u32(self, a): return struct.unpack("<I", self.read(a, 4))[0]
    def f32(self, a): return struct.unpack("<f", self.read(a, 4))[0]
    def u8(self, a): return self.read(a, 1)[0]
    def ptr(self, a): return self.u64(a)
    def sym(self, name): return self.u64(self.base + SYM[name])

    def str(self, a, n=CUT):
        """libstdc++ std::string: data ptr, size"""
        p, sz = struct.unpack("<QQ", self.read(a, 16))
        if sz == 0: return ""
        if sz > 1 << 20: raise ValueError("bad string")
        return cut(self.read(p, min(sz, n + 3)).decode("utf-8", "replace"), n) if sz > n else self.read(p, sz).decode("utf-8", "replace")

    def vec(self, a, elem, cap=100000):
        """std::vector: begin, end -> element addresses"""
        b, e = struct.unpack("<QQ", self.read(a, 16))
        if not b or e < b: return []
        n = (e - b) // elem
        if n > cap: raise ValueError("bad vector")
        return [b + i * elem for i in range(n)]

    def vecp(self, a, cap=100000):
        """std::vector<T*> -> the pointers"""
        b, e = struct.unpack("<QQ", self.read(a, 16))
        if not b or e <= b: return []
        n = (e - b) // 8
        if n > cap: raise ValueError("bad vector")
        return list(struct.unpack(f"<{n}Q", self.read(b, n * 8)))

    def vecstr(self, a, cap=200, last=False):
        """std::vector<std::string> -> at most `cap` strings (the first ones, or the last with last=True). The size is checked
        against the sanity limit of vec(), not against cap: a long HUD log is real (a mod with ~700 msg=
        events made every `game state` fail with 'menus: bad vector')"""
        xs = self.vec(a, 32)
        return [self.str(x) for x in (xs[-cap:] if last else xs[:cap])]


# ------------------------------------------------------------------ data files (stock vs mod), for the vanilla filter
class Data:
    def __init__(self, stock, user, modstxt):
        self.stock = [os.path.join(stock, m) for m in BASE_MODS]
        try: names = [l.strip() for l in open(modstxt) if l.strip() and not l.startswith("#")]
        except OSError: names = []
        self.mods = [os.path.join(user, m) for m in names if m not in BASE_MODS and os.path.isdir(os.path.join(user, m))]
        self._st, self._maps, self._cats, self._ready = None, {}, {}, threading.Event()
        threading.Thread(target=self._scan, daemon=True).start()

    def _scan(self):
        toks = set()
        for d in self.stock:
            for root, _, files in os.walk(d):
                for f in files:
                    if not f.endswith(".txt"): continue
                    try:
                        for line in open(os.path.join(root, f), errors="replace"):
                            if "status" in line and "=" in line: toks.update(re.findall(r"[A-Za-z0-9_\-.]+", line.split("=", 1)[1]))
                    except OSError: pass
        self._st = toks; self._ready.set()

    def stock_statuses(self, wait=2.0):
        self._ready.wait(wait); return self._st

    def modded(self, path):
        """mod dirs that hold this data path (override or APPEND)"""
        return [os.path.basename(m) for m in self.mods if os.path.isfile(os.path.join(m, path))]

    def in_stock(self, path): return any(os.path.isfile(os.path.join(d, path)) for d in self.stock)

    def stock_map(self, path):
        """what the stock version of a map holds: event locations, npc files, enemy types / categories (spawn= too)"""
        if path in self._maps: return self._maps[path]
        info = {"events": [], "npcs": [], "enemies": set(), "exists": False}
        src = next((os.path.join(d, path) for d in reversed(self.stock) if os.path.isfile(os.path.join(d, path))), None)
        if src:
            info["exists"] = True; sec = None
            for line in open(src, errors="replace"):
                line = line.strip()
                if line.startswith("["): sec = line; continue
                if "=" not in line or line.startswith("#"): continue
                k, v = line.split("=", 1)
                if sec == "[event]" and k == "location": info["events"].append(tuple(int(x) for x in re.findall(r"-?\d+", v)[:4]))
                elif sec == "[npc]" and k == "filename": info["npcs"].append(v.strip())
                elif sec in ("[enemy]", "[enemygroup]") and k in ("type", "category", "filename"): info["enemies"].add(v.strip())
                if k in ("spawn", "spawn_level"): info["enemies"].add(v.split(",")[0].strip())
        self._maps[path] = info
        return info

    def enemy_cats(self, path):
        if path not in self._cats:
            cats = {path, os.path.splitext(os.path.basename(path))[0]}
            for d in self.stock:
                f = os.path.join(d, path)
                if os.path.isfile(f):
                    for line in open(f, errors="replace"):
                        if line.startswith("categories="): cats.update(c.strip() for c in line[11:].split(","))
            self._cats[path] = cats
        return self._cats[path]


# ------------------------------------------------------------------ snapshot
ENGINE_KEYS = ["screen", "map", "map_title", "pos", "hp", "hp_max", "mp", "mp_max", "level", "xp", "name", "class", "alive",
               "gold", "inventory", "equipped", "statuses", "menus_open", "talking_to", "enemies", "npcs"]


def _items(m, inv_storage, names):
    out = {}
    n = m.i32(inv_storage + O["ItemStorage.slot_number"]); arr = m.ptr(inv_storage + O["ItemStorage.storage"])
    if not arr or n <= 0 or n > 400: return out
    raw = m.read(arr, n * O["ItemStack.size"])
    for i in range(n):
        iid, q = struct.unpack_from("<Qi", raw, i * 16)
        if iid and q > 0:
            k = names(iid); out[k] = out.get(k, 0) + q
    return out


def _statuses(m, camp):
    """CampaignManager::status (std::map<StatusID, pair<bool, string>>): the set ones"""
    mp = camp + O["CampaignManager.status"]; cnt = m.u64(mp + O["StatusMap.count"])
    if cnt > 20000: raise ValueError("bad status map")
    out, node, hdr, seen = [], m.ptr(mp + O["StatusMap.header_left"]), mp + 8, 0
    while node and node != hdr and seen <= cnt:                       # in-order walk (successor), as std::map iterates
        seen += 1
        v = node + O["RbNode.value"]
        if m.u8(v + 8): out.append(m.str(v + 16, 120))
        r = m.ptr(node + O["RbNode.right"])
        if r:
            node = r
            while (l := m.ptr(node + O["RbNode.left"])): node = l
        else:
            p = m.ptr(node + O["RbNode.parent"])
            while p != hdr and node == m.ptr(p + O["RbNode.right"]): node, p = p, m.ptr(p + O["RbNode.parent"])
            node = p
    return sorted(set(out))


def _events(m, mapr):
    ev = []
    for a in m.vec(mapr + O["Map.events"], O["Event.size"], 5000):
        comps = []
        for c in m.vec(a + O["Event.components"], O["EventComponent.size"], 500):
            t = m.i32(c); s = m.str(c + O["EventComponent.s"], 80)
            comps.append((EC_NAMES[t] if 0 <= t < len(EC_NAMES) else str(t), s))
        loc = struct.unpack("<4i", m.read(a + O["Event.location"], 16))
        act = m.i32(a + O["Event.activate_type"])
        ev.append({"loc": loc, "activate": ACTIVATE[act] if 0 <= act < len(ACTIVATE) else str(act), "comps": comps})
    return ev


def snapshot(m, data):
    E = dict.fromkeys(ENGINE_KEYS); mod = {}; text = []; err = []
    gs = m.sym("gswitch")
    if gs:
        st = m.ptr(gs + O["GameSwitcher.currentState"])
        E["screen"] = VTABLES.get(m.ptr(st) - 16 - m.base, "other") if st else None
        if E["screen"] == "cutscene":                                  # the caption under the current cutscene picture
            try:
                sc = m.vecp(st + O["GameStateCutscene.scenes"], 500); i = m.u64(st + O["GameStateCutscene.scene_index"])
                if i < len(sc) and (t := m.str(sc[i] + O["Scene.caption"])): text.append(f"cutscene: {t}")
            except Exception as e: err.append(f"cutscene: {e}")
    pc = m.sym("pc")
    if not pc or E["screen"] != "in game":
        if E["screen"] is None: E["screen"] = "starting"
        return {"engine": E, "mod": mod, "screen_text": text, **({"error": "; ".join(err)} if err else {})}
    s = pc + O["Entity.stats"]
    try:
        x, y = struct.unpack("<ff", m.read(s + O["StatBlock.pos"], 8))
        cur = m.vec(s + O["StatBlock.current"], 4, 1000)
        E.update(pos={"x": r1(x), "y": r1(y)}, hp=int(round(m.f32(s + O["StatBlock.hp"]))), mp=int(round(m.f32(s + O["StatBlock.mp"]))),
                 hp_max=int(round(m.f32(cur[0]))) if cur else None, mp_max=int(round(m.f32(cur[2]))) if len(cur) > 2 else None,
                 level=m.i32(s + O["StatBlock.level"]), xp=m.u64(s + O["StatBlock.xp"]), name=m.str(s + O["StatBlock.name"], 60),
                 alive=bool(m.u8(s + O["StatBlock.alive"])), **{"class": m.str(s + O["StatBlock.character_class"], 60)})
    except Exception as e: err.append(f"hero: {e}")
    hx, hy = (E["pos"] or {}).get("x", 0), (E["pos"] or {}).get("y", 0)
    dist = lambda p: ((p[0] - hx) ** 2 + (p[1] - hy) ** 2) ** 0.5
    # map
    mapr = m.sym("mapr"); mfile = None
    try:
        if mapr: mfile = m.str(mapr + O["Map.filename"]); E["map"] = mfile; E["map_title"] = m.str(mapr + O["Map.title"], 80)
    except Exception as e: err.append(f"map: {e}")
    map_mods = data.modded(mfile) if mfile else []
    smap = data.stock_map(mfile) if mfile else {"events": [], "npcs": [], "enemies": set(), "exists": False}
    if mfile and (map_mods or not smap["exists"]): mod["map"] = {"file": mfile, "title": E["map_title"], "from": map_mods or ["?"],
                                                                "new": not smap["exists"]}
    # items / inventory
    names_cache = {}
    def iname(iid):
        if iid not in names_cache:
            try:
                its = m.sym("items"); ptrs = m.vecp(its + O["ItemManager.items"], 200000)
                names_cache[iid] = (m.str(ptrs[iid] + O["Item.name"], 60) or f"item {iid}") if iid < len(ptrs) and ptrs[iid] else f"item {iid}"
            except Exception: names_cache[iid] = f"item {iid}"
        return names_cache[iid]
    menu = m.sym("menu")
    try:
        inv = m.ptr(menu + O["MenuManager.inv"])
        carried = _items(m, inv + O["MenuInventory.inventory"] + O["MenuItemStorage.size"], iname)
        E["gold"] = m.i32(inv + O["MenuInventory.currency"])
        E["inventory"] = dict(sorted(carried.items())[:30])
        E["equipped"] = sorted(_items(m, inv + O["MenuInventory.inventory"], iname))[:20]
    except Exception as e: err.append(f"inventory: {e}")
    # campaign statuses
    try:
        sts = _statuses(m, m.sym("camp"))
        E["statuses"] = sts[:40]
        stock = data.stock_statuses()
        if stock is not None:
            ms = [x for x in sts if x not in stock]
            if ms: mod["statuses"] = ms[:30]
    except Exception as e: err.append(f"statuses: {e}")
    # menus / screen text
    try:
        vis = lambda p: bool(p) and bool(m.u8(p + O["Menu.visible"]))
        opened = [k for k in ("inv", "pow", "chr", "questlog", "book", "vendor", "talker", "exit", "stash", "game_over", "devconsole")
                  if vis(m.ptr(menu + O[f"MenuManager.{k}"]))]
        E["menus_open"] = [{"inv": "inventory", "pow": "powers", "chr": "character", "questlog": "log", "exit": "pause menu"}.get(k, k)
                           for k in opened]
        hud = m.ptr(menu + O["MenuManager.hudlog"])
        text += [f"hud: {t}" for t in m.vecstr(hud + O["MenuHUDLog.log_msg"], 8, last=True)]
        rt = m.ptr(menu + O["MenuManager.region_title"])
        if vis(rt) and (t := m.str(rt + O["MenuRegionTitle.title"], 80)): text.append(f"region: {t}")
        if "talker" in opened:
            tk = m.ptr(menu + O["MenuManager.talker"]); npc = m.ptr(tk + O["MenuTalker.npc"])
            if npc:
                nm = m.str(npc + O["NPC.name"], 60); E["talking_to"] = nm
                node, curs = m.i32(tk + O["MenuTalker.dialog_node"]), m.u32(tk + O["MenuTalker.event_cursor"])
                dl = m.vec(npc + O["NPC.dialog"], 24, 2000)
                if 0 <= node < len(dl):
                    comps = m.vec(dl[node], O["EventComponent.size"], 500)
                    if curs < len(comps):
                        t = EC_NAMES[m.i32(comps[curs])] if 0 <= m.i32(comps[curs]) < len(EC_NAMES) else ""
                        who = nm if t == "npc_dialog_them" else (E["name"] or "you") if t == "npc_dialog_you" else ""
                        text.append(f"dialog: {who}: {m.str(comps[curs] + O['EventComponent.s'])}" if who else f"dialog: {m.str(comps[curs] + O['EventComponent.s'])}")
        if "book" in opened: text.append(f"book: {m.str(m.ptr(menu + O['MenuManager.book']) + O['MenuBook.book_name'], 80)}")
        ql = m.ptr(menu + O["MenuManager.questlog"])
        logs = [m.ptr(ql + O["MenuLog.log"] + 8 * i) for i in range(2)]
        quests = m.vecstr(logs[0] + O["WidgetLog.messages"], 60) if logs[0] else []
        text += [f"quest: {q}" for q in quests[:10]]
        if "log" in E["menus_open"]: text += [f"log: {q}" for q in (m.vecstr(logs[1] + O["WidgetLog.messages"], 5, last=True) if logs[1] else [])]
    except Exception as e: err.append(f"menus: {e}")
    # enemies / allies
    mod_ents, n_hostile = [], 0
    try:
        for i, e in enumerate(m.vecp(m.sym("entitym") + O["EntityManager.entities"], 5000)):
            es = e + O["Entity.stats"]; alive = bool(m.u8(es + O["StatBlock.alive"]))
            ally = bool(m.u8(es + O["StatBlock.hero_ally"]))
            if alive and not ally: n_hostile += 1
            tf = m.str(e + O["Entity.type_filename"], 120)
            if m.u8(es + O["StatBlock.summoned"]) and not data.modded(tf): continue
            stock_ok = data.in_stock(tf) and not data.modded(tf)
            if stock_ok and (map_mods or not smap["exists"]):
                stock_ok = bool(data.enemy_cats(tf) & smap["enemies"])
            if stock_ok: continue
            p = struct.unpack("<ff", m.read(es + O["StatBlock.pos"], 8))
            mod_ents.append((dist(p), i, {"name": m.str(es + O["StatBlock.name"], 60), "type": tf, "pos": {"x": r1(p[0]), "y": r1(p[1])},
                                          "hp": int(round(m.f32(es + O["StatBlock.hp"]))), "alive": alive, "ally": ally,
                                          "level": m.i32(es + O["StatBlock.level"])}))
        E["enemies"] = n_hostile
    except Exception as e: err.append(f"entities: {e}")
    # npcs
    try:
        names, npcm = [], m.sym("npcs")
        stock_npcs = list(smap["npcs"])
        for i, n in enumerate(m.vecp(npcm + O["NPCManager.npcs"], 2000)):
            es = n + O["Entity.stats"]; nm = m.str(n + O["NPC.name"], 60); fn = m.str(n + O["NPC.filename"], 120)
            names.append(nm)
            if not data.modded(fn) and data.in_stock(fn) and (not (map_mods or not smap["exists"]) or fn in stock_npcs):
                if fn in stock_npcs: stock_npcs.remove(fn)
                continue
            p = struct.unpack("<ff", m.read(es + O["StatBlock.pos"], 8))
            mod_ents.append((dist(p), 10000 + i, {"name": nm, "npc": fn, "pos": {"x": r1(p[0]), "y": r1(p[1])},
                                                  "talker": bool(m.u8(n + O["NPC.talker"])), "vendor": bool(m.u8(n + O["NPC.vendor"]))}))
        E["npcs"] = sorted(set(names))[:20]
    except Exception as e: err.append(f"npcs: {e}")
    if mod_ents:
        near = sorted(mod_ents, key=lambda t: t[0])[:20]
        ent, seen = {}, {}
        for _, i, d in sorted(near, key=lambda t: t[1]):                 # keys in spawn order: name, name#2, ...
            k = d["name"] or d.get("type") or d.get("npc"); seen[k] = seen.get(k, 0) + 1
            ent[k if seen[k] == 1 else f"{k}#{seen[k]}"] = d
        mod["entities"] = ent
    # events of the map that the stock map does not have
    if mapr and (map_mods or not smap["exists"]):
        try:
            stock_ev = list(smap["events"]); out = {}
            for ev in _events(m, mapr):
                if any(c[0] == "npc_id" for c in ev["comps"]): continue
                if ev["loc"] in stock_ev: stock_ev.remove(ev["loc"]); continue
                k = "event@%d,%d" % ev["loc"][:2]
                while k in out: k += "'"
                out[k] = {"activate": ev["activate"], "size": list(ev["loc"][2:]),
                          "do": [f"{t}={s}" if s else t for t, s in ev["comps"] if t not in ("none",)][:8]}
                if len(out) >= 20: break
            if out: mod["events"] = out
        except Exception as e: err.append(f"events: {e}")
    r = {"engine": E, "mod": mod, "screen_text": [cut(t) for t in text][:30]}
    if err: r["error"] = "; ".join(err)[:400]
    return r


# ------------------------------------------------------------------ server / client
def run(a):
    data = Data(a["stock"], a["user"], a["modstxt"])
    p = subprocess.Popen(a["cmd"])                                     # child of this process (ptrace_scope=1: the parent may read it)
    open(a["pidfile"], "w").write(str(p.pid))
    try: os.unlink(a["sock"])
    except OSError: pass
    srv = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM); srv.bind(a["sock"]); srv.listen(4)
    st = {"mem": None, "sha": None}
    lock = threading.Lock()

    def handle(c):
        try:
            with lock:
                t0 = time.time()
                if p.poll() is not None: out = {"running": False}
                else:
                    if st["mem"] is None or st["mem"].base is None:
                        st["mem"] = Mem(p.pid)
                        if st["mem"].base is not None and st["sha"] is None:
                            import hashlib
                            st["sha"] = hashlib.sha256(open(st["mem"].path, "rb").read()).hexdigest()
                    m = st["mem"]
                    if m.base is None: out = {"running": True, "engine": {"screen": "starting"}, "mod": {}, "screen_text": []}
                    elif st["sha"] != BIN_SHA256: out = {"running": True, "error": f"unknown flare binary {st['sha'][:12]}"}
                    else: out = {"running": True, **snapshot(m, data)}
                out["read_ms"] = int((time.time() - t0) * 1000)
            c.sendall(json.dumps(out, default=str).encode())
        except Exception as e:
            try: c.sendall(json.dumps({"running": p.poll() is None, "error": f"flmon: {e}"[:300]}).encode())
            except OSError: pass
        finally: c.close()

    def serve():
        while True:
            c, _ = srv.accept(); c.settimeout(5)
            threading.Thread(target=handle, args=(c,), daemon=True).start()
    threading.Thread(target=serve, daemon=True).start()
    rc = p.wait()
    try: os.unlink(a["sock"])
    except OSError: pass
    sys.exit(rc)


def query(sock, timeout=4):
    c = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM); c.settimeout(timeout)
    try: c.connect(sock)
    except OSError as e: return {"error": f"no state socket ({e.strerror})"}
    buf = b""
    while (d := c.recv(65536)): buf += d
    return json.loads(buf.decode())


def main(argv):
    if argv[:1] == ["query"]: print(json.dumps(query(argv[1]))); return
    if argv[:1] == ["run"]:
        a, i = {}, 1
        while i < len(argv) and argv[i] != "--":
            a[argv[i].lstrip("-")] = argv[i + 1]; i += 2
        a["cmd"] = argv[i + 1:]; run(a); return
    print(__doc__); sys.exit(2)


if __name__ == "__main__":
    main(sys.argv[1:])
