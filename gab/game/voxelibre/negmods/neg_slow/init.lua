-- negative check: players walk at half speed (expected to fail move_jump only)
core.register_on_joinplayer(function(p) playerphysics.add_physics_factor(p, "speed", "neg_slow", 0.5) end)
