"""Opt-in local inference/lifecycle test. No microphone or playback routes."""
import json
import argparse
import tempfile
import time
from pathlib import Path
from PySide6.QtWidgets import QApplication
from game_soundboard.library import Library, atomic_json, data_directory
from game_soundboard.starters import STARTERS
from game_soundboard.ui import Window

app = QApplication([])
parser = argparse.ArgumentParser()
parser.add_argument("--voice", choices=("trump", "musk"))
args = parser.parse_args()


def pump_until(predicate, timeout=185):
    deadline = time.monotonic() + timeout
    while not predicate() and time.monotonic() < deadline:
        app.processEvents()
        time.sleep(.02)
    assert predicate(), "Timed out waiting for speech lifecycle"


with tempfile.TemporaryDirectory(prefix="soundboard-speech-test-") as tmp:
    root = Path(tmp)
    lib = Library(root)
    lib.seeded = [s[0] for s in STARTERS]
    lib.save()
    (root / "speech").mkdir()
    catalog = json.loads((data_directory() / "speech/voices.json").read_text())
    atomic_json(root / "speech/voices.json", catalog)
    window = Window(lib, live=False)
    panel = window.speech_controls
    panel.update_audio({"ai": {"phase": "normal"}})
    window.tabs.setCurrentIndex(1)
    window.show()
    try:
        for voice in catalog["voices"]:
            if not voice.get("approved") or (args.voice and voice["id"] != args.voice):
                continue
            panel.voice.setCurrentIndex(panel.voice.findData(voice["id"]))
            text = "Good round, team. Let's save our equipment and try again."
            panel.text.setPlainText(text)
            panel.generate()
            assert panel.generator.busy
            panel.text.setPlainText("This changed draft must not replace the generated line.")
            pump_until(lambda: not panel.generator.busy)
            assert panel.generator.phase == "ready", panel.status.text()
            assert panel.result["text"] == text
            assert panel.generator.process is None
            assert not any(e.isPlaying() for e in window.playback.effects.values())
            print("PASS", voice["id"], panel.status.text(), flush=True)
        # Stop All cancels before playback and retains the last successful result.
        previous = panel.result
        panel.generate()
        window.stop_all()
        pump_until(lambda: not panel.generator.busy, 8)
        assert panel.result is previous and panel.generator.phase == "cancelled"
        panel.generate()
        pump_until(lambda: panel.generator.process is not None, 8)
        process = panel.generator.process
        window.close()
        assert process.poll() is not None and not panel.generator.thread.is_alive()
        assert not panel.generator.jobs.exists()
        print("PASS: Stop All and panel close reap real workers; no autoplay", flush=True)
    finally:
        window.close()
