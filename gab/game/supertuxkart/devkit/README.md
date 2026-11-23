# SuperTuxKart development kit (GameAnything-Bench)

SuperTuxKart 1.5 (a 3D kart racer) runs in a sandbox you control with `game` (run `game help`). Screen 1280x720 (the game
window fills it); `game info` prints the paths (stock game data, the instance's data copy and add-ons dir, the game log).

## What you hand in: `deliverable/mod/`
```
mod/
  data/               files laid over the stock game data, same paths (replace or add):
                        stk_config.xml, kart_characteristics.xml, powerup.xml, items.xml   (tuning)
                        karts/<kart>/kart.xml, textures, models ...                         (stock karts)
                        tracks/<track>/scene.xml, scripting.as, textures ...               (stock tracks / arenas)
                        gui/screens/*.stkgui, gui/skins ..., textures/, sfx/, music/        (menus, art, sound)
  addons/             new karts and tracks, as the in-game add-on manager installs them:
    karts/<id>/         kart.xml + models / textures (copy a stock kart dir and change it)
    tracks/<id>/        track.xml + scene.xml + models / textures (+ scripting.as)
  start.yaml          how a fresh start enters your experience (see template/mod/start.yaml)
```
The evaluation lays data/ over a fresh copy of the stock data, puts addons/ into a fresh player home and starts the game:
`mode: menu` (default) opens the main menu; `mode: race` goes straight into a race:
`{mode: race, track: <id>, kart: <id>, numkarts: 4, laps: 3, race_mode: normal | time_trial | follow_leader | battle,
difficulty: 0-3, reverse: false}` (battle needs an arena, e.g. stadium; soccer / capture-the-flag cannot start from the
command line in 1.5). Ids are directory names (stock or yours). Several mods may sit side by side as mod/<name>/{data,addons}.
`game build` runs the structure check (incl. every .xml / .stkgui must parse) and the load test (headless 8 s AI races on every
track and with every kart you add, then the real game reaches your start target); `game check` checks the whole hand-in.

## Building (no Blender in the sandbox: new geometry comes from the stock models, the library and text formats)
- Tuning: kart_characteristics.xml (speed, acceleration, mass, steering, skidding, nitro, rescue ...; per kart class and per
  kart in karts/<k>/kart.xml), powerup.xml / items.xml (items and their effects), stk_config.xml (race rules, laps, physics).
- Tracks: scene.xml places objects (library objects from `data/library/`, items, item boxes, nitro, bananas, lights, sky,
  weather, action triggers, cutscene cameras) on the track; track.xml holds name, laps, music, arena / soccer flags;
  scripting.as is AngelScript run by the track (onStart, onUpdate-like triggers via action-triggers, `Track::`, `Kart::`,
  `GUI::displayMessage`, `Utils::logInfo` ...). Stock tracks and `data/tracks/*/scripting.as` (overworld, tutorial) are the
  best examples. Reference: https://supertuxkart.net/Making_Tracks , https://doc.supertuxkart.net/scripting/
- Art: PNG / JPG textures (replace a texture with the same file name), sounds (.ogg), music (.music + .ogg).
- Read stock files with `game sh 'cat <game_data>/tracks/lighthouse/scene.xml'` (paths from `game info`).

## Testing
- `game start` / `game stop` / `game reset`: a fresh instance with your mod on the screen (the start-up loads every kart:
  ~40 s on a quiet machine, minutes on a busy one).
- `game profile TRACK [KART]`: a headless 8 s AI race with your mod (fast): are the karts driving, crashes, error lines.
- `game shot [FILE]`, input (`game key Return`, `game hold Up 3`, `game click 408 470 1`), `game state` (phase: loading /
  menu / race), `game log` (the game log: XML and script errors show here).
- `game sh 'COMMAND'`: a shell in the sandbox (read game data, logs); `game fetch SANDBOX_PATH WORKDIR_PATH` copies out.
- Keys (the game's defaults): Up accelerate, Down brake / reverse, Left / Right steer, v drift (skid), n nitro, space fire
  item, b look back, BackSpace rescue, Escape pause / back; menus: arrow keys + Return, or click.

## Traces (`deliverable/traces/*.yaml`)
```yaml
name: first lap on the new track
claims: ["the race starts on the desert track with 3 karts", "the jump ramp throws the kart over the river"]
steps:                                  # each trace starts from a fresh start (start.yaml)
  - {do: wait, seconds: 5}                         # countdown
  - {do: hold, keys: [Up], seconds: 4}             # accelerate
  - {do: hold, keys: [Up, Left], seconds: 1.5}     # steer left while accelerating
  - {do: key, keys: "space"}                       # fire the item
  - {do: shot, name: ramp}                         # a screenshot the evaluation keeps
```
Only real player input: the evaluation replays your traces on a clean install and judges what its screenshots show.
