# VoxeLibre development kit (GameAnything-Bench)

VoxeLibre 0.92.4 (a Minecraft-like game) on the Luanti 5.17.0 engine runs in a sandbox you control with `game` (run
`game help`). Singleplayer client, window 800x600; `game info` prints the paths.

## What you hand in: `deliverable/mod/`
```
mod/
  <modname>/          one or more Luanti mods: mod.conf (name = <modname>, depends = ...), init.lua, textures/, models/, sounds/ ...
  start.yaml          optional: {settings: {<minetest.conf key>: <value>}} for the fresh world
```
(a single mod may also sit directly in mod/ with its init.lua). Mod names: lowercase letters, digits, underscore.
The evaluation starts a fresh world (fixed seed, survival, damage on) with your mods as world mods -- the way a player
enables mods for a world -- and the player spawns in it. `game build` runs the same check and load test.

## Building
- Luanti Lua API: `core.*` (= `minetest.*`): nodes, items, entities, mapgen / `core.set_node`, `core.place_schematic`,
  `core.register_on_joinplayer`, `core.after`, formspecs, HUD, sounds, particles, sky / lighting (`player:set_sky`, `set_lighting`).
  Reference: https://api.luanti.org/ ; the engine's builtin Lua and VoxeLibre's own mods are in the sandbox (`game info`,
  `game sh 'ls <game dir>/mods'`) and are the best examples (mobs: mcl_mobs, weather: mcl_weather, ...).
- New art: PNG textures in `<mod>/textures/`, models (.obj / .b3d) in `<mod>/models/`.

## Testing
- `game start` / `game stop` / `game reset`: a fresh world with your mods on the screen.
- `game lua 'return P:get_pos()'` (or `game lua -f FILE.lua`): Lua on the running server (P = the player); for testing only,
  the player cannot do this.
- `game shot [FILE]`, input (`game hold w 2`, `game key space`, `game look 100 0`, `game click 400 300 1`), `game state`, `game log`.
- `game sh 'COMMAND'`: a shell in the sandbox (read game data, logs); `game fetch SANDBOX_PATH WORKDIR_PATH` copies out.
- Keys: w a s d move, space jump, shift sneak, e sprint (aux1), i inventory, t chat, Escape menu; mouse: look (`look DX DY`),
  left click dig / hit, right click place / use.

## Traces (`deliverable/traces/*.yaml`)
```yaml
name: ride to the ice wall
claims: ["the sleigh moves when the player presses w", "wolves attack at night"]
steps:                                  # each trace starts from a fresh world
  - {do: wait, seconds: 3}
  - {do: look, dx: 200, dy: 0}                     # turn right (mouse)
  - {do: hold, keys: [w], seconds: 2.5}            # walk forward
  - {do: key, keys: "space"}
  - {do: click, x: 400, y: 300, button: 3}         # right click = use / place
  - {do: shot, name: wolves}                       # a screenshot the evaluation keeps
```
Only real player input: the evaluation replays your traces on a clean install and judges what its screenshots show.
