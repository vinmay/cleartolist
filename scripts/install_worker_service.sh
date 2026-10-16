#!/usr/bin/env bash
# Install the worker (and optionally Ollama) as systemd user services that start at boot and restart on crash.
#
#   scripts/install_worker_service.sh /path/to/worker.env [/path/to/ollama]
#
# Pass the ollama binary only if Ollama isn't already running as a service. Then, once, so the services run without
# anyone logged in:  loginctl enable-linger "$USER"   (may need sudo)
set -euo pipefail
env_file="$(realpath "${1:?usage: scripts/install_worker_service.sh /path/to/worker.env [/path/to/ollama]}")"
ollama_bin="${2:-}"
repo="$(cd "$(dirname "$0")/.." && pwd)"
units="${XDG_CONFIG_HOME:-$HOME/.config}/systemd/user"
mkdir -p "$units"
[ -r "$env_file" ] || { echo "can't read $env_file" >&2; exit 1; }
uv_dir="$(dirname "$(command -v uv)")"
path="$uv_dir:$HOME/.local/bin:/usr/local/bin:/usr/bin:/bin"

sed -e "s#@REPO@#$repo#g" -e "s#@ENV_FILE@#$env_file#g" -e "s#@PATH@#$path#g" \
  "$repo/scripts/systemd/cleartolist-worker.service" > "$units/cleartolist-worker.service"
if [ -n "$ollama_bin" ]; then
  sed -e "s#@OLLAMA_BIN@#$(realpath "$ollama_bin")#g" -e "s#@OLLAMA_MODELS@#$HOME/.ollama/models#g" \
    "$repo/scripts/systemd/ollama.service" > "$units/ollama.service"
fi
systemctl --user daemon-reload
[ -n "$ollama_bin" ] && systemctl --user enable --now ollama.service
systemctl --user enable --now cleartolist-worker.service
echo "Installed. Status: systemctl --user status cleartolist-worker   Logs: journalctl --user -u cleartolist-worker -f"
