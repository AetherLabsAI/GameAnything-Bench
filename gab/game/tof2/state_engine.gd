# Tanks of Freedom II: engine fields of the state bridge (appended to gab/game/godot_state.gd).
# screen: main_menu (+ panel) | board | map_editor | other; on the board: mode (skirmish / campaign), map, campaign, mission,
# turn, turn_limit, current player (index, side, type, ap), players (side, type, team, ap, alive, units, buildings), cursor tile,
# selected tile (unit / building there), units (nearest 20 to the cursor: template, side, hp / max_hp, move, attacks, level, pos),
# buildings per side and type, story dialog (shown, text), match summary shown, triggers (map story scripts: type, suspended).
# mod extra: the maps / campaigns the mod added or replaced (from its files) and whether the current map is one of them.


func _watch() -> void:
	pass


func _tf_board():
	var s = get_tree().current_scene
	return s if s != null and s.has_method("start_turn") and s.is_node_ready() else null


func _ref_pos():
	var b = _tf_board()
	return Vector2(b.map.tile_box_position) if b != null else null


func _engine() -> Dictionary:
	var e = {"screen": "loading", "fps": Engine.get_frames_per_second(), "uptime_s": snappedf(Time.get_ticks_msec() / 1000.0, 0.1)}
	var s = get_tree().current_scene
	if s == null:
		return e
	var sp = str(s.get_script().resource_path) if s.get_script() != null else ""
	if sp.ends_with("main_menu.gd"):
		e["screen"] = "main_menu"
		e["panel"] = "menu"
		for x in ["map_picker/picker", "skirmish/skirmish", "saves/saves", "settings/settings", "controls/controls",
				"campaign_mission/campaign_mission", "campaign_mission_selection/campaign_mission_selection",
				"campaign_selection/campaign_selection", "online/online", "multiplayer/multiplayer", "credits/credits", "changelog/changelog"]:
			var n = s.get_node_or_null("ui/" + x)
			if n != null and n.is_visible_in_tree():
				e["panel"] = x.get_slice("/", 0)
				break
		return e
	if sp.ends_with("map_editor.gd"):
		e["screen"] = "map_editor"
		return e
	var b = _tf_board()
	if b == null:
		e["screen"] = "other"
		e["scene"] = str(s.scene_file_path)
		return e
	e["screen"] = "board"
	var ms = get_node_or_null("/root/MatchSetup")
	if ms != null:
		e["mode"] = "campaign" if ms.campaign_name != null else "skirmish"
		e["map"] = str(ms.map_name) if ms.map_name != null else null
		e["campaign"] = ms.campaign_name
		e["mission"] = ms.mission_no
		e["turn_limit"] = ms.turn_limit
	var st = b.state
	e["turn"] = int(st.turn)
	var model = b.map.model
	var players = []
	for pl in st.players:
		var side = pl["side"]
		players.append({"side": side, "type": pl["type"], "team": pl["team"], "ap": int(pl["ap"]), "alive": pl["alive"],
				"units": model.get_player_units(side).size(), "buildings": model.get_player_buildings(side).size()})
	e["players"] = players
	if st.current_player >= 0 and st.current_player < players.size():
		var c = players[st.current_player]
		e["current"] = {"index": int(st.current_player), "side": c["side"], "type": c["type"], "ap": c["ap"]}
	var ref = Vector2(b.map.tile_box_position)
	e["cursor"] = _v(b.map.tile_box_position)
	var units = []
	var bcount = {}
	for k in model.tiles:
		var t = model.tiles[k]
		if t.unit.is_present():
			var u = t.unit.tile
			units.append({"t": str(u.template_name), "side": u.side, "hp": int(u.hp), "max_hp": int(u.max_hp), "move": int(u.move),
					"attacks": int(u.attacks), "level": int(u.level), "pos": _v(t.position), "dist": snappedf(Vector2(t.position).distance_to(ref), 0.01)})
		if t.building.is_present():
			var key = str(t.building.tile.side) + ":" + str(t.building.tile.template_name)
			bcount[key] = bcount.get(key, 0) + 1
	units.sort_custom(func(a, c): return a["dist"] < c["dist"])
	e["units"] = {"count": units.size(), "nearest": units.slice(0, 20)}
	e["buildings"] = bcount
	if b.selected_tile != null:
		var t = b.selected_tile
		var sel = {"pos": _v(t.position)}
		if t.unit.is_present():
			sel["unit"] = str(t.unit.tile.template_name) + " (" + str(t.unit.tile.side) + ")"
		if t.building.is_present():
			sel["building"] = str(t.building.tile.template_name) + " (" + str(t.building.tile.side) + ")"
		e["selected"] = sel
	var sd = b.ui.story_dialog
	e["story"] = {"shown": sd.is_visible_in_tree(), "text": str(sd.text.text).left(200) if sd.is_visible_in_tree() else ""}
	e["summary_shown"] = b.ui.summary.is_visible_in_tree()
	var trig = {}
	for name in b.scripting.triggers:
		if trig.size() >= 30:
			break
		var tr = b.scripting.triggers[name]
		trig[str(name).left(60)] = {"suspended": bool(tr.suspended), "one_off": bool(tr.one_off)}
	e["triggers"] = trig
	return e


func _mod_extra() -> Dictionary:
	var maps = []
	var camps = {}
	for p in mod_added.keys() + mod_changed.keys():
		var kind = "added" if mod_added.has(p) else "changed"
		if p.ends_with(".tofmap.json"):
			maps.append({"map": p.get_file().trim_suffix(".tofmap.json"), "kind": kind, "file": p.left(200)})
		var parts = p.replace("res://", "").replace("user://", "").split("/")
		if parts.size() > 2 and ((parts[0] == "assets" and parts[1] == "campaigns") or parts[0] == "campaign"):
			var c = parts[2] if parts[0] == "assets" else parts[1]
			camps[c] = kind if not camps.has(c) or camps[c] == "changed" else camps[c]
	var cs = get_node_or_null("/root/Campaign")
	var cout = {}
	for c in camps:
		cout[c] = {"kind": camps[c], "progress": _plain(cs.campaign_progress.get(c)) if cs != null else null}
	var out = {"maps": maps.slice(0, 20), "campaigns": cout}
	var ms = get_node_or_null("/root/MatchSetup")
	if _tf_board() != null and ms != null:
		var cur = str(ms.map_name) if ms.map_name != null else ""
		if ms.campaign_name != null and cs != null:
			cur = str(cs.get_campaign_mission_map(ms.campaign_name, ms.mission_no))
		out["current_map_from_mod"] = maps.any(func(m): return m["map"] == cur) or camps.has(str(ms.campaign_name))
	return out
