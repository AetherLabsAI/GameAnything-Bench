# stardew_valley: lessons (builder)

Experience memory of this benchmark configuration (gab/refine/memory.py). Format: GameAnything skills/gameanything/references/lessons.md.

Seeded from GameAnything gameanything/games/stardew/LESSONS.md (builder section) at commit 9270ce2e098aa3bb02f9f6dc5196bf179a1de337, privacy-scanned by scripts/memory_seed.py (entries or lines with a hit are left out), filtered to what holds in the bench containers and translated to English (scripts/memory_seed_curation.yaml).

## Development

### stardew-001
- date: 2026-10-24
- game: stardew_valley
- role: builder
- symptom: A `bin/Release/net6.0` folder was installed into Mods: the game starts normally but the mod is not loaded and the log shows no error.
- cause: SMAPI only loads folders with a manifest.json whose EntryDll names an existing DLL, and silently skips the rest.
- fix: Keep manifest.json's EntryDll equal to <AssemblyName>.dll and let `game build` build and install the mod folder; do not copy bin folders by hand.
- evidence: seed: GameAnything gameanything/games/stardew/LESSONS.md stardew-001 (translated, adapted to the bench)
- confidence: verified repeatedly
- status: confirmed

### stardew-003
- date: 2026-10-16
- game: stardew_valley
- role: builder
- symptom: Chinese song titles in a jukebox list show as boxes in the recording.
- cause: The game runs in English, whose fonts have no Chinese glyphs; only the Chinese game uses a Chinese font.
- fix: Give menu and title texts an English / pinyin name as well, shown in the English game; check recordings for boxes.
- evidence: seed: GameAnything gameanything/games/stardew/LESSONS.md stardew-003 (translated)
- confidence: tried once
- status: confirmed

### stardew-004
- date: 2026-10-15
- game: stardew_valley
- role: builder
- symptom: An NPC's 'laugh' sounded like a pig and synthesized applause was not recognizable as applause.
- cause: The agent synthesized the sounds in code and handed them in without anyone listening.
- fix: Prefer the game's own sounds; for new audio use provided files; say in the hand-in that a sound was not listened to.
- evidence: seed: GameAnything gameanything/games/stardew/LESSONS.md stardew-004 (translated, adapted to the bench)
- confidence: verified repeatedly
- status: confirmed

### stardew-005
- date: 2026-10-16
- game: stardew_valley
- role: builder
- symptom: Looping background music breaks every ~83 s.
- cause: The last 6.6 s of the wav file were silence, leaving a gap at each loop.
- fix: Check leading / trailing silence with ffmpeg silencedetect; trim it or loop from where the sound is.
- evidence: seed: GameAnything gameanything/games/stardew/LESSONS.md stardew-005 (translated, adapted to the bench)
- confidence: tried once
- status: confirmed

### stardew-006
- date: 2026-10-16
- game: stardew_valley
- role: builder
- symptom: An NPC at a table 'sits in the air'.
- cause: The sprite was switched to a sitting pose, but no chair furniture was placed at the table; a review of thumbnails only missed it.
- fix: Always put a chair (game furniture or your own) under a sitting character and check front / back overlap; review full-size images.
- evidence: seed: GameAnything gameanything/games/stardew/LESSONS.md stardew-006 (translated)
- confidence: verified repeatedly
- status: confirmed

### stardew-007
- date: 2026-10-17
- game: stardew_valley
- role: builder
- symptom: Scenes looked crude and out of proportion (huge furniture, characters showing only their heads); rejected twice.
- cause: The scene was made as a separate framed picture with a background stretched to the screen, not at the game's tile scale.
- fix: Build it as a real small game location (the game's tiles and furniture, characters at game scale); this passed on the third try.
- evidence: seed: GameAnything gameanything/games/stardew/LESSONS.md stardew-007 (translated)
- confidence: verified repeatedly
- status: confirmed

### stardew-009
- date: 2026-10-17
- game: stardew_valley
- role: builder
- symptom: The next morning another mod clothing item had vanished from the wardrobe (the save was changed).
- cause: The daily outfit code first called 'remove all mod clothing' and then made a new set.
- fix: Remove / replace items by their specific id, never clear a whole category; list any logic that changes the save in the hand-in.
- evidence: seed: GameAnything gameanything/games/stardew/LESSONS.md stardew-009 (translated, adapted to the bench)
- confidence: tried once
- status: candidate

### stardew-010
- date: 2026-10-17
- game: stardew_valley
- role: builder
- symptom: The agent moved farm buildings, cleared debris at the door and maxed out friendship on its own initiative.
- cause: Nothing said 'do not touch what is already in the save'.
- fix: Do not change existing objects or values of the save that the request does not ask for; if you must, list it in the hand-in; put debug shortcuts behind a switch that is off by default.
- evidence: seed: GameAnything gameanything/games/stardew/LESSONS.md stardew-010 (translated, adapted to the bench)
- confidence: verified repeatedly
- status: confirmed

### stardew-011
- date: 2026-10-17
- game: stardew_valley
- role: builder
- symptom: A visiting NPC pops into existence at 10:00 and vanishes at 17:00; a spouse NPC is called home at 13:00 by the vanilla schedule.
- cause: The NPC was warped directly; the vanilla schedule overrides the mod's plan at fixed times.
- fix: Let NPCs walk in and out through map entrances; to change a vanilla schedule, patch it and verify it in a replay.
- evidence: seed: GameAnything gameanything/games/stardew/LESSONS.md stardew-011 (translated)
- confidence: tried once
- status: candidate

### stardew-012
- date: 2026-10-16
- game: stardew_valley
- role: builder
- symptom: Temporary event characters left in the save cause errors once the mod is removed.
- cause: Temporary NPCs were added to a location and not removed before saving.
- fix: Remove temporary NPCs before the game saves and check after every replay that none is left; provide a cleanup before uninstalling.
- evidence: seed: GameAnything gameanything/games/stardew/LESSONS.md stardew-012 (translated, adapted to the bench)
- confidence: verified repeatedly
- status: confirmed
