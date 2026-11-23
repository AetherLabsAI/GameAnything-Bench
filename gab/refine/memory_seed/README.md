# Experience-memory seed

This directory is the starting content of the cross-task experience memory that the `+Ours` refinement rounds use
(`gab refine`, implemented in `gab/refine/memory.py`). The first time a run needs lessons for a game, the matching seed files are
copied into that run's own memory (`<memory_dir>/<run name>/`). After that the run adds and folds in its own lessons. The
files here are never written to.

```
games/minecraft.md          lessons for one commercial game
games/cities_skylines.md
games/stardew_valley.md
games/terraria.md           empty: none of its source entries passed the filters (kept on purpose)
games/valheim.md
roles/builder.md            general development practice for any game
SEED.json                   where every file came from and what happened to each source entry
```

## Where it comes from

The seed comes from the lessons that the GameAnything platform's development agents wrote down while building game mods:
each game's `LESSONS.md` (only its builder section) and the builder role file
`skills/gameanything/references/lessons/builder.md`, at GameAnything commit `9270ce2`.

GameAnything's projects were all built on the five commercial games, so only those games have seeded lessons. Games on the
open-source track start with an empty game file, and all runs get the same seeded `roles/builder.md`. As a result, the gains
from refinement are not directly comparable between the two tracks: commercial-game runs start with prior game knowledge and
open-source runs do not. The paper's runs were set up this way.

## How it was filtered

`scripts/memory_seed.py` applies three steps:

1. **Privacy scan.** It runs GameAnything's private-data checks and also looks for references to the GameAnything user,
   private project names or records, and the content of a user's own project. An entry whose symptom or fix has a hit is
   dropped. An evidence line with a hit is replaced by the seed's provenance (`seed: GameAnything <file> <id>`).
2. **Bench filter** (`scripts/memory_seed_curation.yaml`). An entry that relies on GameAnything's own tooling or lab setup,
   or that does not hold in the bench containers or contradicts the game's devkit README, is dropped with a reason.
   `adapted` marks an entry whose text was changed beyond translation to fit the bench.
3. **English translation** of every kept entry, which is scanned again afterwards.

`SEED.json` stores no matched text, only ids and categories. For each seed file it records `source_file`,
`source_sha256` and `section` (the section's name in the source), plus `counts` (`entries_in_source`, `after_privacy_scan`,
`kept`). It also lists `kept` (ids), `translated` (`"all"` or a list of ids), `evidence_replaced` (ids whose evidence
named a private project or record), `adapted` (`{id: note}`) and `dropped` (`{id: reason}`, or a list of reasons when there
are several). At the top level it records `source` and `commit`.

## File format

The files use GameAnything's markdown lesson format with English field names. Each game file has one section,
`## Development`. Each entry looks like this:

```
### minecraft-007
- date: 2026-10-23
- game: minecraft            (or "any" in roles/builder.md)
- role: builder
- symptom: what went wrong
- cause: why
- fix: what to do instead
- evidence: where it was seen (seed provenance, or "task <id> (<level>) refine r<k>: ..." for learned lessons)
- confidence: tried once | verified repeatedly
- status: candidate | confirmed
```

`gab/refine/memory.py` also reads GameAnything's original format: Chinese field names under `## 开发`. It maps them to the names
above.

## Rebuilding the seed

```
python scripts/memory_seed.py <GameAnything checkout> [--scan PRIVATE_PATTERNS] [--write]
```

Without `--write` the script only prints counts and the list of privacy hits. `--scan` takes a file of regular expressions
for private names, one per line. These patterns are not shipped with the repository. Every entry that passes the scan must
appear in `scripts/memory_seed_curation.yaml`, either kept (with its English text) or dropped (with a reason).

## Running with an empty memory instead

- `gab refine --no-memory` runs the refinement rounds without any experience memory (the ablation setting).
- To keep the memory but start without the GameAnything seed, empty the seeded files in your run's memory before its first
  round. Each game file keeps only its header and `## Development` line, and `roles/builder.md` keeps only its header. For
  example, copy `games/terraria.md` and change the game name. Files that already exist in a run's memory are never
  re-seeded.
