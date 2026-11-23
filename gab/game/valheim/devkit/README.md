# Valheim (BepInEx) development kit (GameAnything-Bench)

Valheim (Linux, Unity, Mono) with BepInEx 5 (BepInExPack_Valheim 5.4.2351, HarmonyX) runs in a sandbox you control with `game`
(run `game help`). Window 1280x720. `game info` prints the paths: the game's assemblies (`managed_assemblies`: assembly_valheim.dll
and the Unity modules you compile against), BepInEx core, your instance's plugins folder, saves and logs.
Valheim is a commercial game: the benchmark user owns it in their Steam account; nothing of it is in your workspace, no account is needed.

## What you hand in: `deliverable/mod/`
```
mod/
  <Name>/                   a BepInEx plugin as a C# project (built for you), or a built plugin folder (DLLs + assets)
    <Name>.csproj           start from template/mod/GabTemplate (net462, compiled against the game's own Mono assemblies;
                            the build passes GameDir / BepInExDir: keep those properties)
    Plugin.cs               [BepInPlugin("you.name", "Name", "0.1.0")] class : BaseUnityPlugin (+ Harmony patches, MonoBehaviours)
    assets/                 files copied next to the DLL (textures, AssetBundles, data); find them from Path.GetDirectoryName(Info.Location)
  <Other>.dll               optional: a built plugin DLL
  worlds/<Name>.fwl + .db   optional: your own world (save it in the dev game: `game sh 'ls save/worlds_local'`, then `game fetch`)
  start.yaml                optional (template/mod/start.yaml): mode: game | menu, seed, world: <Name> (one of worlds/), player
```
The evaluation builds every project (`dotnet build -c Release`), puts each plugin folder into a fresh game's `BepInEx/plugins/<Name>/`
(what a mod manager does), starts the start world WITH your plugins loaded (a new world from start.yaml's seed, or your world) with
a new character at the start temple (vanilla start items), and every fresh start returns to exactly that state. Mode menu: the title
screen. No console and no devcommands for players.

## Building
- C# 9, net462 (the game's Mono: no newer BCL APIs, no async void tricks on Unity objects; Unity runs your code on the main thread in
  Awake / Update / Harmony patches). Harmony (`[HarmonyPatch(typeof(Player), nameof(Player.OnSpawned))]` + Prefix / Postfix) is how
  you change the game's behaviour; `Traverse` / `AccessTools` reach private fields.
- Useful game classes (assembly_valheim): Player (m_localPlayer, Message, m_customData = per-character data saved with it),
  Character / Humanoid / MonsterAI, ZNetScene (prefabs: GetPrefab, m_prefabs / m_namedPrefabs to register a new one), ObjectDB (items,
  recipes, status effects), ZoneSystem (SetGlobalKey, GetGroundHeight, locations), EnvMan (time of day, weather), MessageHud
  (ShowMessage), Hud, InventoryGui, Piece / PieceTable (build pieces), Ship, Minimap (pins), ZRoutedRpc (networked calls).
  Read their members: `game sh 'ls <managed_assemblies>'`; the community docs: https://valheim-modding.github.io/Jotunn/ (concepts;
  Jotunn itself is not installed), https://harmony.pardeike.net/.
- New things: clone a vanilla prefab at runtime (`Object.Instantiate` under an inactive holder, rename, add to ZNetScene) or load an
  AssetBundle from your assets/; register items in ObjectDB.m_items / recipes in ObjectDB.m_recipes after ObjectDB.Awake.
- Text over the game: MessageHud.instance.ShowMessage (centre / top-left; the state shows it) or your own Unity UI / OnGUI.
- `game build` = the evaluation's gate: the structure check, every project compiled (compiler errors are shown), then a clean
  instance loads your plugins and enters the start world (BepInEx errors, a plugin that did not load, a crash are shown).

## Testing
- `game start` / `game reset` / `game stop`: build + install your plugins into the dev instance and start fresh (the first start of a
  plugin set makes the start world: ~1-2 min; later starts restore it in seconds).
- `game console 'COMMAND'` (dev instance only; devcommands are on there): Valheim console commands, e.g. `game console 'spawn Boar 2'`,
  `game console 'goto 100 -50'`, `game console 'tod 0.8'`, `game console 'god'`. Testing only: players have no console.
- `game state`: live from the running game: screen, open menus, world, time of day / weather, player (position, health, stamina,
  biome, mounts / helm), inventory, equipment, skills, status effects, vanilla objects nearby; `mod`: objects with prefabs no vanilla
  game has (yours), non-vanilla items, global keys / player custom data your plugin set; `screen_text`: HUD messages, the hover text.
- `game shot [FILE]`, input (`game key Tab`, `game hold w shift 2`, `game look 200 0` = turn, `game click 640 360` = attack / use,
  `game click 640 360 3` = block), `game log` (BepInEx's LogOutput.log: your Logger lines and exceptions), `game sh 'COMMAND'`.
- Keys: w a s d move, shift run, space jump, ctrl crouch / sneak, e use / interact, Tab inventory, b build menu (hammer equipped),
  q / right click secondary, 1-8 hotbar, m map, F5 console (dev only), Escape menu.

## Traces (`deliverable/traces/*.yaml`)
```yaml
name: the downhill run
claims: ["the start gate shows the timer", "crossing the finish stops the clock and shows the rank"]
steps:                                  # each trace starts from a fresh start (in the start world)
  - {do: wait, seconds: 3}
  - {do: look, dx: 300, dy: 0}           # turn right
  - {do: hold, keys: [w, shift], seconds: 6}
  - {do: shot, name: finish}            # a screenshot the evaluation keeps
```
Only real player input: the evaluation replays your traces on a clean install and judges what its screenshots and the state show.
