# Cities: Skylines development kit (GameAnything-Bench)

Cities: Skylines 1 (Linux, Unity 5.6, Mono with the .NET 3.5 class library) runs in a sandbox you control with `game` (run `game help`).
Window 1280x800. `game info` prints the paths: the game's assemblies (`managed_assemblies`: ICities.dll, Assembly-CSharp.dll,
ColossalManaged.dll, UnityEngine.dll -- what you compile against), your instance's Addons/Mods, Assets, Maps, Saves and Player.log.
Cities: Skylines is a commercial game: the benchmark user owns it in their Steam account; nothing of it is in your workspace, no
account is needed.

## What you hand in: `deliverable/mod/`
```
mod/
  <Name>/            a Cities mod as a C# project, built for you: start from template/mod/GabTemplate (<Name>.csproj, net35, C# 7.3,
                     compiled against the game's own assemblies; the build passes GameDir: keep that property). The folder name,
                     the .csproj name and the AssemblyName are the same. Or a folder the game loads itself: a built <Name>.dll, or
                     Source/*.cs that the game compiles at start (slower; its compiler errors land in Player.log).
                     Every mod needs a class implementing ICities.IUserMod (Name / Description) -- the game lists mods by it.
  assets/*.crp       optional: assets (buildings, props, ...) -> Addons/Assets
  maps/*.crp         optional: maps -> Addons/Maps
  saves/*.crp        optional: saves -> Saves (start one with start.yaml save:)
  start.yaml         optional (template/mod/start.yaml): mode: game | menu, map: <map name> (default Green Plains), city: <name>,
                     or save: <a save of saves/>
```
The evaluation builds every project (`dotnet build -c Release`), puts each mod folder into a fresh instance's Addons/Mods/<Name>/ and
switches it on (new mods start switched off in Cities; the evaluation does what a player does in Content Manager), then starts a new
city on the start map (or loads the save) without menus. Every fresh start returns to exactly that state. Mode menu: the main menu.

## Building
- .NET 3.5 / C# 7.3 (no async, no ValueTuple, no newer BCL APIs). No Harmony and no other libraries are installed: ICities extension
  points (IUserMod, LoadingExtensionBase, ThreadingExtensionBase, EconomyExtensionBase, ...) plus the game's own classes
  (Assembly-CSharp: SimulationManager, NetManager, BuildingManager, ZoneManager, TransportManager, EconomyManager, DistrictManager,
  PrefabCollection<T>, the *AI classes) and ColossalFramework.UI for your own panels and labels.
- Threads: simulation data (money, zoning, roads, buildings, vehicles, citizens) belongs to the simulation thread: change it inside
  `Singleton<SimulationManager>.instance.AddAction(() => { ... })`. UI and Unity objects on the main thread (OnLevelLoaded,
  ThreadingExtensionBase.OnUpdate).
- New content: change or clone prefabs after loading (PrefabCollection<BuildingInfo>.FindLoaded("..."), clone + InitializePrefabs),
  or ship assets (.crp, made in the game's Asset Editor) in assets/.
- `game build` = the evaluation's gate: the structure check, every project compiled (compiler errors are shown), then a clean instance
  enables your mods and loads the start city (a mod that did not load, exceptions, a crash are shown).

## Testing
- `game start` / `game reset` / `game stop`: build + install your mods into the dev instance and start fresh (a city load takes
  ~1-3 min).
- `game state`: live from the running game: status (menu / loading / in_game), city, map, date, paused, speed, money, population,
  happiness, demand, counts (buildings, road segments, vehicles, citizens), camera, current tool, time of day (hour, night, the
  day / night cycle), transport lines (type, stops, vehicles); `mod`: buildings / networks / props of prefabs no stock game has (yours),
  districts, static fields of your assemblies; `screen_text`: Chirper messages, the texts of your own UI components.
- `game shot [FILE]`, input (`game click 640 400`, `game drag x1 y1 x2 y2` = draw a road with the road tool, `game key space` =
  pause, `game key 1` / `2` / `3` = speed), `game log` (Player.log: your Debug.Log lines and exceptions), `game sh 'COMMAND'`.
- Keys: w a s d / arrows move the camera, q / e rotate, the mouse wheel zooms, space pauses, 1-3 set the speed, Escape opens the
  menu; the info views and the free camera are buttons of the game's UI (check the default bindings in Options > Keymapping).

## Traces (`deliverable/traces/*.yaml`)
```yaml
name: the bus line at night
claims: ["buses stop at the three stops and passengers board", "street lights come on at dusk"]
steps:                                  # each trace starts from a fresh start (in the start city)
  - {do: wait, seconds: 5}
  - {do: key, keys: "3"}                 # fastest speed
  - {do: wait, seconds: 30}
  - {do: shot, name: dusk}
```
