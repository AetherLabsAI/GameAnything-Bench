# Celeste 64 development kit (GameAnything-Bench)

Celeste 64: Fragments of the Mountain v1.1.1 (a small open-source 3D platformer by the Celeste team; C# on the Foster
framework, .NET 8, maps in the Quake / TrenchBroom `.map` format) runs in a sandbox you control with `game` (run `game help`).
Screen 1280x720 (the game runs fullscreen); `game info` prints the paths: the stock source (`stock_source`), the stock
content (`stock_content`), your running copy, the save file dir and the logs.

## What you hand in: `deliverable/mod/`
```
mod/
  Content/      files laid over the stock Content/ folder (same relative paths): a file with a stock path REPLACES it,
                new files are added
    Maps/<name>.map      levels (TrenchBroom / Quake brush format, "Generic" game; entity classnames below)
    Levels.json          the level list of the level-select screen (ID, Name, Label, Strawberries, Preview, Map)
    Text/English.json    all text: dialog lines (Granny1, Theo1, Baddy1, signpost keys ...) and UI strings
    Models/ Textures/ Sprites/ Faces/ Audio/ ...   art (glTF .glb models, PNG textures / sprites)
  Source/       C# files laid over the stock Source/ folder: new .cs files, or a stock file replaced by your version
                (copy it first: `game sh 'cat <stock_source>/Actors/Player.cs'`, `game fetch <path> <dest>`)
```
(Several such directories one level down are also accepted; they are applied in name order.) With a `Source/` folder the
game is rebuilt from the stock source + your files (`dotnet build`, 30-60 s; cached while your files do not change). The
evaluation lays your files over a fresh copy of the game -- what a player does when installing a mod -- and starts it at its
title screen. `game build` runs the same structure check, the C# build and the load test (the content loads, title -> level
select -> the first level with the Confirm key, every map incl. yours loads and the player spawns alive).

## Building
- Maps: the stock maps (`stock_content/Maps/1.map` = Forsaken City, `1-1` .. `1-10` = the cassette rooms) are the best
  examples; map units are 5x world units, z is up. Entity classnames (Source/Data/Map.cs `ActorFactories`): PlayerSpawn
  (`name` = a checkpoint name), Checkpoint, Strawberry, Refill (`double`), Cassette (`map`), Coin, Feather, Spring,
  MovingBlock / GateBlock / TrafficBlock / FallingBlock / FloatyBlock / BreakBlock / CassetteBlock (brush entities),
  DeathBlock, SpikeBlock, Granny, Badeline, Theo, SignPost (`dialog` = a key in Text/English.json), StaticProp (`model`),
  EndingArea, Fog, FixedCamera, IntroCar ... Textures by name from Content/Textures.
- A new level appears on the level-select screen when it is in Levels.json (Preview = a texture under Textures/, e.g.
  "Postcards/ForsakenCity"); the first entry is the first card.
- Code: a new actor type = a class deriving from `Actor` (Added / Update / LateUpdate; interfaces IHaveModels, IHaveSprites,
  IHaveUI for HUD drawing with `UI.Text`, IPickup, ...), registered for maps with a module initializer that adds it to
  `Map.ActorFactories` (see devkit/template/mod/Source/). Game rules live in Source/Actors/Player.cs (movement constants,
  states), Source/Scenes/World.cs (level flow, pause menu, HUD), Source/Data/Save.cs (save records: strawberries, flags
  `Save.CurrentRecord.SetFlag(...)`, deaths). Dialog: `Loc.Lines("Key")` and Cutscene `Say`.

## Testing
- `game start` / `game stop` / `game reset`: a fresh copy with your mod on the screen (title screen).
- `game shot [FILE]`, input (`game key c`, `game hold Right 1.5`, `game hold Up c 0.5`), `game state` (screen, map, level,
  player position / state / dashes, save record, the pause menu or level select, actor counts; actors, flags and statics
  your mod added; the text on screen), `game log` (exceptions; the build log path is in `game info`).
- `game sh 'COMMAND'`: a shell in the sandbox in your instance dir (stock source and content are readable there),
  `game fetch SANDBOX_PATH WORKDIR_PATH`.
- Keys: arrow keys move (Up = forward, away from the camera), c = jump / confirm, x = dash / cancel (talk to NPCs with x),
  z or shift = climb (hold at a wall), a / d = turn the camera, w / s = camera up / down, Escape or Return = pause menu
  (Resume, Retry, Options, Save & Quit). A key tap is sent as a short press (0.1 s).

## Traces (`deliverable/traces/*.yaml`)
```yaml
name: reach the summit shrine
claims: ["the new level is the first card", "a dash across the gap reaches the shrine and its message shows"]
steps:                                  # each trace starts from a fresh title screen
  - {do: wait, seconds: 2}
  - {do: key, keys: "c"}                           # title -> level select
  - {do: wait, seconds: 3}
  - {do: key, keys: "c"}                           # open the level card
  - {do: wait, seconds: 2}
  - {do: key, keys: "c"}                           # start the level
  - {do: wait, seconds: 6}
  - {do: hold, keys: [Up], seconds: 1.5}           # run forward
  - {do: key, keys: "c"}                           # jump
  - {do: key, keys: "x"}                           # dash
  - {do: shot, name: shrine}                       # a screenshot the evaluation keeps
```
Only real player input: the evaluation replays your traces on a clean install and judges what its screenshots show.
Use `game state` while you write a trace to see which screen / position each step reaches.
