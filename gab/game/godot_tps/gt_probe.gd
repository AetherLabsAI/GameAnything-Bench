# Copied from the original probes_godot_tps_gt_probe.gd (verified 2026-10-30) + the gab "gate" mode (load test).
# GameAnything G1 regression probe for the Godot TPS demo (Godot 4.7.2). Injected as an extra autoload through the
# instance's override.cfg (never part of the game files). Reads GT_PROBE_OUT (json path), GT_PROBE_ONLY (comma list),
# GT_PROBE_MODE ("headless" game-logic probes, or "render" = menu / hud / frame time on a real GPU window).
# Every probe measures the game's own nodes (positions, states, timers, signals), not pixels (except the render shots).
extends Node

const ORDER_HEADLESS = ["settings", "resources", "level", "move", "jump", "fall_respawn", "camera", "shoot", "enemy", "audio", "online"]
const ORDER_RENDER = ["menu", "hud", "frametime"]
const LEVEL_PATH = "res://level/level.tscn"
const DT = 1.0 / 60.0

var out = {}
var t_start = 0.0
var _host_id = 0       # instance id of the game's own /root/main, once seen (a total-conversion mod may free or replace it)
var _host_gone_msg = ""
var _level_warm = null  # the level PackedScene, held so the resource cache keeps it (see _ready)


func _ready() -> void:
	process_mode = Node.PROCESS_MODE_ALWAYS
	t_start = Time.get_ticks_msec() / 1000.0
	# Headless, menu.gd auto-hosts with ResourceLoader.load_threaded_request(level, "", true). With use_sub_threads the
	# dummy (headless) renderer's RID storage races: "Attempting to initialize the wrong RID", "Another resource is loaded
	# from path", and in ~10% of runs (more on a busy machine) a failed mesh -> level.gd's preloads fail -> "Parse Error
	# level.tscn:42", SIGSEGV / exit 132 or a hang -> no level. An autoload is ready before the main scene, so this
	# main-thread load puts the level in the resource cache and the menu's threaded request just returns it (0 of 20
	# runs failed or logged an error, 2026-11-06). Same files, same errors if a mod breaks the level.
	if _env("GT_PROBE_MODE", "headless") in ["headless", "gate"] and ResourceLoader.exists(LEVEL_PATH):
		_level_warm = ResourceLoader.load(LEVEL_PATH)
		print("GTPROBE level preloaded: ", _level_warm != null, " in ", Time.get_ticks_msec() - int(t_start * 1000.0), " ms")
	print("GTPROBE ready")
	_main.call_deferred()


func _env(k: String, d: String = "") -> String:
	var v = OS.get_environment(k)
	return v if v != "" else d


func _main() -> void:
	var uargs = OS.get_cmdline_user_args()
	if "--gt-client" in uargs:
		await _client_main(uargs[uargs.find("--gt-client") + 1])
		return
	var mode = _env("GT_PROBE_MODE", "headless")
	var only = _env("GT_PROBE_ONLY").split(",", false)
	var order: Array = ORDER_HEADLESS if mode == "headless" else ORDER_RENDER
	out["_mode"] = mode
	out["_engine"] = Engine.get_version_info()["string"]
	out["_timing"] = {}
	if mode == "gate":  # gab load test: every game file loads / compiles / instantiates; whether the level comes up (no wait for it first)
		out["resources"] = await p_resources()
		out["level_loaded"] = (await _wait_level(60.0)) != null
		_finish()
		return
	if mode == "headless":
		var lvl = await _wait_level(90.0)
		out["_level_loaded_s"] = snappedf(Time.get_ticks_msec() / 1000.0 - t_start, 0.1)
		if lvl == null:
			out["error"] = _host_gone_msg if _host_gone() != "" else "level did not load in 90 s (%s)" % _scene_summary()
			out["_host_removed"] = _host_gone() != ""
			_finish()
			return
	for n in order:
		if only.size() > 0 and not (n in only):
			continue
		var t0 = Time.get_ticks_msec()
		print("GTPROBE begin ", n)
		var r = null
		if _host_gone() != "":
			r = {"error": _host_gone_msg, "host_removed": true}
		else:
			r = await call("p_" + n)
		if r == null:  # a script error ended the probe (e.g. a node it held was freed): say so instead of "not probed"
			r = {"error": _host_gone_msg, "host_removed": true} if _host_gone() != "" else {"not_tested": "probe %s aborted by a script error (see the log)" % n}
		out[n] = r
		out["_timing"][n] = snappedf((Time.get_ticks_msec() - t0) / 1000.0, 0.1)
		print("GTPROBE end ", n, " ", JSON.stringify(r).left(300))
		_release_all()
		_write()  # partial results survive a later crash
	_finish()


func _write() -> void:
	var f = FileAccess.open(_env("GT_PROBE_OUT", "user://gt_probe.json"), FileAccess.WRITE)
	f.store_string(JSON.stringify(out, " "))
	f.close()


func _finish() -> void:
	_write()
	print("GTPROBE done")
	get_tree().quit()


# ---------------------------------------------------------------- helpers ----------------------------------------------------------------
func _main_node() -> Node:
	return get_tree().root.get_node_or_null("main")


func _host_gone() -> String:
	"""'' while the game's own main scene (/root/main, first instance seen) is in the tree; else (sticky) why not: a mod that
	frees or replaces it (a total conversion) removes the host game, so no level / menu probe can ever run."""
	if _host_gone_msg != "":
		return _host_gone_msg
	var m = _main_node()
	if _host_id == 0:
		if m:
			_host_id = m.get_instance_id()
		return ""
	var t = snappedf(Time.get_ticks_msec() / 1000.0 - t_start, 0.1)
	if m == null:
		_host_gone_msg = "host main scene removed by the mod: /root/main was freed at %.1f s (%s)" % [t, _scene_summary()]
	elif m.get_instance_id() != _host_id:
		_host_gone_msg = "host main scene replaced by the mod at %.1f s: /root/main is now %s (%s)" % [t, str(m.scene_file_path), _scene_summary()]
	return _host_gone_msg


func _scene_summary() -> String:
	"""root children and main's children (scene paths): what is running instead of the level"""
	var rc = []
	for c in get_tree().root.get_children():
		if c != self:
			rc.append(str(c.name))
	var mc = []
	var m = _main_node()
	if m:
		for c in m.get_children():
			mc.append(str(c.name) + (" <" + c.scene_file_path + ">" if c.scene_file_path != "" else ""))
	return "root: %s; main: %s" % [rc, mc if m else "none"]


func _alive(nodes: Array) -> bool:
	"""every node held across an await still exists (a mod may free the level, player or menu meanwhile)"""
	for n in nodes:
		if not is_instance_valid(n) or not n.is_inside_tree():
			return false
	return true


func _no_level() -> Dictionary:
	if _host_gone() != "":
		return {"error": _host_gone_msg, "host_removed": true}
	return {"not_tested": "level did not (re)load in 60 s (%s)" % _scene_summary()}


func _gone_err(what: String) -> Dictionary:
	if _host_gone() != "":
		return {"error": _host_gone_msg, "host_removed": true}
	return {"error": what + " was freed during the probe (%s)" % _scene_summary()}


func _level() -> Node:
	var m = _main_node()
	return m.get_node_or_null("Level") if m else null


func _wait_level(timeout: float):
	var t0 = Time.get_ticks_msec()
	while Time.get_ticks_msec() - t0 < timeout * 1000.0:
		if _host_gone() != "":
			return null
		var l = _level()
		if l and l.is_node_ready() and _player():
			await _frames(30)
			if not _alive([l]):
				continue
			return l
		await get_tree().process_frame
	return null


func _reload_level():
	"""Fresh copy of the level (robots and player respawned), as the game does when Play is pressed."""
	_release_all()
	if _host_gone() != "":
		return null
	var m = _main_node()
	var old = _level()
	m.change_scene_to_packed(load(LEVEL_PATH))
	var t0 = Time.get_ticks_msec()
	while Time.get_ticks_msec() - t0 < 60000:
		await get_tree().process_frame
		if _host_gone() != "":
			return null
		var l = _level()
		if l and l != old and is_instance_valid(l) and l.is_node_ready() and _player():
			await _frames(30)
			if not _alive([l]):
				continue
			return l
	return null


func _player() -> Node:
	var l = _level()
	return l.get_node_or_null("SpawnedNodes/1") if l else null


func _robots() -> Array:
	var r = []
	var l = _level()
	if l == null:
		return r
	for c in l.get_node("SpawnedNodes").get_children():
		if c.get_script() and str(c.get_script().resource_path).ends_with("red_robot.gd"):
			r.append(c)
	return r


func _frames(n: int) -> void:
	for i in n:
		await get_tree().physics_frame


func _secs(s: float) -> void:
	await _frames(int(round(s * 60.0)))


func _release_all() -> void:
	for a in ["move_forward", "move_back", "move_left", "move_right", "jump", "aim", "shoot"]:
		if InputMap.has_action(a) and Input.is_action_pressed(a):
			Input.action_release(a)


func _v(v: Vector3) -> Array:
	return [snappedf(v.x, 0.001), snappedf(v.y, 0.001), snappedf(v.z, 0.001)]


func _spawn_xf(i: int) -> Transform3D:
	return _level().get_node("PlayerSpawnpoints").get_child(i).global_transform


func _open_dir() -> Vector3:
	"""Fixed walk direction from spawn point 1: 160 deg in the XZ plane (free for ~29 m at knee and chest height)."""
	var a = deg_to_rad(160.0)
	return Vector3(cos(a), 0, sin(a))


func _place_player(pos: Vector3, yaw_dir: Vector3 = Vector3.ZERO) -> void:
	var p = _player()
	p.velocity = Vector3.ZERO
	p.global_position = pos
	if "initial_position" in p:
		pass
	p.motion = Vector2.ZERO
	if yaw_dir != Vector3.ZERO:
		_face_camera(yaw_dir)
		var o: Transform3D = p.orientation
		o.basis = Basis.looking_at(-Vector3(yaw_dir.x, 0, yaw_dir.z).normalized())  # model front is +Z
		p.orientation = o
	p.reset_physics_interpolation()


func _face_camera(dir: Vector3, pitch: float = 0.0) -> void:
	var pi_ = _player().player_input
	var flat = Vector3(dir.x, 0, dir.z).normalized()
	# the spring arm is turned 180 deg: the camera sits on -Z of CameraBase and looks along +Z (walk direction)
	pi_.camera_base.global_basis = Basis.looking_at(-flat)
	pi_.camera_rot.rotation.x = pitch


func _aim_at(target: Vector3) -> void:
	"""Pitch the camera so the view centre (crosshair) points at target (one feedback step per frame)."""
	var pi_ = _player().player_input
	var cam: Camera3D = pi_.camera_camera
	var want: Vector3 = (target - cam.global_position).normalized()
	var cur: Vector3 = -cam.global_basis.z
	var flat = Vector3(want.x, 0, want.z).normalized()
	pi_.camera_base.global_basis = Basis.looking_at(-flat)
	pi_.camera_rot.rotation.x = clampf(pi_.camera_rot.rotation.x - (asin(want.y) - asin(cur.y)), -1.5, 1.2)


func _ray(from: Vector3, to: Vector3, exclude: Array = [], mask: int = 0xFFFFFFFF) -> Dictionary:
	var q = PhysicsRayQueryParameters3D.create(from, to, mask, exclude)
	return _level().get_world_3d().direct_space_state.intersect_ray(q)


func _forklift_rids() -> Array:
	var r = []
	var l = _level()
	for f in l.get_node("FlyingForklifts").get_children():
		if f is CollisionObject3D:
			r.append(f.get_rid())
		for c in f.find_children("*", "CollisionObject3D", true, false):
			r.append(c.get_rid())
	return r


func _const_map(path: String) -> Dictionary:
	var d = {}
	var s = load(path)
	if s == null:
		return {"error": "cannot load " + path}
	var m: Dictionary = s.get_script_constant_map()
	for k in m:
		var v = m[k]
		if v is int or v is float or v is bool or v is String:
			d[k] = v
	return d


func _count_classes(n: Node, acc: Dictionary) -> void:
	var c = n.get_class()
	acc[c] = acc.get(c, 0) + 1
	for ch in n.get_children():
		_count_classes(ch, acc)


# ---------------------------------------------------------------- probes ----------------------------------------------------------------
func p_settings():
	"""Project settings and gameplay tunables the game ships with (what a scene / script / project.godot edit changes directly)."""
	var r = {}
	var ps = {}
	for k in ["physics/3d/default_gravity", "physics/3d/default_gravity_vector", "physics/common/physics_ticks_per_second",
			"physics/3d/physics_engine", "physics/common/physics_interpolation", "application/run/main_scene",
			"physics/3d/default_linear_damp", "physics/3d/default_angular_damp"]:
		ps[k] = str(ProjectSettings.get_setting(k))
	r["project"] = ps
	var auto = []
	for p in ProjectSettings.get_property_list():
		var n: String = p["name"]
		if n.begins_with("autoload/") and n != "autoload/GTProbe":
			auto.append(n + "=" + str(ProjectSettings.get_setting(n)))
	auto.sort()
	r["autoloads"] = auto
	var acts = {}
	for a in InputMap.get_actions():
		if str(a).begins_with("ui_") and not (str(a) in ["ui_accept", "ui_cancel", "ui_select"]):
			continue
		var evs = []
		for e in InputMap.action_get_events(a):
			evs.append(e.as_text())
		evs.sort()
		acts[str(a)] = evs
	r["input_map"] = acts
	var p = _player()
	var tun = {}
	for k in _const_map("res://player/player.gd"):
		tun["player." + k] = _const_map("res://player/player.gd")[k]
	var pic = _const_map("res://player/player_input.gd")
	for k in pic:
		tun["player_input." + k] = pic[k]
	var bc = _const_map("res://player/bullet/bullet.gd")
	for k in bc:
		tun["bullet." + k] = bc[k]
	var rc = _const_map("res://enemies/red_robot/red_robot.gd")
	for k in rc:
		tun["robot." + k] = rc[k]
	tun["player.fire_cooldown"] = p.get_node("FireCooldown").wait_time
	tun["player.spring_length"] = p.get_node("CameraBase/CameraRot/SpringArm3D").spring_length
	tun["player.collision_mask"] = p.collision_mask
	tun["player.collision_layer"] = p.collision_layer
	var rb = _robots()
	if rb.size() > 0:
		tun["robot.health"] = rb[0].health
		tun["robot.collision_layer"] = rb[0].collision_layer
	tun["level.robot_spawnpoints"] = _level().get_node("RobotSpawnpoints").get_child_count()
	tun["level.player_spawnpoints"] = _level().get_node("PlayerSpawnpoints").get_child_count()
	tun["level.robot_respawn_s"] = 15.0  # literal in level.gd; measured by the shoot probe
	r["tunables"] = tun
	var buses = []
	for i in AudioServer.bus_count:
		buses.append("%s vol=%.1f fx=%d send=%s" % [AudioServer.get_bus_name(i), AudioServer.get_bus_volume_db(i), AudioServer.get_bus_effect_count(i), AudioServer.get_bus_send(i)])
	r["audio_buses"] = buses
	return r


func p_resources():
	"""Every scene / script / resource file of the game still loads (ResourceLoader), and every scene instantiates."""
	var files = []
	_walk("res://", files)
	var bad = {}
	var n_ok = 0
	for f in files:
		if f.begins_with("res://gtprobe/") or f.begins_with("res://.godot"):
			continue
		var res = ResourceLoader.load(f, "", ResourceLoader.CACHE_MODE_REUSE)
		if res == null:
			bad[f] = "load failed"
			continue
		if res is GDScript and not res.can_instantiate():
			bad[f] = "script does not compile"
			continue
		if res is PackedScene and f != LEVEL_PATH and not f.begins_with("res://main/"):
			var inst = res.instantiate()
			if inst == null:
				bad[f] = "instantiate failed"
				continue
			inst.free()
		n_ok += 1
	return {"files": files.size(), "ok": n_ok, "failed": bad}


func _walk(dir: String, acc: Array) -> void:
	var d = DirAccess.open(dir)
	if d == null:
		return
	for f in d.get_files():
		var p = dir.path_join(f)
		if f.ends_with(".remap"):
			p = p.trim_suffix(".remap")
		if p.get_extension() in ["tscn", "tres", "gd", "gdshader"]:
			acc.append(p)
	for s in d.get_directories():
		if s.begins_with("."):
			continue
		_walk(dir.path_join(s), acc)


func p_level():
	"""Level geometry and collision: a grid of vertical rays (hit height per cell), horizontal rays at 1 m from the
	spawn area, node counts per class, spawn points, robots spawned at start, flying forklifts animating."""
	var l = _level()
	var ex = _forklift_rids()
	for b in [_player()] + _robots():
		ex.append(b.get_rid())
	var grid = {}
	var hits = 0
	var x = -60.0
	while x <= 100.0:
		var z = -60.0
		while z <= 110.0:
			var c = _ray(Vector3(x, 60, z), Vector3(x, -80, z), ex, 1)
			var key = "%d,%d" % [int(x), int(z)]
			if c.is_empty():
				grid[key] = null
			else:
				grid[key] = snappedf(c.position.y, 0.01)
				hits += 1
			z += 4.0
		x += 4.0
	var ring = []
	var sp = _spawn_xf(0).origin + Vector3(0, 1.0, 0)
	for i in 36:
		var a = deg_to_rad(i * 10.0)
		var d = Vector3(cos(a), 0, sin(a))
		var c = _ray(sp, sp + d * 80.0, ex, 1)
		ring.append(null if c.is_empty() else snappedf(sp.distance_to(c.position), 0.01))
	var ring_low = []
	var sp2 = _spawn_xf(0).origin + Vector3(0, 0.35, 0)
	for i in 36:
		var a = deg_to_rad(i * 10.0)
		var d = Vector3(cos(a), 0, sin(a))
		var c = _ray(sp2, sp2 + d * 80.0, ex, 0xFFFFFFFF)
		ring_low.append(null if c.is_empty() else snappedf(sp2.distance_to(c.position), 0.01))
	var counts = {}
	_count_classes(l, counts)
	var keep = {}
	for k in ["StaticBody3D", "CollisionShape3D", "CollisionPolygon3D", "MeshInstance3D", "OmniLight3D", "SpotLight3D",
			"DirectionalLight3D", "ReflectionProbe", "Area3D", "AudioStreamPlayer", "AudioStreamPlayer3D", "GPUParticles3D",
			"CPUParticles3D", "AnimationPlayer", "Decal", "RigidBody3D", "CharacterBody3D", "Marker3D"]:
		keep[k] = counts.get(k, 0)
	var spawns = []
	for m in l.get_node("PlayerSpawnpoints").get_children():
		spawns.append(_v(m.global_position))
	var rspawns = []
	for m in l.get_node("RobotSpawnpoints").get_children():
		rspawns.append(_v(m.global_position))
	var rob = []
	for r in _robots():
		rob.append(_v(r.global_position))
	var fl = l.get_node("FlyingForklifts")
	var f0 = []
	for f in fl.get_children():
		if f is Node3D:
			f0.append(f.global_position)
	await _secs(2.0)
	if not _alive([l, fl]):
		return _gone_err("the level")
	var fmove = 0.0
	var i = 0
	for f in fl.get_children():
		if f is Node3D:
			fmove = maxf(fmove, f.global_position.distance_to(f0[i]))
			i += 1
	var ap: AnimationPlayer = fl.get_node_or_null("AnimationPlayer")
	return {"grid": grid, "grid_hits": hits, "grid_cells": grid.size(), "ring_1m": ring, "ring_low": ring_low, "counts": keep,
		"player_spawns": spawns, "robot_spawns": rspawns, "robots_at_start": rob.size(), "robot_positions": rob,
		"forklifts": f0.size(), "forklift_moved_2s": snappedf(fmove, 0.01),
		"forklift_anim": (ap.current_animation if ap else null)}


func _run_line(dir: Vector3, secs: float) -> Dictionary:
	var p = _player()
	_place_player(_spawn_xf(0).origin, dir)
	await _secs(1.0)
	var p0: Vector3 = p.global_position
	Input.action_press("move_forward")
	var speeds = []
	var last = p.global_position
	for f in int(secs * 60):
		await get_tree().physics_frame
		var h = Vector3(p.global_position.x - last.x, 0, p.global_position.z - last.z)
		speeds.append(h.length() * 60.0)
		last = p.global_position
	Input.action_release("move_forward")
	if not _alive([p]):
		return _gone_err("the player")
	var p_rel: Vector3 = p.global_position
	await _secs(1.5)
	if not _alive([p]):
		return _gone_err("the player")
	var p_end: Vector3 = p.global_position
	var tail = speeds.slice(speeds.size() - 30)
	var top = 0.0
	for s in tail:
		top += s
	top /= tail.size()
	var hd = Vector3(p_rel.x - p0.x, 0, p_rel.z - p0.z)
	var heading = rad_to_deg(Vector2(dir.x, dir.z).angle_to(Vector2(hd.x, hd.z))) if hd.length() > 0.01 else null
	return {"run_speed": snappedf(top, 0.01), "travel": snappedf(hd.length(), 0.01),
		"stop_slide": snappedf(Vector3(p_end.x - p_rel.x, 0, p_end.z - p_rel.z).length(), 0.01),
		"heading_err_deg": (snappedf(heading, 0.1) if heading != null else null), "dy": snappedf(p_end.y - p0.y, 0.01)}


func p_move():
	"""Walk forward 2 s from spawn point 1 along the spawn's facing (camera turned there), then release."""
	if await _reload_level() == null:
		return _no_level()
	var fwd = _open_dir()
	var r = await _run_line(fwd, 2.0)
	if "error" in r:
		return r
	# strafe/turn: walk right relative to the camera for 1 s (player turns and walks that way)
	var p = _player()
	_place_player(_spawn_xf(0).origin, fwd)
	await _secs(1.0)
	var p0: Vector3 = p.global_position
	Input.action_press("move_right")
	await _secs(1.5)
	Input.action_release("move_right")
	await _secs(1.0)
	if not _alive([p]):
		return _gone_err("the player")
	var d = p.global_position - p0
	var right = fwd.cross(Vector3.UP).normalized()
	r["right_travel"] = snappedf(Vector3(d.x, 0, d.z).dot(right), 0.01)
	r["right_side_drift"] = snappedf(absf(Vector3(d.x, 0, d.z).dot(fwd)), 0.01)
	return r


func p_jump():
	"""Jump in place on the spawn floor: height, air time, gravity fitted from the arc, landing on the floor again."""
	if await _reload_level() == null:
		return _no_level()
	var p = _player()
	_place_player(_spawn_xf(0).origin, -_spawn_xf(0).basis.z)
	await _secs(1.5)
	if not _alive([p]):
		return _gone_err("the player")
	var y0: float = p.global_position.y
	var on_floor0: bool = p.is_on_floor()
	Input.action_press("jump")
	await get_tree().process_frame
	await get_tree().process_frame
	Input.action_release("jump")
	var samples = []
	var t = 0.0
	var left = false
	var air = 0.0
	var landed = false
	var played_jump = false
	for f in 180:
		await get_tree().physics_frame
		if not _alive([p]):
			return _gone_err("the player")
		t += DT
		var y: float = p.global_position.y - y0
		samples.append([t, y])
		if p.sound_effect_jump.playing:
			played_jump = true
		if not left and y > 0.02:
			left = true
		if left and p.is_on_floor():
			air = t
			landed = true
			break
	var hmax = 0.0
	for s in samples:
		hmax = maxf(hmax, s[1])
	var air_s = []
	for s in samples:
		if s[1] > 0.05:
			air_s.append(s)
	return {"on_floor_before": on_floor0, "jump_height": snappedf(hmax, 0.01), "air_time": snappedf(air, 0.02),
		"landed": landed, "gravity": _fit_g(air_s), "end_dy": snappedf(p.global_position.y - y0, 0.02), "jump_sound": played_jump}


func _fit_g(s: Array):
	if s.size() < 6:
		return null
	var n = float(s.size())
	var S = [0.0, 0.0, 0.0, 0.0, 0.0]
	var Z = [0.0, 0.0, 0.0]
	for e in s:
		for k in 5:
			S[k] += pow(e[0], k)
		for k in 3:
			Z[k] += e[1] * pow(e[0], k)
	var M = [[S[0], S[1], S[2], Z[0]], [S[1], S[2], S[3], Z[1]], [S[2], S[3], S[4], Z[2]]]
	for i in 3:
		var piv = i
		for r in range(i, 3):
			if absf(M[r][i]) > absf(M[piv][i]):
				piv = r
		var tmp = M[i]; M[i] = M[piv]; M[piv] = tmp
		if absf(M[i][i]) < 1e-12:
			return null
		for r in 3:
			if r != i:
				var k: float = M[r][i] / M[i][i]
				for c in 4:
					M[r][c] -= k * M[i][c]
	return snappedf(-2.0 * M[2][3] / M[2][2], 0.01)


func p_fall_respawn():
	"""Falling out of the world (below y -40) puts the player back at its start position; screen fades while falling."""
	if await _reload_level() == null:
		return _no_level()
	var p = _player()
	var start: Vector3 = p.initial_position
	var pos = start + Vector3(0, -30, 0)
	p.global_position = Vector3(start.x, -39.0, start.z)
	p.velocity = Vector3(0, -5, 0)
	var back = -1.0
	var fade = 0.0
	for f in 120:
		await get_tree().physics_frame
		if not _alive([p]):
			return _gone_err("the player")
		fade = maxf(fade, p.player_input.color_rect.modulate.a)
		if p.global_position.y > -20.0:
			back = f * DT
			break
	await _secs(1.0)
	if not _alive([p]):
		return _gone_err("the player")
	return {"returned": back >= 0.0, "return_s": snappedf(back, 0.05), "dist_to_start": snappedf(p.global_position.distance_to(start), 0.05),
		"fade_alpha": snappedf(fade, 0.1)}


func p_camera():
	"""Third-person camera: distance behind the player (far mode), closer over-the-shoulder when aiming, back when released;
	the spring arm shortens against a wall behind the player (camera never ends up behind the wall)."""
	if await _reload_level() == null:
		return _no_level()
	var p = _player()
	var cam: Camera3D = p.player_input.camera_camera
	var arm: Node3D = p.get_node("CameraBase/CameraRot/SpringArm3D")
	var fwd = _open_dir()
	_place_player(_spawn_xf(0).origin, fwd)
	await _secs(1.5)
	if not _alive([p, cam]):
		return _gone_err("the player")
	var head: Vector3 = p.global_position + Vector3(0, 1.5, 0)
	var far_d = cam.global_position.distance_to(p.global_position)
	var cur_ok = cam.is_current()
	var behind = (cam.global_position - p.global_position)
	var behind_dot = -Vector3(behind.x, 0, behind.z).normalized().dot(fwd)
	var arm_far = _v(arm.position)
	Input.action_press("aim")
	await _secs(1.2)
	if not _alive([p, cam]):
		return _gone_err("the player")
	var aim_d = cam.global_position.distance_to(p.global_position)
	var arm_aim = _v(arm.position)
	var aiming: bool = p.player_input.aiming
	var crosshair_vis: bool = p.crosshair.is_visible_in_tree() and p.crosshair.self_modulate.a > 0.5
	Input.action_release("aim")  # held > 0.4 s: releasing ends aiming
	await _secs(1.2)
	if not _alive([p, cam]):
		return _gone_err("the player")
	var back_d = cam.global_position.distance_to(p.global_position)
	var cross_after = p.crosshair.self_modulate.a > 0.5
	# wall test: find a wall along -fwd (behind) from spawn and stand 0.8 m in front of it, facing away from it
	var ex = [p.get_rid()] + _forklift_rids()
	var wall_res = {}
	var best = null
	for i in 36:
		var a = deg_to_rad(i * 10.0)
		var d = Vector3(cos(a), 0, sin(a))
		var c = _ray(head, head + d * 30.0, ex, 1)
		if not c.is_empty() and absf(c.normal.y) < 0.3:
			var dist = head.distance_to(c.position)
			if dist > 2.0 and (best == null or dist < best[0]):
				best = [dist, d, c.position]
	if best != null:
		var d: Vector3 = best[1]
		var stand: Vector3 = best[2] - d * 0.8
		stand.y = _spawn_xf(0).origin.y
		_place_player(stand, -d)
		await _secs(1.5)
		if not _alive([p, cam]):
			return _gone_err("the player")
		var hd: Vector3 = p.global_position + Vector3(0, 1.5, 0)
		var cd = cam.global_position.distance_to(p.global_position)
		var through = _ray(p.global_position + Vector3(0, 1.0, 0), cam.global_position, ex, 1)
		wall_res = {"cam_dist_at_wall": snappedf(cd, 0.05), "cam_behind_wall": not through.is_empty(),
			"shortened": cd < far_d - 0.3}
	else:
		wall_res = {"error": "no wall found"}
	return {"current": cur_ok, "far_dist": snappedf(far_d, 0.05), "behind": behind_dot > 0.5, "aim_dist": snappedf(aim_d, 0.05),
		"aiming": aiming, "crosshair": crosshair_vis, "crosshair_after_release": cross_after, "arm_far": arm_far, "arm_aim": arm_aim, "released_dist": snappedf(back_d, 0.05), "wall": wall_res}


func p_shoot():
	"""Aim at robot 1 from 6 m and hold fire for 3 s: fire rate, bullet speed, hits, robot loses 1 health per hit and is destroyed
	after 5 hits (debris parts fly and fall), robot respawns at its spawn point 15 s later."""
	if await _reload_level() == null:
		return _no_level()
	var p = _player()
	var rsp: Transform3D = _level().get_node("RobotSpawnpoints").get_child(int(_env("GT_SHOOT_ROBOT", "0"))).global_transform
	var rob = null
	for r in _robots():
		if r.global_position.distance_to(rsp.origin) < 3.0:
			rob = r
	if rob == null:
		return {"error": "robot 3 not found"}
	var rface: Vector3 = rsp.basis.z  # robot front is +Z
	var stand: Vector3 = rsp.origin + Vector3(rface.x, 0, rface.z).normalized() * float(_env("GT_SHOOT_DIST", "6"))
	stand.y = rsp.origin.y + 0.2
	_place_player(stand, rsp.origin - stand)
	await _secs(0.5)
	if not _alive([p, rob]):
		return _gone_err("the level")
	var spawned = _level().get_node("SpawnedNodes")
	var known = {}
	for c in spawned.get_children():
		known[c] = true
	var h0: int = rob.health
	var bullets = []
	var bullet_track = null
	var b_p0 = Vector3.ZERO
	var b_t = 0
	var b_speed = null
	var hits_t = []
	var dead_t = -1.0
	var parts0 = {}
	var parts_max_up = 0.0
	var exploded = [false]
	var dbg = {}
	rob.exploded.connect(func(): exploded[0] = true)
	Input.action_press("aim")
	var t = 0.0
	var last_h: int = h0
	for f in int(9.0 * 60):
		if is_instance_valid(rob) and not rob.dead:
			_aim_at(rob.global_position + Vector3(0, 1.1, 0))
		if f == 36:
			Input.action_press("shoot")
		await get_tree().physics_frame
		if not _alive([p, spawned]):
			Input.action_release("shoot")
			Input.action_release("aim")
			return _gone_err("the level")
		t += DT
		for c in spawned.get_children():
			if not known.has(c):
				known[c] = true
				if str(c.scene_file_path).ends_with("bullet.tscn"):
					bullets.append(t)
					if bullet_track == null:
						bullet_track = c
						b_p0 = c.global_position
						dbg["target"] = _v(p.player_input.shoot_target)
						dbg["b0"] = _v(c.global_position)
						dbg["rob"] = _v(rob.global_position)
						var cc = _ray(c.global_position, p.player_input.shoot_target + (p.player_input.shoot_target - c.global_position).normalized() * 0.2, [p.get_rid()])
						dbg["collider"] = str(cc.get("collider").get_path()) if not cc.is_empty() else "none"
						var kc = c.move_and_collide(-c.global_basis.z * 4.0, true)
						dbg["kc"] = (str(kc.get_collider().get_path()) + " d=" + str(kc.get_travel().length())) if kc else "none"
						dbg["bdir"] = _v(-c.global_basis.z)
						dbg["cam_fwd"] = _v(-p.player_input.camera_camera.global_basis.z)
						b_t = 0
		if bullet_track != null and is_instance_valid(bullet_track) and b_speed == null:
			b_t += 1
			if b_t == 2:
				b_p0 = bullet_track.global_position
			if b_t == 8 and not bullet_track.hit:
				b_speed = snappedf(bullet_track.global_position.distance_to(b_p0) / (6 * DT), 0.1)
		if is_instance_valid(rob) and rob.health != last_h:
			last_h = rob.health
			hits_t.append(snappedf(t, 0.02))
			if rob.dead and dead_t < 0:
				dead_t = t
				for part in ["PartShield1", "PartShield2", "PartHead"]:
					parts0[part] = rob.get_node("Death/" + part).global_position
		if dead_t > 0 and is_instance_valid(rob):
			for part in parts0:
				var n = rob.get_node_or_null("Death/" + part)
				if n:
					parts_max_up = maxf(parts_max_up, n.global_position.y - parts0[part].y)
		if f == 36 + 180:
			Input.action_release("shoot")  # 3 s of fire
		if dead_t > 0 and t > dead_t + 2.5 and f > 36 + 180:
			break
	Input.action_release("shoot")
	Input.action_release("aim")
	var parts_rest = {}
	if is_instance_valid(rob):
		for part in parts0:
			var n = rob.get_node_or_null("Death/" + part)
			if n:
				parts_rest[part] = {"fell": snappedf(n.global_position.y - parts0[part].y, 0.1), "speed": snappedf(n.linear_velocity.length(), 0.1)}
	var fire_rate = null
	var shoot_bullets = bullets
	if shoot_bullets.size() >= 3:
		fire_rate = snappedf((shoot_bullets.size() - 1) / (shoot_bullets[-1] - shoot_bullets[0]), 0.01)
	# respawn: a new robot appears at spawn point 3 about 15 s after the explosion
	var respawn_s = null
	if dead_t > 0:
		var t1 = t
		for f in int(20.0 * 60):
			await get_tree().physics_frame
			t += DT
			var found = false
			for r in _robots():
				if r != rob and is_instance_valid(r) and r.global_position.distance_to(rsp.origin) < 3.0 and not r.dead:
					found = true
			if found:
				respawn_s = snappedf(t - dead_t, 0.1)
				break
	return {"bullets": shoot_bullets.size(), "fire_rate": fire_rate, "bullet_speed": b_speed, "hits": hits_t.size(),
		"health_start": h0, "killed": dead_t > 0, "exploded_signal": exploded[0],
		"hits_to_kill": (hits_t.size() if dead_t > 0 else null), "parts_max_up": snappedf(parts_max_up, 0.1), "parts": parts_rest,
		"robot_respawn_s": respawn_s, "_dbg": dbg}


func p_enemy():
	"""Robot 4 notices the player standing 9 m in front of it: approaches (turns/walks), aims, fires its laser (shoot
	animation), the laser hit shakes the player's camera; robots far from the player stay idle."""
	if await _reload_level() == null:
		return _no_level()
	var p = _player()
	var rsp: Transform3D = _level().get_node("RobotSpawnpoints").get_child(3).global_transform
	var rob = null
	for r in _robots():
		if r.global_position.distance_to(rsp.origin) < 3.0:
			rob = r
	if rob == null:
		return {"error": "robot 4 not found"}
	var far_robot = null
	for r in _robots():
		if r.global_position.distance_to(rsp.origin) > 30.0:
			far_robot = r
	var far_p0: Vector3 = far_robot.global_position if far_robot else Vector3.ZERO
	var rface: Vector3 = rsp.basis.z
	var stand: Vector3 = rsp.origin + Vector3(rface.x, 0, rface.z).normalized() * 9.0 + Vector3(0, 0.2, 0)
	var r0: Vector3 = rob.global_position
	var cam = p.player_input.camera_camera
	var first = {}
	var trauma_max = 0.0
	var shots = 0
	var was_shooting = false
	var t = 0.0
	var idle_before: int = rob.state
	_place_player(stand, rsp.origin - stand)
	for f in int(20.0 * 60):
		await get_tree().physics_frame
		if not _alive([p, rob, cam]):
			return _gone_err("the level")
		t += DT
		var s: int = rob.state
		var key = ["idle", "approach", "aim", "shooting"][s]
		if not first.has(key):
			first[key] = snappedf(t, 0.1)
		var sa: AnimationPlayer = rob.shoot_animation
		if sa.is_playing() and not was_shooting:
			shots += 1
		was_shooting = sa.is_playing()
		trauma_max = maxf(trauma_max, cam.trauma)
	var to_p: Vector3 = (p.global_position - rob.global_position)
	var face: Vector3 = rob.global_transform.basis.z
	var ang = rad_to_deg(Vector2(face.x, face.z).angle_to(Vector2(to_p.x, to_p.z)))
	return {"state_before": idle_before, "first_s": first, "laser_shots_20s": shots, "robot_moved": snappedf(rob.global_position.distance_to(r0), 0.1),
		"facing_err_deg": snappedf(absf(ang), 1.0), "camera_shake_max": snappedf(trauma_max, 0.1),
		"far_robot_state": (far_robot.state if far_robot else null),
		"far_robot_moved": (snappedf(far_robot.global_position.distance_to(far_p0), 0.1) if far_robot else null)}


func p_audio():
	"""Level music plays; jump / land / shoot / robot-hit sounds trigger and have their streams (dummy audio driver in headless)."""
	if await _reload_level() == null:
		return _no_level()
	var l = _level()
	var p = _player()
	var music: AudioStreamPlayer = l.get_node("Music")
	var r = {"music_stream": music.stream != null, "music_playing": music.playing, "music_bus": str(music.bus)}
	var sfx = {}
	for n in p.get_node("SoundEffects").get_children():
		sfx[n.name] = n.stream != null
	r["player_sfx_streams"] = sfx
	var rb = _robots()[0]
	var rs = {}
	for n in rb.get_node("SoundEffects").get_children():
		rs[n.name] = n.stream != null
	r["robot_sfx_streams"] = rs
	# trigger: jump then land, shoot
	_place_player(_spawn_xf(0).origin, -_spawn_xf(0).basis.z)
	await _secs(1.0)
	if not _alive([p, rb]):
		return _gone_err("the level")
	var heard = {}
	Input.action_press("jump")
	await get_tree().process_frame
	await get_tree().process_frame
	Input.action_release("jump")
	for f in 120:
		await get_tree().physics_frame
		for n in ["Jump", "Land"]:
			if p.sound_effects.get_node(n).playing:
				heard[n] = true
	await _secs(0.5)
	Input.action_press("aim")
	await _secs(0.6)
	Input.action_press("shoot")
	for f in 60:
		await get_tree().physics_frame
		if p.sound_effect_shoot.playing:
			heard["Shoot"] = true
	Input.action_release("shoot")
	Input.action_release("aim")
	if not _alive([p, rb]):
		return _gone_err("the level")
	var hs: AudioStreamPlayer3D = rb.hit_sound
	rb.hit()
	await get_tree().physics_frame
	heard["RobotHit"] = hs.playing
	r["triggered"] = heard
	return r


# ---------------------------------------------------------------- render mode (GPU window) ----------------------------------------------------------------
var shot_meta = {}


func _small(img: Image) -> Image:
	var c = img.duplicate()
	c.resize(160, 90, Image.INTERPOLATE_BILINEAR)
	c.convert(Image.FORMAT_L8)
	return c


func _img_diff(a: Image, b: Image) -> float:
	var da = a.get_data()
	var db = b.get_data()
	var s = 0.0
	for i in da.size():
		s += absf(da[i] - db[i])
	return s / (255.0 * da.size())


func _shot(name: String) -> String:
	"""Screenshot of the game viewport, taken only once frames flow again (60 frames in a row < 100 ms, max 60 s: a loaded,
	shared GPU/CPU stalls the GL renderer), then checked against a second grab 10 frames later (stable = same picture)."""
	var tw = Time.get_ticks_usec()
	var good = 0
	var lastw = tw
	while good < 60 and Time.get_ticks_usec() - tw < 60000000:
		await get_tree().process_frame
		var nw = Time.get_ticks_usec()
		good = good + 1 if nw - lastw < 100000 else 0
		lastw = nw
	var img = get_viewport().get_texture().get_image()
	for i in 10:
		await get_tree().process_frame
	var img2 = get_viewport().get_texture().get_image()
	var d = _img_diff(_small(img), _small(img2))
	shot_meta[name] = {"gate_s": snappedf((Time.get_ticks_usec() - tw) / 1000000.0, 0.1), "frames_flowing": good >= 60,
		"self_diff": snappedf(d, 0.0001), "stable": good >= 60 and d < 0.02}
	var path = _env("GT_PROBE_SHOTS", "user://") .path_join(name + ".png")
	img.save_png(path)
	return path


func p_menu():
	"""Main menu shows Play / Settings / Quit; Settings opens the settings panel, Cancel returns; Play loads the level."""
	var t0 = Time.get_ticks_msec()
	var menu = null
	while Time.get_ticks_msec() - t0 < 60000:
		if _host_gone() != "":
			return _gone_err("the main menu")
		var m = _main_node()
		if m and m.get_child_count() > 0 and m.get_child(0).has_method("_on_play_pressed"):
			menu = m.get_child(0)
			break
		await get_tree().process_frame
	if menu == null:
		return {"error": "main menu not shown (%s)" % _scene_summary()}
	await _secs(3.0)
	if not _alive([menu]):
		return _gone_err("the main menu")
	var r = {"buttons": {}}
	for b in ["Play", "PlayOnline", "Settings", "Quit"]:
		var n = menu.main.get_node_or_null(b)
		r["buttons"][b] = n != null and n.is_visible_in_tree()
	r["shot_main"] = await _shot("menu_main")
	if not _alive([menu]):
		return _gone_err("the main menu")
	menu._on_settings_pressed()
	await _secs(1.0)
	if not _alive([menu]):
		return _gone_err("the main menu")
	r["settings_open"] = menu.settings_menu.is_visible_in_tree() and not menu.main.is_visible_in_tree()
	var opts = 0
	for c in menu.settings_menu.get_children():
		if c is HBoxContainer and c.name != "Actions":
			opts += 1
	r["settings_rows"] = opts
	r["shot_settings"] = await _shot("menu_settings")
	if not _alive([menu]):
		return _gone_err("the main menu")
	menu._on_cancel_pressed()
	await _secs(0.5)
	if not _alive([menu]):
		return _gone_err("the main menu")
	r["settings_closed"] = menu.main.is_visible_in_tree() and not menu.settings_menu.is_visible_in_tree()
	var t1 = Time.get_ticks_msec()
	menu._on_play_pressed()
	var lvl = null
	while Time.get_ticks_msec() - t1 < 90000:
		await get_tree().process_frame
		var l = _level()
		if l and l.is_node_ready() and _player():
			lvl = l
			break
	if lvl == null and _host_gone() != "":
		return _gone_err("the main menu")
	r["play_loads_level"] = lvl != null
	r["load_s"] = snappedf((Time.get_ticks_msec() - t1) / 1000.0, 0.1)
	r["shot_meta"] = shot_meta.duplicate()
	return r


func p_hud():
	"""In-level view from a fixed spot: crosshair shows when aiming, screenshot of the third-person view (compared to vanilla)."""
	if _level() == null:
		await _wait_level(90.0)
	var p = _player()
	if p == null:
		return _no_level()
	_place_player(_spawn_xf(0).origin, -_spawn_xf(0).basis.z)
	await _secs(3.0)
	if not _alive([p]):
		return _gone_err("the player")
	var r = {"shot_view": await _shot("view_far")}
	Input.action_press("aim")
	await _secs(1.5)
	if not _alive([p]):
		Input.action_release("aim")
		return _gone_err("the player")
	r["crosshair_visible"] = p.crosshair.is_visible_in_tree() and p.crosshair.self_modulate.a > 0.5
	r["shot_aim"] = await _shot("view_aim")
	Input.action_release("aim")
	await _secs(1.0)
	r["shot_meta"] = {"view_far": shot_meta.get("view_far"), "view_aim": shot_meta.get("view_aim")}
	return r


func p_frametime():
	"""Median / p95 frame time over 8 s standing at spawn point 1 (vsync off, fixed window), and over 8 s at robot 3."""
	if _level() == null:
		await _wait_level(90.0)
	if _player() == null:
		return _no_level()
	DisplayServer.window_set_vsync_mode(DisplayServer.VSYNC_DISABLED)
	Engine.max_fps = 0
	var r = {}
	for spot in ["spawn", "robot3"]:
		if spot == "spawn":
			_place_player(_spawn_xf(0).origin, -_spawn_xf(0).basis.z)
		else:
			var rsp: Transform3D = _level().get_node("RobotSpawnpoints").get_child(2).global_transform
			var st: Vector3 = rsp.origin + rsp.basis.z * 7.0 + Vector3(0, 0.2, 0)
			_place_player(st, rsp.origin - st)
		# warm-up: wait (max 90 s) until 60 frames in a row took < 50 ms (first-use shader compiles stall the GL renderer)
		var tw = Time.get_ticks_usec()
		var good = 0
		var lastw = tw
		while good < 60 and Time.get_ticks_usec() - tw < 90000000:
			await get_tree().process_frame
			var nw = Time.get_ticks_usec()
			good = good + 1 if nw - lastw < 50000 else 0
			lastw = nw
		if _player() == null:
			return _gone_err("the level")
		var warm_s = snappedf((Time.get_ticks_usec() - tw) / 1000000.0, 0.1)
		var ft = []
		var last = Time.get_ticks_usec()
		var t0 = last
		while Time.get_ticks_usec() - t0 < 8000000 and ft.size() < 600:
			await get_tree().process_frame
			var now = Time.get_ticks_usec()
			ft.append((now - last) / 1000.0)
			last = now
		ft.sort()
		var s = 0.0
		for x in ft:
			s += x
		r[spot] = {"warmup_s": warm_s, "frames": ft.size(), "mean_ms": snappedf(s / ft.size(), 0.01), "median_ms": snappedf(ft[int(ft.size() * 0.5)], 0.01),
			"p95_ms": snappedf(ft[int(ft.size() * 0.95)], 0.01)}
	r["renderer"] = RenderingServer.get_current_rendering_driver_name() + " / " + RenderingServer.get_video_adapter_name()
	return r


# ---------------------------------------------------------------- online (second game process joins the host) ----------------------------------------------------------------
func p_online():
	"""A second instance of the game (headless client, started by this probe) joins this host over ENet on 127.0.0.1:4383
	(the demo's Play Online defaults): the host spawns a second player, the client gets the replicated level (players and
	robots), and the client's input moves its player on the host (server-authoritative movement)."""
	if await _reload_level() == null:
		return _no_level()
	var spawned = _level().get_node("SpawnedNodes")
	var cout = _env("GT_PROBE_OUT", "user://gt_probe.json") + ".client.json"
	if FileAccess.file_exists(cout):
		DirAccess.remove_absolute(cout)
	# client console goes to its own log (its failed auto-host prints expected errors that are not the host's)
	var cmd = "exec '%s' --headless --path '%s' -- --gt-client '%s' > '%s' 2>&1" % [OS.get_executable_path(),
		ProjectSettings.globalize_path("res://"), cout, cout.trim_suffix(".json") + ".log"]
	var pid = OS.create_process("/bin/sh", ["-c", cmd])
	if pid <= 0:
		return {"error": "could not start the client process"}
	var t0 = Time.get_ticks_msec()
	var other = null
	while Time.get_ticks_msec() - t0 < 90000 and other == null:
		await get_tree().process_frame
		if not _alive([spawned]):
			OS.kill(pid)
			return _gone_err("the level")
		for c in spawned.get_children():
			if c.get_script() and str(c.get_script().resource_path).ends_with("player.gd") and str(c.name) != "1":
				other = c
	var r = {"client_started": true, "joined": other != null}
	if other == null:
		OS.kill(pid)
		return r
	r["join_s"] = snappedf((Time.get_ticks_msec() - t0) / 1000.0, 0.1)
	var p0: Vector3 = other.global_position
	var maxd = 0.0
	while Time.get_ticks_msec() - t0 < 150000 and OS.is_process_running(pid):
		await get_tree().physics_frame
		if is_instance_valid(other):
			maxd = maxf(maxd, Vector3(other.global_position.x - p0.x, 0, other.global_position.z - p0.z).length())
	if OS.is_process_running(pid):
		OS.kill(pid)
	var n = 0
	for c in spawned.get_children():
		if c.get_script() and str(c.get_script().resource_path).ends_with("player.gd"):
			n += 1
	r["host_players_after_leave"] = n
	r["client_moved_on_host"] = snappedf(maxd, 0.1)
	r["client_walked"] = maxd > 1.0
	var f = FileAccess.open(cout, FileAccess.READ)
	r["client"] = JSON.parse_string(f.get_as_text()) if f else {"error": "no client output"}
	return r


func _client_main(cout: String) -> void:
	"""Client role: the headless menu auto-hosts first (its port is taken by the probing host, so that fails);
	then join 127.0.0.1:4383 like the menu's Connect button does and reload the level as a client."""
	var r = {}
	var lvl = await _wait_level(90.0)
	var peer = ENetMultiplayerPeer.new()
	var err = peer.create_client("127.0.0.1", 4383)
	r["create_client"] = err
	multiplayer.multiplayer_peer = peer
	var m = _main_node()
	m.change_scene_to_packed(load(LEVEL_PATH))
	var t0 = Time.get_ticks_msec()
	var me = null
	while Time.get_ticks_msec() - t0 < 60000:
		await get_tree().process_frame
		var l = _level()
		if l and l != lvl and peer.get_connection_status() == MultiplayerPeer.CONNECTION_CONNECTED:
			me = l.get_node_or_null("SpawnedNodes/" + str(multiplayer.get_unique_id()))
			if me:
				break
	r["connected"] = peer.get_connection_status() == MultiplayerPeer.CONNECTION_CONNECTED
	r["own_player"] = me != null
	if me:
		await _secs(2.0)
		var players = 0
		var robots = 0
		for c in _level().get_node("SpawnedNodes").get_children():
			var sp = str(c.get_script().resource_path) if c.get_script() else ""
			if sp.ends_with("player.gd"):
				players += 1
			elif sp.ends_with("red_robot.gd"):
				robots += 1
		r["players_seen"] = players
		r["robots_seen"] = robots
		r["camera_current"] = me.player_input.camera_camera.is_current()
		Input.action_press("move_forward")
		await _secs(2.0)
		Input.action_release("move_forward")
		await _secs(0.5)
	var f = FileAccess.open(cout, FileAccess.WRITE)
	f.store_string(JSON.stringify(r))
	f.close()
	multiplayer.multiplayer_peer.close()
	await _secs(0.5)
	get_tree().quit()
