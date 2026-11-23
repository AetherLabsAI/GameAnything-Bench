"""VoxeLibre (Luanti) regression probes + instance control. Ported from the original probes_voxelibre.py
(verified in game 2026-10-30: VoxeLibre 0.92.4 on Luanti 5.17.0, A/A + negative checks in pool_voxelibre.yaml). Changes:
runs through a gab Seat (any host backend) instead of docker exec on a fixed container; root / display / GL wrapper are
arguments; vl-instance.sh also accepts extra minetest.conf lines; no CLI.

Runtime: a real singleplayer client (luanti --go) on the seat's X display; world mod `vlprobe` is the bridge (the host writes
<world>/vlprobe/req.json {id, lua}; the mod runs it on the server thread and writes resp.json). Real input through xdotool.
"""
import base64, json, os, shlex, time, uuid

from gab.game.base import vglrun

LUANTI_DEB = "https://ppa.launchpadcontent.net/minetestdevs/stable/ubuntu/pool/main/m/minetest/minetest_5.17.0-ppa0~ubuntu24.04.1_amd64.deb"
UBUNTU_DEBS = ["pool/main/s/snappy/libsnappy1v5_1.1.10-1build1_amd64.deb",
               "pool/universe/l/leveldb/libleveldb1d_1.23-5build1_amd64.deb",
               "pool/universe/l/luajit/libluajit-5.1-2_2.1.0+git20231223.c525bcb+dfsg-1_amd64.deb",
               "pool/universe/l/luajit/libluajit-5.1-common_2.1.0+git20231223.c525bcb+dfsg-1_all.deb"]
VOXELIBRE_TAG = "0.92.4"
SEED = 20261030
PAD = (300, 120, 300)        # probe pad, away from the 97x97 terrain area around the origin

BRIDGE_LUA = r'''
-- vlprobe: probe bridge for GameAnything-Bench (probes_voxelibre.py). No gameplay effect.
-- Request <world>/vlprobe/req.json {"id","lua"} (host writes tmp + rename); response resp.json {"id","ok","result"|"err"}.
local WP = core.get_worldpath() .. "/vlprobe"
core.mkdir(WP)
vlprobe = {steps = {}, n = 0}
local V = vlprobe
local last_id
local function write(path, s)
	local f = io.open(path .. ".tmp", "w"); f:write(s); f:close(); os.rename(path .. ".tmp", path)
end
function V.player() for _, p in ipairs(core.get_connected_players()) do return p end end
function V.pos(p) local q = p:get_pos(); return {x = q.x, y = q.y, z = q.z} end
function V.items(inv, list)
	local out = {}
	for _, s in ipairs(inv:get_list(list) or {}) do
		if not s:is_empty() then out[s:get_name()] = (out[s:get_name()] or 0) + s:get_count() end
	end
	return out
end
local function handle()
	local f = io.open(WP .. "/req.json", "r"); if not f then return end
	local s = f:read("*a"); f:close()
	local ok_j, req = pcall(core.parse_json, s)
	if not ok_j or type(req) ~= "table" or req.id == last_id then return end
	last_id = req.id
	local out = {id = req.id}
	local fn, err = loadstring("local P, V = ...\n" .. (req.lua or ""))
	if not fn then out.ok = false; out.err = "compile: " .. tostring(err)
	else
		local ok, res = pcall(fn, V.player(), V)
		out.ok = ok; if ok then out.result = res else out.err = tostring(res) end
	end
	local js = core.write_json(out)
	if not js then js = core.write_json({id = req.id, ok = false, err = "result not JSON-serialisable"}) end
	write(WP .. "/resp.json", js)
end
V.hp = {}                -- every player hp change with its reason (fall damage is read from here, not from hp: regen is fast)
core.register_on_player_hpchange(function(player, change, reason)
	V.hp[#V.hp + 1] = {change = change, type = reason and reason.type or "?", t = core.get_us_time()}
	if #V.hp > 200 then table.remove(V.hp, 1) end
end, false)
local t0
local function first(dtime) t0 = core.get_us_time() end
local function lastcb(dtime)
	if t0 then V.n = V.n + 1; V.steps[(V.n - 1) % 400 + 1] = {core.get_us_time() - t0, dtime} end
	handle()
end
core.register_globalstep(first)
core.register_globalstep(lastcb)
core.register_on_mods_loaded(function()      -- time all other globalsteps: first..last
	local gs = core.registered_globalsteps
	for i, f in ipairs(gs) do if f == first then table.remove(gs, i); break end end
	table.insert(gs, 1, first)
	for i, f in ipairs(gs) do if f == lastcb then table.remove(gs, i); break end end
	table.insert(gs, lastcb)
end)
-- state hooks (STATE_SPEC, 2026-10-31): record what the game shows / keeps for mods; every wrapper calls the original with the
-- same arguments and returns its result (no gameplay change). The bridge has no dependencies, so it loads before every mod that
-- has one (Luanti's dependency sort), i.e. before the deliverable's mods in practice.
V.chat, V.fs, V.storages = {}, {}, {}
local function chat_add(s)
	V.chat[#V.chat + 1] = tostring(s); if #V.chat > 30 then table.remove(V.chat, 1) end
end
local o_csp, o_csa = core.chat_send_player, core.chat_send_all
core.chat_send_player = function(name, text, ...) pcall(chat_add, text); return o_csp(name, text, ...) end
core.chat_send_all = function(text, ...) pcall(chat_add, text); return o_csa(text, ...) end
core.register_on_chat_message(function(name, msg) pcall(chat_add, "<" .. tostring(name) .. "> " .. tostring(msg)) end)
local o_sf, o_cf = core.show_formspec, core.close_formspec
core.show_formspec = function(pn, fname, fs, ...)
	pcall(function() if fs == "" then V.fs[pn] = nil else V.fs[pn] = {name = fname, spec = fs} end end)
	return o_sf(pn, fname, fs, ...)
end
core.close_formspec = function(pn, fname, ...)
	pcall(function() if fname == "" or (V.fs[pn] and V.fs[pn].name == fname) then V.fs[pn] = nil end end)
	return o_cf(pn, fname, ...)
end
core.register_on_player_receive_fields(function(player, fname, fields)
	pcall(function()
		local pn = player:get_player_name()
		if fields.quit and V.fs[pn] and V.fs[pn].name == fname then V.fs[pn] = nil end
	end)
end)
local o_gms = core.get_mod_storage
core.get_mod_storage = function(...)
	local s = o_gms(...)
	pcall(function() local m = core.get_current_modname(); if m and s then V.storages[m] = s end end)
	return s
end
write(WP .. "/ready.json", core.write_json({loaded = true}))
'''

INSTANCE_SH = r'''#!/bin/bash
# VoxeLibre probe instance. usage: vl-instance.sh NAME start|stop [MODDIR...]   (env: VL_ROOT VL_DISPLAY VL_SEAT VL_GUARD VL_GL)
set -u
T=${VL_ROOT:-/home/player/vltest}; DSP=${VL_DISPLAY:-:106}; NAME=$1; ACT=$2; shift 2
G=${VL_GUARD:-/lab/bin/lab_guard.py}; [ ! -f "$G" ] || python3 "$G" check --seat "${VL_SEAT:-2}" || exit 3   # seat lease (M2W_LEASE)
R=$T/runs/$NAME
export LD_LIBRARY_PATH=$T/root/usr/lib/x86_64-linux-gnu HOME=$R/home MINETEST_USER_PATH=$R/home/.minetest
stop() { local p; p=$(cat $R/client.pid 2>/dev/null); [ -n "$p" ] || return 0
  kill "$p" 2>/dev/null; for i in 1 2 3 4 5; do kill -0 "$p" 2>/dev/null || break; sleep 1; done; kill -9 "$p" 2>/dev/null; : > $R/client.pid; }
if [ "$ACT" = stop ]; then stop; echo "stopped $NAME"; exit 0; fi
if [ "$ACT" = xstop ]; then p=$(cat $T/xvfb.pid 2>/dev/null); [ -n "$p" ] && ps -o args= -p "$p" | grep -q "Xvfb $DSP" && kill "$p"; exit 0; fi
if ! xdpyinfo -display $DSP >/dev/null 2>&1; then
  setsid nohup Xvfb $DSP -screen 0 1024x768x24 -nolisten tcp > $T/xvfb.log 2>&1 < /dev/null & echo $! > $T/xvfb.pid
  for i in $(seq 1 20); do xdpyinfo -display $DSP >/dev/null 2>&1 && break; sleep 0.5; done
fi
mkdir -p $R; stop; rm -rf $R; mkdir -p $R/home/.minetest/games $R/world/worldmods
ln -s $T/games/voxelibre $R/home/.minetest/games/voxelibre
cp -r $T/bridge/vlprobe $R/world/worldmods/
for m in "$@"; do cp -r "$m" $R/world/worldmods/; done
printf 'gameid = voxelibre\nbackend = sqlite3\nplayer_backend = sqlite3\nauth_backend = sqlite3\nmod_storage_backend = sqlite3\ncreative_mode = false\nenable_damage = true\n' > $R/world/world.mt
cat > $R/home/.minetest/minetest.conf <<CONF
fixed_map_seed = __SEED__
mg_name = v7
screen_w = 800
screen_h = 600
fullscreen = false
enable_sound = false
viewing_range = 60
max_block_send_distance = 6
max_block_generate_distance = 6
enable_shaders = false
enable_dynamic_shadows = false
enable_clouds = false
show_welcome_message = false
mcl_craftguide_progressive_mode = false
mcl_game_music = false
debug_log_level = action
pause_on_lost_focus = false
creative_mode = false
enable_damage = true
CONF
[ -f $T/runs/$NAME.extra.conf ] && cat $T/runs/$NAME.extra.conf >> $R/home/.minetest/minetest.conf
cd $R
DISPLAY=$DSP setsid nohup ${VL_GL:-vglrun -d egl} $T/root/usr/bin/luanti --go --world $R/world --gameid voxelibre \
  --config $R/home/.minetest/minetest.conf --logfile $R/debug.txt > $R/client.out 2>&1 < /dev/null & echo $! > $R/client.pid
echo "started $NAME pid $(cat $R/client.pid)"
'''.replace("__SEED__", str(SEED))

INSTALL_SH = r'''set -e
T=${VL_ROOT:-/home/player/vltest}; mkdir -p $T/debs $T/root $T/games $T/bridge/vlprobe $T/runs
cd $T/debs
[ -f luanti.deb ] || curl -sSL -o luanti.deb "__DEB__"
for f in __UBU__; do b=$(basename $f); [ -f $b ] || curl -sSLO http://archive.ubuntu.com/ubuntu/$f; done
for d in *.deb; do dpkg -x $d $T/root; done
[ -d $T/games/voxelibre ] || git -c advice.detachedHead=false clone -q --depth 1 --branch __TAG__ https://git.minetest.land/VoxeLibre/VoxeLibre.git $T/games/voxelibre
LD_LIBRARY_PATH=$T/root/usr/lib/x86_64-linux-gnu $T/root/usr/bin/luanti --version | head -1
grep ^version $T/games/voxelibre/game.conf
'''.replace("__DEB__", LUANTI_DEB).replace("__UBU__", " ".join(UBUNTU_DEBS)).replace("__TAG__", VOXELIBRE_TAG)

_CALL = r'''
import base64, json, os, sys, time
w, rid, timeout = sys.argv[1], sys.argv[2], float(sys.argv[3]); code = base64.b64decode(sys.argv[4]).decode()
d = w + "/vlprobe"; tmp = d + "/req.json.tmp"
open(tmp, "w").write(json.dumps({"id": rid, "lua": code})); os.rename(tmp, d + "/req.json")
t = time.time()
while time.time() - t < timeout:
    try:
        r = json.load(open(d + "/resp.json"))
        if r.get("id") == rid: print(json.dumps(r)); sys.exit(0)
    except Exception: pass
    time.sleep(0.05)
print(json.dumps({"id": rid, "ok": False, "err": "timeout"}))
'''


class VLInstance:
    """One fresh VoxeLibre world + singleplayer client in the sandbox. NOTE: docker exec over remote docker never delivers EOF on
    stdin (seen 2026-10-30: a stdin-fed call hung), so everything goes in argv (base64) and stdin is never used."""

    def __init__(self, name, seat, root, display=None, gl=None, guard="/lab/bin/lab_guard.py"):
        self.name, self.seat, self.root = name, seat, root
        self.display = display or seat.display
        self.gl = gl or vglrun()
        self.guard = guard
        self.run_dir = f"{root}/runs/{name}"; self.world = f"{self.run_dir}/world"

    def sh(self, cmd, timeout=120):
        return self.seat.exec(cmd, timeout=timeout, env={"VL_ROOT": self.root, "VL_DISPLAY": self.display, "VL_SEAT": self.seat.seat,
                                                         "VL_GUARD": self.guard, "VL_GL": self.gl})

    def put(self, path, text): return self.seat.put(path, text)

    def install(self):
        """Luanti 5.17.0 (official minetestdevs PPA build, unpacked user-local: no root needed) + VoxeLibre + bridge."""
        r = self.sh(INSTALL_SH, timeout=900); self.install_bridge(); return r

    def install_bridge(self):
        self.put(f"{self.root}/bridge/vlprobe/mod.conf", "name = vlprobe\ndescription = GameAnything-Bench probe bridge\n")
        self.put(f"{self.root}/bridge/vlprobe/init.lua", BRIDGE_LUA)
        self.put(f"{self.root}/bridge/vl-instance.sh", INSTANCE_SH)

    def start(self, mod_dirs=(), wait=240):
        """mod_dirs: directories INSIDE the sandbox, copied into the world's worldmods/ (the modded instance)."""
        r = self.sh(f"bash {self.root}/bridge/vl-instance.sh {self.name} start " + " ".join(mod_dirs))
        if r.returncode: raise RuntimeError(f"start failed: {r.stdout[-300:]} {r.stderr[-300:]}")
        t = time.time()
        while time.time() - t < wait:                       # bridge loaded and the player is in the world
            time.sleep(3)
            try:
                if self.lua("return P ~= nil and V.n > 20", timeout=5): time.sleep(8); return True
            except RuntimeError: pass
        raise RuntimeError(f"instance {self.name} did not come up: {self.sh(f'tail -5 {self.run_dir}/debug.txt').stdout}")

    def stop(self): return self.sh(f"bash {self.root}/bridge/vl-instance.sh {self.name} stop")

    def lua(self, code, timeout=15):
        r = self.seat.exec("python3 -c " + " ".join(shlex.quote(x) for x in (_CALL, self.world, uuid.uuid4().hex[:12], str(timeout),
                           base64.b64encode(code.encode()).decode())), timeout=timeout + 30)
        try: out = json.loads(r.stdout.strip().splitlines()[-1])
        except Exception: raise RuntimeError(f"bridge call failed: {r.stderr[-300:]}")
        if not out.get("ok"): raise RuntimeError(f"lua error: {out.get('err')}")
        return out.get("result")

    def xdo(self, *cmds):
        """Real input on the seat display."""
        # no window manager on the lab displays: the client window already has the X focus; XTEST input goes to it
        c = "; ".join(cmds)
        return self.sh(f"export DISPLAY={self.display}; {c}")

    def hold(self, keys, seconds):
        down = "; ".join(f"xdotool keydown {k}" for k in keys); up = "; ".join(f"xdotool keyup {k}" for k in keys)
        return self.xdo(down, f"sleep {seconds}", up)

    def log_text(self): return self.sh(f"cat {self.run_dir}/debug.txt").stdout

    def log_errors(self):
        lines = [l for l in self.log_text().splitlines() if "ERROR[" in l]
        return {"errors": len(lines), "_lines": lines[-20:]}

    def screenshot(self, host_path):
        """F12 = Luanti's own screenshot (PNG in the instance's user dir), copied to the host."""
        self.xdo("xdotool key F12"); time.sleep(1.5)
        r = self.sh(f"ls -t {self.run_dir}/home/.minetest/screenshots/*.png 2>/dev/null | head -1")
        p = r.stdout.strip()
        if p: self.seat.copy_out(p, host_path)
        return p


# --- Lua snippets -------------------------------------------------------------------------------------------------------
L_PAD = r'''
local x, y, z = %d, %d, %d
local p1, p2 = {x = x - 4, y = y - 2, z = z - 8}, {x = x + 22, y = y + 48, z = z + 8}
if not V.pad_ready then
  V.pad_ready = "emerging"
  core.emerge_area(p1, p2, function(bp, action, remaining) if remaining == 0 then V.pad_ready = "emerged" end end)
  return "emerging"
end
if V.pad_ready ~= "emerged" and V.pad_ready ~= "built" then return V.pad_ready end
for xx = x - 2, x + 20 do for zz = z - 6, z + 6 do
  core.set_node({x = xx, y = y - 1, z = zz}, {name = "mcl_core:stone"})
  for yy = y, y + 12 do core.set_node({x = xx, y = yy, z = zz}, {name = "air"}) end
end end
for _, o in ipairs(core.get_objects_inside_radius({x = x + 9, y = y, z = z}, 24)) do
  if not o:is_player() then o:remove() end
end
V.pad_ready = "built"
P:set_pos({x = x + 0.5, y = y + 0.1, z = z + 0.5}); P:set_look_horizontal(-math.pi / 2); P:set_look_vertical(0)
P:set_velocity({x = 0, y = 0, z = 0})
P:set_hp(P:get_properties().hp_max); mcl_hunger.set_hunger(P, 20); mcl_hunger.set_saturation(P, 5); mcl_hunger.set_exhaustion(P, 0)
return "built"
''' % PAD


class VLProbes:
    def __init__(self, inst):
        self.I = inst

    def lua(self, code, timeout=15): return self.I.lua(code, timeout)

    def _pad(self):
        """A clean stone floor at PAD (air above), player on it facing +x, full health / food."""
        self.lua("V.pad_ready = nil; return 1")
        for _ in range(40):
            s = self.lua(L_PAD, timeout=20)
            if s == "built": break
            time.sleep(1)
        else: raise RuntimeError("pad area did not emerge")
        time.sleep(3.0)                                     # the client receives the new blocks (collision)

    def _back(self):
        self.lua("P:set_pos(V.spawn0 or mcl_spawn.get_world_spawn_pos()); P:set_velocity({x=0,y=0,z=0}); return 1"); time.sleep(1.5)

    def _player(self):
        return self.lua("local q = P:get_pos(); return {x = q.x, y = q.y, z = q.z, hp = P:get_hp()}")

    # ---- global settings ----
    def game_rules(self):
        """Every VoxeLibre gamerule (vl_tuning `gamerule:*`) plus the engine / game settings that act as rules."""
        return self.lua(r'''
local out = {}
for name, t in pairs(vl_tuning.registered_settings) do
  if name:sub(1, 9) == "gamerule:" then out[name:sub(10)] = t:get_string() end
end
local s = core.settings
out["@enable_damage"] = tostring(core.settings:get_bool("enable_damage", false))
out["@creative_mode"] = tostring(core.is_creative_enabled(P:get_player_name()))
out["@only_peaceful_mobs"] = tostring(s:get_bool("only_peaceful_mobs", false))
out["@mobs_spawn"] = tostring(s:get_bool("mobs_spawn", true))
out["@mobs_griefing"] = tostring(s:get_bool("mobs_griefing", true))
out["@mcl_enable_hunger"] = tostring(s:get_bool("mcl_enable_hunger", true))
out["@time_speed"] = s:get("time_speed") or "72"
out["@hunger_active"] = tostring(mcl_hunger.get_active())
return out''')

    # ---- world behaviour ----
    def time_advances(self):
        a = self.lua("return {t = core.get_timeofday(), us = core.get_us_time()}"); time.sleep(6)
        b = self.lua("return {t = core.get_timeofday(), us = core.get_us_time()}")
        dt = (b["us"] - a["us"]) / 1e6; d = (b["t"] - a["t"]) % 1.0
        return {"advances": d > 0, "day_fraction_per_s": round(d / dt, 7)}

    def weather_changes(self):
        r = self.lua('local ok = mcl_weather.change_weather("rain", nil, "g1probe"); return {ok = ok, state = mcl_weather.state}'); time.sleep(2)
        r2 = self.lua('return mcl_weather.state')
        c = self.lua('mcl_weather.change_weather("none", nil, "g1probe"); return mcl_weather.state'); time.sleep(1)
        return {"can_rain": r2 == "rain", "can_clear": c == "none"}

    def mobs_exist(self):
        """A passive (pig) and a hostile (zombie) mob placed 6 blocks away still exist 4 s later (peaceful / despawn hooks)."""
        self.lua(r'''
local q = P:get_pos(); V.mobs = {}
for i, n in ipairs({"mobs_mc:pig", "mobs_mc:zombie"}) do
  local o = core.add_entity({x = q.x + 6, y = q.y + 1, z = q.z + 3 * i}, n)
  if o then local e = o:get_luaentity(); if e then e.g1probe = true end; V.mobs[n] = o end
end
return 1''')
        time.sleep(4)
        return self.lua(r'''
local out = {}
for n, o in pairs(V.mobs) do out[n:gsub("mobs_mc:", "") .. "_stays"] = (o:get_pos() ~= nil) and (o:get_luaentity() ~= nil); o:remove() end
out.pig_stays = out.pig_stays or false; out.zombie_stays = out.zombie_stays or false
return out''')

    def mob_spawning(self):
        """Natural spawning really runs: after clearing mobs within 64 blocks (caps), mobs_mc entities the game adds in 45 s at midnight (time restored)."""
        self.lua(r'''
V.spawned = 0
if not V.spawn_hook then
  V.spawn_hook = true
  local old = core.add_entity
  core.add_entity = function(pos, name, ...)
    local o = old(pos, name, ...)
    if o and type(name) == "string" and name:find("^mobs_mc:") and V.counting then V.spawned = V.spawned + 1 end
    return o
  end
end
-- the mob caps count mobs already near the player (earlier probes, earlier minutes): clear them so the caps do not hide spawning
local q = P:get_pos()
for _, o in ipairs(core.get_objects_inside_radius(q, 64)) do
  local e = o:get_luaentity(); if e and type(e.name) == "string" and e.name:find("^mobs_mc:") then o:remove() end
end
V.counting = true; V.tod0 = core.get_timeofday(); core.set_timeofday(0.0)   -- midnight: hostile spawning too
return 1''')
        time.sleep(45)
        return {"spawned_in_45s_night": self.lua("V.counting = false; core.set_timeofday(V.tod0); return V.spawned")}

    def terrain_spawn(self):
        """Surface height of the 97x97 columns around the world origin (fixed seed in every instance)."""
        self.lua("V.ter = nil; core.emerge_area({x=-48,y=-32,z=-48},{x=48,y=127,z=48}, function(bp,a,rem) if rem == 0 then V.ter = true end end); return 1")
        for _ in range(90):
            if self.lua("return V.ter == true"): break
            time.sleep(1)
        return self.lua(r'''
local vm = VoxelManip(); local e1, e2 = vm:read_from_map({x=-48,y=-32,z=-48},{x=48,y=127,z=48})
local a = VoxelArea:new({MinEdge = e1, MaxEdge = e2}); local d = vm:get_data()
local air, ign = core.get_content_id("air"), core.get_content_id("ignore")
local rows = {}
for z = -48, 48 do local row = {}
  for x = -48, 48 do local h = -33
    for y = 127, -32, -1 do local c = d[a:index(x, y, z)]; if c ~= air and c ~= ign then h = y; break end end
    row[#row + 1] = h end
  rows[#rows + 1] = row end
return {heights = rows}''', timeout=60)

    def perf(self):
        """Lua time of one server step (all globalsteps) and the step interval, averaged over 10 s of idle play."""
        time.sleep(10)
        return self.lua(r'''
local n = math.min(V.n, 400); local us, dt = 0, 0
for i = 1, n do us = us + V.steps[i][1]; dt = dt + V.steps[i][2] end
return {lua_ms_per_step = us / n / 1000, step_ms = dt / n * 1000, samples = n}''')

    # ---- player ----
    def player_start(self):
        return self.lua(r'''
local q = P:get_pos(); local sp = mcl_spawn.get_world_spawn_pos()
V.spawn0 = V.spawn0 or {x = q.x, y = q.y, z = q.z}
local inv = P:get_inventory()
local gm = mcl_gamemode and mcl_gamemode.get_gamemode and mcl_gamemode.get_gamemode(P) or "?"
local items = {}
for n, c in pairs(V.items(inv, "main")) do items[#items + 1] = n .. " " .. c end
table.sort(items)
local under = core.registered_nodes[core.get_node({x = q.x, y = q.y - 0.6, z = q.z}).name]
-- no world spawn is set in vanilla: the engine picks a random surface spot near the origin (A/A: up to ~70 blocks apart)
return {near_origin = math.max(math.abs(q.x), math.abs(q.z)) <= 128, on_ground = under ~= nil and under.walkable == true,
        _block = {math.floor(q.x + 0.5), math.floor(q.y + 0.5), math.floor(q.z + 0.5)}, world_spawn = {sp.x, sp.y, sp.z},
        health = P:get_hp(), hp_max = P:get_properties().hp_max, breath = P:get_breath(),
        food = mcl_hunger.get_hunger(P), saturation = mcl_hunger.get_saturation(P), game_mode = gm,
        items = items, main_size = inv:get_size("main"), hotbar = P:hud_get_hotbar_itemcount()}''')

    def move_jump(self):
        """Real keys: W on the flat pad (walking speed), W + sprint key (aux1 = E), then W + space onto a 1-block step."""
        try:
            walks, sprints = [], []
            for _ in range(2):       # A/A: one try in ~8 walked at sprint speed / sprinted slower -> walk = min, sprint = max of 2
                self._pad(); a = self._player(); self.I.hold(["w"], 1.5); time.sleep(0.6); b = self._player(); walks.append(b["x"] - a["x"])
                self._pad(); c = self._player(); self.I.hold(["w", "e"], 1.5); time.sleep(0.6); d = self._player(); sprints.append(d["x"] - c["x"])
            self._pad()
            self.lua("for xx = %d, %d do for zz = %d, %d do core.set_node({x=xx,y=%d,z=zz},{name='mcl_core:stone'}) end end; return 1"
                     % (PAD[0] + 3, PAD[0] + 20, PAD[2] - 6, PAD[2] + 6, PAD[1])); time.sleep(1.5)
            self.I.hold(["w", "space"], 2.0); time.sleep(0.8); e = self._player()
            walk, sprint = min(walks), max(sprints)
            return {"walks": walk >= 2, "sprint_faster": sprint > 1.15 * walk, "jumps_onto_step": e["y"] >= PAD[1] - 0.5 + 0.9,
                    "walk_blocks_1_5s": round(walk, 2), "sprint_blocks_1_5s": round(sprint, 2), "_dbg": {"walks": walks, "sprints": sprints, "e": e}}
        finally: self._back()

    def fall_damage(self):
        """Dropped from 12 blocks onto stone: fall damage taken (sum of hp changes with reason "fall"; vanilla 9)."""
        try:
            self._pad()
            self.lua("V.hp = {}; P:set_pos({x=%f,y=%f,z=%f}); P:set_velocity({x=0,y=0,z=0}); return 1" % (PAD[0] + 0.5, PAD[1] + 12, PAD[2] + 0.5))
            time.sleep(4)
            r = self.lua("local d = 0; for _, h in ipairs(V.hp) do if h.type == 'fall' and h.change < 0 then d = d - h.change end end; return {d = d, hp = P:get_hp(), n = #V.hp}")
            return {"took_damage": r["d"] > 0, "damage": r["d"], "_dbg": r}
        finally:
            self.lua("P:set_hp(P:get_properties().hp_max); return 1"); self._back()

    def hunger_eat(self):
        """Hungry player holds the use button with bread (real mouse, looking at the sky): bread eaten, food goes up."""
        try:
            self._pad()
            self.lua(r'''mcl_hunger.set_hunger(P, 10); V.wi = P:get_wield_index(); P:get_inventory():set_stack("main", V.wi, "mcl_farming:bread 3")
P:set_look_vertical(-math.pi / 2); return 1''')
            time.sleep(1.0)
            self.I.xdo("xdotool mousedown 3", "sleep 3", "xdotool mouseup 3"); time.sleep(1.0)
            r = self.lua('return {food = mcl_hunger.get_hunger(P), bread = P:get_inventory():get_stack("main", V.wi):get_count()}')
            return {"ate": r["bread"] < 3 and r["food"] > 10, "_dbg": r}
        finally:
            self.lua('P:get_inventory():set_stack("main", V.wi or 1, ""); P:set_look_vertical(0); mcl_hunger.set_hunger(P, 20); return 1'); self._back()

    def place_break(self):
        """Engine placement / digging as the player: dirt placed on the floor, dug again, and the dug dirt drops."""
        try:
            self._pad()
            return self.lua(r'''
local x, y, z = %d, %d, %d
local pos = {x = x + 2, y = y, z = z}
local inv = P:get_inventory(); local wi = P:get_wield_index(); inv:set_stack("main", wi, "mcl_core:dirt 1")
local st = inv:get_stack("main", wi)
local left = core.item_place(st, P, {type = "node", under = {x = pos.x, y = y - 1, z = pos.z}, above = pos})
inv:set_stack("main", wi, left or "")
local placed = core.get_node(pos).name == "mcl_core:dirt"
inv:set_stack("main", wi, "")
local dug = core.node_dig(pos, core.get_node(pos), P)
local broken = core.get_node(pos).name == "air"
local drop = false
for _, o in ipairs(core.get_objects_inside_radius(pos, 2)) do
  local e = o:get_luaentity(); if e and e.name == "__builtin:item" and e.itemstring:find("mcl_core:dirt") then drop = true; o:remove() end
end
if V.items(inv, "main")["mcl_core:dirt"] then drop = true; inv:remove_item("main", "mcl_core:dirt 64") end
return {place = placed, ["break"] = broken, drops = drop}''' % PAD)
        finally: self._back()

    def dig_times(self):
        """Dig time and diggability for (block, tool) pairs from the live tool capabilities, and the drops."""
        return self.lua(r'''
local pairs_ = {{"mcl_core:stone", ""}, {"mcl_core:stone", "mcl_tools:pick_wood"}, {"mcl_core:stone", "mcl_tools:pick_iron"},
  {"mcl_core:dirt", ""}, {"mcl_core:dirt", "mcl_tools:shovel_wood"}, {"mcl_core:tree", ""}, {"mcl_core:tree", "mcl_tools:axe_stone"},
  {"mcl_core:obsidian", "mcl_tools:pick_iron"}, {"mcl_core:obsidian", "mcl_tools:pick_diamond"}, {"mcl_core:stone_with_iron", "mcl_tools:pick_wood"},
  {"mcl_core:stone_with_iron", "mcl_tools:pick_stone"}, {"mcl_core:sand", ""}, {"mcl_core:glass", ""}}
local hs = P:get_inventory():get_stack("hand", 1)        -- VoxeLibre's hand is an item in the player's "hand" list
local hand = (hs:is_empty() and ItemStack("") or hs):get_tool_capabilities()
local out = {}
for _, pr in ipairs(pairs_) do
  local def = core.registered_nodes[pr[1]]
  local caps = pr[2] == "" and hand or ItemStack(pr[2]):get_tool_capabilities()
  local dp = core.get_dig_params(def and def.groups or {}, caps)
  local drops = core.get_node_drops(pr[1], pr[2])
  table.sort(drops)
  out[pr[1] .. " / " .. (pr[2] == "" and "hand" or pr[2])] = {diggable = dp.diggable, time = math.floor(dp.time * 1000 + 0.5) / 1000,
     drops = table.concat(drops, ",")}
end
return out''')

    def crafting(self):
        """Key recipes produce the vanilla result, and every vanilla recipe still exists (signature per output item)."""
        return self.lua(r'''
local function r(method, width, items) local o = core.get_craft_result({method = method, width = width, items = items}); return o.item:to_string() end
local W, S, C = "mcl_core:wood", "mcl_core:stick", "mcl_core:cobble"
local key = {
  planks = r("normal", 1, {"mcl_core:tree"}), sticks = r("normal", 1, {W, W}),
  crafting_table = r("normal", 2, {W, W, W, W}), wooden_pickaxe = r("normal", 3, {W, W, W, "", S, "", "", S, ""}),
  furnace = r("normal", 3, {C, C, C, C, "", C, C, C, C}), torch = r("normal", 1, {"mcl_core:coal_lump", S}),
  smelt_raw_iron = r("cooking", 1, {"mcl_raw_ores:raw_iron"}), smelt_sand = r("cooking", 1, {"mcl_core:sand"}),
  fuel_coal = core.get_craft_result({method = "fuel", width = 1, items = {"mcl_core:coal_lump"}}).time }
local sig = {}
for name in pairs(core.registered_items) do
  local rs = name ~= "" and core.get_all_craft_recipes(name) or nil
  if rs then
    local list = {}
    for _, rc in ipairs(rs) do
      local its = {}; local maxi = 0
      for i in pairs(rc.items or {}) do if i > maxi then maxi = i end end
      for i = 1, maxi do its[i] = rc.items[i] or "" end
      local outp = (rc.output or ""):gsub("^(mcl_compass:)%d+", "%1N"):gsub("^(mcl_clock:clock_)%d+", "%1N")   -- frame chosen at random on load
      list[#list + 1] = core.sha1((rc.method or "") .. "|" .. (rc.width or 0) .. "|" .. table.concat(its, ",") .. "|" .. outp):sub(1, 10)
    end
    table.sort(list); sig[name:gsub("^(mcl_compass:)%d+", "%1N"):gsub("^(mcl_clock:clock_)%d+", "%1N")] = list
  end
end
return {key = key, recipes = sig}''', timeout=60)

    def furnace_smelts(self):
        """A real furnace cooks raw iron with coal into an iron ingot (node timer, ~10 s)."""
        try:
            self._pad()
            self.lua(r'''
local pos = {x = %d + 3, y = %d, z = %d + 3}; V.fpos = pos
core.set_node(pos, {name = "mcl_furnaces:furnace"})
local inv = core.get_meta(pos):get_inventory()
inv:set_stack("src", 1, "mcl_raw_ores:raw_iron 1"); inv:set_stack("fuel", 1, "mcl_core:coal_lump 1")
core.get_node_timer(pos):start(1.0)
return 1''' % PAD)
            time.sleep(14)
            return self.lua(r'''local inv = core.get_meta(V.fpos):get_inventory()
local d = inv:get_stack("dst", 1):to_string(); core.remove_node(V.fpos)
return {smelted = d == "mcl_core:iron_ingot", _dbg = d}''')
        finally: self._back()

    def containers(self):
        """A chest placed like a player has its 27-slot inventory and keeps items put in it."""
        try:
            self._pad()
            return self.lua(r'''
local pos = {x = %d + 2, y = %d, z = %d - 3}
core.place_node(pos, {name = "mcl_chests:chest"}, P)
local inv = core.get_meta(pos):get_inventory(); local size = inv:get_size("main")
inv:set_stack("main", 1, "mcl_core:diamond 3")
local back = core.get_meta(pos):get_inventory():get_stack("main", 1):to_string()
local n = core.get_node(pos).name
core.get_meta(pos):get_inventory():set_list("main", {}); core.remove_node(pos)
return {chest_slots = size, items_kept = back == "mcl_core:diamond 3", _dbg = n}''' % PAD)
        finally: self._back()

    def redstone(self):
        """Lever (on the floor) -> 2 redstone dust -> redstone lamp: the lamp lights when the lever is switched on and goes off again."""
        try:
            self._pad()
            self.lua(r'''
local x, y, z = %d + 4, %d, %d + 4
local st = ItemStack("mesecons_walllever:wall_lever_off")      -- placed like a player, onto the stone floor
core.registered_items[st:get_name()].on_place(st, P, {type = "node", under = {x = x, y = y - 1, z = z}, above = {x = x, y = y, z = z}})
for i = 1, 2 do core.place_node({x = x + i, y = y, z = z}, {name = "mesecons:wire_00000000_off"}, P) end
core.place_node({x = x + 3, y = y, z = z}, {name = "mesecons_lightstone:lightstone_off"}, P)
V.lever = {x = x, y = y, z = z}; V.lamp = {x = x + 3, y = y, z = z}; V.rx = x
return core.get_node(V.lamp).name''' % PAD)
            time.sleep(1.0)
            on0 = self.lua('local n = core.get_node(V.lever); core.registered_nodes[n.name].on_rightclick(V.lever, n, P); return core.get_node(V.lever).name')
            time.sleep(2.0)
            lit = self.lua("return core.get_node(V.lamp).name")
            self.lua('local n = core.get_node(V.lever); core.registered_nodes[n.name].on_rightclick(V.lever, n, P); return 1'); time.sleep(2.0)
            off = self.lua("return core.get_node(V.lamp).name")
            return {"lamp_on": lit == "mesecons_lightstone:lightstone_on", "lamp_off_again": off == "mesecons_lightstone:lightstone_off",
                    "_dbg": {"lever": on0, "lit": lit, "off": off}}
        finally:
            self.lua("for i = 0, 3 do core.remove_node({x = V.rx + i, y = %d, z = %d + 4}) end; core.remove_node(V.lever); return 1" % (PAD[1], PAD[2]))
            self._back()

    def combat(self):
        """A punch with the player's hand damages a pig."""
        try:
            self._pad()
            self.lua(r'''local q = P:get_pos(); V.pig = core.add_entity({x = q.x + 2, y = q.y, z = q.z}, "mobs_mc:pig"); return 1''')
            time.sleep(1.5)
            r = self.lua(r'''
local o = V.pig; if not o or not o:get_pos() then return {pig = false} end   -- mcl_mobs keeps health in the luaentity
local h0 = o:get_luaentity().health; P:get_inventory():set_stack("main", P:get_wield_index(), "")
local hs = P:get_inventory():get_stack("hand", 1)
o:punch(P, 10, (hs:is_empty() and ItemStack("") or hs):get_tool_capabilities(), vector.direction(P:get_pos(), o:get_pos()))
return {h0 = h0}''')
            time.sleep(0.8)
            h1 = self.lua("local o = V.pig; local e = o:get_pos() and o:get_luaentity(); local h = e and e.health or 0; if o:get_pos() then o:remove() end; return h")
            return {"damage_dealt": r.get("h0") is not None and h1 < r["h0"], "_dbg": {"h0": r.get("h0"), "h1": h1}}
        finally: self._back()

    def elytra_glide(self):
        """Elytra equipped (armor slot 3); the player drops from a glass block 30 up and holds space (real key) once really
        falling: gliding starts (mcl_playerplus elytra.active). Up to 3 attempts (A/A: the client sometimes does not start the
        fall, and the glide path itself varies a lot, so only "gliding starts" is compared; the descent is debug info)."""
        x, y, z = PAD; tries = []
        try:
            self._pad()
            self.lua(r'''local x, y, z = %d, %d, %d
for xx = x - 2, x + 30 do for zz = z - 2, z + 2 do for yy = y + 13, y + 46 do core.set_node({x = xx, y = yy, z = zz}, {name = "air"}) end end end
mcl_armor.equip(ItemStack("mcl_armor:elytra"), P)
return P:get_inventory():get_stack("armor", 3):get_name()''' % PAD)
            for _ in range(3):
                self.lua(r'''local x, y, z = %d, %d, %d
V.glass = {x = x, y = y + 29, z = z}; core.set_node(V.glass, {name = "mcl_core:glass"})
P:set_pos({x = x + 0.5, y = y + 29.6, z = z + 0.5}); P:set_velocity({x = 0, y = 0, z = 0}); P:set_look_horizontal(-math.pi / 2); P:set_look_vertical(0)
return 1''' % PAD)
                time.sleep(2.5)                   # standing on glass: the client loads the blocks around
                y0 = self.lua("core.remove_node(V.glass); return P:get_pos().y")
                falling = False
                for _ in range(15):               # wait until the player really falls (the client may lag)
                    time.sleep(0.2)
                    if self.lua("return P:get_pos().y") < y0 - 0.8: falling = True; break
                self.I.hold(["space"], 0.4)       # held: a short tap can fall between two control updates
                act = []
                for _ in range(3):
                    act.append(self.lua("local q = P:get_pos(); local e = mcl_playerplus.elytra[P]; return {y = q.y, active = e and e.active or false}"))
                    time.sleep(0.5)
                tries.append({"falling": falling, "samples": act})
                if any(s["active"] for s in act): break
                time.sleep(3); self.lua("P:set_hp(P:get_properties().hp_max); return 1")
            return {"glides": any(s["active"] for t in tries for s in t["samples"]), "_dbg": tries}
        finally:
            time.sleep(4)
            self.lua(r'''P:get_inventory():set_stack("armor", 3, ""); if mcl_armor.update then mcl_armor.update(P) end
if V.glass then core.remove_node(V.glass) end; P:set_hp(P:get_properties().hp_max); return 1'''); self._back()

    def death_respawn(self):
        """Die on the pad holding cobble: the items drop where the player died; then respawn (vanilla: back at the join spot)."""
        try:
            self._pad()
            self.lua('P:get_inventory():set_stack("main", 9, "mcl_core:cobble 3"); P:set_hp(0); return 1'); time.sleep(2.5)
            d = self.lua(r'''
local cob = V.items(P:get_inventory(), "main")["mcl_core:cobble"] or 0
local dropped = 0
for _, o in ipairs(core.get_objects_inside_radius({x = %d, y = %d, z = %d}, 12)) do
  local e = o:get_luaentity(); if e and e.name == "__builtin:item" and e.itemstring:find("mcl_core:cobble") then dropped = dropped + 1; o:remove() end
end
return {cob = cob, dropped = dropped}''' % PAD)
            self.lua("if P.respawn then P:respawn() end; return 1"); time.sleep(3)
            r = self.lua(r'''local q = P:get_pos(); local u = core.registered_nodes[core.get_node({x = q.x, y = q.y - 0.6, z = q.z}).name]
return {block = {math.floor(q.x + 0.5), math.floor(q.y + 0.5), math.floor(q.z + 0.5)}, hp = P:get_hp(), ground = u ~= nil and u.walkable == true}''')
            return {"item_dropped": d["cob"] == 0 and d["dropped"] > 0, "alive": r["hp"] > 0,
                    "respawn_near_origin": max(abs(r["block"][0]), abs(r["block"][2])) <= 128,
                    "_dbg": dict(d, block=r["block"], on_ground=r["ground"])}   # on_ground right after respawn: False under load
        finally:
            self.lua('P:set_hp(P:get_properties().hp_max); P:get_inventory():remove_item("main", "mcl_core:cobble 64"); return 1'); self._back()

    LAST = ("death_respawn",)

    def run(self, names):
        out = {}
        names = sorted(names, key=lambda n: ALL.index(n) if n in ALL else len(ALL))   # canonical order: player_start must run first (the
        # other probes dig, craft and walk, which drains saturation: a sorted run measured join saturation 0 instead of 5, 2026-10-30)
        names = [n for n in names if n not in self.LAST] + [n for n in names if n in self.LAST]
        for n in names:
            try: out[n] = getattr(self, n)()
            except Exception as e: out[n] = {"error": f"{type(e).__name__}: {str(e)[:160]}"}
        return out


ALL = ["player_start", "game_rules", "time_advances", "weather_changes", "mobs_exist", "terrain_spawn", "perf",
       "dig_times", "crafting", "place_break", "furnace_smelts", "containers", "redstone", "combat", "move_jump", "fall_damage",
       "hunger_eat", "death_respawn"]                       # the ready probes (default run)
BLOCKED = ["mob_spawning", "elytra_glide"]                  # work, but too noisy in vanilla A/A (see pool_voxelibre.yaml); run with --probes

# tolerances: PROVISIONAL until the A/A run in the game sets them (see the pool header)
TOL = {"terrain_frac": 0.08, "day_rate_rel": 0.25, "move_rel": 0.25, "fall_dmg_abs": 1, "perf_x": 2.0, "perf_add_ms": 3.0,
       "spawn_min_frac": 0.0}


def compare(name, base, got):
    """-> (ok | None, detail). None = could not test."""
    norm = lambda x: {k: v for k, v in json.loads(json.dumps(x)).items() if not k.startswith("_")} if isinstance(x, dict) \
        else json.loads(json.dumps(x))
    base, got = norm(base), norm(got)
    if isinstance(got, dict) and "error" in got: return None, got["error"]
    if isinstance(base, dict) and "error" in base: return None, f"baseline error: {base['error']}"
    if name == "log_errors":
        return got["errors"] <= base["errors"], f"{got['errors']} ERROR lines vs vanilla {base['errors']}"
    if name == "game_rules":       # rules a mod adds are fine; vanilla rules must keep their values
        diff = {k: f"{base.get(k)} -> {got.get(k)}" for k in base if got.get(k) != base.get(k)}
        return not diff, diff or "unchanged"
    if name == "time_advances":
        if not got.get("advances"): return False, "time does not advance"
        b, g = base["day_fraction_per_s"], got["day_fraction_per_s"]
        return abs(g - b) <= TOL["day_rate_rel"] * b, f"{g:.6f} vs vanilla {b:.6f} day/s"
    if name == "terrain_spawn":
        bh, gh = base["heights"], got["heights"]
        n = sum(1 for r1, r2 in zip(bh, gh) for a, b in zip(r1, r2) if abs(a - b) > 1); tot = sum(len(r) for r in bh)
        return n / tot < TOL["terrain_frac"], f"{n / tot:.1%} of the 97x97 columns around the origin differ in height"
    if name == "perf":
        b, g = base["lua_ms_per_step"], got["lua_ms_per_step"]
        ok = g <= max(TOL["perf_x"] * b, b + TOL["perf_add_ms"]) and got["step_ms"] <= 1.5 * base["step_ms"] + 5
        return ok, f"Lua {g:.2f} ms/step vs vanilla {b:.2f}; step {got['step_ms']:.0f} vs {base['step_ms']:.0f} ms"
    if name == "mob_spawning":
        b, g = base["spawned_in_45s_night"], got["spawned_in_45s_night"]
        if b > 0 and g == 0: return False, f"no mob spawned in 45 s at night (vanilla {b})"
        return True, f"spawned {g} in 45 s at night (vanilla {b})"
    if name == "crafting":
        kd = {k: f"{base['key'].get(k)} -> {got['key'].get(k)}" for k in base["key"] if got["key"].get(k) != base["key"].get(k)}
        lost = [i for i, rs in base["recipes"].items() if not set(rs) <= set(got["recipes"].get(i, []))]
        return not kd and not lost, {"key_changed": kd, "items_with_lost_recipes": lost[:15], "n_lost": len(lost)} if (kd or lost) else "unchanged"
    if name == "player_start":
        diffs = {k: f"{base[k]} -> {got.get(k)}" for k in base if base[k] != got.get(k)}
        return not diffs, diffs or "unchanged"
    if name == "move_jump":
        bools = {k: f"{base[k]} -> {got.get(k)}" for k in ("walks", "sprint_faster", "jumps_onto_step") if base[k] != got.get(k)}
        b, g = base["walk_blocks_1_5s"], got["walk_blocks_1_5s"]
        if b and abs(g - b) > TOL["move_rel"] * b: bools["walk_speed"] = f"{b} -> {g} blocks in 1.5 s"
        return not bools, bools or f"walk {g} blocks/1.5 s (vanilla {b})"
    if name == "fall_damage":
        ok = got["took_damage"] == base["took_damage"] and abs(got["damage"] - base["damage"]) <= TOL["fall_dmg_abs"]
        return ok, f"damage {got['damage']} vs vanilla {base['damage']}"
    if name == "dig_times":
        diff = {k: f"{base[k]} -> {got.get(k)}" for k in base if got.get(k) != base[k]}
        return not diff, diff or "unchanged"
    return base == got, f"{base} -> {got}" if base != got else "unchanged"
