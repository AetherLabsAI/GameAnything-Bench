# Flare development kit (GameAnything-Bench)

Flare 1.15 (an isometric action RPG) with its Empyrean campaign runs in a sandbox you control with `game` (run `game help`).
Window 1280x720; `game info` prints the paths (stock data, your instance's mods dir, saves, log).

## What you hand in: `deliverable/mod/`
```
mod/
  <modname>/          one or more Flare mods: settings.txt + data dirs mirroring the stock mods
    settings.txt        description=..., game=flare-game, version=1.0, requires=empyrean_campaign
    items/ powers/ enemies/ npcs/ loot/ quests/ maps/ tilesetdefs/ scripts/ cutscenes/ menus/ engine/ languages/
    images/ animations/ soundfx/ music/ ...
  mods.txt            optional: load order of your mods (one name per line; default: name order)
```
(a single mod may also sit directly in mod/ with its settings.txt). Mod names: letters, digits, `_ . -`; not a stock name
(fantasycore, empyrean_campaign, default, ...). The evaluation copies your mods into a fresh install's user mods dir and
enables them after `empyrean_campaign` -- what a player does with the Mods menu -- then starts the game at the title menu.
`game build` runs the same structure check and the load test (title menu + a level-1 save loaded into Arrival, no crash).

## Building
- Flare data is plain text. Mods lower in the load order win: a file with the same path replaces the stock one; a file that
  starts with `APPEND` adds to it (new `[item]` / `[power]` / `[enemy]` blocks; a block with a stock id overrides that id).
  Give new things new ids (items / powers from 9000 up) so you never change stock content by accident.
- The stock mods are the reference: `game sh 'ls <data_dir>/empyrean_campaign/maps'`, `game sh 'cat <data_dir>/fantasycore/items/items.txt'`.
  Format docs: https://flarerpg.org/ and https://github.com/flareteam/flare-engine/wiki (attribute reference).
- Maps: `maps/<name>.txt` (Tiled-compatible text maps: header, tilesets, layers, `[event]` / `[npc]` / `[enemy]` sections).
  Map events (`[event] type=event location=x,y,w,h activate=on_trigger intermap=maps/x.txt,10,10 ...`) connect maps,
  give rewards, set statuses, spawn enemies, show messages / cutscenes.
- A new game starts on `maps/spawn.txt` (empyrean: an event that sends the hero on to the backstory / Arrival): change it,
  or add an exit / event on a stock map, to lead a new player into your content.
- NPCs (`npcs/x.txt`: portrait, talker, `[dialog]` topics with requires_status / set_status / reward_*), quests
  (`quests/x.txt`: quest log entries keyed by statuses), cutscenes (`cutscenes/x.txt`: images + captions).
- New art: PNG in `images/` (icons, portraits, tiles), sprite sheets + `animations/*.txt`.

## Testing
- `game start` / `game stop` / `game reset`: a fresh install with your mods, title menu on the screen (new game: Play Game,
  pick a class, Create; the backstory cutscene skips with Escape).
- `game console 'EVENT'` (dev instance only; the developer console, F5): runs event components in the running game, e.g.
  `game console 'intermap=maps/my_map.txt,10,12'`, `game console 'reward_xp=500'`, `game console 'set_status=my_flag'`,
  `game console 'spawn=zombie_lvl2,22,20'`. Testing only: the evaluation has the console off and players cannot use it.
- `game shot [FILE]`, input (`game click 830 498`, `game key i`, `game hold w 1.5`), `game state` (live from the running game:
  screen, map, position, HP/MP, level/xp, gold, inventory, campaign statuses, open menus / dialog; `mod`: statuses, NPCs,
  creatures and map events your mod added; `screen_text`: HUD messages, dialog line, quest log, cutscene caption), `game log` (Flare prints `ERROR:` lines for
  bad data, missing files, unknown ids).
- `game sh 'COMMAND'`: a shell in the sandbox, `game fetch SANDBOX_PATH WORKDIR_PATH` copies out.
- Keys: left click move / attack / talk / pick up, right click (and 1-0) powers from the action bar, w a s d move
  (keyboard movement), q / e health / mana potion, i inventory, c character, p powers, l quest log, m map, Escape menu / back,
  ctrl+click buy / sell at a vendor.

## Traces (`deliverable/traces/*.yaml`)
```yaml
name: into the flooded crypt
claims: ["a new game starts in the harbour with the ferryman", "talking to him opens the crypt door"]
steps:                                  # each trace starts from a fresh title menu
  - {do: click, x: 830, y: 498}                    # Play Game
  - {do: wait, seconds: 2.5}
  - {do: click, x: 757, y: 168}                    # pick the first class
  - {do: click, x: 350, y: 690}                    # Create
  - {do: wait, seconds: 4}
  - {do: key, keys: "Escape"}                      # skip the backstory cutscene (repeat if your mod adds pages)
  - {do: wait, seconds: 4}
  - {do: hold, keys: [s, d], seconds: 1.5}         # walk south-east
  - {do: click, x: 640, y: 170}                    # talk to the NPC above the hero
  - {do: shot, name: ferryman}                     # a screenshot the evaluation keeps
```
Only real player input: the evaluation replays your traces on a clean install and judges what its screenshots show.
