# Xonotic development kit (GameAnything-Bench)

Xonotic 0.8.6 (an arena first-person shooter on the DarkPlaces engine) runs in a sandbox you control with `game` (run
`game help`). Screen 1280x720; `game info` prints the paths (stock data, the instance's userdir, the game log).

## What you hand in: `deliverable/mod/`
```
mod/
  start.yaml          how a fresh start enters your experience (see template/mod/start.yaml): mode game | menu, map, gametype, bots
  autoexec.cfg        console script run at every start (cvars, aliases, binds); other *.cfg files you exec from it
  zz-<name>.pk3       content packs (zip files) -- or the same unpacked as a directory zz-<name>.pk3dir/
```
Everything except start.yaml is copied into the userdir's `data/` folder -- what a player does to install a mod -- and loads
on top of the stock data (later file names win: start pack names with `zz-`). The evaluation starts a fresh userdir with
your files and enters the match of start.yaml (default: deathmatch on stormkeep, no bots). `game build` runs the same check
and load test (a server with your mod on the start map, then the real client); `game check` checks the whole hand-in.

## What a mod can change (no compiler in the sandbox: the gameplay code, progs.dat / csprogs.dat, stays stock)
- **Settings** (cfg): physics (`sv_gravity`, `sv_maxspeed`, `sv_jumpvelocity` ...), weapon balance (`g_balance_<weapon>_*`;
  `sv_cmd dumpweapons` lists all), items, game rules (`g_*`, `fraglimit`, `timelimit`), bots (`bot_number`, `skill`), HUD
  (`hud_panel_*`), effects. Stock defaults: `game sh 'unzip -p <stock data>/xonotic-20230620-data.pk3 balance-xonotic.cfg'`.
- **Content** (pk3 / pk3dir, same paths as the stock packs): textures (`textures/`), models (`models/`, .md3 / .iqm / .obj),
  sounds (`sound/`, .ogg / .wav), HUD and menu graphics (`gfx/`), particle effects (`effectinfo.txt`), shaders (`scripts/*.shader`),
  map info (`maps/<map>.mapinfo`), and entity overrides of a map: `maps/<map>.ent` replaces the stock map's entity list
  (items, weapons, spawn points, jump pads, lights) -- dump one with `game sh` (`unzip -p .../xonotic-20230620-maps.pk3
  maps/stormkeep.bsp > /tmp/s.bsp`) or write it by hand. New level geometry (.bsp) needs a map compiler, which the sandbox lacks.
- Reference: https://gitlab.com/xonotic/xonotic/-/wikis/home (cvars, mapinfo, .ent files); the stock packs in `<stock data>`.

## Testing
- `game start` / `game stop` / `game reset`: a fresh userdir with your mod, straight into the start.yaml match.
- `game console 'CMD'`: a console command on the running game (e.g. `sv_gravity`, `sv_cmd setbots 2`, `map catharsis`);
  for testing only, the evaluation's player uses real input.
- `game shot [FILE]`, input (`game hold w 2`, `game key space`, `game look 200 0`, `game click 640 360 1`), `game state`
  (map, player position / health / armor), `game log` (the console log: cfg errors show here).
- `game sh 'COMMAND'`: a shell in the sandbox (read game data, logs); `game fetch SANDBOX_PATH WORKDIR_PATH` copies out.
- Keys: w a s d move, space jump, ctrl crouch, mouse look (`look DX DY`), left click fire (button 1), right click secondary
  fire (button 3), 1-9 / mouse wheel weapons, q last weapon, tab scores, Escape menu, shift+Escape console.

## Traces (`deliverable/traces/*.yaml`)
```yaml
name: grab the rocket launcher
claims: ["the player spawns next to the new armour pickups", "jumps are higher than in stock Xonotic"]
steps:                                  # each trace starts from a fresh start (start.yaml)
  - {do: wait, seconds: 3}
  - {do: look, dx: 300, dy: 0}                     # turn right (mouse)
  - {do: hold, keys: [w], seconds: 2}              # run forward
  - {do: key, keys: "space"}                       # jump
  - {do: click, x: 640, y: 360, button: 1}         # fire
  - {do: shot, name: pickups}                      # a screenshot the evaluation keeps
```
Only real player input: the evaluation replays your traces on a clean install and judges what its screenshots show.
