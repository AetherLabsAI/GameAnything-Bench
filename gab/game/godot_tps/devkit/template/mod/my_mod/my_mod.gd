extends Node
# template: an autoload that waits for the level and adds a HUD line (replace with your mod)

var level: Node = null


func _process(_delta: float) -> void:
	var main = get_tree().root.get_node_or_null("main")
	if main == null or main.get_child_count() == 0:
		return
	var top = main.get_child(main.get_child_count() - 1)
	if top.has_node("SpawnedNodes") and top != level:
		level = top
		var hud = CanvasLayer.new()
		var label = Label.new()
		label.text = "my mod is loaded"
		label.position = Vector2(20, 680)
		hud.add_child(label)
		level.add_child(hud)
