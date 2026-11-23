# GameAnything-Bench state bridge for Godot 4 games (generic part; docs/STATE_SPEC.md). Added to a game instance as an extra
# autoload (GABState) through override.cfg by the adapter; the game's files are not touched. Idle unless GAB_STATE_DIR is set.
# Protocol: the adapter writes <dir>/req (a nonce); within a few frames the bridge answers <dir>/state.json
# {nonce, engine, mod, screen_text} read from the running scene tree (no input, no pause, no game state written); every 2 s it
# also refreshes <dir>/last.json, which the adapter returns (engine.stale_s = its age) when the game cannot answer in time.
# engine = the game's _engine() / _ref_pos() (appended to this file by the adapter: <game>/state_engine.gd).
# mod = generic: nodes whose script / scene file the mod added (kind "added") or replaced ("changed"), nearest 20 to the player,
# with their script variables; autoloads the mod added, with their script variables; input actions the mod added; + the
# game's _mod_extra(). The mod's file list (modfiles.txt: A = added, C = replaces a vanilla file) comes from the adapter.
# screen_text = visible Label / Button / RichTextLabel / LineEdit / Label3D texts, in tree order.
extends Node

const OURS = ["GABState", "GTProbe", "ProbeBridge"]
var dir := ""
var mod_added := {}
var mod_changed := {}
var vanilla_autoloads := []
var vanilla_actions := []
var walked := 0
var last_snap := 0


func _ready() -> void:
	process_mode = Node.PROCESS_MODE_ALWAYS
	dir = OS.get_environment("GAB_STATE_DIR")
	if dir == "":
		set_process(false)
		return
	for l in _lines("modfiles.txt"):
		var f = l.substr(2)
		var p = "user://" + f.substr(5) if f.begins_with("user/") else "res://" + f
		if l.begins_with("A "):
			mod_added[p] = true
		elif l.begins_with("C "):
			mod_changed[p] = true
	vanilla_autoloads = _lines("vanilla_autoloads.txt")
	vanilla_actions = _lines("vanilla_actions.txt")


func _lines(name: String) -> Array:
	var p = dir.path_join(name)
	if not FileAccess.file_exists(p):
		return []
	return Array(FileAccess.get_file_as_string(p).split("\n", false))


func _process(_d) -> void:
	if Engine.get_process_frames() % 30 == 0:
		_watch()
	var rq = dir.path_join("req")
	if FileAccess.file_exists(rq):
		var nonce = FileAccess.get_file_as_string(rq).strip_edges()
		DirAccess.remove_absolute(rq)
		_snap(nonce, "state.json")
	elif Time.get_ticks_msec() - last_snap > 2000:  # a recent snapshot for a moment the game cannot answer (loading, GPU stall)
		_snap("", "last.json")


func _snap(nonce: String, name: String) -> void:
	last_snap = Time.get_ticks_msec()
	var s = {"nonce": nonce, "engine": _engine(), "mod": _mod(), "screen_text": _screen_text()}
	var out = dir.path_join(name)
	var f = FileAccess.open(out + ".tmp", FileAccess.WRITE)
	if f == null:
		return
	f.store_string(JSON.stringify(s))
	f.close()
	DirAccess.rename_absolute(out + ".tmp", out)


# ---------------------------------------------------------------- generic: mod ----------------------------------------------------------------
func _mod() -> Dictionary:
	var nodes = []
	walked = 0
	var ref = _ref_pos()
	_walk_mod(get_tree().root, nodes, ref)
	nodes.sort_custom(func(a, b): return a.get("dist", 1e9) < b.get("dist", 1e9))
	var al = {}
	for c in get_tree().root.get_children():
		var n = str(c.name)
		if n in OURS or n in vanilla_autoloads or not ProjectSettings.has_setting("autoload/" + n):
			continue
		al[n.left(60)] = _vars(c, 30)
	var acts = []
	if not vanilla_actions.is_empty():
		for a in InputMap.get_actions():
			if not str(a) in vanilla_actions and not str(a).begins_with("ui_"):
				acts.append(str(a).left(60))
	var r = {"nodes": nodes.slice(0, 20), "nodes_total": nodes.size(), "autoloads": al, "input_actions": acts.slice(0, 30)}
	r.merge(_mod_extra())
	return r


func _walk_mod(n: Node, acc: Array, ref) -> void:
	walked += 1
	if walked > 30000 or str(n.name) in OURS:
		return
	var sp = ""
	var s = n.get_script()
	if s != null:
		sp = str(s.resource_path)
	var fp = str(n.scene_file_path)
	var kind = ""
	if mod_added.has(sp) or mod_added.has(fp):
		kind = "added"
	elif mod_changed.has(sp) or mod_changed.has(fp):
		kind = "changed"
	if kind != "":
		var d = {"name": str(n.name).left(60), "kind": kind, "class": n.get_class(), "path": str(n.get_path()).right(120)}
		if sp != "":
			d["script"] = sp
		if fp != "":
			d["scene"] = fp
		if n is Node3D:
			d["pos"] = _v(n.global_position)
			d["visible"] = n.is_visible_in_tree()
			if ref is Vector3:
				d["dist"] = snappedf(n.global_position.distance_to(ref), 0.01)
		elif n is Node2D:
			d["pos"] = _v(n.global_position)
			d["visible"] = n.is_visible_in_tree()
			if ref is Vector2:
				d["dist"] = snappedf(n.global_position.distance_to(ref), 0.01)
		elif n is CanvasItem:
			d["visible"] = n.is_visible_in_tree()
		var v = _vars(n, 12)
		if not v.is_empty():
			d["vars"] = v
		acc.append(d)
	for c in n.get_children():
		_walk_mod(c, acc, ref)


func _vars(n: Object, cap: int) -> Dictionary:
	var out = {}
	for p in n.get_property_list():
		if out.size() >= cap:
			break
		if not (p["usage"] & PROPERTY_USAGE_SCRIPT_VARIABLE):
			continue
		var v = _plain(n.get(p["name"]))
		if v != null:
			out[str(p["name"]).left(60)] = v
	return out


func _plain(v, depth := 0):
	match typeof(v):
		TYPE_BOOL, TYPE_INT:
			return v
		TYPE_FLOAT:
			return snappedf(v, 0.001)
		TYPE_STRING, TYPE_STRING_NAME, TYPE_NODE_PATH:
			return str(v).left(200)
		TYPE_VECTOR2, TYPE_VECTOR2I, TYPE_VECTOR3, TYPE_VECTOR3I:
			return _v(v)
		TYPE_COLOR:
			return v.to_html()
		TYPE_ARRAY, TYPE_PACKED_INT32_ARRAY, TYPE_PACKED_FLOAT32_ARRAY, TYPE_PACKED_STRING_ARRAY:
			if depth > 1 or v.size() > 10:
				return {"size": v.size()}
			var a = []
			for x in v:
				a.append(_plain(x, depth + 1))
			return a
		TYPE_DICTIONARY:
			if depth > 1 or v.size() > 10:
				return {"size": v.size()}
			var d = {}
			for k in v:
				d[str(k).left(60)] = _plain(v[k], depth + 1)
			return d
	return null


func _v(v) -> Array:
	if v is Vector2 or v is Vector2i:
		return [snappedf(v.x, 0.01), snappedf(v.y, 0.01)]
	return [snappedf(v.x, 0.01), snappedf(v.y, 0.01), snappedf(v.z, 0.01)]


# ---------------------------------------------------------------- generic: screen text ----------------------------------------------------------------
func _screen_text() -> Array:
	var acc = []
	walked = 0
	_texts(get_tree().root, acc)
	return acc


func _texts(n: Node, acc: Array) -> void:
	walked += 1
	if acc.size() >= 40 or walked > 30000 or str(n.name) in OURS:
		return
	if (n is CanvasItem or n is CanvasLayer or n is Node3D) and not n.visible:
		return
	if n is CanvasItem and n.modulate.a < 0.05:
		return
	var t = ""
	if n is Label or n is Button or n is LineEdit or n is Label3D:
		t = str(n.text)
	elif n is RichTextLabel:
		t = n.get_parsed_text()
	t = str(TranslationServer.translate(t)).strip_edges() if t.length() < 200 else t.strip_edges()  # what the player reads: keys translated
	if t != "" and (acc.is_empty() or acc[-1] != t.left(200)):
		acc.append(t.left(200))
	for c in n.get_children():
		_texts(c, acc)


# ---------------------------------------------------------------- per game (appended) ----------------------------------------------------------------
# func _engine() -> Dictionary (fixed engine fields), func _ref_pos() (the player's position: Vector3 / Vector2 / null),
# func _watch() (every 30 frames: cheap bookkeeping, e.g. when the level started), func _mod_extra() -> Dictionary (game-specific
# mod content the engine holds, merged into `mod`)
