#!/usr/bin/env bash
set -euo pipefail
SOUNDBOARD_ROOT="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
export PYTHONPATH="$SOUNDBOARD_ROOT/src${PYTHONPATH:+:$PYTHONPATH}"
exec "$SOUNDBOARD_ROOT/.venv/bin/python" -m game_soundboard.app "$@"
