# Adding a game

A game needs four things: a docker image, an adapter, a dev kit for the agent and a verified regression pool.

1. **Image** (`docker/<game>/Dockerfile`, `install.sh`, an entry in `docker/images.yaml`): the game installed on the
   shared base image (`docker/base`), every first-run dialog pre-answered, network off. Commercial games: a runtime-only
   image; the game files are mounted read-only from the user's own copy (see `docker/stardew_valley`, `docker/minecraft`).
   `docker/build.sh <game>` builds it with tag `local` (`gab setup --set docker_tag local`).
2. **Adapter** (`gab/game/<game>/adapter.py`, class `Adapter(GameAdapter)`, `gab/game/base.py`): `requirements()` (GPU,
   memory), `provision`, `install_mod`, `build` / `load_check` (the gate), `start` / `stop`, `screenshot`, `input` and
   `replay(trace)`, `state()` (structured game state, [STATE_SPEC.md](STATE_SPEC.md)), `version()`, and the regression
   probes (`probes.py`: `run(names) -> data`, `compare(spec, base, got) -> (ok, detail)`). A bridge mod inside the game
   usually provides state and probes (see `gab/game/*/bridge/`).
3. **Dev kit** (`gab/game/<game>/devkit/`): `README.md` (mod layout, build command, the trace format, game-specific tips) and
   `template/` (a minimal mod + `ACCESS.md`). The agent gets a copy in `work/devkit/`.
4. **Regression pool** (`benchmark/pools/<game>/pool.yaml`): probe items with their vanilla expectations; `gate:` names the
   stability item and one or two "the host game is still there" items. `gab selfcheck <game>` runs the A/A check (two
   vanilla instances agree), the negative mods (`gab/game/<game>/negmods/`: each must fail its items) and a dialog-free
   start; mark verified items `status: ready`. `gab run` skips games whose gate items are not ready (`--allow-unverified`).
5. **Host fidelity**: add the game's five identity features to `benchmark/host_fidelity/checklist.json`.

Then write tasks (`benchmark/construction/tasks/<id>/` + `rubrics/<id>/`, schemas in `schemas/`) and check them with
`gab task validate`. Private tasks can keep their rubrics in `hidden_dir` (`gab setup --hidden DIR`).
