# valheim: lessons (builder)

Experience memory of this benchmark configuration (gab/refine/memory.py). Format: GameAnything skills/gameanything/references/lessons.md.

Seeded from GameAnything gameanything/games/valheim/LESSONS.md (builder section) at commit 9270ce2e098aa3bb02f9f6dc5196bf179a1de337, privacy-scanned by scripts/memory_seed.py (entries or lines with a hit are left out), filtered to what holds in the bench containers and translated to English (scripts/memory_seed_curation.yaml).

## Development

### valheim-002
- date: 2026-10-25
- game: valheim
- role: builder
- symptom: A plugin built for .NET 5+ (or loaded without its symbols by a hot-reloader) does not load.
- cause: BepInEx on the game's Mono loads .NET Framework 4.x / netstandard plugins only.
- fix: Target net462 (as the devkit template does); do not keep the same plugin in two plugin folders; call harmony.UnpatchSelf() in OnDestroy.
- evidence: seed: GameAnything gameanything/games/valheim/LESSONS.md valheim-002 (translated, adapted to the bench)
- confidence: verified repeatedly
- status: confirmed

### valheim-003
- date: 2026-10-24
- game: valheim
- role: builder
- symptom: Added items disappear after entering the world.
- cause: The game rebuilds ZNetScene / ObjectDB every time a world is entered.
- fix: Register them in Postfix patches of ZNetScene.Awake and ObjectDB.Awake.
- evidence: seed: GameAnything gameanything/games/valheim/LESSONS.md valheim-003 (translated, adapted to the bench)
- confidence: tried once
- status: candidate

### valheim-004
- date: 2026-10-24
- game: valheim
- role: builder
- symptom: A plugin project fails with CS0012 for a Unity type (e.g. FontStyle).
- cause: The project does not reference the Unity module that defines the type (here UnityEngine.TextRenderingModule).
- fix: Start from the devkit template and add a <Reference> with a HintPath into the game's managed assemblies for every Unity module you use.
- evidence: seed: GameAnything gameanything/games/valheim/LESSONS.md valheim-004 (translated, adapted to the bench)
- confidence: tried once
- status: confirmed
