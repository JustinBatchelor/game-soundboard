"""Opt-in integration test using synthetic tones and isolated virtual devices only.
Never captures the physical microphone or sends audio to physical headphones.
"""
import math
import struct
import subprocess
import tempfile
import time
import wave
from pathlib import Path
from PySide6.QtWidgets import QApplication
from PySide6.QtMultimedia import QMediaDevices, QSoundEffect
from PySide6.QtCore import QUrl
from game_soundboard.library import Library, DEFAULTS
from game_soundboard.playback import Playback
from game_soundboard.routing import Routing, pactl_binary

app = QApplication([])
router = Routing('game-soundboard-audio-test')
extras = []
players = []
recorders = []


def pump(seconds):
    end = time.monotonic() + seconds
    while time.monotonic() < end:
        app.processEvents()
        time.sleep(.005)


def tone(path, frequency):
    with wave.open(str(path), 'wb') as file:
        file.setnchannels(2)
        file.setsampwidth(2)
        file.setframerate(48000)
        data = b''.join(struct.pack('<hh', *([int(6000 * math.sin(2 * math.pi * frequency * i / 48000))] * 2))
                        for i in range(48000 * 4))
        file.writeframes(data)


def spectrum(path):
    data = path.read_bytes()
    usable = len(data) // 4 * 4
    data = data[max(0, usable - 48000):usable]  # last quarter second, stereo int16
    samples = struct.unpack('<' + 'h' * (len(data)//2), data)[::2]
    assert samples, 'No recorded samples'
    values = {}
    for frequency in [440, 660, 880, 1320]:
        real = sum(x * math.cos(2 * math.pi * frequency * i / 48000) for i, x in enumerate(samples))
        imag = sum(x * math.sin(2 * math.pi * frequency * i / 48000) for i, x in enumerate(samples))
        values[frequency] = round(2 * math.hypot(real, imag) / len(samples), 1)
    return values


try:
    with tempfile.TemporaryDirectory(prefix='soundboard-live-test-') as tmp:
        root = Path(tmp)
        # Physical-looking test source is a remapped null-sink monitor, not a microphone.
        for name in ['gsbtest_micfeed', 'gsbtest_headphones']:
            extras.append(int(router.call('load-module', 'module-null-sink', f'sink_name={name}', 'rate=48000')))
        extras.append(int(router.call('load-module', 'module-remap-source',
            'master=gsbtest_micfeed.monitor', 'source_name=synthetic_soundboard_test_mic')))
        settings = dict(DEFAULTS, microphone='synthetic_soundboard_test_mic', output='gsbtest_headphones', monitor=True)
        router.reconcile(settings)
        lib = Library(root / 'library')
        lib.settings.update(settings)
        records = []
        for frequency in [440, 660, 1320]:
            path = root / f'{frequency}.wav'
            tone(path, frequency)
            record = lib.prepare(path)
            lib.commit(record)
            records.append(record)
        playback = Playback(lib)
        playback.routes(router.clips, 'gsbtest_headphones')
        pump(.5)
        devices = {bytes(d.id()).decode(): d for d in QMediaDevices.audioOutputs()}
        mic_file = root / 'voice.wav'
        tone(mic_file, 880)
        mic = QSoundEffect(devices['gsbtest_micfeed'])
        mic.setSource(QUrl.fromLocalFile(str(mic_file)))
        mic.setLoopCount(QSoundEffect.Infinite.value)
        mic.setVolume(.5)
        players.append(mic)
        # Preload preview as well as the two clip effects.
        playback.effect(records[2], True)
        pump(.5)
        assert all(e.status() == QSoundEffect.Ready for e in list(playback.effects.values()) + list(playback.preview_effects.values()) + [mic])
        for name, source in [('mix', router.source), ('headphones', 'gsbtest_headphones.monitor')]:
            path = root / f'{name}.pcm'
            file = path.open('wb')
            process = subprocess.Popen([str(Path(pactl_binary()).with_name('parec')), '--raw', '--format=s16le',
                '--rate=48000', '--channels=2', '--latency-msec=20', '--device=' + source], stdout=file, stderr=subprocess.PIPE)
            recorders.append((process, file))
        pump(.3)
        start_offset = (root / 'mix.pcm').stat().st_size
        started = time.monotonic()
        playback.play(records[0]['id'])
        while time.monotonic() - started < 1:
            pump(.005)
            with (root / 'mix.pcm').open('rb') as capture:
                capture.seek(start_offset)
                captured = capture.read()
            if any(captured):
                break
        else:
            raise AssertionError('No triggered audio received')
        print(f'Observed warm trigger-to-capture upper bound: {(time.monotonic()-started)*1000:.1f} ms (synthetic devices, current load)', flush=True)
        playback.play(records[0]['id'])  # same effect restarts, rather than adding a voice
        assert len(playback.effects) == len(records)
        mic.play()
        playback.play(records[0]['id'])
        playback.play(records[1]['id'])
        playback.play(records[2]['id'], preview=True)
        pump(1.1)
        mix = spectrum(root / 'mix.pcm')
        headphones = spectrum(root / 'headphones.pcm')
        print('Mix spectrum:', mix, flush=True)
        print('Headphone spectrum:', headphones, flush=True)
        assert all(mix[f] > 100 for f in [440, 660, 880]) and mix[1320] < 30
        assert all(headphones[f] > 100 for f in [440, 660, 1320]) and headphones[880] < 30
        print('PASS: overlapping clips + voice in virtual mic; preview excluded; no voice in headphones', flush=True)
        settings['mic_muted'] = True
        router.reconcile(settings)
        pump(.5)
        assert spectrum(root / 'mix.pcm')[880] < 30
        settings['mic_muted'] = False
        router.reconcile(settings)
        playback.stop_all()
        pump(.5)
        mix = spectrum(root / 'mix.pcm')
        headphones = spectrum(root / 'headphones.pcm')
        assert mix[880] > 100 and all(mix[f] < 30 for f in [440, 660, 1320])
        assert all(v < 30 for v in headphones.values())
        print('PASS: voice mute and Stop All preserve intended routes', flush=True)
        playback.stop_all()
        mic.stop()
finally:
    for process, file in recorders:
        process.terminate()
        process.wait(timeout=3)
        file.close()
    for player in players:
        player.stop()
    router.cleanup()
    for module in reversed(extras):
        router.call('unload-module', str(module))
    print('Temporary test routes removed', flush=True)
