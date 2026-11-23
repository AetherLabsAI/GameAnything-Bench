-- runs when the world loads (world mod). Luanti Lua API: core.* (alias minetest.*); VoxeLibre items: mcl_core:stone, ...
core.register_on_joinplayer(function(player)
	core.chat_send_player(player:get_player_name(), "gab_template loaded")
end)
