# Adding an agent

## Any program: `--agent-cmd`

```bash
gab run  --name mine --agent-cmd "my_agent --brief {brief} --refs {refs} --out {out}" --task T-IO-43b34cf1fa --no-eval
gab bench --name mine --agent-cmd "my_agent --brief {brief} --out {out}" --model my-model
```

The command runs with `bash -c` in the staged workspace and is killed at the time budget (`--minutes`). Placeholders:

| Placeholder | Value |
|---|---|
| `{brief}` | `brief.md`: the complete prompt (request, reference files, the `game` tool, hand-in rules, time budget) |
| `{refs}` / `{clips}` | the reference files (space-separated paths) / only the video clips |
| `{task}` | the workspace directory (`reference/`, `task/`, `devkit/`, `deliverable/`) |
| `{out}` | the hand-in directory (`<task>/deliverable`) |
| `{game}`, `{level}`, `{model}` | the game id, `fine`, the `--model` value |

On its `PATH`: `game` (build, start, stop, shot, input, replay, check, time; `game help`), `transcribe`, `ffmpeg`,
`ffprobe` and the generation tools (`gen-image`, `gen-3d`, `gen-sfx`, `gen-music`; they answer `{"ok": false, ...}` when not
available). `TMPDIR` is private to the trial.

The hand-in (`{out}`): `mod/` (layout in `devkit/README.md`), `ACCESS.md` (how a player reaches the experience from a fresh
start), `traces/*.yaml` (2-4 player-input traces with `claims`), `shots/*.png` (3-6 screenshots), and an empty `DONE` file
when finished. `game check` lists what is missing.

Tokens and cost: write `{task}/.gab/usage.json` = `{"tokens_in": n, "tokens_out": n, "tokens_cached": n, "usd": x}` and
the result records it.

Editing tasks (`gab edit`) work the same way: the workspace already holds the earlier project (`deliverable/` with the mod),
and the brief asks for the change.

## A harness adapter

For a harness that should be a first-class `--agent` (session resume, cost parsing, running inside the trial container):
add `gab/agent/<name>/adapter.py` with a class `Adapter(AgentAdapter)` (contract in `gab/agent/base.py`):

- `run(workdir, instruction, budget, tools) -> AgentRun`: start one agent on `workdir` with `instruction` as its prompt and
  the tool directory first on `PATH`; stop at `budget.minutes` (`exit_reason` timeout), otherwise `done` / `crash`.
- credentials only through the agent process's environment (never argv, files or logs);
- fill `tokens_in / tokens_out / tokens_cached / usd` from the harness's usage report (no USD: leave `usd` None and the
  price table `gab/agent/prices.yaml` is used);
- optional: `can_resume = True` + `opts["resume"]` (continue a session: used when the agent stops before writing `DONE` and
  after transient API errors, `gab/agent/until_done.py`); `container_route()` / `container_command()` for
  `--agent-in-container` (see `gab/agent/claude_code` and `gab/agent/_common.py` CLIAdapter).

Register the short name in `gab/cli.py` `AGENTS`. `codex` and `opencode` are compact examples of CLI harnesses.
