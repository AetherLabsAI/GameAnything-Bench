# Structured game state for verification (v1)

Functions & Playability evidence = screenshot + structured state. Every value comes from the RUNNING game, read through the
adapter's `state(inst)`. Never from anything the mod reports about itself (no self-report API, no files the mod writes for us):
a mod's data counts because the engine holds it, not because the mod says so.

## What `state(inst)` returns

```
{"running": bool,
 "engine": {...},    # fixed, game-defined fields (below): the same keys every call
 "mod":    {...},    # what mods created inside the engine, read generically (below)
 "screen_text": [...]  # text the game is showing: HUD lines, dialog / message boxes, chat (last N), objectives
}
```

- **engine**: per game a fixed field set, documented in the adapter's docstring. At least where the game has them: player
  position / map / area, health and other vitals, inventory or resources, money / score / stats, time of day / weather / game
  time, current screen (menu, in game, dialog, cutscene), race / match status.
- **mod**: read generically, so it works for any mod without the mod doing anything: entities / objects / NPCs that are not
  vanilla (name + key properties + position, nearest N), variables and storage the engine keeps for mods (Luanti player meta
  and mod storage, Tuxemon game variables, Xonotic cvars that differ from vanilla, quest /
  campaign flags), node / tile metadata near the player where the engine has it. Values may be renamed only by truncation.
- **screen_text**: what a player can read on screen, from the engine (HUD elements, formspecs / message boxes, chat buffer,
  objective texts), not OCR.

## Rules

- Live values from the running game; a save file is acceptable only when the game has no other channel, and the field then says
  so (`"source": "save"`).
- Cheap: one `state()` call < 1 s typically, < 3 s worst case; no game pause, no input, no side effects.
- Compact JSON: lists capped (e.g. 20 nearest entities, 30 variables), long strings cut at 200 chars; full dumps are not needed.
- Stable keys so `gab.game.base.state_diff(a, b)` can diff two snapshots (changed / added / removed leaf paths).
- Self-check: a vanilla instance returns the engine fields and an empty / vanilla-only `mod`; a test mod that sets a variable,
  spawns an entity and shows HUD text makes all three appear (add it to `gab selfcheck <game>`).
