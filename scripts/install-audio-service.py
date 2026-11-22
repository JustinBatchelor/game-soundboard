#!/usr/bin/env python3
"""Install the user's persistent microphone service; no system-wide changes."""
import os
from pathlib import Path
import subprocess
from game_soundboard.audio_service import UNIT
from game_soundboard.library import data_directory


def quote(value):
    value = str(value)
    if "\n" in value or "\r" in value:
        raise ValueError("Newlines in installation paths are unsupported")
    return '"' + value.replace('\\', '\\\\').replace('"', '\\"').replace('%', '%%') + '"'


root = Path(__file__).resolve().parents[1]
directory = Path(os.environ.get('XDG_CONFIG_HOME', Path.home() / '.config')) / 'systemd/user'
directory.mkdir(parents=True, exist_ok=True)
unit = directory / UNIT
unit.write_text(
    '[Unit]\nDescription=Game Soundboard persistent microphone\nAfter=pipewire-pulse.service\n\n'
    '[Service]\nType=simple\n'
    f'Environment={quote("PYTHONPATH=" + str(root / "src"))}\n'
    'Environment=PYTHONUNBUFFERED=1\n'
    f'ExecStart={quote(root / ".venv/bin/python")} -m game_soundboard.audio_service '
    f'--data-dir {quote(data_directory().absolute())}\n'
    'Restart=on-failure\nRestartSec=2\nTimeoutStopSec=15\n\n'
    '[Install]\nWantedBy=default.target\n')
subprocess.run(['systemd-analyze', '--user', 'verify', str(unit)], check=True)
subprocess.run(['systemctl', '--user', 'daemon-reload'], check=True)
subprocess.run(['systemctl', '--user', 'enable', '--now', UNIT], check=True)
print(f'Persistent microphone installed: {unit}')
