# Godot TPS demo: engine fields of the state bridge (appended to gab/game/godot_state.gd).
# screen: loading | menu | level | other; menu_panel (main / online / settings / loading); player (pos, heading, velocity,
# on_floor, aiming, shooting, camera); robots (count, alive, nearest 8: pos, health, state, dead, dist); bullets; players;
# online (server, peers); level_time_s; fps. The player has no health in this game.
const GT_ROBOT_STATES = ["idle", "approach", "aim", "shooting"]
var gt_level_id := 0
var gt_level_t0 := 0


func _gt_main():
	return get_tree().root.get_node_or_null("main")


func _gt_top():
	var m = _gt_main()
	if m == null or m.get_child_count() == 0:
		return null
	return m.get_child(m.get_child_count() - 1)


func _gt_player():
	var t = _gt_top()
	if t == null or not t.has_node("SpawnedNodes"):
		return null
	var p = t.get_node_or_null("SpawnedNodes/" + str(multiplayer.get_unique_id()))
	return p if p != null else t.get_node_or_null("SpawnedNodes/1")


func _ref_pos():
	var p = _gt_player()
	return p.global_position if p != null else null


func _watch() -> void:
	var t = _gt_top()
	if t != null and t.has_node("SpawnedNodes") and t.get_instance_id() != gt_level_id:
		gt_level_id = t.get_instance_id()
		gt_level_t0 = Time.get_ticks_msec()


func _mod_extra() -> Dictionary:
	return {}


func _engine() -> Dictionary:
	var e = {"screen": "loading", "fps": Engine.get_frames_per_second(), "uptime_s": snappedf(Time.get_ticks_msec() / 1000.0, 0.1)}
	var t = _gt_top()
	if t == null:
		return e
	if t.has_method("_on_play_pressed"):
		e["screen"] = "menu"
		for x in ["Loading", "Settings", "Online", "Main"]:
			var n = t.get_node_or_null("UI/" + x)
			if n != null and n.is_visible_in_tree():
				e["menu_panel"] = x.to_lower()
				break
		return e
	if not t.has_node("SpawnedNodes"):
		e["screen"] = "other"
		e["scene"] = str(t.scene_file_path)
		return e
	e["screen"] = "level"
	_watch()
	e["level_time_s"] = snappedf((Time.get_ticks_msec() - gt_level_t0) / 1000.0, 0.1)
	e["online"] = {"server": multiplayer.is_server(), "peers": multiplayer.get_peers().size()}
	var p = _gt_player()
	var ref = p.global_position if p != null else Vector3.ZERO
	var robots = []
	var players = 0
	var bullets = 0
	var alive = 0
	for c in t.get_node("SpawnedNodes").get_children():
		var sp = str(c.get_script().resource_path) if c.get_script() != null else ""
		if sp.ends_with("red_robot.gd"):
			if not c.dead:
				alive += 1
			var st = int(c.state)
			robots.append({"name": str(c.name), "pos": _v(c.global_position), "health": int(c.health), "dead": bool(c.dead),
					"state": GT_ROBOT_STATES[st] if st >= 0 and st < GT_ROBOT_STATES.size() else st,
					"dist": snappedf(c.global_position.distance_to(ref), 0.01)})
		elif sp.ends_with("player.gd"):
			players += 1
		elif sp.ends_with("bullet.gd"):
			bullets += 1
	robots.sort_custom(func(a, b): return a["dist"] < b["dist"])
	e["robots"] = {"count": robots.size(), "alive": alive, "nearest": robots.slice(0, 8)}
	e["players"] = players
	e["bullets"] = bullets
	if p != null:
		var pi_ = p.player_input
		var fwd = p.orientation.basis.z
		e["player"] = {"pos": _v(p.global_position), "heading_deg": snappedf(rad_to_deg(atan2(fwd.x, fwd.z)), 0.1),
				"velocity": _v(p.velocity), "on_floor": p.is_on_floor(), "airborne_s": snappedf(p.airborne_time, 0.01),
				"aiming": bool(pi_.aiming), "shooting": bool(pi_.shooting), "crosshair": p.crosshair.is_visible_in_tree(),
				"camera_pos": _v(pi_.camera_camera.global_position)}
	return e
