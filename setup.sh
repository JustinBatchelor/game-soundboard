#!/usr/bin/env bash
set -euo pipefail
SOUNDBOARD_ROOT="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
cd "$SOUNDBOARD_ROOT"
if command -v uv >/dev/null; then
    uv venv --python 3.12 .venv
    uv pip install --python .venv/bin/python -e '.[test]'
else
    python3 -m venv .venv
    .venv/bin/python -m pip install -e '.[test]'
fi
.venv/bin/python scripts/install-desktop.py
if ! command -v pactl >/dev/null && ! test -x .runtime/usr/bin/pactl; then
    echo 'Install the audio control utility: sudo apt install pulseaudio-utils'
fi
printf '\nLaunch Game Soundboard from your applications menu, or run ./run.sh\n'
