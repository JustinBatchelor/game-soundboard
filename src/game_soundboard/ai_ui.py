"""Panel-only controls; conversion belongs to the persistent service."""
from PySide6.QtCore import QObject, Signal
from PySide6.QtWidgets import (QWidget, QVBoxLayout, QHBoxLayout, QLabel, QPushButton,
    QSpinBox, QComboBox, QDialog, QFormLayout, QLineEdit, QFileDialog, QDialogButtonBox,
    QMessageBox)
from .ai_models import ModelLibrary, NAMED_PRESETS


class Completion(QObject):
    done = Signal(object)


class AIControls(QWidget):
    def __init__(self, window):
        super().__init__(window)
        self.window = window
        self.models = ModelLibrary(window.library.root)
        self.state = {}
        self.completion = Completion(self)
        self.completion.done.connect(self.imported)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        row = QHBoxLayout()
        row.addWidget(QLabel("AI pitch"))
        self.pitch = QSpinBox()
        self.pitch.setRange(-12, 12)
        self.pitch.setSuffix(" semitones")
        self.pitch.setValue(window.library.settings["ai_pitch"])
        row.addWidget(self.pitch)
        self.profile = QComboBox()
        self.profile.addItem("Responsive", "responsive")
        self.profile.addItem("Balanced", "balanced")
        self.profile.setCurrentIndex(max(0, self.profile.findData(window.library.settings["ai_profile"])))
        row.addWidget(self.profile)
        manage = QPushButton("AI models…")
        manage.clicked.connect(self.manage)
        row.addWidget(manage)
        self.retry = QPushButton("Apply / Retry AI")
        self.retry.clicked.connect(self.activate_selected)
        row.addWidget(self.retry)
        normal = QPushButton("Return to Normal")
        normal.clicked.connect(window.return_normal)
        row.addWidget(normal)
        layout.addLayout(row)
        self.status = QLabel("AI off · Normal on service startup · model weights stay local")
        self.status.setWordWrap(True)
        self.status.setObjectName("muted")
        layout.addWidget(self.status)
        self.populate()

    def populate(self):
        combo = self.window.voice_effect
        selected = combo.currentData()
        combo.blockSignals(True)
        # Generic entries precede all AI entries.
        while combo.count() and str(combo.itemData(combo.count() - 1)).startswith("ai:"):
            combo.removeItem(combo.count() - 1)
        records = self.models.records()
        available = {r.get("preset") for r in records if r.get("auditioned")}
        for record in records:
            suffix = " · AI impression" if record.get("auditioned") else " · AI impression (audition)"
            combo.addItem(record["name"] + suffix, "ai:" + record["id"])
        for key, name, source, reason in NAMED_PRESETS:
            if key in available:
                continue
            combo.addItem(name + " · unavailable", "ai:unavailable:" + key)
            item = combo.model().item(combo.count() - 1)
            item.setEnabled(False)
            item.setToolTip(reason + "\n" + source)
        combo.setCurrentIndex(max(0, combo.findData(selected)))
        combo.blockSignals(False)

    def choose(self, ident):
        try:
            record = self.models.get(ident)
            values = record["tuning"]
            self.pitch.setValue(values["pitch"])
            self.profile.setCurrentIndex(max(0, self.profile.findData(values["profile"])))
            self.activate(ident)
        except Exception as exc:
            self.window.notice(str(exc))

    def activate_selected(self):
        selection = self.window.voice_effect.currentData() or ""
        ident = selection[3:] if selection.startswith("ai:") else self.window.library.settings["ai_model"]
        if ident:
            self.activate(ident)
        else:
            self.window.notice("Import and select an AI model first")

    def activate(self, ident):
        try:
            pitch, profile = self.pitch.value(), self.profile.currentData()
            self.models.set_tuning(ident, pitch, profile)
            self.window.library.settings.update(ai_model=ident, ai_pitch=pitch, ai_profile=profile, voice_effect="normal")
            self.window.library.save()
            if self.window.router:
                self.window.router.command("ai_activate", model=ident, pitch=pitch, profile=profile)
                self.status.setText("AI loading requested · Normal voice continues until ready")
            else:
                self.status.setText("Preview mode · AI activation disabled")
        except Exception as exc:
            self.window.notice("AI selection failed: " + str(exc))

    def update_state(self, state):
        self.state = state.get("ai", {})
        ai = self.state
        phase = ai.get("phase", "normal")
        reason = ai.get("reason", "")
        labels = {"normal": "AI off", "loading": "Loading AI · Normal voice active",
                  "active": "AI impression active", "failed": "AI failed · Normal voice restored"}
        text = labels.get(phase, phase)
        if reason:
            text += " · " + reason + " · choose Apply / Retry AI to retry"
        if "processing_ms" in ai:
            text += (f' · processing {ai["processing_ms"]:.0f} ms (p95 {ai.get("processing_p95_ms", 0):.0f} ms)'
                     f' · dropped blocks {ai.get("overruns", 0)} · end-to-end latency not measured')
        self.status.setText(text)
        requested = ai.get("requested", "normal")
        value = "ai:" + requested if phase in ("active", "loading", "failed") else state.get("active_effect", "normal")
        combo = self.window.voice_effect
        combo.blockSignals(True)
        combo.setCurrentIndex(max(0, combo.findData(value)))
        combo.blockSignals(False)
        self.retry.setEnabled(bool(self.window.library.settings["ai_model"]))

    def manage(self):
        dialog = QDialog(self)
        dialog.setWindowTitle("Local AI voice models")
        dialog.setMinimumWidth(650)
        form = QFormLayout(dialog)
        choices = QComboBox()
        detail = QLabel()
        detail.setWordWrap(True)
        def refresh():
            choices.clear()
            for r in self.models.records():
                choices.addItem(r["name"], r)
        def describe():
            r = choices.currentData()
            detail.setText((r["source"] + "\n" + r["terms"] + "\nSHA-256: " + r["sha256"]
                            + ("\nListening check passed; gameplay performance still needs verification."
                               if r.get("auditioned") else "\nImported for audition; quality and gameplay performance need verification.")) if r else "No models imported.")
        choices.currentIndexChanged.connect(describe)
        refresh()
        form.addRow("Installed", choices)
        form.addRow(detail)
        explanation = QLabel("Import one standard RVC v1/v2 .pth, or a ZIP containing one.\n"
                             "Weights are validated in a separate process. Archive scripts are never run.\n"
                             "Retrieval indexes are ignored in this first version. Only one model runs at a time.")
        explanation.setWordWrap(True)
        form.addRow(explanation)
        add = QPushButton("Import model…")
        add.clicked.connect(self.import_dialog)
        form.addRow(add)
        remove = QPushButton("Remove selected model")
        def delete():
            record = choices.currentData()
            if not record:
                return
            if self.window.router:
                self.window.router.command("ai_remove", model=record["id"])
                self.window.notice("Model removal requested; active conversion returns to Normal")
            else:
                self.models.remove(record["id"])
            if self.window.library.settings["ai_model"] == record["id"]:
                self.window.library.settings["ai_model"] = ""
                self.window.library.save()
            dialog.accept()
        remove.clicked.connect(delete)
        form.addRow(remove)
        close = QDialogButtonBox(QDialogButtonBox.StandardButton.Close)
        close.rejected.connect(dialog.reject)
        form.addRow(close)
        self.window.exec_shortcut_dialog(dialog)
        self.populate()

    def import_dialog(self):
        path, _ = QFileDialog.getOpenFileName(self, "Import local RVC model", "", "RVC checkpoint (*.pth *.zip)")
        if not path:
            return
        dialog = QDialog(self)
        dialog.setWindowTitle("Model source and usage terms")
        form = QFormLayout(dialog)
        fields = [QLineEdit() for _ in range(3)]
        for label, field in zip(("Voice/model name", "Original source URL", "Documented usage terms / license URL"), fields):
            form.addRow(label, field)
        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel)
        buttons.accepted.connect(dialog.accept)
        buttons.rejected.connect(dialog.reject)
        form.addRow(buttons)
        if not self.window.exec_shortcut_dialog(dialog):
            return
        values = [f.text() for f in fields]
        self.window.pending += 1
        self.window.notice("Validating AI model… your microphone stays available")
        future = self.window.executor.submit(self.models.import_model, path, *values)
        def completed(f):
            try:
                result = f.result()
            except Exception as exc:
                result = exc
            self.completion.done.emit(result)
        future.add_done_callback(completed)

    def imported(self, result):
        self.window.pending -= 1
        self.populate()
        self.window.notice("Model import failed: " + str(result) if isinstance(result, Exception)
                           else "Imported " + result["name"] + " · select it and use Hear my voice to audition")
