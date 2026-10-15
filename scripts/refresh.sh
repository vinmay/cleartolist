#!/usr/bin/env bash
# Pull new/updated recalls and update the indexes incrementally. Safe to run from cron, e.g.
#   17 3 * * *  /path/to/cleartolist/scripts/refresh.sh >> /path/to/cleartolist/data/refresh.log 2>&1
set -euo pipefail
cd "$(dirname "$0")/.."
uv run python -m ingest.cpsc --incremental
uv run python -m index.build
