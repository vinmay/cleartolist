#!/usr/bin/env bash
# One-command local setup: Python deps, Ollama models, recall ingest, indexes.
set -euo pipefail
cd "$(dirname "$0")/.."

VISION_MODEL="${CTL_VISION_MODEL:-gemma4:e4b}"
TEXT_MODEL="${CTL_TEXT_MODEL:-embeddinggemma:300m}"

command -v uv >/dev/null || { echo "Install uv first: https://docs.astral.sh/uv/"; exit 1; }
command -v ollama >/dev/null || { echo "Install Ollama first: https://ollama.com/download"; exit 1; }

echo "==> Python dependencies"
uv sync

echo "==> Ollama models ($VISION_MODEL, $TEXT_MODEL)"
curl -sf http://127.0.0.1:11434/api/version >/dev/null || { echo "Start Ollama first (ollama serve)"; exit 1; }
ollama pull "$VISION_MODEL"
ollama pull "$TEXT_MODEL"

echo "==> CPSC recalls since 2015 + recall photos (incremental on re-run)"
if [ -f data/recalls.db ]; then
  uv run python -m ingest.cpsc --incremental
else
  uv run python -m ingest.cpsc --since 2015-01-01
fi

echo "==> Image and text indexes"
uv run python -m index.build

echo "Done. Start the app with:  uv run uvicorn app.main:app --port 8000"
