-- state self-check test mod: sets variables (mod storage + player meta), spawns an entity, shows HUD text and a chat line
local S = core.get_mod_storage()
S:set_string("gab_quest_stage", "stage_two")

core.register_entity("gabtest:beacon", {
	initial_properties = {visual = "cube", textures = {"default_stone.png", "default_stone.png", "default_stone.png",
		"default_stone.png", "default_stone.png", "default_stone.png"}, hp_max = 7, physical = false, static_save = true},
	_gab_mark = "beacon_alpha",
	_gab_level = 3,
})

core.register_node("gabtest:marker", {description = "GAB marker", tiles = {"default_stone.png"}, groups = {cracky = 3}})

core.register_on_joinplayer(function(player)
	local name = player:get_player_name()
	player:get_meta():set_string("gab_flag", "visited_shrine")
	player:hud_add({type = "text", position = {x = 0.5, y = 0.2}, text = "GAB TEST HUD: find the beacon", number = 0xFFFF00,
		alignment = {x = 0, y = 0}, scale = {x = 100, y = 100}})
	core.after(3, function()
		local p = core.get_player_by_name(name); if not p then return end
		local q = p:get_pos()
		core.add_entity({x = q.x + 2, y = q.y + 1, z = q.z}, "gabtest:beacon")
		local np = {x = math.floor(q.x + 0.5) + 3, y = math.floor(q.y + 0.5) + 2, z = math.floor(q.z + 0.5)}
		core.set_node(np, {name = "gabtest:marker"})
		core.get_meta(np):set_string("gab_note", "marker_meta_ok")
		core.chat_send_player(name, "gabtest: the beacon is near")
	end)
end)
