"""Opt-in process test: panel restarts preserve the microphone and native capture."""
import os
import subprocess
import sys
import tempfile
import time
from pathlib import Path
from game_soundboard.library import Library
from game_soundboard.routing import Routing
from game_soundboard.audio_service import AudioClient
from game_soundboard.starters import STARTERS


def stop(process):
    if process and process.poll() is None:
        process.terminate()
        try:
            process.communicate(timeout=15)
        except subprocess.TimeoutExpired:
            process.kill()
            process.wait()
            raise


with tempfile.TemporaryDirectory(prefix='soundboard-instance-') as tmp:
    lib = Library(tmp)
    lib.seeded = [entry[0] for entry in STARTERS]
    lib.settings['devices_initialized'] = True  # no physical microphone selected
    lib.save()
    env = dict(os.environ, SOUNDBOARD_DATA_DIR=tmp, QT_QPA_PLATFORM='offscreen')
    command = [sys.executable, '-m', 'game_soundboard.app']
    service = subprocess.Popen([sys.executable, '-m', 'game_soundboard.audio_service', '--data-dir', tmp],
                               env=env, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
    process = recorder = None
    router = Routing(tmp)
    client = AudioClient(tmp)
    try:
        deadline = time.monotonic() + 10
        while time.monotonic() < deadline:
            try:
                client.request('status')
                break
            except (OSError, RuntimeError):
                assert service.poll() is None, service.communicate()
                time.sleep(.1)
        else:
            raise AssertionError('Service did not create routes')
        source_index = next(s['index'] for s in router.listing('sources') if s['name'] == router.source)
        recorder = subprocess.Popen(['pw-record', '--target', router.source, '--properties',
            '{ application.name = SoundboardPanelLifecycleTest node.dont-fallback = true }',
            '--raw', '--format=s16', '--rate=48000', '--channels=1', '-'],
            stdout=subprocess.DEVNULL, stderr=subprocess.PIPE)
        capture_id = None
        for launch in range(2):
            process = subprocess.Popen(command, env=env, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
            deadline = time.monotonic() + 8
            while not (Path(tmp) / 'app.lock').exists() and time.monotonic() < deadline:
                assert process.poll() is None, process.communicate()
                time.sleep(.1)
            time.sleep(1)
            second = subprocess.run(command, env=env, capture_output=True, text=True, timeout=8)
            assert second.returncode == 0, second.stderr
            assert process.poll() is None
            stop(process)
            assert process.returncode == 0
            assert not (Path(tmp) / 'app.lock').exists()
            assert next(s['index'] for s in router.listing('sources') if s['name'] == router.source) == source_index
            capture = next(a for a in router.capture_apps() if a['name'] == 'SoundboardPanelLifecycleTest')
            assert capture['connected']
            if capture_id is None:
                capture_id = capture['index']
            assert capture['index'] == capture_id
            assert client.request('status')['persistent']
        print('PASS: single instance and two panel closes/reopens preserve device ID and native capture', flush=True)
        # A second daemon must not remove the owner's graph or control socket.
        duplicate = subprocess.run([sys.executable, '-m', 'game_soundboard.audio_service', '--data-dir', tmp],
                                   env=env, capture_output=True, text=True, timeout=5)
        assert duplicate.returncode == 0, duplicate.stderr
        assert client.request('ping')['pid'] == service.pid
        print('PASS: duplicate service exits without disturbing audio', flush=True)
    finally:
        stop(process)
        stop(recorder)
        stop(service)
    assert not any(router.owned(m) for m in router.listing('modules'))
    assert not client.path.exists()
    print('PASS: explicit service stop removes owned routes and socket', flush=True)
