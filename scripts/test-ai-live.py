"""Opt-in real worker test. Synthetic private audio only; no game/physical output."""
import argparse
import json
import os
from pathlib import Path
import shutil
import signal
import subprocess
import tempfile
import time

from game_soundboard.audio_service import AudioClient
from game_soundboard.library import Library, ffmpeg_binary
from game_soundboard.routing import Routing, pactl_binary
from game_soundboard.starters import STARTERS

parser = argparse.ArgumentParser()
parser.add_argument('model_directory', type=Path)
parser.add_argument('--seconds', type=int, default=15)
parser.add_argument('--crash-worker', action='store_true')
parser.add_argument('--panel-cycle', action='store_true')
parser.add_argument('--other-model', type=Path)
args = parser.parse_args()
processes, extras = [], []
with tempfile.TemporaryDirectory(prefix='soundboard-ai-live-') as tmp:
    root = Path(tmp)
    router = Routing(root)
    defaults = (router.call('get-default-source'), router.call('get-default-sink'))
    client = AudioClient(root)
    try:
        feed = router.prefix + '_testfeed'
        # Test source is intentionally outside gsb_ filtering (physical-looking).
        mic = 'synthetic_ai_' + router.prefix
        extras.append(int(router.call('load-module', 'module-null-sink', 'sink_name=' + feed, 'rate=48000')))
        extras.append(int(router.call('load-module', 'module-remap-source', 'master=' + feed + '.monitor', 'source_name=' + mic)))
        lib = Library(root)
        lib.seeded = [s[0] for s in STARTERS]
        lib.settings.update(devices_initialized=True, microphone=mic, output='', mic_volume=.63)
        lib.save()
        record = json.loads((args.model_directory / 'model.json').read_text())
        target = root / 'models' / record['id']
        shutil.copytree(args.model_directory, target)
        service = subprocess.Popen([os.sys.executable, '-m', 'game_soundboard.audio_service', '--data-dir', str(root)],
                                   stdout=subprocess.DEVNULL, stderr=subprocess.PIPE)
        processes.append(service)
        for _ in range(80):
            if client.path.exists(): break
            time.sleep(.1)
        client.request('refresh')
        source = next(s['index'] for s in router.listing('sources') if s['name'] == router.source)
        pacat = str(Path(pactl_binary()).with_name('pacat'))
        generator = subprocess.Popen([ffmpeg_binary(), '-v', 'error', '-re', '-f', 'lavfi', '-i', 'sine=frequency=220:sample_rate=48000',
                                      '-f', 's16le', '-ac', '1', '-'], stdout=subprocess.PIPE, stderr=subprocess.DEVNULL)
        processes.append(generator)
        player = subprocess.Popen([pacat, '--playback', '--device=' + feed, '--raw', '--rate=48000', '--channels=1',
                                   '--format=s16le', '--latency-msec=20'], stdin=generator.stdout)
        generator.stdout.close()
        processes.append(player)
        capture = subprocess.Popen(['pw-record', '--target', router.source, '--rate', '48000', '--channels', '2',
                                    str(root / 'capture.wav')], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        processes.append(capture)
        before = client.request('ai_activate', model=record['id'])
        assert before['ai']['active'] == 'normal'
        deadline = time.monotonic() + 65
        while time.monotonic() < deadline:
            state = client.request('status')
            if state['ai']['phase'] in ('active', 'failed'): break
            time.sleep(.2)
        if state['ai']['phase'] != 'active':
            raise AssertionError(str(state['ai']) + '\n' + (root / 'ai-worker.log').read_text()[-6000:])
        print('READY', state['ai'], flush=True)
        capture_id = next(a['index'] for a in state['capture_apps'] if a['name'] == 'pw-record')
        deadline = time.monotonic() + args.seconds
        while time.monotonic() < deadline:
            state = client.request('status')
            assert state['ai']['phase'] == 'active', state['ai']
            assert next(s['index'] for s in router.listing('sources') if s['name'] == router.source) == source
            time.sleep(.25)
        print('ACTIVE', state['ai'], flush=True)
        if args.panel_cycle:
            env = dict(os.environ, SOUNDBOARD_DATA_DIR=str(root), QT_QPA_PLATFORM='offscreen')
            panel = subprocess.Popen([os.sys.executable, '-m', 'game_soundboard.app'], env=env,
                                     stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
            processes.append(panel)
            time.sleep(3)
            assert panel.poll() is None
            panel.terminate(); panel.wait(timeout=15)
            assert client.request('status')['ai']['phase'] == 'active'
            print('PASS: closing the panel leaves AI conversion active', flush=True)
        if args.other_model:
            other = json.loads((args.other_model / 'model.json').read_text())
            shutil.copytree(args.other_model, root / 'models' / other['id'])
            switched = client.request('ai_activate', model=other['id'])
            assert switched['ai']['active'] == 'normal'
            deadline = time.monotonic() + 65
            while time.monotonic() < deadline:
                state = client.request('status')
                if state['ai']['phase'] in ('active', 'failed'): break
                time.sleep(.2)
            assert state['ai']['active'] == other['id'], state['ai']
            print('PASS: switching models retains Normal during loading and activates the new worker', flush=True)
        # Read the child PID from /proc rather than exposing a kill command in IPC.
        if args.crash_worker:
            children = Path(f'/proc/{service.pid}/task/{service.pid}/children').read_text().split()
            workers = [int(pid) for pid in children if b'ai_worker.py' in Path(f'/proc/{pid}/cmdline').read_bytes()]
            assert len(workers) == 1, workers
            lib.settings['mic_muted'] = True
            lib.save()
            client.request('refresh')
            os.kill(workers[0], signal.SIGKILL)
            deadline = time.monotonic() + 3
            while time.monotonic() < deadline:
                state = client.request('status')
                if state['ai']['phase'] == 'failed': break
                time.sleep(.1)
            assert state['ai']['phase'] == 'failed' and state['ai']['active'] == 'normal', state['ai']
            voice_mix = next(m['index'] for m in router.listing('modules') if m['name'] == 'module-loopback'
                             and f'source={router.voice}.monitor sink={router.mix}' in m['argument'])
            streams = [s for s in router.listing('sink-inputs') if str(s.get('owner_module')) == str(voice_mix)]
            assert streams and all(s['mute'] for s in streams)
            print('PASS: killed AI worker restores Normal within three seconds while retaining mute', flush=True)
        # Cancellation must preserve the microphone node and native capture.
        state = client.request('ai_cancel')
        assert state['ai']['active'] == 'normal'
        assert next(s['index'] for s in router.listing('sources') if s['name'] == router.source) == source
        assert any(a['index'] == capture_id and a['connected'] for a in state['capture_apps'])
        assert defaults == (router.call('get-default-source'), router.call('get-default-sink'))
        print('PASS: real RVC worker, bounded streaming, cancellation, persistent device/capture, unchanged defaults', flush=True)
    finally:
        for process in reversed(processes):
            if process.poll() is None:
                process.terminate()
                try: process.wait(timeout=8)
                except subprocess.TimeoutExpired: process.kill(); process.wait()
        for module in reversed(extras):
            try: router.call('unload-module', str(module))
            except RuntimeError: pass
        router.cleanup()
