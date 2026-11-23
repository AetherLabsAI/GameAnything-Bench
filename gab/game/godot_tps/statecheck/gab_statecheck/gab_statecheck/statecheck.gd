extends Node
# gab selfcheck state test: a variable, an entity, a HUD text

var gab_marker := 4242
var gab_note := "state check"


func _ready() -> void:
	var b = Node3D.new()
	b.set_script(load("res://gab_statecheck/beacon.gd"))
	b.name = "GabBeacon"
	var hud = CanvasLayer.new()
	var l = Label.new()
	l.text = "GAB STATE CHECK"
	l.position = Vector2(500, 20)
	hud.add_child(l)
	get_tree().root.add_child.call_deferred(b)
	get_tree().root.add_child.call_deferred(hud)
