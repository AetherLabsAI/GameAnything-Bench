# Godot TPS Demo development kit (GameAnything-Bench)

The Godot third-person shooter demo (godotengine/tps-demo: a robot hero, a sci-fi hangar level, red enemy robots, Play Online
as LAN host / join) runs from its source project on Godot 4.7.2 in a sandbox you control with `game` (run `game help`).
Window 1280x720; `game info` prints the paths (the vanilla project, your instance's project, user dir, logs, the Godot binary).

## What you hand in: `deliverable/mod/`
```
mod/                   files laid over the Godot project (res://), same paths: a file replaces the stock one, a new path adds
  player/player.gd       e.g. a changed stock script
  level/level.tscn       e.g. a changed stock scene
  my_mod/...             your own scenes / scripts / resources / assets (any new folder)
  override.cfg           optional: project settings on top of project.godot, e.g. [autoload] MyMod="*res://my_mod/my_mod.gd"
  project.godot          optional: a full replacement (prefer override.cfg; keep config_version=5)
```
Several mods may also sit side by side as mod/<name>/ (each laid out as above; laid over in name order).
The evaluation copies the vanilla project, lays your files over it, imports it (`godot --headless --import`: new textures,
models, sounds are imported here) and starts the game windowed: a fresh start shows the main menu (Play / Play Online /
Settings / Quit) unless your mod changes the first scene. `.godot/` (the import cache) is reserved; do not ship `.import` files.
`game build` runs the structure check and the load test: every scene / script / resource of the project (yours included) must
load, compile and instantiate in a headless run, then the real game must come up.

## Building
- Godot 4 text formats: scenes `.tscn`, resources `.tres`, scripts `.gd` (GDScript 2), shaders `.gdshader`. Read the stock ones:
  `game sh 'cat <vanilla_project>/player/player.gd'`, `game sh 'ls <vanilla_project>/enemies/red_robot'`.
- The game: `main/main.gd` (scene switching: menu -> level), `menu/menu.tscn` + `menu.gd` (main menu, settings),
  `level/level.tscn` + `level.gd` (the hangar, spawn points, SpawnedNodes: players by peer id, robots, bullets),
  `player/player.gd` (movement, jump JUMP_SPEED, shooting, bullets, camera / aiming in `player_input.gd`),
  `enemies/red_robot/red_robot.gd` (health 5, state machine idle / approach / aim / shooting, laser),
  `menu/settings.gd` (the Settings autoload). Input actions: project.godot [input].
- New behaviour without touching stock files: an autoload in override.cfg that finds the level
  (`get_tree().root.get_node("main")`'s child with `SpawnedNodes`) and adds nodes / connects signals; or your own scene.
- Assets: glTF `.glb` / `.gltf` models, PNG / JPG / WebP textures, OGG / WAV sounds, fonts: put them in your folder; they are
  imported on install (no Blender in the sandbox: build geometry from Godot primitives (MeshInstance3D + BoxMesh / CSG*) or
  reuse stock models).
- Check a script fast: `game sh '<godot> --headless --path <instance_project> --check-only --script res://my_mod/x.gd'`
  (paths from `game info`; the instance project holds your mod after `game start`).
- Docs: https://docs.godotengine.org/en/stable/ (class reference, GDScript; the game runs Godot 4.7).

## Testing
- `game start` / `game stop` / `game reset`: a fresh install of your mod, main menu on the screen.
- `game state`: live game state: screen (menu / level), player position / heading / aiming, robots (health, AI state), and what
  your mod added (nodes with your scripts and their variables, autoloads, input actions), the texts on screen.
- `game shot [FILE]`, input (`game click 160 277` = Play, `game hold w 2`, `game key space`, `game look 200 0`,
  `game click 640 360 3` = aim), `game log` (Godot prints `SCRIPT ERROR` / `ERROR` lines with file and line).
- `game sh 'COMMAND'`: a shell in the sandbox, `game fetch SANDBOX_PATH WORKDIR_PATH` copies out.
- Keys (stock): w a s d / arrows move, mouse look, space jump, right mouse aim (hold), left mouse shoot (while aiming fires
  bullets), Escape back to the main menu, F3 debug overlay. Main menu buttons (1280x720): Play (160, 277), Play Online (160, 343),
  Settings (160, 410), Quit (160, 477).

## Traces (`deliverable/traces/*.yaml`)
```yaml
name: the new jump pad
claims: ["a glowing pad stands next to the spawn", "stepping on it throws the player onto the upper walkway"]
steps:                                  # each trace starts from a fresh start (main menu)
  - {do: click, x: 160, y: 277}                    # Play
  - {do: wait, seconds: 12}                        # the level loads
  - {do: look, dx: 300, dy: 0}                     # turn right
  - {do: hold, keys: [w], seconds: 2.5}            # walk onto the pad
  - {do: wait, seconds: 2}
  - {do: shot, name: on_the_walkway}               # a screenshot the evaluation keeps
```
Only real player input: the evaluation replays your traces on a clean install and judges what its screenshots show.
