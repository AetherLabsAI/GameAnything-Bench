# Tuxemon development kit (GameAnything-Bench)

Tuxemon 0.4.35 (an open-source monster-catching RPG in Python / pygame-ce, top-down tile maps) runs in a sandbox you control
with `game` (run `game help`). Window 1280x720; `game info` prints the paths (the stock game root, your instance's game root,
the user dir with saves, the log).

## What you hand in: `deliverable/mod/`
```
mod/                      files laid out like the game root; each file is copied over a fresh game install
  mods/tuxemon/           the game's data (the only data mod the game loads: put everything here)
    maps/<name>.tmx       Tiled maps (orthogonal, 16 px tiles; tilesets from gfx/tilesets/)
    maps/<name>.yaml      the map's events (actions + conditions, see below)
    db/<table>/<slug>.yaml  database entries: monster/, technique/, item/, npc/, economy/ (shop prices), encounter/ (wild
                          monsters per map), dialogue/, mission/, element/, status/ ...
    l18n/en_US/LC_MESSAGES/base.po   all English text (msgid -> msgstr); dialog actions use these keys
    gfx/  sprites/  animations/  music/  sounds/   art and audio (PNG / OGG)
  tuxemon/                optional: game code (Python), e.g. new event actions in tuxemon/event/actions/
```
(Several such directories one level down are also accepted; they are applied in name order.) A file with the path of a
stock file REPLACES it (copy the stock file first and edit it: `game sh 'cat <game_root_vanilla>/mods/tuxemon/maps/spyder_paper_town.yaml'`,
`game fetch <path> <dest>`); new files with new names never change the stock game. The evaluation copies your files over a
fresh install -- what a player does when dropping a mod into the game folder -- and starts the game at its title screen.
`game build` runs the same structure check and the load test (the game reaches its title screen, the database loads, and
every map in your mod loads).

## Building
- Events (`maps/<map>.yaml`): `events: {Name: {type: event, x, y, [width, height], conditions: [...], actions: [...]}}`.
  Conditions: `is char_at player`, `is variable_set key:value`, `not variable_set ...`, `is char_facing_tile player`, ...;
  actions (one line each, comma-separated args): `translated_dialog <base.po key>`,
  `translated_dialog_choice yes:no,<variable>`, `set_variable key:value`, `transition_teleport player,<map>.tmx,x,y,0.3`,
  `teleport player,<map>.tmx,x,y`, `add_monster <slug>,<level>`, `add_item <slug>[,qty]`, `wild_encounter <slug>,<level>`,
  `start_battle player,<npc slug>`, `create_npc <slug>,x,y`, `char_face`, `char_move`, `play_music`, `wait`, `set_money`, `open_shop`,
  `lock_controls` / `unlock_controls`, `screen_transition` ... (all of them: `tuxemon/event/actions/*.py` in the game root;
  conditions: `tuxemon/event/conditions/`). Stock maps' .yaml files are the best examples (spyder_*.yaml = the Spyder
  campaign: spyder_bedroom -> spyder_downstairs -> spyder_paper_town -> spyder_route1 ...).
- A new game of the Spyder campaign starts in spyder_bedroom.tmx; add an event / door / NPC on a stock map (replacing that
  map's .yaml, or its .tmx for new door objects) to lead a new player into your content, or change the start.
- Map objects in the .tmx (object layer): `collision`, `event` objects (doors: type `event` + properties `act1 =
  transition_teleport ...`, `cond1 = is char_at player`), NPC spawns are events with `create_npc`.
- Database entries: copy a stock YAML of the same table (`db/monster/rockitten.yaml`, `db/npc/spyder_billie.yaml`, ...) and
  give it a new slug; text names / descriptions are base.po keys.
- New art: PNG sprites / tilesets in `gfx/` and `sprites/` (16 px grid, the game scales by 5 at 1280x720).

## Testing
- `game start` / `game stop` / `game reset`: a fresh install with your mod, title screen on the screen ("Press Any Key" ->
  New Game -> campaign -> character -> dialogs).
- `game action 'LINE' ['LINE' ...]` (dev instance only): run event-action lines in the running game like a map event, e.g.
  `game action 'teleport player,my_map.tmx,5,5'`, `game action 'add_monster rockitten,10' 'wild_encounter nut,4'`,
  `game action 'set_variable my_flag:yes'`. Testing only: the evaluation has no such hook and players cannot do this.
- `game probe gate` (load test), `game probe maps` (every stock map still loads), `game probe new_game,walk,battle,...`:
  the evaluation's headless probes on a fresh copy with your mod (they play the stock Spyder campaign).
- `game shot [FILE]`, input (`game key Return`, `game hold Up 0.6`, `game key Escape`), `game state` (engine: screen = the game
  state on top, map, tile, facing, money, bag, party; mod: the game variables, NPCs and map events your mod added;
  screen_text: the dialog / menu on screen), `game log` (ERROR lines and Python tracebacks).
- `game sh 'COMMAND'`: a shell in the sandbox in your instance dir (game root: src/), `game fetch SANDBOX_PATH WORKDIR_PATH`.
- Keys: arrow keys walk (one tile per step; hold to keep walking), Return = A (talk / interact while facing, confirm),
  Escape = back / world menu (TUXEMON, BAG, PLAYER, SAVE, OPTIONS ...), shift = B. A key tap is sent as a short press.

## Traces (`deliverable/traces/*.yaml`)
```yaml
name: meet the lighthouse keeper
claims: ["a new game starts in the bedroom", "walking down to the harbour starts the keeper's dialog"]
steps:                                  # each trace starts from a fresh title screen
  - {do: key, keys: "Return"}                      # Press Any Key
  - {do: wait, seconds: 1.5}
  - {do: key, keys: "Return"}                      # New Game
  - {do: wait, seconds: 1.5}
  - {do: key, keys: "Return"}                      # TUXEMON: SPYDER AND THE CATHEDRAL
  - {do: wait, seconds: 2}
  - {do: key, keys: "Return"}                      # first character
  - {do: wait, seconds: 2}
  - {do: key, keys: "Down"}                        # a menu choice
  - {do: key, keys: "Return"}
  - {do: hold, keys: [Down], seconds: 1.2}         # walk down ~4 tiles
  - {do: key, keys: "Return"}                      # talk to whoever the player faces
  - {do: shot, name: keeper}                       # a screenshot the evaluation keeps
```
Only real player input: the evaluation replays your traces on a clean install and judges what its screenshots show.
Use `game state` while you write a trace to see which screen / tile each step reaches.
