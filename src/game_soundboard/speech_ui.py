"""Generate first, audition privately, then explicitly send or save a clip."""
import copy
import time
from PySide6.QtCore import QTimer
from PySide6.QtWidgets import (QGroupBox, QVBoxLayout, QHBoxLayout, QComboBox,
    QPlainTextEdit, QPushButton, QLabel, QInputDialog, QDialog)
from .speech import SpeechGenerator, MAX_TEXT


class SpeechControls(QGroupBox):
    def __init__(self, window):
        super().__init__("AI Speech", window)
        self.window = window
        self.generator = SpeechGenerator(window.library.root, self)
        self.result = None
        self.clip_id = None
        self.saved = False
        self.saving = False
        self.audio_confirmed = False
        self.returning_normal = False
        self.started = 0
        layout = QVBoxLayout(self)
        top = QHBoxLayout()
        self.voice = QComboBox()
        self.voice.setPlaceholderText("No approved voices installed")
        self.voice.setAccessibleName("AI speech voice")
        top.addWidget(self.voice, 1)
        self.counter = QLabel("0 / 500")
        top.addWidget(self.counter)
        layout.addLayout(top)
        self.text = QPlainTextEdit()
        self.text.setPlaceholderText("Type a line to generate. Preview listens privately; Play sends it to the game.")
        self.text.setAccessibleName("Words for AI speech")
        self.text.setMinimumHeight(90)
        self.text.setMaximumHeight(115)
        layout.addWidget(self.text)
        row = QHBoxLayout()
        for attr, label, callback in [("generate_button", "Generate", self.generate),
                                      ("cancel_button", "Cancel", self.generator.cancel),
                                      ("preview_button", "Preview", lambda: self.play(True)),
                                      ("play_button", "Play", lambda: self.play(False)),
                                      ("save_button", "Save as sound", self.save)]:
            button = QPushButton(label)
            button.clicked.connect(callback)
            setattr(self, attr, button)
            row.addWidget(button)
        self.generate_button.setObjectName("primary")
        layout.addLayout(row)
        self.result_label = QLabel("No generated clip yet")
        self.result_label.setWordWrap(True)
        layout.addWidget(self.result_label)
        self.status = QLabel("Local generation · GPU released after each line · no automatic playback")
        self.status.setWordWrap(True)
        layout.addWidget(self.status)
        layout.addStretch()
        self.timer = QTimer(self)
        self.timer.setInterval(250)
        self.timer.timeout.connect(self.tick)
        self.generator.changed.connect(self.changed)
        self.generator.ready.connect(self.completed)
        self.text.textChanged.connect(self.buttons)
        self.voice.currentIndexChanged.connect(self.buttons)
        window.import_signals.finished.connect(self.save_finished)
        self.populate()

    def populate(self):
        selected = self.voice.currentData()
        try:
            records = {r["id"]: r for r in self.generator.voices.records()}
        except Exception as exc:
            records = {}
            self.status.setText(str(exc))
        self.voice.clear()
        for ident, name in (("trump", "Donald Trump"), ("musk", "Elon Musk")):
            record = records.get(ident)
            enabled = bool(record and record.get("approved"))
            self.voice.addItem(name + " · AI impression" + ("" if enabled else " · unavailable"), ident)
            self.voice.model().item(self.voice.count() - 1).setEnabled(enabled)
        first = next((i for i in range(self.voice.count()) if self.voice.model().item(i).isEnabled()), -1)
        preferred = self.voice.findData(selected)
        if preferred >= 0 and self.voice.model().item(preferred).isEnabled():
            first = preferred
        self.voice.setCurrentIndex(first)
        path = self.generator.voices.path
        self.catalog_stamp = path.stat().st_mtime_ns if path.exists() else None
        self.buttons()

    def update_audio(self, state):
        path = self.generator.voices.path
        stamp = path.stat().st_mtime_ns if path.exists() else None
        if stamp != self.catalog_stamp:
            self.populate()
        # Old panels could leave live conversion running in the persistent service.
        legacy = state.get("ai", {}).get("phase") in {"loading", "active"}
        self.audio_confirmed = not legacy
        if legacy and not self.returning_normal:
            self.returning_normal = True
            self.window.router.command("voice_normal")
            self.status.setText("Returning the live microphone to Normal before generating speech…")
        elif not legacy:
            self.returning_normal = False
        self.buttons()

    def buttons(self):
        length = len(self.text.toPlainText().strip())
        self.counter.setText(f"{length} / {MAX_TEXT}")
        self.generate_button.setEnabled(bool(self.voice.currentData()) and 0 < length <= MAX_TEXT
                                        and self.audio_confirmed and not self.generator.busy and not self.saving)
        self.cancel_button.setEnabled(self.generator.busy)
        self.preview_button.setEnabled(self.result is not None)
        self.play_button.setEnabled(self.result is not None)
        self.save_button.setEnabled(self.result is not None and not self.saved and not self.saving
                                    and not self.generator.busy)

    def generate(self):
        if not self.audio_confirmed or self.saving:
            return
        try:
            self.started = time.monotonic()
            self.generator.start(self.text.toPlainText(), self.voice.currentData())
            self.timer.start()
        except Exception as exc:
            self.status.setText("Generation failed: " + str(exc))
        self.buttons()

    def tick(self):
        if self.generator.busy:
            phase = "Loading voice" if self.generator.phase == "loading" else "Generating speech"
            self.status.setText(f"{phase}… {time.monotonic() - self.started:.0f}s · normal microphone continues")

    def changed(self, state):
        phase = state["phase"]
        if phase in {"ready", "failed", "cancelled"}:
            self.timer.stop()
        if phase == "failed":
            self.status.setText("Generation failed: " + state.get("reason", "Unknown error"))
        elif phase == "cancelled":
            self.status.setText("Generation cancelled · previous clip retained")
        elif phase == "ready":
            self.status.setText(f'Ready in {state.get("elapsed_seconds", 0):.1f}s · '
                                f'load {state.get("load_seconds", 0):.1f}s · '
                                f'generate {state.get("inference_seconds", 0):.1f}s · GPU released')
        self.buttons()

    def completed(self, result):
        try:
            ident = self.window.playback.add_temporary(result["output"], result["voice"]["name"])
        except Exception as exc:
            self.status.setText("Cannot load generated clip: " + str(exc))
            self.generator.discard(result)
            return
        if self.clip_id:
            self.window.playback.remove(self.clip_id)
        if self.result:
            self.generator.discard(self.result)
        self.result, self.clip_id, self.saved = result, ident, False
        self.result_label.setText(result["voice"]["name"] + ': “' + result["text"] + '”')
        self.buttons()

    def play(self, preview):
        if self.clip_id:
            self.window.playback.play(self.clip_id, preview=preview)

    def save(self):
        if not self.result or self.saving or self.saved or self.generator.busy:
            return
        result = copy.deepcopy(self.result)
        dialog = QInputDialog(self)
        dialog.setWindowTitle("Save generated sound")
        dialog.setLabelText("Button name")
        dialog.setTextValue(result["voice"]["name"].split(" —")[0] + ": " + result["text"][:55])
        if self.window.exec_shortcut_dialog(dialog) != QDialog.DialogCode.Accepted:
            return
        name = dialog.textValue().strip()
        if not name:
            return
        self.saving = True
        self.buttons()
        def prepare():
            record = self.window.library.prepare(result["output"], name, result["voice"]["source"])
            record["generation"] = {"kind": "ai-impression", "text": result["text"],
                                    "voice": result["voice"], "job_id": result["job_id"]}
            return record
        self.window.submit(prepare, "speech-save:" + result["job_id"])

    def save_finished(self, result, seed):
        if not seed.startswith("speech-save:"):
            return
        self.saving = False
        self.saved = not isinstance(result, Exception)
        self.buttons()

    def close_generator(self):
        self.timer.stop()
        if self.clip_id:
            self.window.playback.remove(self.clip_id)
        self.generator.close()
