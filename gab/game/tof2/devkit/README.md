# Tanks of Freedom II development kit (GameAnything-Bench)

Tanks of Freedom II 1.0.9 (a turn-based voxel tactics game: skirmish maps, story campaigns with scripted missions, a map editor)
runs from its source project on Godot 4.4.1 in a sandbox you control with `game` (run `game help`). Window 1280x720; `game info`
prints the paths (the vanilla project, its stock maps and campaigns, your instance's project and user dir, the log).

## What you hand in: `deliverable/mod/`
```
mod/                          files laid over the Godot project (res://), same paths: a file replaces the stock one, a new path adds
  assets/maps/skirmish/<name>.tofmap.json        a skirmish map (listed in Skirmish -> Stock maps, sorted by name)
  campaign/<name>/campaign.json                  a new campaign (listed after the stock ones): name (= the folder), title, icon,
  campaign/<name>/maps/*.tofmap.json               missions [{name, title, map, description, marker [x, y] on the campaign map
                                                   picture, players [{side, ap, type: human | ai}]}]; optional "map": a PNG in the folder
  assets/campaigns/<stock>/...                   changed stock campaigns (core_prologue, tutorial ...; a NEW folder here is not
                                                   listed unless scripts/services/campaign.gd registers it)
  scenes/... scripts/... assets/...               changed or new game scenes / scripts / art (.tscn, .gd, .png, .ogg ...)
  override.cfg                                    optional: project settings, e.g. [autoload] MyMod="*res://my_mod/my_mod.gd"
  user/...                                        optional: goes to the player's user dir (user://): custom maps
                                                   <name>.tofmap.json are listed under Skirmish -> Custom only with the
                                                   editor's list user/known_maps_list.json; a stock-list map needs no list
```
Several mods may also sit side by side as mod/<name>/ (each laid out as above; laid over in name order).
The evaluation copies the vanilla project, lays your files over it (re-imports when you bring new binary assets), puts user/
into a fresh player dir and starts the game: a fresh start shows the main menu (Campaign, Skirmish, Multiplayer, Load game,
Online, Editor, Settings). `game build` runs the structure check (JSON parses, maps have tiles, campaigns have missions) and the
load test: every skirmish map starts, every campaign mission starts and runs its turn-1 story scripts, the menus list them, no
script error stops the game; then the real game comes up.

## Building
- Maps are JSON: `metadata` {name (= the file name without .tofmap.json), ...}, `tiles` {"x_y": {ground, frame, decoration,
  terrain, building {tile, side, rotation}, unit {tile, side, rotation, stats ...}, damage}}, `scripts` {triggers, stories}.
  Read stock maps: `game sh 'cat <skirmish_maps>/2p_crossroad.tofmap.json'`, campaign maps under `<campaigns>/core_prologue/maps/`.
  Sides: blue, red, yellow, green (+ neutral buildings); tile / unit names: `scenes/map/templates.gd` in the vanilla project.
- The easiest way to draw a map: the in-game Editor (main menu -> Editor; place ground, buildings, units, save it under a
  name): it lands in the user dir (`game info` -> user_dir, `<name>.tofmap.json`); `game fetch` it and put it in mod/ as
  assets/maps/skirmish/<name>.tofmap.json (listed automatically) or as a campaign mission; set metadata.name to the file name.
- Story scripts (campaign and skirmish maps): `triggers` {"<name>": {type, details, story, one_off}} fire a `story` of
  `stories` {"<name>": [steps]}. Trigger types: turn, move, claim, deploy, decimate, assassination, attacked, building_lost,
  ability, resources (`scenes/board/logic/scripting/triggers/*.gd`). Story steps {action, details}: message (name, portrait,
  side, text), camera (where, zoom), lock / unlock (HUD), spawn, despawn, move, attack, claim, die, ap, objective, trigger
  (suspend / resume a trigger), end_game (winner), terrain_add / terrain_remove, level_up, ... (`.../outcomes/*.gd`). The stock
  campaigns (`core_prologue`, `tutorial`) show every pattern. A map with triggers replaces the default win rule (capture the
  enemy HQ) by its own: add an `end_game` story where the mission is won or lost.
- Text: story texts may be plain text; stock texts are translation keys (assets/translations).
- Game code: `scenes/board/board.gd` (turns, battle, selection), `scenes/board/logic/` (state, AI, scripting), unit scenes
  `scenes/tiles/units/templates/*.tscn` (hp, attack, armor, move, abilities), buildings `scenes/tiles/buildings/`.

## Testing
- `game start` / `game stop` / `game reset`: a fresh install of your mod, main menu on the screen.
- `game state`: live game state: screen (main_menu + panel / board / map_editor), on the board the map, campaign / mission, turn,
  players (side, AP, units, buildings), the current player, the cursor and selected tile, units near the cursor, the story
  dialog text, the map's triggers (fired one-off triggers are removed or suspended), and what your mod added (maps, campaigns,
  nodes with your scripts); the texts on screen.
- `game shot [FILE]`, input (`game click 640 340` = Skirmish on the main menu, `game key c`, `game drag ...`), `game log`
  (Godot prints `SCRIPT ERROR` / `ERROR` lines with file and line; "Invalid trigger" for a broken trigger).
- `game sh 'COMMAND'`: a shell in the sandbox, `game fetch SANDBOX_PATH WORKDIR_PATH` copies out.
- Controls (stock): left click select a tile / unit, click a marked tile to move / attack / capture, right click (or x) context
  menu (abilities, production in a building), c end turn, f switch camera style, q / e rotate, mouse wheel zoom, middle drag
  pan the camera, move the mouse to the screen edge to pan, Escape menu, r undo the last move. Main menu buttons (1280x720):
  Campaign (640, 295), Skirmish (640, 340), Multiplayer (640, 385), Load game (640, 430), Online (640, 475), Editor (640, 520),
  Settings (640, 565). Story messages close with a click.

## Traces (`deliverable/traces/*.yaml`)
```yaml
name: the bridge ambush
claims: ["the new campaign is listed", "its first mission opens with the colonel's briefing", "crossing the bridge springs the ambush"]
steps:                                  # each trace starts from a fresh start (main menu)
  - {do: click, x: 640, y: 295}                    # Campaign
  - {do: wait, seconds: 2}
  - {do: shot, name: campaigns}                    # a screenshot the evaluation keeps
  - {do: click, x: 400, y: 300}                    # your campaign's tile (look at the screenshot for the position)
  - {do: wait, seconds: 8}
  - {do: click, x: 640, y: 600}                    # close the briefing
  - {do: shot, name: briefing}
```
Only real player input: the evaluation replays your traces on a clean install and judges what its screenshots show.
