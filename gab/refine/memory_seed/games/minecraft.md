# minecraft: lessons (builder)

Experience memory of this benchmark configuration (gab/refine/memory.py). Format: GameAnything skills/gameanything/references/lessons.md.

Seeded from GameAnything gameanything/games/minecraft/LESSONS.md (builder section) at commit 9270ce2e098aa3bb02f9f6dc5196bf179a1de337, privacy-scanned by scripts/memory_seed.py (entries or lines with a hit are left out), filtered to what holds in the bench containers and translated to English (scripts/memory_seed_curation.yaml).

## Development

### minecraft-003
- date: 2026-10-22
- game: minecraft
- role: builder
- symptom: Many names changed in 26.3: Blocks.RED_WOOL / PINK_STAINED_GLASS do not exist, ServerPlayer.hurtMarked is gone, TextDisplay.getText/setText are private.
- cause: 26.3 is a new, unobfuscated version (Mojang's own names); most older tutorials and remembered APIs are outdated.
- fix: Look the real code up in the decompiled sources (path in `game info`) or with javap before writing it; coloured blocks via Blocks.WOOL.pick(DyeColor.X)-style calls.
- evidence: seed: GameAnything gameanything/games/minecraft/LESSONS.md minecraft-003 (translated, adapted to the bench)
- confidence: verified repeatedly
- status: confirmed

### minecraft-005
- date: 2026-10-22
- game: minecraft
- role: builder
- symptom: Scripted characters end up buried in the ground, standing on roofs or well covers, or vanish on bridges.
- cause: Blocks written directly while building do not update the heightmap, so it returns the water or dirt below; MOTION_BLOCKING also misses custom leaves without collision.
- fix: Find standing height by scanning down from above for the first solid surface (skipping leaves); use fixed heights for pavilions, bridges and upper floors; load the chunk first (getChunk), an unloaded chunk reads as air.
- evidence: seed: GameAnything gameanything/games/minecraft/LESSONS.md minecraft-005 (translated)
- confidence: verified repeatedly
- status: confirmed

### minecraft-007
- date: 2026-10-23
- game: minecraft
- role: builder
- symptom: A second boat built from block_display parts never showed up: one boat fell apart, the other stayed at spawn.
- cause: The parts of both boats used the same tag, so the per-tick `tp @e[tag=..,limit=1]` moved only one of them.
- fix: Tag parts as <part><instance id>; find anything assembled from block_display (boats, figures) by a unique tag or a fixed UUID.
- evidence: seed: GameAnything gameanything/games/minecraft/LESSONS.md minecraft-007 (translated)
- confidence: tried once
- status: confirmed

### minecraft-008
- date: 2026-10-23
- game: minecraft
- role: builder
- symptom: Flower beds, hedges, stairs and walls are pitch black although the same commands lit them before; flat plants render as black silhouettes.
- cause: Light blocks placed while building sometimes do not get their lighting computed; placing a light block where one already is changes nothing.
- fix: About 30 s after building, force-load those chunks, remove every light block and place it again; give flat decorative blocks a lightLevel.
- evidence: seed: GameAnything gameanything/games/minecraft/LESSONS.md minecraft-008 (translated)
- confidence: verified repeatedly
- status: confirmed

### minecraft-009
- date: 2026-10-23
- game: minecraft
- role: builder
- symptom: Block states fail to parse; display entities made of grass, ferns or oak leaves render black; pink blocks float in the air.
- cause: 26.3 needs block_state:{id:"..",properties:{..}} (the old "name[k=v]" form is rejected); biome-tinted blocks render black in displays; unsupported decoration blocks look like they float.
- fix: Use the new format; for greenery in displays use untinted blocks (azalea leaves, moss, flowers); after decorating, look through the camera for floating blocks.
- evidence: seed: GameAnything gameanything/games/minecraft/LESSONS.md minecraft-009 (translated)
- confidence: verified repeatedly
- status: confirmed

### minecraft-010
- date: 2026-10-22
- game: minecraft
- role: builder
- symptom: The client dies while loading a model; the log says `PartDefinition parent is null`.
- cause: In the model json a child part comes before its parent.
- fix: Sort parts by their parent chain in the script that writes the model; check the sign of arm / limb rotations on one posed screenshot first (a negative right-arm yRot turns inwards; the wrong sign gives a T-pose).
- evidence: seed: GameAnything gameanything/games/minecraft/LESSONS.md minecraft-010 (translated)
- confidence: verified repeatedly
- status: confirmed

### minecraft-011
- date: 2026-10-22
- game: minecraft
- role: builder
- symptom: Subtitles cover the characters in the middle of the screen; a scene's lines keep showing after the camera has left for another room; long lines are cut off.
- cause: The title channel is in the screen centre; the scene did not stop when the player left; a 1280 px wide window fits only about 34 Latin characters (16 CJK) per line.
- fix: Show text in your own HUD element or the action bar along the bottom; stop the scene and clear its text about 1.5 s after the player leaves its range; split lines to the window width. Never have the mod write to chat.
- evidence: seed: GameAnything gameanything/games/minecraft/LESSONS.md minecraft-011 (translated)
- confidence: verified repeatedly
- status: confirmed

### minecraft-014
- date: 2026-10-21
- game: minecraft
- role: builder
- symptom: Commands that spawn butterflies or plaques flood the chat, which then shows in screenshots and recordings.
- cause: The server console command source ignores withSuppressedOutput().
- fix: Run them with a CommandSourceStack built from CommandSource.NULL; while capturing, turn off the send_command_feedback and command_block_output game rules.
- evidence: seed: GameAnything gameanything/games/minecraft/LESSONS.md minecraft-014 (translated)
- confidence: verified repeatedly
- status: confirmed

### minecraft-015
- date: 2026-10-22
- game: minecraft
- role: builder
- symptom: The review said a hall was unchanged or showed a 'black panel', yet the changes were there: the camera stood inside a solid block of an old gate, a railing or a bookshelf.
- cause: A spectator camera inside a block fills the screen with that block; structures left from an earlier version block the view of new ones.
- fix: When a review disagrees with what you saw, first check the block at the camera position and along the line of sight (`game query block` / `game query blocks`); keep pillars out of 2 blocks and trees out of 13 blocks around fixed camera spots.
- evidence: seed: GameAnything gameanything/games/minecraft/LESSONS.md minecraft-015 (translated, adapted to the bench)
- confidence: verified repeatedly
- status: confirmed

### minecraft-016
- date: 2026-10-22
- game: minecraft
- role: builder
- symptom: Aerial shots show only sky and fog, distant areas show bare pillars; after interior changes the screenshot still shows the old state; 12 shots after a long teleport are all the first frame.
- cause: The client rebuilds chunk meshes slowly (blocks 30-60 s, display entities at once); far chunks are not rendered yet.
- fix: Wait at least 12 s after a long teleport and over 30 s after large block changes; take distant views last, with longer waits.
- evidence: seed: GameAnything gameanything/games/minecraft/LESSONS.md minecraft-016 (translated)
- confidence: verified repeatedly
- status: confirmed

### minecraft-022
- date: 2026-10-26
- game: minecraft
- role: builder
- symptom: block_display entities spawned by a command are all air (invisible), and the command reports no error.
- cause: 26.3 block_state must be {id:"minecraft:x",properties:{k:"v"}}; {Name:"minecraft:x"} is read as air.
- fix: Always use id / properties when summoning block_display; after spawning, check once with `data get entity @e[tag=..,limit=1] block_state` that it is not minecraft:air.
- evidence: seed: GameAnything gameanything/games/minecraft/LESSONS.md minecraft-022 (translated)
- confidence: tried once
- status: candidate

### minecraft-023
- date: 2026-10-26
- game: minecraft
- role: builder
- symptom: Characters spawned with fixed UUIDs (hash of a name): on the second tour some appear and some do not.
- cause: Entities with the same UUID left in unloaded chunks by the previous tour collide with the new ones, which are dropped; kill only reaches loaded entities.
- fix: Spawn with random UUIDs (keep a table from id to UUID); at the start of a tour forceload the scene's chunks, then kill; give all characters a common tag for cleanup.
- evidence: seed: GameAnything gameanything/games/minecraft/LESSONS.md minecraft-023 (translated)
- confidence: tried once
- status: candidate

### minecraft-024
- date: 2026-10-26
- game: minecraft
- role: builder
- symptom: Posable humanoid figures with outfits are tedious to build from block_display parts and hard to re-dress.
- cause: 26.3 has a vanilla minecraft:mannequin entity.
- fix: Use a mannequin with dyed leather helmet / chestplate / leggings / boots in its equipment (dyed_color as an integer RGB); pose crouching (sitting) or swimming (lying), immovable:1b, hide_description:1b, CustomName as a name tag; move it each tick with teleportTo from Java.
- evidence: seed: GameAnything gameanything/games/minecraft/LESSONS.md minecraft-024 (translated, adapted to the bench)
- confidence: tried once
- status: candidate

### minecraft-025
- date: 2026-10-26
- game: minecraft
- role: builder
- symptom: The green Glowing outline shows through walls and hills; an aerial shot of the island has a cluster of green dots.
- cause: Glowing outlines are not occluded by blocks.
- fix: Turn the outline on only for the scenes that need it (data merge ... Glowing:1b) and off for the rest and before still screenshots.
- evidence: seed: GameAnything gameanything/games/minecraft/LESSONS.md minecraft-025 (translated)
- confidence: tried once
- status: candidate
