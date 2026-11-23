"""Tanks of Freedom II 1.0.9 (Godot 4.4.1) regression probes. Ported from the original probes_tof2.py (verified
in game 2026-10-30: A/A + negative checks in games/tof2/pool.yaml). Changes: the host side runs through a gab Seat (any host
backend) instead of docker exec on a fixed container; install root is an argument; instance names carry a timestamp (two
evaluations on one sandbox never share a dir); every probe run is detached in the sandbox and polled (a long exec can be cut by
the sandbox's exec wrapper); no host CLI. The bridge (BRIDGE_GD), the instance scripts and compare() / item_result() are
unchanged, except applymod.sh: a replaced asset's .import hard link is broken too (a re-import must not write into src/), and
a mod's own override.cfg gets the bridge autoload appended again (it replaced the instance's).

Install (<root> = /home/player/oss-tof2): src/ (upstream P1X-in/tanks-of-freedom-ii dbe629e, imported), godot/
(Godot_v4.4.1-stable_linux.x86_64), bridge/ (bridge.gd + mkinst.sh + applymod.sh + run.sh, written by TOFProbes.install()),
inst/<name>/{game,data,out} one probe instance: assets/ hard-linked to src, the rest copied, the bridge autoload ProbeBridge
added through override.cfg, data = XDG_DATA_HOME (user://: settings.json intro / sound / music / hq_cam off).
Bridge: each probe is its own short headless run `godot --headless --path game -- probe=<name> out=<json>`; the autoload drives
the game through its own services and scenes and writes JSON. Engine.time_scale 4 (AI 8); story dialogs are read and closed.
A mod is a directory laid out like the project (res://); a top-level `user/` folder goes to user:// (custom maps, saves);
files are copied over the instance (hard links broken first); non-text assets trigger a re-import.
"""
import json, re, shlex, time
from pathlib import Path

VERSION = "Tanks of Freedom II 1.0.9 (git dbe629e), Godot 4.4.1-stable"
GODOT_BIN = "godot/Godot_v4.4.1-stable_linux.x86_64"        # under the install root (reference install and image)

ORDER = ["data", "maps", "menu", "turns", "produce_move", "combat", "abilities", "capture_win", "save", "editor", "camera", "campaign", "ai"]
PROBE_ARGS = {"ai": ["speed=8", "timeout=300"]}
PROBE_TIMEOUT = {"campaign": 600, "ai": 420, "maps": 300}
ERR = re.compile(r"^(SCRIPT ERROR|ERROR|USER ERROR|USER SCRIPT ERROR|Parse Error)")
ERR_IGNORE = re.compile(r"resources still in use at exit|RID allocations .* leaked at exit|Pages in use exist at exit|ObjectDB instances leaked")
TEXT_EXT = {".json", ".gd", ".tscn", ".tres", ".cfg", ".txt", ".md", ".csv", ".import", ".uid"}


BRIDGE_GD = r'''extends Node
# GameAnything probe bridge for Tanks of Freedom II (Godot 4.4). Installed as an autoload through override.cfg in a probe
# instance; does nothing unless the command line carries `-- probe=<name> out=<file>`. Measures through the game's own
# scenes and services (MatchSetup / SceneSwitcher / board / map builder / editor / main menu) and prints TOFPROBE json lines.

var args := {}
var result := {}
var story_texts := []
var auto_story := true
var t_start := 0


func _ready():
	for a in OS.get_cmdline_user_args():
		var kv = a.split("=", true, 1)
		if kv.size() == 2:
			args[kv[0]] = kv[1]
	if not args.has("probe"):
		return
	t_start = Time.get_ticks_msec()
	process_mode = Node.PROCESS_MODE_ALWAYS
	Engine.time_scale = float(args.get("speed", "4"))
	call_deferred("_run")


func _process(_d):
	if not args.has("probe") or not auto_story:
		return
	var b = get_tree().current_scene
	if b != null and b.has_method("start_turn") and b.is_node_ready():
		var sd = b.ui.story_dialog
		if sd.is_visible():
			story_texts.append(str(sd.text.text))
			sd.hide()


func _run():
	var p = args["probe"]
	await frames(5)
	var ok = true
	if has_method("probe_" + p):
		await call("probe_" + p)
	else:
		result["error"] = "unknown probe " + p
	result["_wall_ms"] = Time.get_ticks_msec() - t_start
	if args.has("shot"):
		await shot(args["shot"])
	var out = args.get("out", "")
	var txt = JSON.stringify(result, "", true)
	print("TOFPROBE_RESULT " + txt)
	if out != "":
		var f = FileAccess.open(out, FileAccess.WRITE)
		f.store_string(txt)
		f.close()
	print("TOFPROBE_DONE")
	get_tree().quit(0)


# ------------------------------------------------------------------ helpers ------------------------------------------------------------------
func frames(n):
	for i in range(n):
		await get_tree().process_frame


func secs(s):
	# game-time seconds (scaled by Engine.time_scale like the game's own timers)
	await get_tree().create_timer(s).timeout


func wait_for(cond: Callable, timeout_ms := 20000) -> bool:
	var t0 = Time.get_ticks_msec()
	while Time.get_ticks_msec() - t0 < timeout_ms:
		if cond.call():
			return true
		await get_tree().process_frame
	return false


func shot(path):
	await frames(3)
	var img = get_viewport().get_texture().get_image()
	if img != null and not img.is_empty():
		img.save_png(path)
		result["_shot"] = path


func scene_id() -> int:
	var s = get_tree().current_scene
	return s.get_instance_id() if s != null else 0


func changed(old_id: int) -> bool:
	var s = get_tree().current_scene
	return s != null and s.get_instance_id() != old_id


func cur():
	return get_tree().current_scene


func is_board():
	var s = cur()
	return s != null and s.has_method("start_turn") and s.is_node_ready()


func map_sides(map_name) -> Array:
	var data = MapManager.get_map_data(map_name)
	var sides = []
	var hqs = ["modern_hq", "steampunk_hq", "futuristic_hq", "feudal_hq"]
	for y in range(MapManager.MAX_MAP_SIZE):
		for x in range(MapManager.MAX_MAP_SIZE):
			var key = str(x) + "_" + str(y)
			if data.has("tiles") and data["tiles"].has(key):
				var b = data["tiles"][key]["building"]
				if b["tile"] != null and b["tile"] in hqs and not (b["side"] in sides):
					sides.append(b["side"])
	return sides


func start_skirmish(map_name, types := [], ap := 50, turn_limit := 0) -> Object:
	var old = scene_id()
	MatchSetup.reset()
	MatchSetup.map_name = map_name
	MatchSetup.turn_limit = turn_limit
	var i = 0
	for side in map_sides(map_name):
		var t = "human"
		if i < types.size():
			t = types[i]
		MatchSetup.add_player(side, ap, t, true, null)
		i += 1
	SceneSwitcher.board()
	await wait_for(func(): return changed(old) and is_board(), 30000)
	await frames(10)
	return cur()


func start_campaign(cname, mission_no) -> Object:
	var old = scene_id()
	var manifest = Campaign.get_campaign(cname)
	var md = manifest["missions"][mission_no]
	MatchSetup.reset()
	MatchSetup.campaign_name = cname
	MatchSetup.mission_no = mission_no
	for pl in md["players"]:
		MatchSetup.add_player(pl["side"], pl["ap"], pl["type"], pl.get("alive", true), pl.get("team", null))
	SceneSwitcher.board()
	await wait_for(func(): return changed(old) and is_board(), 30000)
	await frames(10)
	return cur()


func tile_key(t):
	return str(t.position.x) + "_" + str(t.position.y)


func sorted_tiles(model):
	var keys = model.tiles.keys()
	keys.sort_custom(func(a, b):
		var pa = a.split("_"); var pb = b.split("_")
		if int(pa[1]) != int(pb[1]): return int(pa[1]) < int(pb[1])
		return int(pa[0]) < int(pb[0]))
	var out = []
	for k in keys:
		out.append(model.tiles[k])
	return out


func unit_info(u):
	return {"t": u.template_name, "side": u.side, "hp": int(u.hp), "move": int(u.move), "attacks": int(u.attacks), "level": int(u.level), "exp": int(u.experience)}


func snapshot(board) -> Dictionary:
	var units = {}
	var buildings = {}
	var count_u = {}
	var count_b = {}
	var ground = 0
	var terrain = 0
	for t in sorted_tiles(board.map.model):
		if t.ground.is_present(): ground += 1
		if t.terrain.is_present(): terrain += 1
		if t.unit.is_present():
			units[tile_key(t)] = unit_info(t.unit.tile)
			var k = t.unit.tile.side + ":" + t.unit.tile.template_name
			count_u[k] = count_u.get(k, 0) + 1
		if t.building.is_present():
			buildings[tile_key(t)] = {"t": t.building.tile.template_name, "side": t.building.tile.side}
			var k2 = t.building.tile.side + ":" + t.building.tile.template_name
			count_b[k2] = count_b.get(k2, 0) + 1
	var players = []
	for pl in board.state.players:
		players.append({"side": pl["side"], "type": pl["type"], "ap": int(pl["ap"]), "alive": pl["alive"]})
	return {"units": units, "buildings": buildings, "unit_counts": count_u, "building_counts": count_b, "ground_tiles": ground,
			"terrain_tiles": terrain, "players": players, "turn": int(board.state.turn), "current": int(board.state.current_player)}


func side_counts(board) -> Dictionary:
	var out = {}
	for pl in board.state.players:
		var s = pl["side"]
		out[s] = {"units": board.map.model.get_player_units(s).size(), "buildings": board.map.model.get_player_buildings(s).size(),
				  "ap": int(pl["ap"]), "alive": pl["alive"]}
	return out


func free_pair(board) -> Array:
	# two east-west neighbouring empty, plain tiles (ground, nothing on top) where any land unit can stand
	for t in sorted_tiles(board.map.model):
		var n = board.map.model.get_tile(t.position + Vector2i(1, 0))
		if n == null: continue
		var ok = true
		for x in [t, n]:
			if not x.can_acommodate_unit() or x.terrain.is_present() or x.ground.tile.unit_can_fly:
				ok = false
		if ok:
			return [t, n]
	return []


func end_turn_and_wait(board):
	var before = [board.state.turn, board.state.current_player]
	board.end_turn()
	await wait_for(func(): return [board.state.turn, board.state.current_player] != before, 10000)
	await frames(15)


# ------------------------------------------------------------------ probes ------------------------------------------------------------------
func probe_data():
	# stock unit / hero / building templates: stats and abilities
	var T = load("res://scenes/map/templates.gd").new()
	var holder = Node3D.new()
	add_child(holder)
	var units = {}
	var heroes = {}
	var buildings = {}
	for group in [["_unit_templates", units], ["_hero_templates", heroes], ["_building_templates", buildings]]:
		var keys = T.get(group[0]).keys()
		keys.sort()
		for key in keys:
			var inst = T.get_template(key)
			holder.add_child(inst)
			await frames(1)
			var d = {}
			if group[0] == "_building_templates":
				d = {"ap_gain": inst.ap_gain, "require_crew": inst.require_crew, "capture_value": inst.capture_value, "abilities": []}
				for ab in inst.abilities:
					var ad = {"type": ab.TYPE, "index": ab.index, "label": ab.label, "ap_cost": ab.ap_cost, "cooldown": ab.cooldown}
					if "template_name" in ab: ad["unit"] = ab.template_name
					d["abilities"].append(ad)
			else:
				d = {"max_hp": inst.max_hp, "max_move": inst.max_move, "attack": inst.attack, "armor": inst.armor, "max_attacks": inst.max_attacks,
					 "can_capture": inst.can_capture, "can_fly": inst.can_fly, "unit_value": inst.unit_value, "unit_class": inst.unit_class, "abilities": []}
				for ab in inst.active_abilities:
					d["abilities"].append({"type": ab.TYPE, "index": ab.index, "label": ab.label, "ap_cost": ab.ap_cost, "cooldown": ab.cooldown, "range": ab.ability_range})
				if inst.passive_ability != null:
					d["passive"] = {"label": inst.passive_ability.label}
			group[1][key] = d
			inst.queue_free()
	# who may attack whom (can_attack overrides, e.g. air units)
	var matrix = {}
	var ukeys = T._unit_templates.keys()
	ukeys.sort()
	var inst_by = {}
	for k in ukeys:
		inst_by[k] = T.get_template(k); holder.add_child(inst_by[k])
	await frames(1)
	for a in ukeys:
		if not a.begins_with("blue_") and not a.contains("blue"): continue
		var row = []
		for b in ukeys:
			if not b.contains("red"): continue
			if inst_by[a].can_attack(inst_by[b]): row.append(b)
		matrix[a] = row
	holder.queue_free()
	result = {"units": units, "heroes": heroes, "buildings": buildings, "can_attack": matrix}


func probe_maps():
	var keys = MapManager.skirmish.keys()
	keys.sort()
	var out = {}
	for m in keys:
		var b = await start_skirmish(m)
		if b == null:
			out[m] = {"error": "board did not start"}
			continue
		var s = snapshot(b)
		out[m] = {"sides": map_sides(m), "ground_tiles": s["ground_tiles"], "terrain_tiles": s["terrain_tiles"], "unit_counts": s["unit_counts"],
				  "building_counts": s["building_counts"], "buildings": s["buildings"], "players": s["players"], "turn": s["turn"],
				  "objectives": str(b.ui.objectives.raw_text)}
	result = {"stock_maps": keys, "maps": out, "special": MapManager.special.keys()}


func probe_turns():
	var m = args.get("map", "2p_crossroad")
	var b = await start_skirmish(m, ["human", "human"], 50)
	var log = []
	log.append({"turn": b.state.turn, "side": b.state.get_current_side(), "ap": b.state.get_current_ap()})
	for i in range(6):
		await end_turn_and_wait(b)
		log.append({"turn": b.state.turn, "side": b.state.get_current_side(), "ap": b.state.get_current_ap()})
	var income = {}
	for pl in b.state.players:
		var s = pl["side"]; var inc = 0
		for bl in b.map.model.get_player_buildings(s):
			inc += b.abilities.get_modified_ap_gain(bl.ap_gain, bl)
		income[s] = inc
	result = {"map": m, "log": log, "income_now": income, "autosave_written": SavesManager.autosave[0] != null}


func _produce(b):
	# select an own building with a production ability, activate it, spawn on the first marked neighbour
	var side = b.state.get_current_side()
	for t in sorted_tiles(b.map.model):
		if not (t.building.is_present() and t.building.tile.side == side): continue
		for ab in t.building.tile.abilities:
			if ab.TYPE != "production" or ab.disabled: continue
			b.select_tile(t.position)
			await frames(3)
			if b.ui.is_radial_open():
				b.toggle_radial_menu()
				await frames(3)
			b.selected_tile = t
			var ap0 = b.state.get_current_ap()
			b._activate_production_ability(ab)
			await frames(3)
			for dir in ["n", "e", "s", "w"]:
				var n = t.get_neighbour(dir)
				if n != null and b.ability_markers.marker_exists(n.position):
					b.select_tile(n.position)
					await frames(5)
					if b.ui.is_radial_open():
						b.toggle_radial_menu(); await frames(3)
					return {"building": t.building.tile.template_name, "unit": ab.template_name, "ap_before": ap0, "ap_after": b.state.get_current_ap(),
							"spawned": n.unit.is_present() and n.unit.tile.template_name == ab.template_name, "tile": n, "pos": tile_key(n),
							"unit_state": unit_info(n.unit.tile) if n.unit.is_present() else null}
			b.cancel_ability()
	return {"error": "no production building with a free spot"}


func probe_produce_move():
	var m = args.get("map", "2p_crossroad")
	var b = await start_skirmish(m, ["human", "human"], 100)
	var pr = await _produce(b)
	if pr.has("error"):
		result = pr; return
	var ut = pr["tile"]; pr.erase("tile")
	var unit = ut.unit.tile
	# movement range of the fresh unit
	b.selected_tile = ut
	b.show_contextual_select()
	await frames(3)
	if b.ui.is_radial_open():
		b.toggle_radial_menu(); await frames(3)
	var reach = {}
	var best = null; var best_cost = -1
	for t in sorted_tiles(b.map.model):
		var c = b.movement_markers.get_tile_cost(t)
		if c != null and c > 0 and t.can_acommodate_unit(unit):
			reach[tile_key(t)] = c
			if c > best_cost:
				best_cost = c; best = t
	var mv = {"reachable": reach.size(), "max_cost": best_cost, "costs": reach}
	if best != null:
		var ap0 = b.state.get_current_ap()
		b.select_tile(best.position)
		await secs(2.0)
		mv["moved"] = best.unit.is_present() and best.unit.tile == unit and not ut.unit.is_present()
		mv["ap_used"] = ap0 - b.state.get_current_ap()
		mv["move_left"] = unit.move
		mv["max_move"] = unit.max_move
		await end_turn_and_wait(b)
		await end_turn_and_wait(b)
		mv["move_after_new_turn"] = unit.move
	result = {"map": m, "produce": pr, "move": mv}


func probe_combat():
	var m = args.get("map", "2p_oasis")
	var b = await start_skirmish(m, ["human", "human"], 100)
	var pair = free_pair(b)
	if pair.is_empty():
		result = {"error": "no free tile pair"}; return
	var a_t = pair[0]; var d_t = pair[1]
	var T = b.map.templates
	var atk_keys = []; var def_keys = []
	for k in T._unit_templates.keys():
		if k.contains("blue"): atk_keys.append(k)
		if k.contains("red"): def_keys.append(k)
	atk_keys.sort(); def_keys.sort()
	var fights = {}
	seed(1234)
	for a in atk_keys:
		for d in def_keys:
			b.map.builder.place_unit(a_t.position, a, 0, b.state.get_current_side())
			b.map.builder.place_unit(d_t.position, d, 0, "red")
			b.state.set_player_ap(b.state.current_player, 100)
			var au = a_t.unit.tile; var du = d_t.unit.tile
			au.team = b.state.get_current_team(); du.team = b.state.get_player_team("red")
			var allowed = d_t.can_unit_interact(au)
			if allowed:
				b.selected_tile = a_t
				b.handle_interaction(d_t)
				await secs(0.4)
			var r = {"allowed": allowed}
			r["def_hp"] = d_t.unit.tile.hp if d_t.unit.is_present() else 0
			r["atk_hp"] = a_t.unit.tile.hp if a_t.unit.is_present() else 0
			fights[a + ">" + d] = r
			b.unselect_tile()
			for t in [a_t, d_t]:
				if t.unit.is_present(): t.unit.clear()
			for t in [a_t, d_t]:
				if t.damage.is_present(): t.damage.clear()
			await frames(2)
	result = {"map": m, "tiles": [tile_key(a_t), tile_key(d_t)], "fights": fights}


func probe_capture_win():
	var m = args.get("map", "2p_oasis")
	var b = await start_skirmish(m, ["human", "human"], 100)
	var side = b.state.get_current_side()
	var inf = side + "_infantry"
	var out = {"side": side}
	# 1) capture the first neutral non-HQ building from a free neighbour
	for kind in ["neutral", "enemy_hq"]:
		var done = false
		for t in sorted_tiles(b.map.model):
			if done or not t.building.is_present(): continue
			var bl = t.building.tile
			if kind == "neutral" and bl.side != "neutral": continue
			if kind == "enemy_hq" and (bl.side == side or bl.side == "neutral" or not bl.template_name.ends_with("_hq")): continue
			for dir in ["n", "e", "s", "w"]:
				var n = t.get_neighbour(dir)
				if n == null or not n.can_acommodate_unit() or n.ground.tile.unit_can_fly: continue
				b.map.builder.place_unit(n.position, inf, 0, side)
				n.unit.tile.team = b.state.get_current_team()
				var ap0 = b.state.get_current_ap()
				var allowed = t.can_unit_interact(n.unit.tile)
				b.selected_tile = n
				b.handle_interaction(t)
				await secs(0.5)
				out[kind] = {"building": bl.template_name, "allowed": allowed, "new_side": t.building.tile.side,
							 "unit_left_after": n.unit.is_present(), "ap_used": ap0 - b.state.get_current_ap()}
				done = true
				break
	await secs(1.0)
	out["summary_shown"] = b.ui.summary.is_visible()
	var alive = {}
	for pl in b.state.players: alive[pl["side"]] = pl["alive"]
	out["alive"] = alive
	result = out


func probe_ai():
	var m = args.get("map", "2p_crossroad")
	var n = int(args.get("turns", "6"))
	seed(int(args.get("seed", "7")))
	var b = await start_skirmish(m, ["ai", "ai"], 50, n)
	var per_turn = []
	var last = [-1, -1]
	var t0 = Time.get_ticks_msec()
	while Time.get_ticks_msec() - t0 < int(args.get("timeout", "240")) * 1000:
		if b.ui.summary.is_visible(): break
		var k = [b.state.turn, b.state.current_player]
		if k != last:
			last = k
			per_turn.append({"turn": k[0], "player": k[1], "sides": side_counts(b)})
		await frames(1)
	result = {"map": m, "turn_limit": n, "finished": b.ui.summary.is_visible(), "final": side_counts(b), "per_turn": per_turn,
			  "turn": b.state.turn, "ai_ms": Time.get_ticks_msec() - t0}


func probe_campaign():
	var out = {}
	var names = []
	for c in Campaign.get_campaigns():
		var cname = c["name"]
		names.append(cname)
		var missions = []
		for mi in range(c["missions"].size()):
			missions.append({"name": c["missions"][mi]["name"], "map": c["missions"][mi]["map"]})
		out[cname] = {"title": c.get("title", ""), "missions": missions, "core": Campaign.core_campaigns_by_name.has(cname)}
	var only = args.get("missions", "all")   # first | all
	for cname in names:
		var cnt = out[cname]["missions"].size()
		var idxs = [0] if only == "first" else range(cnt)
		for mi in idxs:
			story_texts = []
			if not Campaign.get_campaign(cname)["missions"][mi].has("players"):
				out[cname]["missions"][mi]["skipped"] = "manifest has no players list (the game cannot start it)"
				continue
			var b = await start_campaign(cname, mi)
			if b == null:
				out[cname]["missions"][mi]["error"] = "board did not start"; continue
			# wait until the turn-1 stories are over: no story running, no dialog, camera idle (30 frames and 0.5 s in a row);
			# stop early if the turn passes on (then the AI would play and timing would matter)
			var t0 = Time.get_ticks_msec(); var idle_f = 0; var idle_t = 0
			while Time.get_ticks_msec() - t0 < 60000:
				if b.state.turn != 1 or b.state.current_player != 0 or b.state.is_current_player_ai(): break
				var busy = b.map.camera.script_operated or b.ui.story_dialog.is_visible() or b.map.camera.camera_in_transit
				if busy:
					idle_f = 0; idle_t = Time.get_ticks_msec()
				else:
					idle_f += 1
				if idle_f > 30 and Time.get_ticks_msec() - idle_t > 500: break
				await frames(1)
			var s = snapshot(b)
			var md = out[cname]["missions"][mi]
			md["triggers"] = b.scripting.triggers.size()
			md["stories"] = (b.map.model.scripts.get("stories", {}) as Dictionary).size()
			md["story_texts"] = story_texts.duplicate()
			md["objectives"] = str(b.ui.objectives.raw_text)
			md["unit_counts"] = s["unit_counts"]
			md["building_counts"] = s["building_counts"]
			md["players"] = s["players"]
			if b.state.is_current_player_ai() or b.state.current_player != 0:
				# an AI turn is running: stop it; its AP spending depends on timing, keep only who plays
				b.ai.abort()
				md["ai_turn"] = true
				for pl in md["players"]: pl["ap"] = null
			md["turn_after_stories"] = [int(b.state.turn), int(b.state.current_player)]
	result = {"campaigns": names, "detail": out}


func probe_save():
	var m = args.get("map", "2p_crossroad")
	var b = await start_skirmish(m, ["human", "human"], 100)
	var pr = await _produce(b)
	await end_turn_and_wait(b)        # autosave happens here
	pr = await _produce(b)
	await frames(5)
	var before = snapshot(b)
	var c = SavesManager.compile_save_data(b)
	SavesManager.add_new_save(c["map_name"], c["map_label"], c["turn_no"], c["save_data"])
	var sid = SavesManager.saves.size() + 2
	var old = scene_id()
	b.ui.saves.load_state_from_id(sid)
	await wait_for(func(): return changed(old) and is_board(), 30000)
	await frames(20)
	b = cur()
	var after = snapshot(b)
	var diffs = []
	for k in ["units", "buildings", "turn", "current", "players"]:
		if JSON.stringify(before[k]) != JSON.stringify(after[k]): diffs.append(k)
	await end_turn_and_wait(b)
	result = {"map": m, "save_id": sid, "same_after_load": diffs.size() == 0, "diffs": diffs, "before": before, "after": after,
			  "runs_on": b.state.turn > after["turn"] or b.state.current_player != after["current"],
			  "autosaves": SavesManager.autosave.filter(func(x): return x != null).size()}


func probe_editor():
	var old = scene_id()
	SceneSwitcher.map_editor()
	await wait_for(func(): return changed(old) and cur() != null and cur().has_method("handle_picker_output") and cur().is_node_ready(), 30000)
	await frames(10)
	var e = cur()
	var B = e.map.builder
	e.handle_picker_output([args.get("map", "2p_oasis"), "load"])
	await frames(10)
	var loaded_ground = 0
	for t in sorted_tiles(e.map.model):
		if t.ground.is_present(): loaded_ground += 1
	# edit: on the first free plain tile put a neutral barracks, on the next one a blue tank
	var placed = []
	for t in sorted_tiles(e.map.model):
		if placed.size() >= 2: break
		if t.can_acommodate_unit() and not t.terrain.is_present() and not t.ground.tile.unit_can_fly:
			if placed.size() == 0:
				e._place_tile(B.CLASS_BUILDING, t.position, "modern_barracks", 0); placed.append([tile_key(t), "modern_barracks"])
			else:
				e._place_tile(B.CLASS_UNIT, t.position, "blue_tank", 0); placed.append([tile_key(t), "blue_tank"])
	e.handle_picker_output(["tofprobe_edit", "save"])
	await frames(10)
	var saved = MapManager.get_map_data("tofprobe_edit")
	var in_file = []
	for p in placed:
		var td = saved.get("tiles", {}).get(p[0], {})
		in_file.append(td.get("building", {}).get("tile") == p[1] or td.get("unit", {}).get("tile") == p[1])
	e.wipe_editor()
	await frames(5)
	e.handle_picker_output(["tofprobe_edit", "load"])
	await frames(10)
	var reloaded = []
	for p in placed:
		var t = e.map.model.tiles[p[0]]
		reloaded.append((t.building.is_present() and t.building.tile.template_name == p[1]) or (t.unit.is_present() and t.unit.tile.template_name == p[1]))
	var b = await start_skirmish("tofprobe_edit", ["human", "human"], 50)
	var in_game = []
	for p in placed:
		var t = b.map.model.tiles[p[0]]
		in_game.append((t.building.is_present() and t.building.tile.template_name == p[1]) or (t.unit.is_present() and t.unit.tile.template_name == p[1]))
	result = {"editor_opened": true, "stock_map_ground_tiles": loaded_ground, "placed": placed, "in_file": in_file, "reloaded_in_editor": reloaded,
			  "in_skirmish": in_game, "custom_list": MapManager.maps.keys()}


func probe_camera():
	var m = args.get("map", "2p_oasis")
	var b = await start_skirmish(m, ["human", "human"], 50)
	var cam = b.map.camera
	await secs(1.0)
	var out = {"focus": get_window().has_focus(), "mode0": cam.camera_mode}
	var p0 = cam.get_position()
	# pan: the edge-pan path (mouse at the screen edge) for 1 game second
	cam.camera_pan = Vector2(1, 0)
	await secs(1.0)
	cam.camera_pan = Vector2(0, 0)
	var p1 = cam.get_position()
	out["pan_dx"] = snappedf(p1.x - p0.x, 0.1); out["pan_dz"] = snappedf(p1.z - p0.z, 0.1)
	out["tile_box_followed"] = b.map.tile_box_position
	# zoom: mouse wheel actions through the input system
	var z0 = cam.tof_camera_distance
	for i in range(3):
		var ev = InputEventAction.new(); ev.action = "mouse_zoom_in"; ev.pressed = true
		Input.parse_input_event(ev)
		await frames(2)
	var z1 = cam.tof_camera_distance
	out["zoom_in_delta"] = snappedf(z1 - z0, 0.01)
	if z1 == z0:   # no window focus (headless): call the handler the wheel event reaches
		for i in range(3): cam._mouse_zoom_in()
		out["zoom_in_delta_direct"] = snappedf(cam.tof_camera_distance - z0, 0.01)
	# camera style switch (TOF -> AW -> FREE -> TOF)
	var modes = []
	for i in range(3):
		cam.switch_camera(); modes.append(cam.camera_mode)
	out["switch_cycle"] = modes
	# script / AI camera move to a tile
	var hq = b.map.model.get_player_bunker_position(b.state.get_current_side())
	b.map.move_camera_to_position(Vector2(20, 20))
	await secs(3.0)
	var p2 = cam.get_position()
	out["moved_to"] = [snappedf(p2.x, 0.5), snappedf(p2.z, 0.5)]
	out["in_transit_after"] = cam.camera_in_transit
	out["limits"] = {"tof_min": cam.tof_camera_distance_min, "tof_max": cam.tof_camera_distance_max, "space": cam.camera_space_size}
	result = out


func probe_menu():
	var mm = cur()
	await secs(1.0)
	var out = {"scene": mm.scene_file_path, "menu_visible": mm.ui.menu.is_visible()}
	mm.open_picker()
	await secs(1.0)
	out["picker_visible"] = mm.ui.picker.is_visible()
	var btn = []
	for x in mm.ui.picker.map_selection_buttons:
		if x.is_visible(): btn.append(str(x.get_node("label").text) if x.has_node("label") else x.name)
	out["picker_buttons"] = btn
	mm.handle_picker_output([args.get("map", "2p_oasis")])
	await secs(1.0)
	var panels = []
	for p in mm.ui.skirmish.player_panels:
		if p.is_visible(): panels.append({"side": p.side, "ap": p.ap, "type": p.type})
	out["skirmish_players"] = panels
	mm.close_skirmish(); await secs(0.5)
	mm.close_picker(); await secs(0.5)
	mm.open_campaign_selection(); await secs(1.0)
	var ctiles = []
	for t in mm.ui.campaign_selection.campaign_tiles:
		if t.is_visible(): ctiles.append(t.name)
	out["campaign_tiles_visible"] = ctiles.size()
	mm.close_campaign_selection(); await secs(0.5)
	mm.open_settings(); await secs(0.8)
	out["settings_visible"] = mm.ui.settings.is_visible()
	mm.close_settings(); await secs(0.5)
	out["menu_back"] = mm.ui.menu.is_visible()
	# start the skirmish from the menu's own start button
	mm.open_picker(); await secs(0.8)
	mm.handle_picker_output([args.get("map", "2p_oasis")]); await secs(0.8)
	var old = scene_id()
	mm.ui.skirmish._on_start_button_pressed()
	var started = await wait_for(func(): return changed(old) and is_board(), 30000)
	out["skirmish_started"] = started
	if started:
		await frames(10)
		out["board_players"] = snapshot(cur())["players"]
	result = out


func _open_area(board, r) -> Array:
	# centre tile with every tile within Manhattan distance r plain ground (a unit can stand, no terrain, not water)
	for t in sorted_tiles(board.map.model):
		var ok = true
		for dx in range(-r, r + 1):
			for dy in range(-r, r + 1):
				if abs(dx) + abs(dy) > r: continue
				var n = board.map.model.get_tile(t.position + Vector2i(dx, dy))
				if n == null or not n.can_acommodate_unit() or n.terrain.is_present() or n.ground.tile.unit_can_fly:
					ok = false
		if ok:
			return [t]
	return []


func probe_abilities():
	# every stock unit/hero of the current side with an active ability, levelled to 1, in a fixed small scene: enemy tanks at
	# (+2,0) and (0,-2), a damaged friendly infantry at (-1,0), a damaged friendly tank at (0,+1). For each ability the game
	# shows (is_visible), activate it, record the marked tiles (relative), use it on the first marked tile, record the scene.
	var m = args.get("map", "4p_ridge")
	var b = await start_skirmish(m, ["human", "human", "human", "human"], 100)
	var area = _open_area(b, int(args.get("r", "2")))
	if area.is_empty():
		result = {"error": "no open area on " + m}; return
	var c = area[0]
	var side = b.state.get_current_side()
	var enemy = "red" if side != "red" else "blue"
	var T = b.map.templates
	var keys = []
	for k in T._unit_templates.keys():
		if k.begins_with(side + "_"): keys.append(k)
	for k in T._hero_templates.keys(): keys.append(k)
	keys.sort()
	var props = [[Vector2i(2, 0), enemy + "_tank", enemy, 10], [Vector2i(0, -2), enemy + "_tank", enemy, 10],
				 [Vector2i(-1, 0), side + "_infantry", side, 5], [Vector2i(0, 1), side + "_tank", side, 5]]
	var out = {}
	for k in keys:
		var tmp = T.get_template(k)
		var nab = 0
		for ch in tmp.get_children():
			if "TYPE" in ch: nab += 1
		tmp.free()
		var ab_idx = 0
		while true:
			# fresh scene for every ability
			for t in sorted_tiles(b.map.model):
				if t.position.distance_to(c.position) <= 3:
					if t.unit.is_present(): t.unit.clear()
					if t.damage.is_present(): t.damage.clear()
			b.map.builder.place_unit(c.position, k, 0, side)
			var u = c.unit.tile
			u.team = b.state.get_current_team(); u.level_up(); u.replenish_moves()
			for pr in props:
				b.map.builder.place_unit(c.position + pr[0], pr[1], 0, pr[2])
				var pu = b.map.model.get_tile(c.position + pr[0]).unit.tile
				pu.team = b.state.get_player_team(pr[2]); pu.hp = pr[3]
			await frames(2)
			if ab_idx >= u.active_abilities.size(): break
			var ab = u.active_abilities[ab_idx]; ab_idx += 1
			var ab_label = str(ab.label)
			var r = {"cost": ab.get_cost(), "cooldown": ab.cooldown, "visible": ab.is_visible(b)}
			out[k + ":" + ab_label] = r
			if not r["visible"]: continue
			b.state.set_player_ap(b.state.current_player, 100)
			b.selected_tile = c
			b._activate_ability(ab)
			await frames(2)
			var marks = []
			for t in sorted_tiles(b.map.model):
				if b.ability_markers.marker_exists(t.position):
					marks.append([t.position.x - c.position.x, t.position.y - c.position.y])
			r["markers"] = marks
			if marks.size() > 0:
				var tp = c.position + Vector2i(marks[0][0], marks[0][1])
				var ap0 = b.state.get_current_ap()
				b.select_tile(tp)
				await secs(0.6)
				r["ap_used"] = ap0 - b.state.get_current_ap()
				r["cooldown_after"] = ab.cd_turns_left if is_instance_valid(ab) else "source gone"
				var scene = {}
				for off in [Vector2i(0, 0), Vector2i(2, 0), Vector2i(0, -2), Vector2i(-1, 0), Vector2i(0, 1), tp - c.position]:
					var tt = b.map.model.get_tile(c.position + off)
					scene[str(off.x) + "," + str(off.y)] = unit_info(tt.unit.tile) if tt.unit.is_present() else null
				r["after"] = scene
			b.cancel_ability(); b.unselect_tile()
			await frames(2)
	for t in sorted_tiles(b.map.model):
		if t.position.distance_to(c.position) <= 3 and t.unit.is_present(): t.unit.clear()
	result = {"map": m, "centre": tile_key(c), "side": side, "abilities": out}
'''


MKINST_SH = r'''#!/bin/bash
# mkinst.sh NAME : fresh probe instance $ROOT/inst/NAME/{game,data}
set -e
R=__ROOT__; I=$R/inst/$1
rm -rf "$I"; mkdir -p "$I/game" "$I/data" "$I/out"
cp -al $R/src/assets "$I/game/assets"
cp -a $R/src/.godot $R/src/scenes $R/src/scripts $R/src/campaign $R/src/project.godot "$I/game/"
mkdir -p "$I/game/_probe"; cp $R/bridge/bridge.gd "$I/game/_probe/bridge.gd"
printf '[autoload]\n\nProbeBridge="*res://_probe/bridge.gd"\n' > "$I/game/override.cfg"
U="$I/data/godot/app_userdata/Tanks of Freedom II"; mkdir -p "$U"
printf '{"show_intro": false, "sound": false, "music": false, "hq_cam": false, "cam_shake": false, "show_controls": false}' > "$U/settings.json"
'''

APPLY_SH = r'''#!/bin/bash
# applymod.sh NAME MODDIR : copy a mod (project layout; user/ -> user://) over instance NAME, re-import if it has binary assets
set -e
R=__ROOT__; I=$R/inst/$1; M=$2
U="$I/data/godot/app_userdata/Tanks of Freedom II"
cd "$M"
find . -type f | while read -r f; do
  f=${f#./}
  case "$f" in user/*) d="$U/${f#user/}";; *) d="$I/game/$f";; esac
  mkdir -p "$(dirname "$d")"; cp --remove-destination "$f" "$d"
  case "$f" in user/*) ;; *) if [ -e "$R/src/$f.import" ]; then cp --remove-destination "$R/src/$f.import" "$d.import"; fi;; esac   # gab: a re-import must not write through the hard link to src
done
if [ -f override.cfg ]; then printf '\n[autoload]\n\nProbeBridge="*res://_probe/bridge.gd"\n' >> "$I/game/override.cfg"; fi   # gab: a mod's override.cfg keeps the bridge
if find . -type f ! -path './user/*' | grep -qvE '\.(json|gd|tscn|tres|cfg|txt|md|csv|import|uid)$'; then
  cd "$I/game" && XDG_DATA_HOME=$I/data timeout 900 $R/godot/Godot_v4.4.1-stable_linux.x86_64 --headless --path . --import > $I/out/import.log 2>&1 || true
fi
'''

RUN_SH = r'''#!/bin/bash
# run.sh NAME PROBE [k=v ...] : one headless probe run in instance NAME -> inst/NAME/out/PROBE.{json,log}
R=__ROOT__; I=$R/inst/$1; P=$2; shift 2
mkdir -p $I/out; rm -f $I/out/$P.json
cd $I/game && XDG_DATA_HOME=$I/data timeout ${TOF_TIMEOUT:-300} $R/godot/Godot_v4.4.1-stable_linux.x86_64 --headless --path . -- probe=$P out=$I/out/$P.json "$@" > $I/out/$P.log 2>&1
echo "rc=$?" >> $I/out/$P.log
'''


def postprocess(p, d):
    if p == "turns" and "log" in d:
        d["order"] = [[e["turn"], e["side"]] for e in d["log"]]
        d["ap"] = [e["ap"] for e in d["log"]]
    if p == "ai" and "per_turn" in d:
        d["order"] = [[e["turn"], e["player"]] for e in d["per_turn"]]
        sides = sorted(d.get("final", {}))
        # buildings each AI owned at its last turn start / most units it ever had
        d["max_buildings"] = {s: max(e["sides"][s]["buildings"] for e in d["per_turn"]) for s in sides}
        d["max_units"] = {s: max(e["sides"][s]["units"] for e in d["per_turn"]) for s in sides}
    if p == "produce_move":
        d["produce"] = d.get("produce", {"error": d.get("error", "no result")}); d["move"] = d.get("move", {"error": d.get("error", "no result")})
    if p == "capture_win":
        d["win"] = {k: d.get(k) for k in ("enemy_hq", "summary_shown", "alive")}
    return d


class TOFProbes:
    """one fresh probe instance (+ mods) through the asked probes, one headless Godot run per probe"""
    ORDER = ORDER

    def __init__(self, seat, root, label="p", log=print):
        self.seat, self.root, self.log = seat, root, log
        self.name = f"gab-{re.sub(r'[^A-Za-z0-9_.-]', '_', label)[:30]}-{time.strftime('%m%d%H%M%S')}"

    def sh(self, cmd, timeout=900, check=False): return self.seat.exec(cmd, timeout=timeout, check=check)

    def install(self):
        R = self.root; self.seat.put(f"{R}/bridge/bridge.gd", BRIDGE_GD)
        for n, t in (("mkinst.sh", MKINST_SH), ("applymod.sh", APPLY_SH), ("run.sh", RUN_SH)): self.seat.put(f"{R}/bridge/{n}", t.replace("__ROOT__", R))
        self.sh(f"chmod +x {R}/bridge/*.sh", check=True)

    def build(self, mods=()):
        self.install(); R, n = self.root, self.name
        self.sh(f"{R}/bridge/mkinst.sh {n}", check=True)
        for i, m in enumerate(mods):
            self.seat.copy_in(m, f"{R}/inst/{n}/mod{i}")
            self.sh(f"{R}/bridge/applymod.sh {n} {R}/inst/{n}/mod{i}", timeout=1200, check=True)

    def run(self, names=None):
        out, timing = {}, {}; R, n = self.root, self.name
        for p in [x for x in ORDER if not names or x in names]:
            t0 = time.time(); lim = PROBE_TIMEOUT.get(p, 180); o = f"{R}/inst/{n}/out/{p}"
            cmd = f"TOF_TIMEOUT={lim} {R}/bridge/run.sh {n} {p} {' '.join(PROBE_ARGS.get(p, []))}"
            self.sh(f"mkdir -p {R}/inst/{n}/out && rm -f {o}.log {o}.json && setsid -f bash -c {shlex.quote(cmd)} > /dev/null 2>&1 < /dev/null")
            while time.time() - t0 < lim + 60:
                time.sleep(3)
                if "rc=" in self.sh(f"tail -c 200 {o}.log 2>/dev/null").stdout: break
            log = self.sh(f"cat {o}.log 2>/dev/null").stdout
            raw = self.sh(f"cat {o}.json 2>/dev/null").stdout
            try: d = json.loads(raw)
            except Exception: d = {"error": "no result json"}
            rc = re.findall(r"rc=(\d+)", log)
            errs = log_errors(log)
            if "TOFPROBE_DONE" not in log or not rc or rc[-1] != "0":
                d["_crashed"] = f"rc={rc[-1] if rc else '?'} done={'TOFPROBE_DONE' in log}"
            if any(l.startswith("SCRIPT ERROR") for l in errs) and "error" not in d and len(d) <= 2:
                d["error"] = "probe stopped by a script error: " + errs[0][:150]
            d["_log_errors"] = len(errs); d["_error_kinds"] = sorted(set(errs)); d["_error_lines"] = list(dict.fromkeys(errs))[:60]   # log order
            out[p] = postprocess(p, d); timing[p] = round(time.time() - t0, 1)
        out["_timing"] = timing
        return out

    def session(self, names=None, mods=()):
        res = {"version": VERSION, "sandbox": self.seat.sandbox, "mods": [str(m) for m in mods], "started": time.strftime("%Y-%m-%d %H:%M:%S")}
        try:
            self.build(mods); res.update(self.run(names))
        except Exception as ex:
            res["error"] = f"{type(ex).__name__}: {str(ex)[:300]}"
        res["log_errors"] = sum(v.get("_log_errors", 0) for v in res.values() if isinstance(v, dict))
        return res

    def cleanup(self): self.sh(f"rm -rf {self.root}/inst/{self.name}")


def log_errors(log):
    """error lines before the bridge's DONE marker (exit-time leak reports ignored); each as 'message @ location'"""
    lines = log.split("TOFPROBE_DONE")[0].splitlines()
    out = []
    for i, l in enumerate(lines):
        if ERR.search(l) and not ERR_IGNORE.search(l):
            at = lines[i + 1].strip() if i + 1 < len(lines) and lines[i + 1].strip().startswith("at:") else ""
            out.append(f"{l.strip()} @ {at[3:].strip()}" if at else l.strip())
    return out


# ------------------------------------------------------------------ compare ------------------------------------------------------------------
AI_TOL = 0.5          # AI: units / buildings may be at most 50% below vanilla (set from the A/A runs, see pool header)
CAM_TOL = 0.10
CAM_ARRIVE = 4.0       # half a tile: the scripted pan stops within ~2 units of the target (A/A 164/164 vs 166/162)


def _diff(b, g, keys=None, limit=12):
    keys = keys if keys is not None else sorted(set(b) | set(g)) if isinstance(b, dict) and isinstance(g, dict) else None
    if keys is None: return {} if b == g else {"value": f"{b} -> {g}"}
    d = {}
    for k in keys:
        bv, gv = (b or {}).get(k), (g or {}).get(k)
        if json.dumps(bv, sort_keys=True) != json.dumps(gv, sort_keys=True):
            d[k] = f"{json.dumps(bv)[:80]} -> {json.dumps(gv)[:80]}"
    return dict(list(d.items())[:limit], **({"more": len(d) - limit} if len(d) > limit else {}))


def compare(name, base, got):
    """(ok | None, detail) for one probe; base = vanilla. None = could not be measured in vanilla."""
    if not isinstance(base, dict) or base.get("error") or base.get("_crashed"): return None, f"baseline: {(base or {}).get('error') or (base or {}).get('_crashed')}"
    if not isinstance(got, dict): return False, "not probed"
    if got.get("error") or (got.get("_crashed") and len(got) <= 4): return False, got.get("error") or got.get("_crashed")
    if name == "data":   # every vanilla unit / hero / building unchanged (new ones may be added)
        d = {}
        for grp in ("units", "heroes", "buildings"):
            for k, v in base[grp].items():
                if json.dumps(v, sort_keys=True) != json.dumps(got.get(grp, {}).get(k), sort_keys=True):
                    d[f"{grp}.{k}"] = _diff(v, got.get(grp, {}).get(k) or {}, limit=4)
        for k, v in base["can_attack"].items():
            if v != got.get("can_attack", {}).get(k): d[f"can_attack.{k}"] = f"{v} -> {got.get('can_attack', {}).get(k)}"
        return not d, d or "all stock units, heroes and buildings as vanilla"
    if name == "maps":
        d = {}
        miss = [m for m in base["stock_maps"] if m not in got.get("stock_maps", [])]
        if miss: d["missing_maps"] = miss
        for m, v in base["maps"].items():
            dd = _diff(v, got.get("maps", {}).get(m) or {}, limit=4)
            if dd: d[m] = dd
        return not d, d or f"{len(base['stock_maps'])} stock maps load as vanilla"
    if name == "menu":
        keys = ["menu_visible", "picker_visible", "skirmish_players", "settings_visible", "menu_back", "skirmish_started", "board_players"]
        d = _diff(base, got, keys)
        if not got.get("picker_buttons"): d["picker_buttons"] = "map picker empty"
        if (got.get("campaign_tiles_visible") or 0) < base.get("campaign_tiles_visible", 0):
            d["campaign_tiles_visible"] = f"{base.get('campaign_tiles_visible')} -> {got.get('campaign_tiles_visible')}"
        return not d, d or "menu, map picker, skirmish setup, campaigns, settings work; skirmish starts from the menu"
    if name in ("turns", "produce", "move", "capture", "win", "save", "editor", "abilities", "combat"):
        skip = {"_wall_ms", "_log_errors", "_error_kinds", "_crashed", "_shot", "before", "after", "log", "custom_list", "save_id"}
        keys = [k for k in sorted(set(base) | set(got)) if k not in skip]
        d = _diff(base, got, keys)
        if name == "abilities": d = _diff(base.get("abilities", {}), got.get("abilities", {}), sorted(base.get("abilities", {})))
        if name == "combat": d = _diff(base.get("fights", {}), got.get("fights", {}), sorted(base.get("fights", {})))
        return not d, d or "same as vanilla"
    if name == "camera":
        d = {}
        for k in ("pan_dx", "pan_dz"):
            b, g = base.get(k), got.get(k)
            if b is None or g is None or abs(g - b) > max(CAM_TOL * abs(b), 1.0): d[k] = f"{b} -> {g}"
        for k in ("zoom_in_delta", "switch_cycle", "in_transit_after", "mode0"):
            if base.get(k) != got.get(k): d[k] = f"{base.get(k)} -> {got.get(k)}"
        mb, mg = base.get("moved_to") or [0, 0], got.get("moved_to") or [1e9, 1e9]
        if abs(mb[0] - mg[0]) > CAM_ARRIVE or abs(mb[1] - mg[1]) > CAM_ARRIVE: d["moved_to"] = f"{mb} -> {mg}"
        return not d, d or "pan, zoom, camera styles and scripted camera moves as vanilla"
    if name == "campaign":
        d = {}
        miss = [c for c in base["campaigns"] if c not in got.get("campaigns", [])]
        if miss: d["missing_campaigns"] = miss
        for c, v in base["detail"].items():
            gv = got.get("detail", {}).get(c) or {}
            if len(v["missions"]) != len(gv.get("missions", [])): d[c] = f"{len(v['missions'])} -> {len(gv.get('missions', []))} missions"; continue
            for i, (bm, gm) in enumerate(zip(v["missions"], gv["missions"])):
                dd = _diff(bm, gm, limit=4)
                if dd: d[f"{c}#{i + 1}"] = dd
        return not d, dict(list(d.items())[:10]) or f"{sum(len(v['missions']) for v in base['detail'].values())} missions of {len(base['campaigns'])} campaigns load, stories run as vanilla"
    if name == "ai":
        d = {}
        if not got.get("finished"): d["finished"] = f"AI match did not reach the turn limit ({got.get('turn')}/{base.get('turn_limit')})"
        if got.get("order") != base.get("order"): d["order"] = f"{len(base.get('order', []))} -> {len(got.get('order', []))} AI turns"
        for k in ("max_buildings", "max_units"):
            for s, b in (base.get(k) or {}).items():
                g = (got.get(k) or {}).get(s, 0)
                if g < (1 - AI_TOL) * b: d[f"{k}.{s}"] = f"{b} -> {g}"
        return not d, d or {"max_buildings": got.get("max_buildings"), "max_units": got.get("max_units"), "vanilla": [base.get("max_buildings"), base.get("max_units")]}
    d = _diff(base, got)
    return not d, d or "unchanged"


SUB = {"produce": ("produce_move", "produce"), "move": ("produce_move", "move"), "capture": ("capture_win", "neutral"), "win": ("capture_win", "win")}


def item_result(spec, base, got):
    """Pool item `probe:` spec on two session JSONs -> (ok | None, detail). Specs: log_errors, <probe>, or the derived views
    produce / move (of produce_move), capture / win (of capture_win)."""
    if spec == "log_errors":
        crashed = {p: v.get("_crashed") for p, v in got.items() if isinstance(v, dict) and v.get("_crashed")}
        if crashed: return False, f"crash / timeout in {crashed}"
        # a vanilla bug can repeat an error once per frame (count = timing noise), so compare the kinds of errors per probe
        new = {}
        for p, v in got.items():
            if isinstance(v, dict) and "_error_kinds" in v:
                bk = set((base.get(p) or {}).get("_error_kinds", []))
                n = [e for e in v["_error_kinds"] if e not in bk]
                if n: new[p] = n[:3]
        b = sum(v.get("_log_errors", 0) for k, v in base.items() if isinstance(v, dict) and k in got)
        g = sum(v.get("_log_errors", 0) for v in got.values() if isinstance(v, dict))
        return not new, ({"new_error_kinds": new} if new else {}) | {"lines": f"{g} error lines vs vanilla {b}"}
    if "." in spec:   # probe.f1+f2: only these fields of the probe (as in g1_regress.item_result)
        p, fields = spec.split(".", 1); fields = fields.split("+")
        b, g = base.get(p), got.get(p)
        if isinstance(b, dict) and isinstance(g, dict):
            b = {k: v for k, v in b.items() if k in fields}
            g = {k: v for k, v in g.items() if k in fields or k in ("error", "_crashed")}
        return compare(p, b, g)
    p, field = SUB.get(spec, (spec, None))
    b, g = base.get(p), got.get(p)
    if field:
        if isinstance(b, dict) and (b.get("error") or b.get("_crashed")): return None, f"baseline: {b.get('error') or b.get('_crashed')}"
        if not isinstance(g, dict): return False, "not probed"
        if g.get("error") and field not in g: return False, g["error"]
        b, g = (b or {}).get(field), g.get(field)
        if not isinstance(g, dict): return False, f"no {field} result"
    return compare(spec, b, g)
