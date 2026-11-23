# Evaluation protocol

## Construction

1. **Gate** (on a clean game instance with the mod installed the way a player installs it): the mod builds, installs and
   loads; the game runs stably (no crash, hang or new fatal error kind compared with vanilla); and the host game is still
   there (one or two stock-content probes per game, `benchmark/pools/<game>/pool.yaml` `gate:`). A failed gate sets every
   score to 0 (status `build_failed`, `load_failed`, `unstable` or `host_removed`). A check that cannot run because of the
   environment (its vanilla baseline fails too) makes the trial `infra_error`, never a 0.
2. **Rubric items** (`benchmark/construction/rubrics/<task>/rubric.yaml`), each scored 1 / 0.5 / 0 against written anchors,
   with the reference evidence it cites (quoted text passages, reference images, frames of the clip):
   - Content Realization (CR) and Functions & Playability (FP): the verifiers below;
   - Visual & Presentation (VP): a visual judge compares screenshots (the hand-in's and ours) with the item's keyframes /
     reference images / passages.
3. **Preservation (P)**: the host-fidelity judge decides from the saved evidence whether the experience is built on and
   played through the host game (its world, avatar, controls, systems and look) or a replacement that merely boots inside
   it (`benchmark/host_fidelity/`).
4. **Overall** = 0.3 CR + 0.3 FP + 0.3 VP + 0.1 P; a missing part is left out and the weights renormalized.

Verifiers for CR / FP:

| | Full (default) | Lite (`--lite`) |
|---|---|---|
| replay verifier: the hand-in's own input traces replayed with real input on a clean install; screenshots + structured game state after every step | yes | yes |
| play verifier: an independent Claude Code agent plays the build through `play` (player input only; 40 actions / 30 min; first blind, then with ACCESS.md, the traces and the rubric's play suggestions) | yes | no |
| adjudication: equal scores stand; otherwise a third call sees both verdicts and decides | yes | no |
| cost / time per submission (paper appendix) | about $0.56 / 13 min | about $0.20 / 6 min |
| agreement with human raters (mean absolute error, 0-100 scale) | 8.8 | 9.8 (scores a little lower than Full) |

The judge is Claude Sonnet 5.5 through the Claude Code CLI (config `judge_model`). Every judge call is validated and retried
once; a call that still fails makes the trial `infra_error` (`judge_failed`), never a silent partial score. Prompts, raw
answers and costs are kept under `eval/`. A developer's own claims count for nothing unless the captures show them.

Outputs: Full -> `eval/` + `result.json`; Lite -> `eval@lite/` + `result@lite.json`; after K refinement rounds
`result@r<K>.json` / `result@lite-r<K>.json` (`gab eval --round K`). `gab score --name NAME [--lite] [--round K]`.

## Editing

Gate as above, then the edit rubric (`benchmark/editing/rubrics.json`): Edit Success items (the change is there and works),
a completion-quality item, and Preservation items (what the project did before still works). Evidence: the hand-in's traces
replayed on a clean install (screenshots, state, recordings) next to the BEFORE captures of the start checkpoint; a locator
call first finds, per item, the parts of the recordings where its evidence should be. Preservation items are scored relative
to the start checkpoint (an item the checkpoint itself fails is not counted; one it half passes counts as fully preserved
when kept at that level). Items that can only pass once the change is made are scored as Edit Success
(`benchmark/editing/p_to_es.json`). Host fidelity as in Construction.

Overall = E x [1 - 0.4 (1 - P) - 0.1 (1 - H)] (E = Edit Success, P = Preservation, H = host fidelity): a no-op scores 0, a
correct edit that breaks everything else about 0.5. A missing part is left out and the weights renormalized.
Editing has one evaluation mode. Output: `result@edit.json` (`result@edit-r<K>.json` after K refinement rounds).

## Infrastructure errors

A failure of the environment is not the agent's: tool calls, transcripts and gate logs are classified (`gab/run/infra.py`:
docker, sandbox, model API, game environment, harness). `infra_error` results are not scores; reports count them as pending,
and the next run of the same command runs them again (transient causes are retried automatically).

## Main table

`gab score` / `gab bench` write `report/main_table.md` in the paper's format: Construction Content, Func., Vis., Pres.,
Overall, Cost; Editing Edit Succ., Pres., Fidelity, Overall, Cost (scores x 100; cost = mean agent USD per task), for all
games and per track (open-source / commercial), with the number of evaluated tasks.
