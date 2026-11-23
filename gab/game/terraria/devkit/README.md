# Terraria (tModLoader) development kit (GameAnything-Bench)

Terraria 1.4.4.9 with tModLoader 2026.08 (stable, .NET 8, FNA) runs in a sandbox you control with `game` (run `game help`).
Single player, window 1280x720. `game info` prints the paths: the tModLoader folder (with `tModLoader.xml`, the API documentation
of every tModLoader class and hook), your instance's save folder (Mods/, Worlds/, Players/), the game log.
Terraria is a commercial game: the benchmark user owns it and installed it with tModLoader in their Steam account; nothing of it is
in your workspace and you never need an account.

## What you hand in: `deliverable/mod/`
```
mod/
  <ModName>/                one folder per tModLoader mod; folder name = mod name = namespace = the Mod class name
    build.txt               displayName = ..., author = ..., version = 0.1, side = Both
    description.txt
    <ModName>.cs            the Mod class (+ any other classes; any folder layout)
    Items/MyItem.cs + Items/MyItem.png      every ModItem / ModNPC / ModProjectile / ModTile / ModWall / ModBuff needs its texture
                                            PNG at the class's path (or `public override string Texture => ...`)
    Localization/en-US_Mods.<ModName>.hjson display names / tooltips (else the key shows in game)
  <Other>.tmod              optional: a mod you built already
  worlds/<Name>.wld (+ .twld)   optional: your own world file (save one from the dev game: Worlds/ in `game info`)
  start.yaml                optional: how a fresh start begins (template/mod/start.yaml):
                            mode: game | menu, seed, size: small | medium | large, world: <Name> (one of worlds/), player
```
Mod names: letters, digits, `_`; not ModLoader / GABState / GAProbe. The evaluation builds every source folder with tModLoader's
own packer, puts the .tmod files into a fresh save folder's Mods/ and switches them on (what a player's Mod Browser / Mods menu does),
generates the start world WITH your mods loaded (seed / size from start.yaml; or loads your world file), creates the start player
like the character creation screen (your `AddStartingItems` hooks included), then starts the game straight into that world as that
player (mode game) or at the title menu (mode menu). Every fresh start is that same state.

## Building
- Start from `template/mod/GabTemplate` (a chat line on entering a world, a banner drawn every frame, a new sword with a texture and a
  recipe): copy it, rename the folder, namespace and Mod class together.
- tModLoader 2026.08 = the 1.4.4 API: `ModItem`, `ModNPC`, `ModProjectile`, `ModTile`, `ModSystem` (world hooks, `PostWorldGen`,
  `ModifyWorldGenTasks` for structures in the generated world, `ModifyInterfaceLayers` / `UpdateUI` for UI), `ModPlayer`
  (`OnEnterWorld`, `PreUpdate`, `ProcessTriggers`, `AddStartingItems`), `GlobalNPC` / `GlobalItem` (change vanilla things),
  `ModBiome`, `ModSceneEffect`. Read the API in `tModLoader.xml` (`game sh 'grep -n "ModSystem.PostWorldGen" -A6 <tmodloader>/tModLoader.xml'`)
  and https://github.com/tModLoader/tModLoader/wiki (ExampleMod shows every feature).
- `Main.NewText(text, r, g, b)` = a chat line; `Utils.DrawBorderString(Main.spriteBatch, ...)` inside a `LegacyGameInterfaceLayer`
  = text over the game; `Mod.Logger.Info(...)` = a line in the game log (`game log`).
- World changes at runtime (structures, NPCs) belong to the main thread: `ModSystem.PostUpdateWorld`, `OnWorldLoad`; tiles with
  `WorldGen.PlaceTile` / `Main.tile[x, y]`, then `WorldGen.SquareTileFrame`.
- `game build` = the evaluation's gate: the structure check, every mod compiled and packed by tModLoader (C# errors are shown), then
  a clean instance makes the start world with your mods and enters it (load errors, a disabled mod or a crash are shown).

## Testing
- `game start` / `game reset` / `game stop`: build + install your mods into the dev instance and start fresh (first start of a new
  mod set makes the world: ~1 min; later starts reuse it). `game worldgen`: make the start world again now.
- `game state`: live from the running game: screen, world, time, player (position in tiles, life, mana, inventory, buffs, biome),
  vanilla NPCs nearby, bosses; `mod`: what your mod added (its NPCs / projectiles / items / tiles / tile entities near the player,
  the fields of your ModSystem / ModPlayer classes and static fields, open UIs); `screen_text`: chat lines, NPC dialog, signs,
  combat texts, your UI texts and the text your interface layers draw.
- `game shot [FILE]`, input (`game key Escape`, `game hold d 1.5`, `game click 640 360`, `game click 640 360 3` = right click),
  `game log` (look for `ERROR` / `Exception` lines of your mod), `game sh 'COMMAND'` (a shell in the sandbox, in the instance folder),
  `game fetch SANDBOX_PATH WORKDIR_PATH` (e.g. a world you saved in the dev game -> deliverable/mod/worlds/).
- Keys: a / d walk, space jump, s drop through platforms, w / up climb, left click use the held item (attack / mine / place),
  right click interact (talk to an NPC, open a door / chest), Escape inventory (crafting is in the inventory's lower left), 1-0
  hotbar, h heal potion, m map, Enter chat. The game has no console; test from your own code and the state.

## Traces (`deliverable/traces/*.yaml`)
```yaml
name: the skate rail
claims: ["a skateboard is in the start inventory", "jumping onto the rail grinds along it"]
steps:                                  # each trace starts from a fresh start (in the world, as start.yaml says)
  - {do: wait, seconds: 2}
  - {do: key, keys: "2"}                # select hotbar slot 2
  - {do: hold, keys: [d], seconds: 2.5}  # ride right
  - {do: key, keys: space}
  - {do: shot, name: grind}             # a screenshot the evaluation keeps
```
Only real player input: the evaluation replays your traces on a clean install and judges what its screenshots and the state show.
