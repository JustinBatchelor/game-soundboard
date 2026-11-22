#!/usr/bin/env python3
"""Install only a per-user launcher; never change audio defaults or enable autostart."""
import os
from pathlib import Path

root = Path(__file__).resolve().parents[1]
applications = Path(os.environ.get('XDG_DATA_HOME', Path.home() / '.local/share')) / 'applications'
applications.mkdir(parents=True, exist_ok=True)
# Desktop Exec quoting uses double quotes and backslash escaping, not shell quoting.
executable = str(root / 'run.sh').replace('\\', '\\\\').replace('"', '\\"').replace('`', '\\`').replace('$', '\\$').replace('%', '%%')
target = applications / 'io.github.local.GameSoundboard.desktop'
target.write_text('[Desktop Entry]\nType=Application\nName=Game Soundboard\n'
                  'Comment=Sound clips and your voice in one virtual microphone\n'
                  f'Exec="{executable}"\nIcon=audio-card\nTerminal=false\n'
                  'Categories=AudioVideo;Audio;\nStartupNotify=true\n')
print(target)
