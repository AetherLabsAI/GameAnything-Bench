# builder: lessons (any game)

Experience memory of this benchmark configuration (gab/refine/memory.py). Format: GameAnything skills/gameanything/references/lessons.md.
Seeded from GameAnything skills/gameanything/references/lessons/builder.md at commit 9270ce2e098aa3bb02f9f6dc5196bf179a1de337, privacy-scanned by scripts/memory_seed.py (entries or lines with a hit are left out), filtered to what holds in the bench containers and translated to English (scripts/memory_seed_curation.yaml).

### builder-003
- date: 2026-10-24
- game: any
- role: builder
- symptom: `pkill -f` / `pgrep -f` matched the agent's own shell (exit 144) or killed other games and services.
- cause: -f matches the whole command line, including the command itself.
- fix: List PIDs in a separate call first (ps -eo pid,comm,args, excluding bash / sh / awk), then kill those exact numbers; start and stop the game only with `game start` / `game stop`.
- evidence: seed: GameAnything skills/gameanything/references/lessons/builder.md builder-003 (translated, adapted to the bench)
- confidence: verified repeatedly
- status: confirmed

### builder-008
- date: 2026-10-17
- game: any
- role: builder
- symptom: A development agent hit its time limit and was stopped; its last message was empty and nothing was handed in.
- cause: It did not wrap up before the limit.
- fix: Well before the time is up, have the hand-in complete (mod, traces, shots, ACCESS.md) and the mod source buildable offline.
- evidence: seed: GameAnything skills/gameanything/references/lessons/builder.md builder-008 (translated, adapted to the bench)
- confidence: tried once
- status: confirmed

### builder-009
- date: 2026-10-22
- game: any
- role: builder
- symptom: One problem was changed back and forth dozens of times (the same camera spot kept being blocked by pillars and trees).
- cause: Parameters were tuned in one direction only, without changing the approach.
- fix: After the same failure three times, stop, write down the known cause and change direction (e.g. remove what is in the way instead of moving the camera again).
- evidence: seed: GameAnything skills/gameanything/references/lessons/builder.md builder-009 (translated)
- confidence: tried once
- status: candidate

### builder-022
- date: 2026-10-23
- game: any
- role: builder
- symptom: Real-world rules of a theme were written from memory and were wrong (a 10-point review scale where the real one has 6 points).
- cause: The official description was not checked.
- fix: Check real-world rules (rating scales, procedures, terms) against the official description before writing them; when converting, keep the original difficulty.
- evidence: seed: GameAnything skills/gameanything/references/lessons/builder.md builder-022 (translated)
- confidence: tried once
- status: candidate
