"""Opt-in live process test: second launch activates, SIGTERM cleans routing."""
import os
import subprocess
import sys
import tempfile
import time
from pathlib import Path
from game_soundboard.library import Library
from game_soundboard.routing import Routing
from game_soundboard.starters import STARTERS

with tempfile.TemporaryDirectory(prefix='soundboard-instance-') as tmp:
    lib = Library(tmp)
    lib.seeded = [entry[0] for entry in STARTERS]
    lib.settings['devices_initialized'] = True
    lib.save()
    env = dict(os.environ, SOUNDBOARD_DATA_DIR=tmp, QT_QPA_PLATFORM='offscreen')
    command = [sys.executable, '-m', 'game_soundboard.app']
    process = subprocess.Popen(command, env=env, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
    try:
        deadline = time.monotonic() + 8
        router = Routing(tmp)
        while time.monotonic() < deadline:
            if any(router.owned(m) for m in router.listing('modules')):
                break
            assert process.poll() is None, process.communicate()
            time.sleep(.1)
        else:
            raise AssertionError('App did not create audio routes')
        second = subprocess.run(command, env=env, capture_output=True, text=True, timeout=8)
        assert second.returncode == 0, second.stderr
        assert process.poll() is None
        print('PASS: second launch exits and original instance stays running', flush=True)
    finally:
        process.terminate()
        try:
            out, err = process.communicate(timeout=15)
        except subprocess.TimeoutExpired:
            process.kill()
            process.wait()
            raise
    assert process.returncode == 0, err.decode()
    assert not any(router.owned(m) for m in router.listing('modules'))
    assert not (Path(tmp) / 'app.lock').exists()
    print('PASS: graceful SIGTERM, unlocked library and removed audio routes', flush=True)
