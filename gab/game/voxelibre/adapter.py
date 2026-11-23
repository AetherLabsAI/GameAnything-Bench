"""VoxeLibre 0.92.4 on Luanti 5.17.0. Install: the minetestdevs PPA build unpacked user-local + VoxeLibre git tag, under
<seat home>/oss-voxelibre (the verified reference install; `install()` reproduces it on another seat sandbox). No EULA
/ ToS (GPL / CC-BY-SA). A fresh world per start (fixed seed 20261030, survival, damage on), singleplayer client `--go` on the
seat's display (straight into the world: no menu, no dialog), GPU through VirtualGL.

Deliverable mod/ (devkit/README.md): one or more Luanti mods (dirs with mod.conf + init.lua) installed as world mods, or a
single mod at mod/ itself; optional start.yaml {settings: {minetest.conf key: value}} for the fresh world.
"""
import base64, json, re, shlex, time, uuid
from pathlib import Path

import yaml

from .. import base as gbase
from ..base import GameAdapter
from . import probes as P

NAME_RE = re.compile(r"^[a-z0-9_]+$")
STATE_LUA = r'''
-- STATE_SPEC snapshot (adapter.state docstring). Works with the old bridge too (no hooks: V.chat / V.fs / V.storages nil).
local p = P; if not p then return {error = "no player yet"} end
local function r1(x) return math.floor(x * 10 + 0.5) / 10 end
local function rp(q) return {x = r1(q.x), y = r1(q.y), z = r1(q.z)} end
local function txt(s, n)
  s = tostring(s or "")
  if core.get_translated_string then local ok, t = pcall(core.get_translated_string, "en", s); if ok and t then s = t end end
  s = s:gsub("\27%([^)]*%)", ""):gsub("\27.", ""):gsub("%s+", " "):gsub("^ ", ""):gsub(" $", "")
  return #s > (n or 200) and s:sub(1, (n or 200) - 3) .. "..." or s
end
-- deliverable mods: loaded mods outside the game's own mod tree (not the bridge, not the engine)
if not V.gab_dmods then
  local gp = (core.get_game_info and core.get_game_info().path) or ""
  local set, list = {}, {}
  for _, m in ipairs(core.get_modnames()) do
    local mp = core.get_modpath(m) or ""
    if m ~= "vlprobe" and (gp == "" or mp:sub(1, #gp) ~= gp) and not mp:find("/games/voxelibre/", 1, true) then set[m] = true; list[#list + 1] = m end
  end
  table.sort(list); V.gab_dmods = {set = set, list = list, vanilla = {}}
  for _, m in ipairs(core.get_modnames()) do if not set[m] then V.gab_dmods.vanilla[m] = true end end
end
local D = V.gab_dmods
local function modof(name) return (tostring(name):match("^:?([^:]+):") or "") end
local function is_mod_item(name)
  local d = core.registered_items[name]; local o = d and d.mod_origin or modof(name)
  return D.set[o] or D.set[modof(name)] or false
end
local q = p:get_pos(); local pn = p:get_player_name(); local inv = p:get_inventory()
-- engine
local near, ents = {}, {}
for _, o in ipairs(core.get_objects_inside_radius(q, 32)) do
  if not o:is_player() then
    local e = o:get_luaentity(); local n = e and e.name or "?"; near[n] = (near[n] or 0) + 1
    local d = e and core.registered_entities[n]; local org = d and d.mod_origin or modof(n)
    local mine = e and (D.set[org] or D.set[modof(n)] or (n == "__builtin:item" and e.itemstring and is_mod_item(ItemStack(e.itemstring):get_name())))
    if mine then ents[#ents + 1] = {o = o, e = e, d = vector.distance(q, o:get_pos())} end
  end
end
local fs = V.fs and V.fs[pn]
local hp = p:get_hp()
local engine = {pos = rp(q), yaw = math.floor(p:get_look_horizontal() * 100 + 0.5) / 100, pitch = math.floor(p:get_look_vertical() * 100 + 0.5) / 100,
  hp = hp, hp_max = p:get_properties().hp_max, breath = p:get_breath(),
  food = mcl_hunger and mcl_hunger.get_hunger and mcl_hunger.get_hunger(p) or nil,
  xp_level = mcl_experience and mcl_experience.get_level and mcl_experience.get_level(p) or nil,
  creative = core.is_creative_enabled(pn), time_of_day = math.floor(core.get_timeofday() * 1000) / 1000, day = core.get_day_count(),
  weather = mcl_weather and mcl_weather.state or nil, wielded = p:get_wielded_item():get_name(), inventory = V.items(inv, "main"),
  objects_near = near, screen = hp <= 0 and "dead" or (fs and "formspec" or "in_game"), formspec = fs and fs.name or nil,
  n_mods = #core.get_modnames()}
-- mod: entities of the deliverable mods (nearest 20; scalar luaentity fields, timers left out)
table.sort(ents, function(a, b) return a.d < b.d end)
local mod_ents = {}
for i = 1, math.min(#ents, 20) do
  local o, e = ents[i].o, ents[i].e
  local id = o.get_guid and o:get_guid() or tostring(i); id = tostring(id); if #id > 10 then id = id:sub(-10) end
  local f, keys, seen = {}, {}, {}
  local SKIP = {name = true, mod_origin = true, _active_object_id = true}
  for _, src in ipairs({e, core.registered_entities[e.name] or {}}) do      -- the instance's own fields, then its prototype's
    for k, v in pairs(src) do
      local t = type(v)
      if type(k) == "string" and not seen[k] and not SKIP[k] and (t == "number" or t == "string" or t == "boolean") and not k:lower():find("time") then
        keys[#keys + 1] = k; seen[k] = true
      end
    end
  end
  table.sort(keys)
  for j = 1, math.min(#keys, 12) do
    local v = e[keys[j]]
    if type(v) == "number" then v = (v == math.floor(v)) and v or r1(v) elseif type(v) == "string" then v = txt(v, 60) end
    f[keys[j]] = v
  end
  mod_ents[e.name .. "#" .. id] = {name = e.name, hp = o:get_hp(), pos = rp(o:get_pos()), fields = f}
end
-- mod: player meta keys that are not VoxeLibre's
local pm, nk = {}, 0
local VAN = V.gab_vanilla_meta or {}
for k, v in pairs(p:get_meta():to_table().fields or {}) do
  local pre = k:match("^([^:]+):")
  if not VAN[k] and not (pre and D.vanilla[pre]) and nk < 30 then pm[k] = txt(v); nk = nk + 1 end
end
-- mod: mod storage of the deliverable mods (live StorageRef caught by the bridge hook; else the host reads the db)
local st, need = {}, {}
for _, m in ipairs(D.list) do
  local s = V.storages and V.storages[m]
  if s then
    local vals, n = {}, 0
    for k, v in pairs(s:to_table().fields or {}) do if n < 30 then vals[k] = txt(v); n = n + 1 end end
    st[m] = {source = "live", values = vals}
  else need[#need + 1] = m end
end
-- mod: node metadata of deliverable nodes within 8 nodes (10 nodes, 10 fields each)
local nm, nn = {}, 0
if #D.list > 0 then
  for _, np in ipairs(core.find_nodes_with_meta(vector.subtract(vector.round(q), 8), vector.add(vector.round(q), 8))) do
    local node = core.get_node(np)
    if nn < 10 and is_mod_item(node.name) then
      local f, n = {}, 0
      for k, v in pairs(core.get_meta(np):to_table().fields or {}) do if n < 10 then f[k] = txt(v); n = n + 1 end end
      nm[np.x .. "," .. np.y .. "," .. np.z] = {node = node.name, fields = f}; nn = nn + 1
    end
  end
end
-- screen_text: HUD text elements, the formspec the server showed, chat (last 10)
local sc = {}
for _, h in pairs(p.hud_get_all and p:hud_get_all() or {}) do
  local ty = h.type or h.hud_elem_type
  if (ty == "text" or ty == "waypoint") and #sc < 15 then
    local t = txt(ty == "text" and h.text or h.name)
    if t ~= "" then sc[#sc + 1] = "hud: " .. t end
  end
end
table.sort(sc)
if fs then
  local function un(s) return (s:gsub("\\(.)", "%1")) end
  local function split(s) local out, cur, esc = {}, "", false
    for c in s:gmatch(".") do
      if esc then cur = cur .. "\\" .. c; esc = false elseif c == "\\" then esc = true elseif c == ";" then out[#out + 1] = cur; cur = "" else cur = cur .. c end
    end
    out[#out + 1] = cur; return out end
  local IDX = {label = {2}, vertlabel = {2}, button = {4}, button_exit = {4}, image_button = {5}, image_button_exit = {5},
               item_image_button = {5}, textarea = {4, 5}, field = {4, 5}, checkbox = {3}, hypertext = {4}, textlist = {4}}
  local n = 0
  for ty, body in tostring(fs.spec):gmatch("([%w_]+)%[(.-[^\\])%]") do
    local idx = IDX[ty]
    if idx and n < 15 then
      local a = split(body)
      if ty == "field" and #a == 3 then idx = {2, 3} end
      for _, i in ipairs(idx) do
        local t = a[i] and txt((un(a[i]):gsub("<[^>]*>", ""))) or ""
        if t ~= "" and n < 15 then sc[#sc + 1] = "form: " .. t; n = n + 1 end
      end
    end
  end
end
local ch = V.chat or {}
for i = math.max(1, #ch - 9), #ch do local t = txt(ch[i]); if t ~= "" then sc[#sc + 1] = "chat: " .. t end end
return {engine = engine, mod = {mods = D.list, entities = mod_ents, player_meta = pm, storage = st, node_meta = nm}, screen_text = sc,
        need_db = need, hooks = V.chat ~= nil}
'''


# player meta keys VoxeLibre 0.92.4 itself sets (literal keys of its source; "<mod>:..." keys of a game mod are filtered by prefix)
VANILLA_META = ["_has_bad_omen", "_is_cat", "_is_fire_proof", "_is_invisible", "_is_leaping", "_is_poisoned", "_is_regenerating",
                "_is_strong", "_is_swift", "_is_water_breating", "_is_weak", "_is_withering", "_mcl_minecarts_carts", "darkness", "gamemode",
                "inv", "mcl_info_show", "night_vision", "xp", "workaround", "player_leaves", "set_name", "glow", "inv_items"]
VANILLA_META_LUA = "if not V.gab_vanilla_meta then V.gab_vanilla_meta = {" + ", ".join(f'["{k}"] = true' for k in VANILLA_META) + "} end\n"
ENGINE_KEYS = ["pos", "yaw", "pitch", "hp", "hp_max", "breath", "food", "xp_level", "creative", "time_of_day", "day", "weather", "wielded",
               "inventory", "objects_near", "screen", "formspec", "n_mods"]
MOD_KEYS = {"mods": list, "entities": dict, "player_meta": dict, "storage": dict, "node_meta": dict}
# runs in the sandbox: game process alive? -> the bridge call -> mod storage of mods the hook did not catch, from the world db
STATE_PY = r"""
import base64, json, os, sqlite3, sys, time
w, pidf, rid, timeout = sys.argv[1], sys.argv[2], sys.argv[3], float(sys.argv[4]); code = base64.b64decode(sys.argv[5]).decode()
def alive():
    try: pid = int(open(pidf).read().strip()); return open(f"/proc/{pid}/stat").read().rsplit(")", 1)[1].split()[0] != "Z"
    except Exception: return False
if not alive(): print(json.dumps({"running": False})); sys.exit(0)
d = w + "/vlprobe"; tmp = d + "/req.json.tmp"
open(tmp, "w").write(json.dumps({"id": rid, "lua": code})); os.rename(tmp, d + "/req.json")
t, r = time.time(), None
while time.time() - t < timeout:
    try:
        x = json.load(open(d + "/resp.json"))
        if x.get("id") == rid: r = x; break
    except Exception: pass
    time.sleep(0.03)
if r is None: print(json.dumps({"running": alive(), "error": "the bridge did not answer in %.1f s (loading, or the server is stuck)" % timeout})); sys.exit(0)
if not r.get("ok"): print(json.dumps({"running": True, "error": "lua: " + str(r.get("err"))[:300]})); sys.exit(0)
res = r.get("result") or {}
need = res.pop("need_db", None) or []
if isinstance(need, list) and need:
    if not isinstance(res.get("mod"), dict): res["mod"] = {}
    if not isinstance(res["mod"].get("storage"), dict): res["mod"]["storage"] = {}
    st = res["mod"]["storage"]
    try:
        db = sqlite3.connect("file:%s/mod_storage.sqlite?mode=ro" % w, uri=True, timeout=0.5)
        for m in need:
            rows = db.execute("SELECT key, value FROM entries WHERE modname = ? LIMIT 30", (m,)).fetchall()
            if rows:
                dec = lambda b: (b.decode("utf-8", "replace") if isinstance(b, bytes) else str(b))
                st[m] = {"source": "db", "values": {dec(k): (lambda v: v if len(v) <= 200 else v[:197] + "...")(dec(v)) for k, v in rows}}
    except Exception as e: res["mod"]["storage_error"] = str(e)[:120]
res["running"] = True
print(json.dumps(res))
"""


def shape_state(out):
    """the bridge answer -> the STATE_SPEC layout with the fixed engine keys (null when unknown); empty Lua tables (JSON null / [])
    become {} / []"""
    if not isinstance(out, dict): out = {"error": f"bad answer {str(out)[:100]}"}
    eng = out.get("engine") if isinstance(out.get("engine"), dict) else {}
    mod = out.get("mod") if isinstance(out.get("mod"), dict) else {}
    st = {"running": bool(out.get("running")), "engine": {k: eng.get(k) for k in ENGINE_KEYS},
          "mod": {k: (mod.get(k) if isinstance(mod.get(k), t) else t()) for k, t in MOD_KEYS.items()},
          "screen_text": [str(x)[:200] for x in (out.get("screen_text") or []) if isinstance(out.get("screen_text"), list)]}
    for k in ("inventory", "objects_near"):
        if not isinstance(st["engine"][k], dict): st["engine"][k] = {}
    for e in st["mod"]["entities"].values():
        if isinstance(e, dict) and not isinstance(e.get("fields"), dict): e["fields"] = {}
    for v in st["mod"]["storage"].values():
        if isinstance(v, dict) and not isinstance(v.get("values"), dict): v["values"] = {}
    for v in st["mod"]["node_meta"].values():
        if isinstance(v, dict) and not isinstance(v.get("fields"), dict): v["fields"] = {}
    if mod.get("storage_error"): st["mod"]["storage_error"] = mod["storage_error"]
    if out.get("error"): st["error"] = str(out["error"])[:300]
    if out.get("hooks") is False: st["note"] = "old bridge (started before the state hooks): no formspec / chat lines, storage from the db"
    return st


FATAL = r"ModError|Failed to load and run|ServerError|AsyncErr"
# Luanti log lines worth showing: ERROR[...] (mod errors, Lua tracebacks: every traceback line carries the prefix); the audio
# device a headless seat lacks and the pointer to debug.txt are noise
ERR_SHOW, ERR_NOISE = r"ERROR\[", r"Audio: |Failed to open device|Check debug\.txt for details"


def error_lines(debug_txt): return gbase.error_lines(debug_txt, ERR_SHOW, ERR_NOISE)


class Adapter(GameAdapter):
    name = "voxelibre"
    title = "VoxeLibre (Luanti)"
    extra_help = "  lua 'CODE' | lua -f FILE.lua  run Lua on the running game's server thread (P = the player); returns the value\n"
    screen = (800, 600)            # client window at the top left of the 1024x768 seat screen (no window manager)

    def __init__(self, seat, root=None):
        super().__init__(seat)
        self.root = root or f"{seat.home}/oss-voxelibre"
        self._inst = {}

    def requirements(self):
        return {"game": "voxelibre", "gpu": True, "mem_gb": 1.5, "steam": False, "hosts": ["pool", "docker", "local"],
                "image": "gameanything/voxelibre:0.92.4", "eula": None}

    def I(self, inst):
        if inst not in self._inst: self._inst[inst] = P.VLInstance(inst, self.seat, self.root)
        return self._inst[inst]

    def mdir(self, inst): return f"{self.root}/gab/{inst}"
    def paths(self, inst): return {"game_dir": f"{self.root}/games/voxelibre", "engine_builtin": f"{self.root}/root/usr/share/luanti/builtin",
                                   "world": self.I(inst).world, "game_log": f"{self.I(inst).run_dir}/debug.txt"}

    def install(self):
        ok = lambda: self.seat.exec(f"test -x {self.root}/root/usr/bin/luanti && test -f {self.root}/games/voxelibre/game.conf && echo ok").stdout.strip() == "ok"
        if not ok():
            r = self.I("_install").install()
            if not ok(): raise RuntimeError(f"VoxeLibre install failed: {r.stdout[-400:]} {r.stderr[-400:]}")
        self.I("_install").install_bridge()            # always refresh the bridge + instance script (adds extra-conf support)
        return True

    def provision(self, inst):
        self.install(); self.stop(inst)
        self.seat.exec(f"rm -rf {self.mdir(inst)} {self.root}/runs/{inst} {self.root}/runs/{inst}.extra.conf && mkdir -p {self.mdir(inst)}/mods", check=True)
        self._mods = []
        return {"instance": inst, "dir": self.I(inst).run_dir, "display": self.seat.display}

    @staticmethod
    def _layout(mod_dir):
        m = Path(mod_dir)
        mods = [m] if (m / "init.lua").exists() else sorted(p for p in m.iterdir() if p.is_dir() and (p / "init.lua").exists()) if m.exists() else []
        st = yaml.safe_load((m / "start.yaml").read_text()) if (m / "start.yaml").exists() else {}
        return mods, st or {}

    @staticmethod
    def mod_name(p):
        c = Path(p) / "mod.conf"
        if c.exists():
            m = re.search(r"^\s*name\s*=\s*(\S+)", c.read_text(errors="replace"), re.M)
            if m: return m.group(1)
        return Path(p).name

    def install_mod(self, inst, mod_dir):
        """copy each mod into the instance; start() puts them in the new world's worldmods/ (a player's world-mod install)"""
        mods, st = self._layout(mod_dir); d = self.mdir(inst)
        self.seat.exec(f"rm -rf {d}/mods && mkdir -p {d}/mods")
        self._mods = []
        for m in mods:
            dst = f"{d}/mods/{self.mod_name(m)}"; self.seat.copy_in(m, dst); self._mods.append(dst)
        extra = "".join(f"{k} = {v}\n" for k, v in (st.get("settings") or {}).items())
        self.seat.put(f"{self.root}/runs/{inst}.extra.conf", extra)
        self.seat.put(f"{d}/mods.json", json.dumps(self._mods))
        return {"mods": [Path(x).name for x in self._mods], "settings": st.get("settings") or {}}

    def _installed(self, inst):
        if getattr(self, "_mods", None) is None or not self._mods:
            r = self.seat.exec(f"cat {self.mdir(inst)}/mods.json 2>/dev/null").stdout.strip()
            self._mods = json.loads(r) if r else []
        return self._mods

    def build(self, inst, mod_dir=None):
        mods, st = self._layout(mod_dir) if mod_dir else (None, None)
        L = []
        if mod_dir is not None and not mods: L.append("mod/ holds no Luanti mod (a dir with init.lua, or init.lua at mod/ itself)")
        for m in mods or []:
            n = self.mod_name(m)
            if not NAME_RE.match(n): L.append(f"{m.name}: mod name {n!r} must match [a-z0-9_]+")
            if not (m / "mod.conf").exists(): L.append(f"{m.name}: no mod.conf")
        names = [self.mod_name(m) for m in mods or []]
        if len(set(names)) != len(names): L.append("duplicate mod names")
        return not L, "\n".join(L) or f"ok: mods {names}"

    def start(self, inst):
        """fresh world with the installed mods; ok when the bridge answers (all mods loaded without a fatal Lua error)"""
        I = self.I(inst)
        for k in range(3):                 # a starved client on a shared GPU pod can fail its handshake (probe module note)
            try:
                I.start(self._installed(inst))
                # no window manager: the client window opens centred; move it to 0,0 so screenshots and clicks use window coordinates
                self._x(inst, "for w in $(xwininfo -root -tree | grep -oE '0x[0-9a-f]+ \"(Luanti|Minetest)' | cut -d' ' -f1); do xdotool windowmove $w 0 0; done")
                time.sleep(1)
                return True, "in world"
            except RuntimeError as e:
                I.stop(); err = str(e)
                if "did not come up" in err and self._fatal(inst): break
        return False, err + "\n" + self.log_tail(inst)

    def _fatal(self, inst):
        t = self.seat.exec(f"grep -E 'ModError|Failed to load and run|error:' {self.I(inst).run_dir}/debug.txt | tail -5").stdout
        return bool(t.strip())

    def load_check(self, inst, out_dir=None):
        """the world comes up with the mods and the bridge answers; the log's error lines (mod errors, Lua tracebacks) first"""
        ok, log = self.start(inst)
        txt = self.seat.exec(f"cat {self.I(inst).run_dir}/debug.txt 2>/dev/null").stdout
        why = None if ok else ("a mod error stopped the server (below)" if re.search(FATAL, txt) else
                               "the world with the mods did not come up (the game did not answer within 240 s)")
        if ok: log += "\n" + json.dumps(self.I(inst).log_errors())
        return ok, gbase.gate_log(error_lines(txt), why, log[-3000:])

    def stop(self, inst): self.I(inst).stop()

    def log_tail(self, inst, n=40): return self.seat.exec(f"tail -{n} {self.I(inst).run_dir}/debug.txt").stdout

    def state(self, inst):
        """STATE_SPEC snapshot, live through the bridge (one exec; < 3 s even when the game is gone):
        engine (fixed keys, null when unknown): pos {x,y,z} (0.1), yaw, pitch (rad, 0.01), hp, hp_max, breath, food (VoxeLibre
          hunger 0-20), xp_level, creative, time_of_day (0-1), day, weather, wielded (item), inventory {item: count} (main list),
          objects_near {entity name: count} (radius 32), screen (in_game | formspec | dead), formspec (name of the formspec the
          server showed, or null), n_mods (loaded mods).
        mod (only the deliverable's mods = loaded mods outside the game's mod tree, never the bridge): mods [names];
          entities {"<name>#<guid tail>": {name, hp, pos, fields (scalar luaentity fields, timers left out)}} nearest 20 within 32
          of the deliverable's entities (or dropped items of its items); player_meta {key: value} minus VoxeLibre's keys;
          storage {mod: {source: live | db, values {key: value}}} (live = the mod's StorageRef caught by the bridge's
          get_mod_storage hook; db = the world's mod_storage.sqlite, saved every ~5 s by the engine); node_meta {"x,y,z": {node,
          fields}} of the deliverable's nodes within 8.
        screen_text: "hud: ..." text / waypoint HUD elements, "form: ..." texts of the formspec the server showed (labels, buttons,
          fields, ...), "chat: ..." the last 10 chat lines (chat_send_player / chat_send_all / player chat; bridge hooks).
        Values cut at 200 chars, lists capped (20 entities, 30 keys)."""
        I = self.I(inst)
        code = VANILLA_META_LUA + STATE_LUA
        try:
            r = self.seat.exec("python3 -c " + " ".join(shlex.quote(x) for x in (STATE_PY, I.world, f"{I.run_dir}/client.pid", uuid.uuid4().hex[:12],
                               "2.5", base64.b64encode(code.encode()).decode())), timeout=20)
            out = json.loads(r.stdout.strip().splitlines()[-1])
        except Exception as e: return {"running": False, "engine": dict.fromkeys(ENGINE_KEYS), "mod": {}, "screen_text": [], "error": f"state call failed: {str(e)[:200]}"}
        return shape_state(out)

    def lua(self, inst, code, timeout=30): return self.I(inst).lua(code, timeout=timeout)

    def dev_command(self, inst, k, v, mod, wd):
        if k != "lua": return None
        try:
            code = Path(v[1]).read_text() if v and v[0] == "-f" else " ".join(v)
            return {"ok": True, "result": self.lua(inst, code)}
        except Exception as e: return {"ok": False, "error": str(e)[:1500]}

    def log_errors(self, inst): return self.I(inst).log_errors()

    def display(self, inst): return self.seat.display

    # ---- regression
    def run_probes(self, names, mod_dir=None, label="vanilla"):
        """fresh instance (+ mod) -> the probes (VLProbes.run), as probes_voxelibre.run_instance()"""
        inst = f"probe-{label}"
        self.provision(inst)
        if mod_dir: self.install_mod(inst, mod_dir)
        else: self._mods = []
        ok, log = self.start(inst)
        if not ok: return {n: {"error": f"instance did not start: {log[-200:]}"} for n in names}
        try:
            res = P.VLProbes(self.I(inst)).run([n for n in names if n != "log_errors"])
            res["log_errors"] = self.I(inst).log_errors()
            return res
        finally: self.stop(inst)

    def compare(self, spec, base, got):
        """pool `probe:` spec -> (ok | None, detail): "x" = the module's compare() with its tolerances; "x.f1+f2" = those
        fields equal to vanilla (as g1_regress.item_result())"""
        name, _, fields = spec.partition(".")
        b, g = base.get(name), got.get(name, {"error": "not run"})
        if b is None: return None, "not in the vanilla baseline"
        if isinstance(g, dict) and "error" in g: return None, f"probe error: {g['error']}"
        if isinstance(b, dict) and "error" in b: return None, f"vanilla baseline broken: {b['error']}"
        if not fields: return P.compare(name, b, g)
        diffs = {f: f"{b.get(f)} -> {g.get(f)}" for f in fields.split("+") if b.get(f) != g.get(f)}
        return not diffs, diffs or "same as vanilla"

    def version(self):
        v = self.seat.exec(f"LD_LIBRARY_PATH={self.root}/root/usr/lib/x86_64-linux-gnu {self.root}/root/usr/bin/luanti --version 2>/dev/null | head -1; "
                           f"grep -m1 ^version {self.root}/games/voxelibre/game.conf 2>/dev/null").stdout.split("\n")
        return {"game": "voxelibre", "build": (v[1].split("=", 1)[-1].strip() if len(v) > 1 else "?"), "mod_loader": v[0].strip(), "image": self.seat.sandbox}
