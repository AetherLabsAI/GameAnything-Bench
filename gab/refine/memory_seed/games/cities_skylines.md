# cities_skylines: lessons (builder)

Experience memory of this benchmark configuration (gab/refine/memory.py). Format: GameAnything skills/gameanything/references/lessons.md.

Seeded from GameAnything gameanything/games/cities_skylines/LESSONS.md (builder section) at commit 9270ce2e098aa3bb02f9f6dc5196bf179a1de337, privacy-scanned by scripts/memory_seed.py (entries or lines with a hit are left out), filtered to what holds in the bench containers and translated to English (scripts/memory_seed_curation.yaml).

## Development

### cities_skylines-001
- date: 2026-10-25
- game: cities_skylines
- role: builder
- symptom: Replacing an installed mod's DLL while the game runs (in a city or the main menu) does not reload it, even after a minute.
- cause: The Linux version does not hot-reload mods (Unity / Mono keeps the loaded assembly).
- fix: After C# changes the game has to restart (`game start` / `game reset`, a city load takes minutes): batch C# changes and test them together.
- evidence: seed: GameAnything gameanything/games/cities_skylines/LESSONS.md cities_skylines-001 (translated, adapted to the bench)
- confidence: verified repeatedly
- status: confirmed

### cities_skylines-006
- date: 2026-10-20
- game: cities_skylines
- role: builder
- symptom: Parked cars, benches and shop props disappear right after being placed.
- cause: The simulation deletes props and trees that sit on road or pedestrian-path segments (whichever was built first).
- fix: Put props on grass and show paving another way; check with a screenshot that they are still there after the simulation has run.
- evidence: seed: GameAnything gameanything/games/cities_skylines/LESSONS.md cities_skylines-006 (translated)
- confidence: verified repeatedly
- status: confirmed

### cities_skylines-008
- date: 2026-10-23
- game: cities_skylines
- role: builder
- symptom: Things built in the running city are gone after the game restarts.
- cause: What is built at runtime lives only in the running city; every start of the bench is fresh.
- fix: Have the mod build everything when the level loads (terrain, water, roads, props, models, tints); before handing in, check from a clean `game reset` that the mod alone rebuilds it all.
- evidence: seed: GameAnything gameanything/games/cities_skylines/LESSONS.md cities_skylines-008 (translated, adapted to the bench)
- confidence: verified repeatedly
- status: confirmed

### cities_skylines-010
- date: 2026-10-23
- game: cities_skylines
- role: builder
- symptom: The result of something the mod just did does not show in Player.log.
- cause: The game writes Player.log buffered; the last lines can arrive minutes late.
- fix: For immediate feedback keep your status in static fields of your assembly (shown under mod in `game state`) or write your own log file; read Player.log (`game log`) for compiler errors and exceptions.
- evidence: seed: GameAnything gameanything/games/cities_skylines/LESSONS.md cities_skylines-010 (translated, adapted to the bench)
- confidence: verified repeatedly
- status: confirmed

### cities_skylines-011
- date: 2026-10-22
- game: cities_skylines
- role: builder
- symptom: Generated building models look hollow and broken from an oblique angle.
- cause: The meshes are single-sided and their back faces are culled.
- fix: Make meshes double-sided or add back faces before importing; check with screenshots from the angles the experience uses.
- evidence: seed: GameAnything gameanything/games/cities_skylines/LESSONS.md cities_skylines-011 (translated)
- confidence: tried once
- status: candidate

### cities_skylines-017
- date: 2026-10-26
- game: cities_skylines
- role: builder
- symptom: On Linux, Chinese text on buttons and labels is blank; a font in the user font folder does not help, and a whole 8 MB CJK OTF froze the game at start.
- cause: Unity 5.6 lists only system fonts (8 DejaVu fonts) and does not scan the user font folder.
- fix: Do not rely on fonts: pre-render the text to images (e.g. PIL with a Noto font), ship them with the mod and draw the images when no CJK system font exists.
- evidence: seed: GameAnything gameanything/games/cities_skylines/LESSONS.md cities_skylines-017 (translated)
- confidence: tried once
- status: candidate

### cities_skylines-022
- date: 2026-10-27
- game: cities_skylines
- role: builder
- symptom: Tufts of grass grow through custom flat paths / paving (drawn only near the camera); raising them 0.45 m or making the decoration colour transparent does not help.
- cause: The blades are TerrainProperties grass / fertile decorations, drawn from the terrain regardless of models on top.
- fix: From the mod, after the level loads, set m_useGrassDecorations and m_useFertileDecorations of the loaded TerrainProperties to false; lawns look the same from afar.
- evidence: seed: GameAnything gameanything/games/cities_skylines/LESSONS.md cities_skylines-022 (translated, adapted to the bench)
- confidence: verified repeatedly
- status: candidate

### cities_skylines-023
- date: 2026-10-27
- game: cities_skylines
- role: builder
- symptom: After planting about 3500 trees an IndexOutOfRange in TreeInstance.PopulateGroupData pops up.
- cause: Too many trees in one render group.
- fix: Keep one area to about 1300 trees with at least 6.5 m spacing (check on a grid); start with fewer and add.
- evidence: seed: GameAnything gameanything/games/cities_skylines/LESSONS.md cities_skylines-023 (translated)
- confidence: tried once
- status: candidate
