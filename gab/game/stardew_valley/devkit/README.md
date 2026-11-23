# Stardew Valley development kit (GameAnything-Bench)

Stardew Valley 1.6.15 with SMAPI 4.5.2 (the standard mod loader) runs in a sandbox you control with `game` (run `game help`).
Screen 1280x720, sound off. `game info` prints the paths (your instance's game folder and Mods, saves, the SMAPI log, the
decompiled game sources when this machine has them, the local NuGet feed).

A fresh start loads the benchmark's start save: a new farm (standard farm "Bench", farmer "Gab"), spring 1, year 1, 6:00 am,
the farmer in bed in the farmhouse with the starter tools -- what a new player sees after the intro. (Commercial game: the
benchmark operator's own copy; nothing of it is in your hand-in.)

## What you hand in: `deliverable/mod/`
```
mod/
  <ModName>/                one SMAPI mod per folder (several folders = several mods)
    manifest.json             Name, Author, Version, Description, UniqueID (unique, e.g. yourname.ModName),
                              EntryDll (= <AssemblyName>.dll), MinimumApiVersion "4.0.0"
    <ModName>.csproj          C# source, net6.0, PackageReference Pathoschild.Stardew.ModBuildConfig 4.3.0 (see the template)
    ModEntry.cs ...           your code (class ModEntry : Mod)
    assets/ i18n/ ...         files your mod loads (copied with the build)
  start.yaml                optional: {screen: title} starts at the title screen instead of the start save
```
A single mod may also sit directly in `mod/` (manifest.json at `mod/`). Templates: `devkit/template/mod/GabTemplate` (C# mod),
`devkit/template/cp/GabCPTemplate` (Content Patcher pack, below). C# mods and content packs can be mixed in one `mod/`.
The evaluation builds each .csproj in the sandbox (`dotnet build -c Release`, ModBuildConfig; the game and SMAPI are given
to it as GamePath), copies the built mod folders into a fresh game's `Mods/` -- what a player does with a downloaded mod --
and starts the game. `game build` runs exactly that: the build (compiler errors are shown), then a load test on a clean
instance (SMAPI loads every one of your mods, none skipped or failing on entry, the save comes up, the game still runs 10 s
later); a failure answers with the error lines from the build / the SMAPI log.

Rules of the build: offline. NuGet packages come only from the local feed (ModBuildConfig 4.3.0, Harmony 2.3.3 through
`<EnableHarmony>true</EnableHarmony>`, and what else is in it: `game info` -> nuget_feed); do not add package sources.
Folder names: letters, digits, `_ . -`; `ContentPatcher`, `GabBridge`, `ConsoleCommands`, `SaveBackup` are taken.

## Content Patcher packs
Content Patcher 2.9.0 (Pathoschild, MIT; the framework most Stardew content mods use) is installed in every game, next to SMAPI's
own mods and the harness mod. A content pack edits game data, images and maps with JSON only, no C#:
```
mod/<PackName>/
  manifest.json     Name, Author, Version, Description, UniqueID, "ContentPackFor": {"UniqueID": "Pathoschild.ContentPatcher"}
                    (no EntryDll)
  content.json      {"Format": "2.9.0", "Changes": [ {"Action": "EditData" | "Load" | "EditImage" | "EditMap" | "Include", ...} ]}
  assets/ ...       PNG / .tmx / JSON files your patches load (FromFile)
```
- Template: `devkit/template/cp/GabCPTemplate` (an item edit in `Data/Objects`, a replaced dialogue line in
  `Characters/Dialogue/Lewis`, a letter in `Data/Mail` sent by a `Data/TriggerActions` entry on the first morning).
- Docs: the author guide https://github.com/Pathoschild/StardewMods/blob/develop/ContentPatcher/docs/author-guide.md (also in
  the Content Patcher source; tokens such as `{{ModId}}`, conditions `When`, `EditData` with `Entries` / `Fields` /
  `TargetField`, custom locations, `Include` for splitting content.json). content.json may hold `//` comments and trailing commas.
- `game build` copies the pack (nothing to compile) and runs the load test: SMAPI loads it and Content Patcher's own error /
  warning lines in the SMAPI log fail it (a content.json it cannot parse, a patch it ignores, a patch it cannot apply); the
  answer shows those lines. Patches whose `When` conditions only match later (another day, location) are checked only when
  they apply: test them with `game console 'patch summary'` (lists every patch and why it is or is not applied),
  `game console 'patch export Data/Objects'` and `game log`.
- `game state` lists the pack under `mod.content_packs`; its edits show under `mod.data` as entries added / changed / removed
  against the vanilla game data (e.g. `Data/Objects` changed `24`, `Characters/Dialogue/Lewis` changed `Mon`, `Data/Mail` added `<id>_Welcome`; a trigger action that
  ran shows under `mod.flags` as `trigger:<id>`).
- C# and CP together: a C# mod can use Content Patcher's API (add `"Dependencies": [{"UniqueID": "Pathoschild.ContentPatcher"}]`),
  or simply ship a separate content pack folder next to it.

## Building a mod
- SMAPI docs: https://stardewvalleywiki.com/Modding:Modder_Guide (events, content API, input, data, Harmony).
- Game data and assets from C# (or as a Content Patcher pack, above) (`helper.Events.Content.AssetRequested`, `e.Edit(...)` / `e.LoadFrom(...)`): `Data/Objects`,
  `Data/Characters` (new villagers: + `Characters/<Name>` sprite, `Portraits/<Name>`, `Characters/Dialogue/<Name>`,
  `Characters/schedules/<Name>`), `Data/Events/<Location>` (cutscenes: event scripts with preconditions), `Data/Mail`,
  `Data/Shops`, `Data/Locations` + `Maps/<name>` (new locations; .tmx/.tbin maps loaded with `helper.ModContent.Load<Map>`),
  `Data/TriggerActions`, `Data/Quests`, `Strings/...`. Images: PNG in your mod folder, loaded with `helper.ModContent`.
- Game code: `Game1` (player, currentLocation, timeOfDay, addHUDMessage, drawDialogue, warpFarmer), `GameLocation`,
  `NPC`, `Farmer`, `Item`/`ItemRegistry`, `Event`. Read the decompiled sources when available (`game info`), e.g.
  `game sh 'grep -rn "public static void warpFarmer" <decompiled_sources>/Stardew\ Valley/StardewValley/Game1.cs'`.
- Store your mod's state in the engine: `Game1.player.modData["<your id>/key"]`, `location.modData`, mail flags
  (`Game1.player.mailReceived`), `helper.Data.WriteSaveData` -- they are saved with the game and visible in `game state`.
- Lead a new player to your experience from the start save (a letter, an event on entering a location, a villager, an item),
  and describe the way in `ACCESS.md`.

## Testing
- `game build` (build + load test, several minutes: give your shell command a timeout of at least 5 min).
- `game start` / `game stop` / `game reset`: a fresh copy of the start save with your mods, in game in about 15-30 s.
- `game state`: live from the running game: screen, location, tile, facing, health, stamina, money, date, time, weather,
  inventory, held item, skills, quests, friendship, villagers here, flags set since the load; `mod`: the mods SMAPI loaded,
  characters / objects / items / locations that are not vanilla, mod data, new flags / stats / quests, a running modded
  event, and game data entries your mod added or changed; `screen_text`: HUD messages, the dialogue line and its answers,
  letters, chat, and the other text drawn on screen.
- `game log [N]`: the SMAPI log (your `Monitor.Log` lines, errors with stack traces).
- `game console 'COMMAND'` (dev instance only): SMAPI console commands, e.g. `world_settime 1800`, `debug warp Town 50 70`,
  `player_add name "Parsnip" 5`, `world_setday 12`, `help`. Testing only: the evaluation never uses the console.
- `game shot [FILE]`, `game sh 'COMMAND'` (a shell in the sandbox, in your instance dir), `game fetch PATH DEST`.

## Player input (traces, `game key` / `hold` / `click`)
w a s d walk (hold), left click / c use the held tool, right click / x check / talk / open (right-click a villager to talk),
1-9 0 - = toolbar slots, e or Escape menu, f journal, m map, t chat, Escape closes menus, left click in menus. Keys are held
0.1 s so the game sees them. The screen is 1280x720 at zoom 1: a tile is 64 px; the camera follows the farmer (centred except
near the edges of a map).

## Traces (`deliverable/traces/*.yaml`)
```yaml
name: the lantern letter
claims: ["a letter about the lantern festival is in the mailbox on day 1", "reading it adds the quest"]
steps:                                  # each trace starts from a fresh start (start save, in bed, 6:00 am)
  - {do: hold, keys: [a], seconds: 0.4}            # step out of bed
  - {do: hold, keys: [s], seconds: 1.4}            # walk down to the door
  - {do: wait, seconds: 2}
  - {do: shot, name: outside}                      # a screenshot the evaluation keeps
  - {do: click, x: 700, y: 300, button: 3}         # right click: check / talk
  - {do: wait, seconds: 1.5}
  - {do: shot, name: letter}
```
Only real player input: the evaluation replays your traces on a clean install and judges what its screenshots and the
game state show.
