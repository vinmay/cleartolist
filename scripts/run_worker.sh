#!/usr/bin/env bash
# Start the self-hosted GPU worker with settings from a private env file (never commit it).
#   scripts/run_worker.sh /path/to/worker.env
# The file needs CTL_SERVER_URL and CTL_WORKER_SECRET (see .env.example). Ollama must be running.
set -euo pipefail
env_file="${1:?usage: scripts/run_worker.sh /path/to/worker.env}"
[ -r "$env_file" ] || { echo "can't read $env_file" >&2; exit 1; }
set -a
# shellcheck disable=SC1090
. "$env_file"
set +a
curl -fsS "${OLLAMA_HOST_URL:-http://127.0.0.1:11434}/api/tags" >/dev/null || { echo "Ollama isn't running" >&2; exit 1; }
cd "$(dirname "$0")/.."
exec uv run python -m worker.worker
