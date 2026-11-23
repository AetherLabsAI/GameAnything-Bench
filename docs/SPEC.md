# GameAnything-Bench: framework overview

## Layout

```
benchmark/construction/tasks/<id>/     task.yaml, instruction_fine.md, references.yaml (public part; references.yaml is never shown to agents)
benchmark/construction/rubrics/<id>/   rubric.yaml (CR / FP / VP items), regression.yaml
benchmark/construction/targets.json    the paper's target pictures (dataset paths + sha256)
benchmark/editing/                     tasks.json, rubrics.json, p_to_es.json, checkpoints.json
benchmark/host_fidelity/               checklist.json, anchor.md, vanilla.json
benchmark/pools/<game>/pool.yaml       regression probes per game (gate items)
gab/                                   the framework (below)       schemas/   JSON schemas of the task files
docker/                                images (one per game, shared base)   tests/   pytest (no game, docker or model needed)
```

`gab/`: `cli.py` (the `gab` command), `config.py`, `util.py`, and the subpackages `run/` (tasks, trials, scheduler, preflight,
infrastructure records, interrupts, the agent's `game` tool), `edit/` (the Editing track and its commands), `refine/` (refine
rounds, experience memory + `memory_seed/`, replay summaries), `eval/` (evaluation and scoring), `setup/` (getting the games,
Steam, self-checks, dataset media), `agent/`, `game/`, `host/`, `report/`, `tools/`.

Data (config, `gab setup`): `media_dir` (reference media), `edit_store` (editing checkpoints), `targets_dir`, `runs_dir`
(trials and results), all under `~/.gab/` by default; `gab media fetch` fills the first three from the dataset (`hf_dataset`).
Config order: `GAB_<KEY>` environment > `gab.local.json` (`gab setup --set`) > `gab.toml` > defaults (`gab/config.py`).

## Task package

`task.yaml`: `task_id` (T-VO-* video, T-TO-* text, T-IO-* images, T-HY-* hybrid), `category` (scene_world |
mechanics_behavior | narrative_event), `modality`, `track` (core = open-source game | commercial), `target_game`,
`references` (ids). `references.yaml`: per reference file its kind, file name, dataset path and sha256 (clips also the source
range; texts an anchored judge copy). `rubric.yaml`: the items per column (anchors 1 / 0.5 / 0, evidence: clip times, text
paragraph anchors, image numbers; VP keyframes; `cross` items on hybrid tasks), explicit requirements, play suggestions.
`gab task validate` checks a package against `schemas/`. Editing tasks name their parent Construction task, the change
request and the start checkpoint (a finished mod of the parent task, made by one of the paper's agents).

## Trial (`gab run`, gab/run/trial.py)

1. Stage the workspace: `work/brief.md`, `reference/` (the files only: no titles, sources or captions), `task/`, `devkit/`,
   `targets/` (target pictures), `deliverable/`; the tools (`game`, `transcribe`, ffmpeg, the generation tools) live outside
   the work dir.
2. Lease a seat (one docker container per trial, `gab/host/docker.py`), provision a dev instance of the game.
3. Run the agent (one autonomous attempt, 120 min): the brief says what is judged, how to compare screenshots with the
   reference and the target pictures, the tools, the hand-in format and the budget. The run ends at `deliverable/DONE` or the
   budget; a harness that stops early is continued in the same session (`gab/agent/until_done.py`), and transient API errors
   are resumed.
4. Freeze the hand-in (`mod/`, `ACCESS.md`, `traces/*.yaml`, `shots/*.png`), record cost and any infrastructure incidents.
5. Evaluate on a fresh instance ([evaluation.md](evaluation.md)) -> `result.json`.

Trial dir: `<runs_dir>/<name>/<task>/fine/` (`<name>/<edit>/edit/` for Editing): `work/`, `tools/`, `agent/` (agent.json,
transcript), `deliverable/`, `eval/`, `infra.jsonl`, `attempts/<n>/` (earlier attempts and refinement rounds), `result*.json`.

Agents never see rubrics, reference metadata, media sources or the checkpoint store: they are in the deny lists of the host
agents, outside the container mounts, and every transcript is audited for those paths (a hit is recorded as a leak).
`--agent-in-container` runs the harness inside the trial container (no network; model calls go through a host-side key
proxy that adds the key and enforces the cost caps `run_max_usd`, `refine_max_usd`, `max_request_tokens`).

## Refinement (+Ours, `gab refine`, gab/refine/refine.py)

Per round: replay and record the hand-in; a critic agent (OpenCode + a vision model, in the trial's container, no game)
compares the recordings with the reference and the target pictures and writes feedback by issue kind; the trial's own agent
revises for 60 minutes with the feedback and lessons from the run's experience memory (`gab/refine/memory.py`); the after-state is
recorded and reviewed, and 0-3 new lessons are extracted. `gab edit-refine` does the same for Editing (the critic compares
BEFORE and AFTER, and regressions against the start checkpoint are named).

## Adapters

Game adapters: `gab/game/<game>/` (`GameAdapter` in `gab/game/base.py`: provision, install, build / load gate, start, input,
replay, screenshot, structured state [STATE_SPEC.md](STATE_SPEC.md), regression probes). Agent adapters: `gab/agent/<name>/`
(`AgentAdapter` in `gab/agent/base.py`). See [adding_a_game.md](adding_a_game.md), [adding_an_agent.md](adding_an_agent.md).

## Results

`result.json`: `trial`, `versions` (game, image, gab, judge), `status` (completed | timeout | build_failed | load_failed |
unstable | host_removed | error | infra_error | budget_exceeded), `gate`, `scores` (`C` per column, `P`, `overall`), `items`
(score, evidence, judge, could_not_test per item), `explicit_requirements`, `host_fidelity`, `cost` (agent and evaluation:
time, tokens, USD), `eval_settings`, `infra`. Editing results add `edit`, ES / P / P_fidelity scores, per-item baselines.
`gab score` aggregates a run (means over tasks with 95% bootstrap intervals, by reference kind and track); infrastructure
errors are counted, not scored.

## Parallel runs

Before a batch, `gab run` / `edit` / `refine` / `bench` print a plan by game type: cores, RAM and GPUs, what one trial of
each game needs, and how many run at once (2D games use CPU only; 3D games share GPUs, `trials_per_gpu`). `--plan` only
prints it, `--workers N` caps it, `--yes` starts without asking. Per-trial needs are config keys (`trial_ram_gb`,
`trial_cores`, `trials_per_gpu`, `host_reserve_*`).
