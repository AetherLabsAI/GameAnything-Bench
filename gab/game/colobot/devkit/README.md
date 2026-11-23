# Colobot development kit (GameAnything-Bench)

Colobot: Gold Edition 0.2.1-alpha (a real-time strategy game where you program robots in CBOT, a C++/Java-like language) runs
in a sandbox you control with `game` (run `game help`). Window 800x600; `game info` prints the paths (stock data dir, stock
levels and programs, your instance's save dir and log).

## What you hand in: `deliverable/mod/`
```
mod/
  levels/custom/<name>/chaptertitle.txt          a chapter of user levels: Title.E text="..." Resume.E text="..."
  levels/custom/<name>/level001/scene.txt        a level (terrain, objects, bots, goals): see the stock levels
  levels/custom/<name>/level001/help/...         optional: the SatCom instruction pages of the level
  ai/<program>.txt                               CBOT programs the level's bots run (script1="<program>.txt" run=1)
  models/ textures/ sounds/ music/ icons/ help/  optional: new or replaced art / sound (same path = replaces the stock file)
  start.yaml                                     optional: {scene: custom101} where a fresh start goes; default: menu
```
The layout is the game's data dir: a file with the same path as a stock file replaces it, a new path adds content. Several such
mods may also sit one level down (mod/<name>/levels/...). The evaluation copies your mod into a fresh install's save dir
(`mods/`) and enables it -- what the game's Mods screen does -- then starts the game with the start.yaml scene
(`-runscene`), else at the main menu (Missions, Free game, Exercises, Challenges, Code battles, Custom levels: your
levels/custom chapters are under Custom levels). `game build` runs the structure check and the load test (the start scene, else every
level of the mod, loads and keeps running for 14 s).

Scene names: `<category><chapter><level, 2 digits>`; custom chapters are numbered in name order, so the first level of your
only custom chapter is `custom101` (`game levels` lists yours). Do not use `levels/challenges/chapter009` (reserved).
A level that ends (all goals reached) closes a game started with a scene, as Colobot does for a single mission.

## Building
- Levels: `scene.txt` is plain text, one command per line. Copy a stock level and change it (`game sh 'ls <stock_levels>/missions/chapter001'`,
  `game sh 'cat <stock_levels>/missions/chapter001/level001/scene.txt'`). Key lines: `Title.E`, `Resume.E`, `#Include file=...`
  (stock planet / terrain), `TerrainRelief` / `TerrainResource` / `TerrainCreate`, `EnableResearch` / `DoneResearch` /
  `EnableBuild`, `CreateObject pos=x;y dir=d type=<Category> [script1="prog.txt" run=1] [power=1.0]` (the astronaut is
  `type=Me` and every level needs one), `EndMissionTake pos=x;y dist=d type=<Category> [min=n]` (goals), `Audio`, `Camera`.
- CBOT programs (`ai/*.txt`): `extern void object::Name() { ... }`; API: move, turn, goto, grab, drop, radar, radarall,
  build, factory, research, produce, jet, aim, fire, wait, message, abstime, position, energyCell, shieldLevel ...
  The stock programs (`<stock_programs>`) and the game's help (`help/` in the data dir) are the reference;
  https://colobot.info/ documents the language.
- A bot whose level line has `run=1` starts its program when the level starts; `message("text")` shows a HUD line.
- New art: models (.mod / .txt), textures (.png) under the same relative paths as the stock ones.

## Testing
- `game start` / `game stop` / `game reset`: a fresh start with your mod (the start.yaml scene, or the main menu).
- `game state` (live from the running game): screen (menu / level / won / lost), level file, the astronaut (position,
  shield, energy, what he carries), object counts; `mod`: the objects of your level (nearest 20), the running CBOT programs
  with their variables; `screen_text`: the recent message() texts. `game log`: the game log (level load errors, CBOT errors).
- `game levels`: your levels and their scene names. `game probe NAME`: a verified regression probe with your mod (several
  minutes; e.g. `game probe stock_levels` checks the stock levels still load).
- `game shot [FILE]`, input (`game click 400 300`, `game key Escape`, `game hold Up 2`), `game sh 'COMMAND'` (a shell in the
  sandbox), `game fetch SANDBOX_PATH WORKDIR_PATH`.
- Keys: arrows (or w a s d) move the selected bot / the astronaut, Shift / Ctrl jetpack up / down, Space camera, Tab next object,
  Home the astronaut, F1 instructions (SatCom), F2 program list, F4 camera visit, F5 quicksave, F9 quickload, p pause,
  Escape menu / close a window; mouse: left click selects an object or a button of the bottom panel (programs, actions).

## Traces (`deliverable/traces/*.yaml`)
```yaml
name: the grabber fetches the cube
claims: ["the level starts with the astronaut next to a grabber", "the grabber says hello and carries the titanium cube"]
steps:                                  # each trace starts from a fresh start (start.yaml scene)
  - {do: wait, seconds: 4}
  - {do: shot, name: start}                        # a screenshot the evaluation keeps
  - {do: key, keys: "Tab"}                         # select the next object (the grabber)
  - {do: wait, seconds: 8}
  - {do: hold, keys: [Up], seconds: 2}             # drive / walk forward
  - {do: shot, name: cube}
```
Only real player input: the evaluation replays your traces on a clean install and judges what its screenshots show.
