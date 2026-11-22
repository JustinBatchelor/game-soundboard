"""Live window smoke test. Does not play clips, assign shortcuts, or change audio defaults."""
import tempfile
import os
import signal
import time
import wave
from pathlib import Path
from PySide6.QtCore import QTimer
from PySide6.QtWidgets import QApplication
from game_soundboard import APP_ID
from game_soundboard.audio_service import AudioClient
from game_soundboard.library import Library
from game_soundboard.ui import Window
from game_soundboard.starters import STARTERS
from game_soundboard.routing import Routing

app = QApplication([])
app.setDesktopFileName(APP_ID)
# Never open or mutate the user's personal library, even if their app is running.
workspace = tempfile.TemporaryDirectory(prefix="soundboard-ui-test-")
root = Path(workspace.name)
source = root / "fixture.wav"
with wave.open(str(source), "wb") as audio:
    audio.setnchannels(1)
    audio.setsampwidth(2)
    audio.setframerate(48000)
    audio.writeframes(b"\0\0" * 4800)
library = Library(root / "library")
devices = Routing(root)
sources, sinks = devices.devices()
library.settings.update(devices_initialized=True,
                        microphone=sources[0]['name'] if sources else '',
                        output=sinks[0]['name'] if sinks else '')
library.seeded = [entry[0] for entry in STARTERS]
for name in ("Test tone", "Second tone", "Bomb search fixture"):
    library.commit(library.prepare(source, name))
window = Window(library)
window.show()
failures = []
def verify():
    try:
        assert 'No app is using Soundboard Microphone' in window.audio_status.text(), window.audio_status.text()
        assert window.voice_effect.count() == 5  # normal + four generic effects
        assert window.speech_controls.voice.count() == 2
        assert not window.speech_controls.generate_button.isEnabled()  # no approved voices in test library
        assert not window.voice_monitor.isChecked()
        window.voice_effect.setCurrentIndex(window.voice_effect.findData('robot'))
        assert Library(library.root).settings['voice_effect'] == 'robot'
        window.voice_effect.setCurrentIndex(0)
        assert window.playback.clip_device is not None
        assert window.playback.output_device is not None
        assert window.library.settings['microphone']
        window.grab().save('/tmp/game-soundboard-final.png')
        window.search.setText('bomb')
        assert len(window.pads) == 1
        window.search.clear()
        assert len(window.pads) == 3
        print('PASS: live panel, three sound cards, search, persistent device selection and audio outputs', flush=True)
    except Exception as exc:
        import traceback
        traceback.print_exc()
        failures.append(str(exc))
    finally:
        window.close()
QTimer.singleShot(5000, verify)
app.exec()
client = AudioClient(library.root)
pid = client.request("ping")["pid"]
os.kill(pid, signal.SIGTERM)
for _ in range(50):
    if not client.path.exists():
        break
    time.sleep(.1)
assert not client.path.exists(), "Temporary audio service did not stop"
workspace.cleanup()
if failures:
    raise RuntimeError(failures)
