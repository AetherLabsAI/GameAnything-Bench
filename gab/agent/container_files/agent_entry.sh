#!/bin/bash
# Inside a trial container: start the model-socket forwarder (if a model route is configured) and the image-generation
# forwarder (if OPENROUTER_API_KEY is set on the host), then the harness.
# Files the agent makes are writable by the host user afterwards (gab chowns them back when the run ends).
umask 000
mkdir -p "$HOME" "${TMPDIR:-/tmp}"
if [ -n "${GAB_LLM_PORT:-}" ] && [ -S /run/gab/llm.sock ]; then
  python3 /run/gab/llm_forward.py "$GAB_LLM_PORT" /run/gab/llm.sock > "${TMPDIR:-/tmp}/gab-llm-forward.log" 2>&1 &
  for i in $(seq 1 50); do (exec 3<>/dev/tcp/127.0.0.1/$GAB_LLM_PORT) 2>/dev/null && break; sleep 0.1; done
fi
if [ -n "${GAB_IMAGE_PORT:-}" ] && [ -S /run/gab/images.sock ]; then
  python3 /run/gab/llm_forward.py "$GAB_IMAGE_PORT" /run/gab/images.sock > "${TMPDIR:-/tmp}/gab-image-forward.log" 2>&1 &
  for i in $(seq 1 50); do (exec 3<>/dev/tcp/127.0.0.1/$GAB_IMAGE_PORT) 2>/dev/null && break; sleep 0.1; done
fi
exec "$@"
