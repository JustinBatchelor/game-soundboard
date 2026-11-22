"""Live window smoke test. Does not play clips, assign shortcuts, or change audio defaults."""
import tempfile
import wave
from pathlib import Path
from PySide6.QtCore import QTimer
from PySide6.QtWidgets import QApplication
from game_soundboard import APP_ID
from game_soundboard.library import Library
from game_soundboard.ui import Window
from game_soundboard.starters import STARTERS

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
library.seeded = [entry[0] for entry in STARTERS]
for name in ("Test tone", "Second tone", "Bomb search fixture"):
    library.commit(library.prepare(source, name))
window = Window(library)
window.show()
failures = []
def verify():
    try:
        assert 'No app is using Soundboard Microphone' in window.audio_status.text(), window.audio_status.text()
        assert window.voice_effect.count() == 5
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
        failures.append(str(exc))
    finally:
        window.close()
QTimer.singleShot(5000, verify)
app.exec()
workspace.cleanup()
if failures:
    raise RuntimeError(failures)
