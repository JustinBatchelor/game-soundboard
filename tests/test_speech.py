import json
import os
from pathlib import Path
import sys
import time
import wave

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
import pytest
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication
from game_soundboard.library import Library, atomic_json
from game_soundboard.speech import SpeechGenerator, SpeechVoices, validate_text, validate_audio


@pytest.fixture(scope="module")
def app():
    yield QApplication.instance() or QApplication([])


def wait_for(predicate, timeout=5):
    deadline = time.monotonic() + timeout
    while not predicate() and time.monotonic() < deadline:
        QTest.qWait(10)
    assert predicate()


@pytest.fixture
def generator(tmp_path, app):
    speech = tmp_path / "speech"
    speech.mkdir()
    ref = speech / "reference.wav"
    ref.write_bytes(b"fixture")
    atomic_json(speech / "voices.json", {"version": 1, "voices": [
        {"id": "trump", "name": "Trump", "engine": "f5", "approved": True,
         "reference": str(ref), "reference_text": "Reference", "source": "fixture", "terms": "fixture"}]})
    script = tmp_path / "worker.py"
    script.write_text('''import sys,json,time,wave
from pathlib import Path
j=json.loads(Path(sys.argv[1]).read_text())
def emit(phase,**kw):print(json.dumps(dict(job_id=j['job_id'],phase=phase,**kw)),flush=True)
emit('generating')
if j['text']=='slow':time.sleep(30)
if j['text']=='oom':
 emit('failed',reason='CUDA out of memory');sys.exit(1)
if j['text']=='crash':sys.exit(9)
if j['text']=='corrupt':Path(j['output']).write_text('broken')
else:
 with wave.open(j['output'],'wb') as w:
  w.setparams((2,2,48000,0,'NONE','not compressed'));w.writeframes(b'\\x01\\x00'*4800)
emit('ready',output=j['output'])
''')
    gen = SpeechGenerator(tmp_path, command_factory=lambda j, f: [sys.executable, str(script), str(f / "job.json")])
    yield gen
    gen.close()


def test_text_and_output_limits(tmp_path):
    assert validate_text(" hello ") == "hello"
    assert len(validate_text("a" * 500)) == 500
    for text in (" ", "a" * 501):
        with pytest.raises(ValueError): validate_text(text)
    p = tmp_path / "long.wav"
    with wave.open(str(p), "wb") as w:
        w.setparams((2,2,48000,0,'NONE','not compressed'))
        w.writeframes(b'\0' * (48000 * 61 * 4))
    with pytest.raises(ValueError, match="60 seconds"): validate_audio(p)


def test_corrupt_model_assets_fail_before_inference(tmp_path):
    from game_soundboard.speech_worker import verify_assets
    (tmp_path / 'f5').mkdir()
    (tmp_path / 'f5/model.safetensors').write_bytes(b'corrupt')
    atomic_json(tmp_path / 'f5-manifest.json', {'files': {'model.safetensors': '0' * 64}})
    with pytest.raises(ValueError, match='checksum mismatch'):
        verify_assets(tmp_path, 'f5')
    atomic_json(tmp_path / 'f5-manifest.json', {'files': {'../outside': '0' * 64}})
    with pytest.raises(ValueError, match='asset path'):
        verify_assets(tmp_path, 'f5')


def test_unapproved_and_missing_voices_do_not_start(generator):
    catalog = generator.voices.path
    data = json.loads(catalog.read_text())
    data["voices"][0]["approved"] = False
    atomic_json(catalog, data)
    with pytest.raises(ValueError, match="listening check"): generator.start("hello", "trump")
    assert not generator.busy and generator.process is None
    data["voices"][0]["approved"] = True
    Path(data["voices"][0]["reference"]).unlink()
    atomic_json(catalog, data)
    with pytest.raises(ValueError, match="missing"): generator.start("hello", "trump")


def test_completed_clip_can_be_saved_and_played_without_engine(generator):
    ready = []
    generator.ready.connect(ready.append)
    generator.start("hello", "trump")
    wait_for(lambda: bool(ready))
    assert not generator.busy and generator.process is None
    assert ready[0]["text"] == "hello"
    lib = Library(generator.root)
    record = lib.prepare(ready[0]["output"], "Trump: hello", "fixture")
    record["generation"] = {"text": ready[0]["text"], "voice_id": "trump", "engine": "f5"}
    lib.commit(record)
    generator.close()
    loaded = Library(generator.root)
    assert loaded.sounds == [record]
    assert record["volume"] == .5 and record["shortcut"] == ""
    validate_audio(generator.root / record["cache"])


@pytest.mark.parametrize("failure", ["oom", "crash", "corrupt"])
def test_failures_preserve_previous_result(generator, failure):
    ready, states = [], []
    generator.ready.connect(ready.append)
    generator.changed.connect(states.append)
    generator.start("hello", "trump")
    wait_for(lambda: not generator.busy)
    original = Path(ready[0]["output"]).read_bytes()
    generator.start(failure, "trump")
    wait_for(lambda: not generator.busy)
    assert states[-1]["phase"] == "failed"
    assert len(ready) == 1
    assert Path(ready[0]["output"]).read_bytes() == original


def test_cancel_repeated_generation_and_stale_events(generator):
    ready = []
    generator.ready.connect(ready.append)
    generator.start("slow", "trump")
    old = generator.job_id
    with pytest.raises(ValueError): generator.start("hello", "trump")
    generator.cancel()
    wait_for(lambda: not generator.busy)
    assert not (generator.jobs / old).exists()
    generator.start("hello", "trump")
    generator._receive({"job_id": old, "phase": "failed"})
    assert generator.busy
    wait_for(lambda: len(ready) == 1)
    assert ready[0]["text"] == "hello"


def test_timeout_and_panel_close_reap_worker(generator):
    generator.timeout = .15
    states = []
    generator.changed.connect(states.append)
    generator.start("slow", "trump")
    wait_for(lambda: not generator.busy)
    assert states[-1]["phase"] == "failed" and "timed out" in states[-1]["reason"]
    generator.timeout = 10
    generator.start("slow", "trump")
    wait_for(lambda: generator.process is not None)
    process = generator.process
    generator.close()
    assert process.poll() is not None and not generator.thread.is_alive()
    assert not generator.jobs.exists()


def test_speech_panel_keeps_result_separate_from_draft_and_confirms_normal(tmp_path, app):
    from PySide6.QtCore import QObject, Signal
    from PySide6.QtWidgets import QWidget, QDialog
    from game_soundboard.speech_ui import SpeechControls
    class ImportSignals(QObject):
        finished = Signal(object, str)
    class Playback:
        def __init__(self): self.actions = []
        def add_temporary(self, path, name): return "temporary"
        def remove(self, ident): self.actions.append(("remove", ident))
        def play(self, ident, preview=False): self.actions.append(("play", ident, preview))
    class Router:
        def __init__(self): self.commands = []
        def command(self, name): self.commands.append(name)
    class Board(QWidget):
        def __init__(self):
            super().__init__()
            self.library = Library(tmp_path)
            self.import_signals = ImportSignals()
            self.playback = Playback()
            self.router = Router()
        def exec_shortcut_dialog(self, dialog):
            dialog.setTextValue("Saved line")
            return QDialog.DialogCode.Accepted
        def submit(self, function, seed):
            record = function()
            self.library.commit(record)
            self.import_signals.finished.emit(record, seed)
    board = Board()
    panel = SpeechControls(board)
    panel.update_audio({"ai": {"phase": "active"}})
    assert board.router.commands == ["voice_normal"] and not panel.audio_confirmed
    panel.update_audio({"ai": {"phase": "active"}})
    assert len(board.router.commands) == 1
    panel.update_audio({"ai": {"phase": "normal"}})
    assert panel.audio_confirmed
    output = tmp_path / "speech/jobs/fixture/speech.wav"
    output.parent.mkdir(parents=True)
    with wave.open(str(output), "wb") as w:
        w.setparams((2,2,48000,0,'NONE','not compressed'));w.writeframes(b'\0' * 1920)
    result = {"job_id": "fixture", "output": str(output), "text": "Original line",
              "voice": {"id": "trump", "name": "Trump — AI impression", "engine": "chatterbox", "source": "fixture"}}
    panel.completed(result)
    assert not board.playback.actions  # generation never auto-plays
    panel.text.setPlainText("Changed draft")
    panel.changed({"phase": "failed", "reason": "CUDA out of memory"})
    assert panel.result["text"] == "Original line"
    panel.play(True)
    panel.play(False)
    assert board.playback.actions == [("play", "temporary", True), ("play", "temporary", False)]
    panel.save()
    saved = Library(tmp_path).sounds[0]
    assert saved["generation"]["text"] == "Original line" and panel.saved
    panel.close_generator()
