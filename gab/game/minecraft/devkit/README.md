# Minecraft development kit (GameAnything-Bench)

Minecraft: Java Edition **26.3** with **Fabric Loader 0.19.5** and **Fabric API 0.162.0+26.3**, Java 25. The game runs in a
sandbox you control with `game` (run `game help`): an offline single-player world (player name `Player`, no account, no
network), window 1280x720. `game info` prints the paths (game files, the instance, its mods/ and game log, and decompiled game
sources when the sandbox has them). Minecraft 26.x ships **unobfuscated**: class and method names are Mojang's own names
(`net.minecraft.world.entity.player.Player`, `ServerLevel`, `Identifier`, ...). Read the real code instead of guessing APIs:
`game sh 'grep -rn "class ServerLevel" <decompiled_sources>'`, or list a class of the game jar with
`game sh 'javap -cp <client_jar> net.minecraft.world.item.Items | head'`.

## What you hand in: `deliverable/mod/`
```
mod/
  start.yaml             how a fresh start enters your experience (optional; see template/mod/start.yaml)
  <your_mod>/            a Fabric mod SOURCE project (built by the evaluation, see below)
    src/main/resources/fabric.mod.json     id, entrypoints, (mixins), depends
    src/main/resources/assets/<id>/...     textures, models, lang, sounds (sounds.json)
    src/main/resources/data/<id>/...       recipes, loot tables, advancements, worldgen, structures, functions
    src/main/java/...                      your code (optional client code: src/client/java + a "client" entrypoint)
  <name>.jar             or a built Fabric mod jar
  <pack>/                a datapack (pack.mcmeta + data/) or resource pack (pack.mcmeta + assets/); also datapacks/<pack>/,
                         resourcepacks/<pack>/ or .zip files
  saves/<world>/         a world you built (level.dat + region/ ...); `world: <world>` in start.yaml loads it at every start
  config/                files for the instance's config/ folder
```
Installed the way a player installs mods: jars into `mods/` (next to Fabric API), resource packs into `resourcepacks/` and
enabled, datapacks into the world's `datapacks/` folder, `config/` into the instance. **Every start is fresh**: a new world
from start.yaml (default: seed 2024, survival, normal difficulty, cheats off) with your datapacks, or a fresh copy of your
save. Nothing you do in the running game is kept unless it is in `mod/`.

**Building** (no Gradle, no network): `game build` compiles every source project with `javac --release 25` against the game,
the Fabric loader and its libraries (Mixin included) and Fabric API, packs the classes + resources + fabric.mod.json into a
jar, and test-loads it on a clean instance (the evaluation's gate: the game must reach the start world with every mod id of
yours loaded). Errors come back with the javac messages or the game log's error lines. `game build` closes your running game
first (`game start` again afterwards); `game start` builds too. Only Fabric API is available as a
dependency (`"depends": {"fabric-api": "*"}`); other libraries can be put as jars in `<your_mod>/libs/` for compiling, but they
are NOT shipped -- do not depend on them at run time. Mixins work (no refmap is needed: the game is unobfuscated); give them a
mixins json in resources and list it in fabric.mod.json. `"version": "${version}"` is replaced by 1.0.0.

Start from `template/mod/` (a Fabric project with a join message and a `/gabhello` command): copy it to `deliverable/mod/`,
rename the mod id, package and entrypoint.

## start.yaml
```yaml
world: my_map          # optional: load saves/my_map/ instead of creating a world (the save must be made in 26.3)
seed: 2024         # fresh world: seed
game_mode: survival    # survival | creative | adventure | spectator
difficulty: normal     # peaceful | easy | normal | hard
world_type: normal     # normal | flat | large_biomes | amplified | single_biome_surface
cheats: false          # allow the player commands in chat
structures: true       # generate villages etc.
```
A player in a fresh world spawns near the world spawn. If your experience lives elsewhere, either ship a save, build it
where the player spawns (a mod / datapack can place structures and teleport the player when they join), or say in ACCESS.md
how to get there with player input.

## Testing
- `game start` / `game stop` / `game reset`: install your current `mod/` (built) into the dev instance and start fresh.
- `game cmd 'COMMAND'`: a server command on the running world as an operator (`time set noon`, `give @p diamond 3`,
  `tp @p 10 80 10`, `locate structure village`, `function mypack:start`); the answer has the command's output. For testing
  only: the evaluation's player uses real input (no commands unless your start.yaml sets cheats: true and you tell them).
- `game query KIND ...`: read the world: `block x=1 y=64 z=2`, `blocks x1= y1= z1= x2= y2= z2=` (non-air blocks, <= 40000 cells),
  `entities type=minecraft:zombie r=32`, `container x= y= z=`, `spawn`, `perf`, `gamerules`, `time`.
- `game state`: structured state of the running game (position, health, inventory, screen, ...; your mod's entities,
  scoreboard, storage, boss bars, signs; on-screen text: titles, action bar, boss bars, sidebar, chat, open screen).
- `game shot [FILE]`, input (`game hold w 2`, `game key space`, `game look 300 0`, `game click 640 360 1`), `game log`
  (the game log: your mod's LOGGER lines, mixin / datapack errors show here).
- `game sh 'COMMAND'`: a shell in the instance dir (`saves/gab_world/` is the running world). To ship a world you built in
  the game: `game cmd 'save-all flush'`, `game stop`, `game sh 'cp -r saves/gab_world gab/export'`, then
  `game fetch <instance>/gab/export deliverable/mod/saves/my_map` and `world: my_map` in start.yaml (the next `game start`
  replaces saves/ with a fresh world).
- Keys: w a s d move, space jump, shift sneak, ctrl sprint, mouse look (`look DX DY`; ~0.15 degrees per pixel), left click
  (button 1) attack / break, right click (button 3) use / place, 1-9 hotbar, e inventory, q drop, t chat, / command, f5 camera,
  Escape menu. The mouse is captured in game: `look` turns the camera; `click X Y` clicks where the crosshair is.

## Traces (`deliverable/traces/*.yaml`)
```yaml
name: ring the bell
claims: ["the bell tower stands next to the spawn", "ringing it starts the countdown on the sidebar"]
steps:                                   # each trace starts from a fresh start (start.yaml)
  - {do: wait, seconds: 3}
  - {do: look, dx: 600, dy: 0}           # turn around
  - {do: hold, keys: [w], seconds: 2.5}  # walk forward
  - {do: click, x: 640, y: 360, button: 3}   # use the block under the crosshair
  - {do: shot, name: countdown}          # a screenshot the evaluation keeps
```
Only real player input: the evaluation replays your traces on a clean install and judges its screenshots and the game state.
