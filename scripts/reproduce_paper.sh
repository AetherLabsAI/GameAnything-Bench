#!/usr/bin/env bash
# Reproduce the paper's main results: the 9 agent configurations on all 125 Construction + 80 Editing tasks, the native
# attempt (Base) and the +Ours refinement rounds (R1; R2 + R3 for three configurations; Claude Fable 5.2: Base only).
#
#   bash scripts/reproduce_paper.sh                 # everything (expect days of machine time and real API cost)
#   CONFIGS="sonnet55-claudecode" bash scripts/reproduce_paper.sh
#   TRACK=open EVAL=lite bash scripts/reproduce_paper.sh
#
# Needs: gab setup + gab get-game (all 14 games for TRACK=all) + gab media fetch, the judge (Claude Code CLI login or
# ANTHROPIC_API_KEY), each harness's own login / key (README "Credentials"), the critic key for the refinement rounds and
# the generation-tool keys. EVAL=full (default) is the Full evaluation protocol; EVAL=lite the replay verifier only.
# IN_CONTAINER=1 (default) runs every agent inside its trial container, as the paper did (Claude Code: subscription token
# from `claude setup-token` in CLAUDE_CODE_OAUTH_TOKEN; Codex: CODEX_BASE_URL + CODEX_KEY_ENV). Every command is resumable:
# run the script again to continue.
set -euo pipefail

TRACK=${TRACK:-all}                      # open | commercial | all
EVAL=${EVAL:-full}                       # full (default) | lite
WORKERS=${WORKERS:-}                     # empty = the parallel plan's suggestion
BASE_URL=${BASE_URL:-https://openrouter.ai/api/v1}   # OpenAI-compatible endpoint for the OpenCode configurations
API_KEY_ENV=${API_KEY_ENV:-OPENROUTER_API_KEY}      # the NAME of the variable holding its key
CODEX_BASE_URL=${CODEX_BASE_URL:-https://api.openai.com/v1}   # Responses-API endpoint for Codex inside the container
CODEX_KEY_ENV=${CODEX_KEY_ENV:-OPENAI_API_KEY}
IN_CONTAINER=${IN_CONTAINER:-1}          # 1 (default, the paper's setting): agents run inside the trial container; 0: on the host

# name | harness | model | refinement rounds (0 = Base only)
ALL_CONFIGS="
fable52-claudecode|claude|claude-fable-5-2|0
sonnet55-claudecode|claude|claude-sonnet-5-5|3
gpt56sol-codex|codex|gpt-5.6-sol|1
gemini38flash-opencode|opencode|google/gemini-3.8-flash|1
glm53flash-opencode|opencode|z-ai/glm-5.3-flash|1
qwen38max-opencode|opencode|qwen/qwen3.8-max-0902|1
kimik3-opencode|opencode|moonshotai/kimi-k3|1
qwen38flash-opencode|opencode|qwen/qwen3.8-flash|3
dsv41flash-opencode|opencode|deepseek/deepseek-v4.1-flash|3
"
CONFIGS=${CONFIGS:-$(echo "$ALL_CONFIGS" | cut -d'|' -f1 | xargs)}

lite=(); [ "$EVAL" = lite ] && lite=(--lite)
evidence=(--evidence "$EVAL")
batch=(--yes); [ -n "$WORKERS" ] && batch+=(--workers "$WORKERS")

for cfg in $CONFIGS; do
  line=$(echo "$ALL_CONFIGS" | grep "^$cfg|") || { echo "unknown configuration $cfg"; exit 1; }
  IFS='|' read -r name agent model rounds <<< "$line"
  agent_args=(--agent "$agent" --model "$model")
  route=()
  [ "$agent" = opencode ] && route=(--base-url "$BASE_URL" --api-key-env "$API_KEY_ENV")
  [ "$agent" = codex ] && [ "$IN_CONTAINER" = 1 ] && route=(--base-url "$CODEX_BASE_URL" --api-key-env "$CODEX_KEY_ENV")
  [ "$IN_CONTAINER" = 1 ] && route+=(--agent-in-container)
  agent_args+=("${route[@]}")
  echo "=== $name: Base (native agent, 120 min Construction / 60 min Editing) ==="
  gab bench --name "$name" "${agent_args[@]}" --track "$TRACK" "${lite[@]}" "${batch[@]}" || true
  for k in $(seq 1 "$rounds"); do
    echo "=== $name: +Ours R$k (critic review + 60 min revision + experience memory) ==="
    gab refine --name "$name" "${route[@]}" --track "$TRACK" --rounds "$k" --minutes 60 "${batch[@]}" || true
    gab eval --name "$name" --track "$TRACK" --round "$k" "${evidence[@]}" --workers "${WORKERS:-4}" || true
    gab edit-refine --name "$name" "${route[@]}" --track "$TRACK" --rounds "$k" --minutes 60 "${batch[@]}" || true
    gab eval-edit --name "$name" --track "$TRACK" --round "$k" --workers "${WORKERS:-4}" || true
    gab score --name "$name" --round "$k" "${lite[@]}" > /dev/null
  done
done

names=""
for cfg in $CONFIGS; do names+="${names:+,}$cfg$([ "$EVAL" = lite ] && echo @lite)"; done
gab table --names "$names" --table main
echo "per-run reports: <runs_dir>/<name>/report*/main_table.md"
