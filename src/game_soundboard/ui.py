from __future__ import annotations
import re
import threading
import textwrap
from concurrent.futures import ThreadPoolExecutor
from PySide6.QtCore import Qt, QObject, Signal, QTimer
from PySide6.QtGui import QDesktopServices
from PySide6.QtCore import QUrl
from PySide6.QtWidgets import (QMainWindow, QWidget, QVBoxLayout, QHBoxLayout, QGridLayout,
    QLabel, QPushButton, QLineEdit, QFileDialog, QMessageBox, QScrollArea, QFrame,
    QSlider, QCheckBox, QComboBox, QDialog, QFormLayout, QDialogButtonBox)
from .playback import Playback
from .audio_service import AudioClient
from .voice_effects import PRESETS
from .shortcuts import Shortcuts
from .keybinds import ShortcutRecorder, normalized_trigger
from .starters import STARTERS, download_starter
from .ai_ui import AIControls


class RoutingWorker(QObject):
    result = Signal(dict)
    error = Signal(str)
    connected = Signal(str)

    def __init__(self, library):
        super().__init__()
        self.client = AudioClient(library.root)
        self.connect_requests = []
        self.commands = []
        self.lock = threading.Lock()
        self.wake = threading.Event()
        self.stopping = threading.Event()
        self.thread = threading.Thread(target=self.run, daemon=True)

    def update(self, settings):
        # The GUI has atomically saved the manifest before notifying this worker.
        self.wake.set()

    def connect_app(self, app):
        with self.lock:
            self.connect_requests.append(dict(app))
        self.wake.set()

    def command(self, command, **values):
        with self.lock:
            self.commands.append((command, values))
        self.wake.set()

    def run(self):
        while not self.stopping.is_set():
            try:
                self.client.ensure_running()
                state = self.client.request("refresh")
                with self.lock:
                    requests, self.connect_requests = self.connect_requests, []
                    commands, self.commands = self.commands, []
                for command, values in commands:
                    try:
                        state = self.client.request(command, **values)
                    except Exception as exc:
                        self.connected.emit("Voice command failed: " + str(exc))
                for requested in requests:
                    try:
                        state = self.client.request("connect", app=requested)
                        self.connected.emit(requested["name"] + " connected to Soundboard Microphone")
                    except Exception as exc:
                        self.connected.emit("Could not connect app: " + str(exc))
                self.result.emit(state)
            except Exception as exc:
                self.error.emit(str(exc))
            self.wake.wait(3)
            self.wake.clear()

    def stop(self):
        self.stopping.set()
        self.wake.set()
        self.thread.join(timeout=15)


class ImportSignals(QObject):
    finished = Signal(object, str)


def button(text, callback, name=""):
    widget = QPushButton(text)
    widget.setObjectName(name)
    widget.clicked.connect(callback)
    return widget


def slider(value, callback):
    widget = QSlider(Qt.Orientation.Horizontal)
    widget.setRange(0, 100)
    widget.setValue(round(value * 100))
    widget.valueChanged.connect(lambda n: callback(n / 100))
    widget.setMinimumWidth(90)
    return widget


STYLE = """
QMainWindow, QDialog { background: #10151f; color: #e6edf5; }
QWidget { color: #e6edf5; font-family: 'DejaVu Sans'; font-size: 13px; }
QLabel#title { font-size: 28px; font-weight: bold; }
QLabel#subtitle, QLabel#muted { color: #9eafc3; }
QFrame#card { background: #1b2433; border: 1px solid #334258; border-radius: 12px; }
QPushButton { background: #29364b; border: 1px solid #40516c; border-radius: 7px; padding: 9px 12px; }
QPushButton:hover { background: #354963; border-color: #62d7c1; }
QPushButton:disabled { color: #6c7a8c; }
QPushButton#primary { background: #177c70; border-color: #3acbb2; }
QPushButton#stop { background: #6c2b39; border-color: #bc5c72; }
QPushButton#pad { font-size: 20px; font-weight: bold; padding: 24px 12px; background: #213f4a; }
QPushButton#pad[playing="true"] { background: #177c70; border: 2px solid #64efd0; }
QLineEdit, QComboBox, QSpinBox { background: #172130; padding: 9px; border: 1px solid #40516c; border-radius: 6px; }
QComboBox QAbstractItemView { background: #172130; selection-background-color: #177c70; }
QWidget#gridbody { background: #10151f; }
QScrollArea { border: none; background: transparent; }
QSlider::groove:horizontal { background: #334258; height: 5px; border-radius: 2px; }
QSlider::sub-page:horizontal { background: #49cbb4; border-radius: 2px; }
QSlider::handle:horizontal { background: #d9fff5; width: 13px; margin: -5px 0; border-radius: 6px; }
QCheckBox { spacing: 8px; }
QToolTip { color: #10151f; background: #e6edf5; }
"""


class Window(QMainWindow):
    def __init__(self, library, live=True):
        super().__init__()
        self.library = library
        self.live = live
        self.accepted = {}
        self.shortcuts_paused = False
        self.pads = {}
        self.sources, self.sinks = [], []
        self.capture_apps = []
        self.pending = 0
        self.pending_seeds = set()
        self.executor = ThreadPoolExecutor(max_workers=1)
        self.import_signals = ImportSignals()
        self.import_signals.finished.connect(self.import_done)
        self.playback = Playback(library, self)
        self.playback.error.connect(self.notice)
        self.playback.changed.connect(self.playing)
        self.shortcuts = Shortcuts(self) if live else None
        self.router = RoutingWorker(library) if live else None
        self.setWindowTitle("Game Soundboard")
        self.setMinimumSize(720, 560)
        self.resize(1080, 760)
        self.setAcceptDrops(True)
        self.setStyleSheet(STYLE)
        body = QWidget()
        self.setCentralWidget(body)
        layout = QVBoxLayout(body)
        layout.setContentsMargins(28, 24, 28, 20)
        layout.setSpacing(16)
        header = QHBoxLayout()
        titles = QVBoxLayout()
        title = QLabel("Game Soundboard")
        title.setObjectName("title")
        titles.addWidget(title)
        subtitle = QLabel("Your clips. Your voice. One microphone.")
        subtitle.setObjectName("subtitle")
        titles.addWidget(subtitle)
        header.addLayout(titles)
        header.addStretch()
        header.addWidget(button("Audio && shortcuts", self.settings_dialog))
        header.addWidget(button("+ Import sounds", self.import_files, "primary"))
        layout.addLayout(header)
        hint = QLabel('In your game or Discord, select “Soundboard Microphone” as the input device.')
        hint.setWordWrap(True)
        input_row = QHBoxLayout()
        input_row.addWidget(hint, 1)
        input_row.addWidget(button("Connect app", self.connect_app_dialog))
        layout.addLayout(input_row)
        toolbar = QHBoxLayout()
        self.search = QLineEdit()
        self.search.setPlaceholderText("Search sounds…  ·  or drop audio files here")
        self.search.textChanged.connect(self.render)
        toolbar.addWidget(self.search, 1)
        self.seed_button = button("Get starter clips", self.seed)
        toolbar.addWidget(self.seed_button)
        toolbar.addWidget(button("■  Stop all sounds", self.playback.stop_all, "stop"))
        layout.addLayout(toolbar)
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        self.grid_body = QWidget()
        self.grid_body.setObjectName("gridbody")
        self.grid = QGridLayout(self.grid_body)
        self.grid.setSpacing(14)
        self.grid.setAlignment(Qt.AlignmentFlag.AlignTop)
        scroll.setWidget(self.grid_body)
        layout.addWidget(scroll, 1)
        controls = QHBoxLayout()
        controls.addWidget(QLabel("Clips"))
        controls.addWidget(slider(library.settings["master"], lambda v: self.setting("master", v)))
        controls.addWidget(QLabel("Voice"))
        controls.addWidget(slider(library.settings["mic_volume"], lambda v: self.setting("mic_volume", v)))
        mute = QCheckBox("Mute voice")
        mute.setChecked(library.settings["mic_muted"])
        mute.toggled.connect(lambda v: self.setting("mic_muted", v))
        controls.addWidget(mute)
        monitor = QCheckBox("Hear clips locally")
        monitor.setChecked(library.settings["monitor"])
        monitor.toggled.connect(lambda v: self.setting("monitor", v))
        controls.addWidget(monitor)
        layout.addLayout(controls)
        effects = QHBoxLayout()
        effects.addWidget(QLabel("Voice effect"))
        self.voice_effect = QComboBox()
        for key, preset in PRESETS.items():
            self.voice_effect.addItem(preset[0], key)
        self.voice_effect.setCurrentIndex(0)
        self.voice_effect.currentIndexChanged.connect(self.voice_selected)
        effects.addWidget(self.voice_effect, 1)
        self.voice_monitor = QCheckBox("Hear my voice")
        self.voice_monitor.setToolTip("Hear the processed microphone through your selected headphones. Adds a short delay; use headphones to avoid feedback.")
        self.voice_monitor.setChecked(library.settings["voice_monitor"])
        self.voice_monitor.toggled.connect(lambda value: self.setting("voice_monitor", value))
        effects.addWidget(self.voice_monitor)
        layout.addLayout(effects)
        self.ai_controls = AIControls(self)
        layout.addWidget(self.ai_controls)
        self.audio_status = QLabel("Connecting audio…" if live else "Preview mode · audio routing disabled")
        self.shortcut_status = QLabel("No global shortcuts assigned")
        self.feedback = QLabel("Ready · WAV, MP3, OGG and FLAC")
        for widget in (self.audio_status, self.shortcut_status, self.feedback):
            widget.setObjectName("muted")
            widget.setWordWrap(True)
            layout.addWidget(widget)
        self.render()
        if live:
            self.router.result.connect(self.routed)
            self.router.error.connect(self.routing_error)
            self.router.connected.connect(self.notice)
            self.shortcuts.activated.connect(self.trigger)
            self.shortcuts.status.connect(self.shortcut_status.setText)
            self.shortcuts.bindings.connect(self.binding_labels)
            self.router.thread.start()
            QTimer.singleShot(500, self.bind_shortcuts)
            QTimer.singleShot(1000, self.seed)

    def notice(self, text):
        self.feedback.setText(text)

    def setting(self, key, value):
        self.library.settings[key] = value
        self.library.save()
        self.playback.volumes()
        if self.router:
            self.router.update(self.library.settings)

    def voice_selected(self):
        value = self.voice_effect.currentData() or "normal"
        if value.startswith("ai:"):
            self.ai_controls.choose(value[3:])
        else:
            self.setting("voice_effect", value)
            if self.router:
                self.router.command("voice_effect", effect=value)

    def return_normal(self):
        self.setting("voice_effect", "normal")
        self.voice_effect.blockSignals(True)
        self.voice_effect.setCurrentIndex(0)
        self.voice_effect.blockSignals(False)
        if self.router:
            self.router.command("voice_normal")

    def routed(self, state):
        if state.get("initial_settings") and not self.library.settings.get("devices_initialized"):
            for key in ("microphone", "output", "devices_initialized"):
                self.library.settings[key] = state["initial_settings"][key]
            self.library.save()
        self.sources, self.sinks = state["sources"], state["sinks"]
        self.capture_apps = state.get("capture_apps", [])
        self.audio_status.setText(state["message"])
        self.ai_controls.populate()
        self.ai_controls.update_state(state)
        self.playback.routes(state["clip_sink"], state["output"])

    def routing_error(self, text):
        self.capture_apps = []
        self.audio_status.setText("Audio unavailable: " + text)
        self.playback.routes("", "")

    def connect_app_dialog(self):
        dialog = QDialog(self)
        dialog.setWindowTitle("Connect an application's microphone")
        form = QFormLayout(dialog)
        choices = QComboBox()
        for app in self.capture_apps:
            suffix = " · already connected" if app["connected"] else " · using another input"
            choices.addItem(app["name"] + suffix, app)
        form.addRow("Recording app", choices)
        info = QLabel("Open your game's microphone test if it is missing here.\n"
                      "Connect sends your voice and clips to this active input.\n"
                      "For future sessions, select Soundboard Microphone in the game.\n"
                      "Push-to-talk and the game's voice settings still apply.")
        info.setWordWrap(True)
        form.addRow(info)
        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel)
        buttons.button(QDialogButtonBox.StandardButton.Ok).setText("Connect")
        buttons.button(QDialogButtonBox.StandardButton.Ok).setEnabled(bool(self.capture_apps) and bool(self.router))
        buttons.accepted.connect(dialog.accept)
        buttons.rejected.connect(dialog.reject)
        form.addRow(buttons)
        if self.exec_shortcut_dialog(dialog) and self.router and choices.currentData():
            self.router.connect_app(choices.currentData())

    def trigger(self, ident):
        if self.shortcuts_paused:
            return
        if ident == "stop-all":
            self.playback.stop_all()
        elif ident == "voice-normal":
            self.return_normal()
        else:
            self.playback.play(ident)

    def binding_labels(self, labels):
        self.accepted = labels
        self.render()

    def entries(self):
        result = [(s["id"], "Play " + s["name"], s["shortcut"]) for s in self.library.sounds if s["shortcut"]]
        if self.library.settings["stop_shortcut"]:
            result.append(("stop-all", "Stop all sounds", self.library.settings["stop_shortcut"]))
        if self.library.settings["normal_shortcut"]:
            result.append(("voice-normal", "Return to Normal voice", self.library.settings["normal_shortcut"]))
        return result

    def bind_shortcuts(self):
        if self.shortcuts:
            self.shortcuts.bind(self.entries())

    def render(self, *_):
        while self.grid.count():
            item = self.grid.takeAt(0)
            if item.widget():
                item.widget().deleteLater()
        self.pads = {}
        records = [s for s in self.library.sounds if self.search.text().casefold() in s["name"].casefold()]
        columns = max(1, (self.width() - 70) // 290)
        if not records:
            empty = QLabel("Drop your first sound here\n\nImport audio files or get the starter clips.")
            empty.setAlignment(Qt.AlignmentFlag.AlignCenter)
            empty.setMinimumHeight(220)
            self.grid.addWidget(empty, 0, 0)
        for index, record in enumerate(records):
            ident = record["id"]
            card = QFrame()
            card.setObjectName("card")
            box = QVBoxLayout(card)
            box.setContentsMargins(14, 14, 14, 14)
            pad = button("▶  " + textwrap.fill(record["name"], width=20, max_lines=2, placeholder="…"), lambda checked=False, i=ident: self.playback.play(i), "pad")
            pad.setToolTip(record["name"])
            pad.setMinimumHeight(108)
            self.pads[ident] = pad
            box.addWidget(pad)
            label = self.accepted.get(ident) or ("Pending: " + record["shortcut"] if record["shortcut"] else "No keybind assigned")
            key = QLabel(label)
            key.setObjectName("muted")
            box.addWidget(key)
            box.addWidget(slider(record["volume"], lambda v, r=record: self.clip_volume(r, v)))
            actions = QHBoxLayout()
            actions.addWidget(button("Preview", lambda checked=False, i=ident: self.playback.play(i, True)))
            actions.addWidget(button("Edit", lambda checked=False, r=record: self.edit(r)))
            actions.addWidget(button("←", lambda checked=False, i=ident: self.move(i, -1)))
            actions.addWidget(button("→", lambda checked=False, i=ident: self.move(i, 1)))
            box.addLayout(actions)
            self.grid.addWidget(card, index // columns, index % columns)
            if effect := self.playback.effects.get(ident):
                self.playing(ident, effect.isPlaying())

    def resizeEvent(self, event):
        super().resizeEvent(event)
        if hasattr(self, "grid"):
            self.render()

    def playing(self, ident, playing):
        if pad := self.pads.get(ident):
            pad.setProperty("playing", playing)
            pad.style().unpolish(pad)
            pad.style().polish(pad)

    def clip_volume(self, record, value):
        record["volume"] = value
        self.library.save()
        self.playback.volumes()

    def move(self, ident, delta):
        self.library.move(ident, delta)
        self.render()

    def validate_shortcut(self, value, current=None):
        value = value.strip()
        if not value:
            return value
        if not re.fullmatch(r"(?:(?:CTRL|ALT|SHIFT|LOGO|NUM)\+)*[A-Za-z0-9_]+", value):
            raise ValueError("Record a key combination, or click Clear to disable the shortcut.")
        if any(normalized_trigger(trigger) == normalized_trigger(value) and ident != current
               for ident, _, trigger in self.entries()):
            raise ValueError("That shortcut is already assigned in this board.")
        return value

    def exec_shortcut_dialog(self, dialog):
        previous = self.shortcuts_paused
        self.shortcuts_paused = True
        try:
            return dialog.exec()
        finally:
            self.shortcuts_paused = previous

    def edit(self, record):
        dialog = QDialog(self)
        dialog.setWindowTitle("Edit sound")
        form = QFormLayout(dialog)
        name = QLineEdit(record["name"])
        key = ShortcutRecorder(record["shortcut"])
        form.addRow("Name", name)
        form.addRow("Global keybind", key)
        form.addRow(QLabel("Click the field and press your keys. Clear disables the shortcut.\n"
                           "The desktop approves the binding. Shortcuts won’t play sounds while editing."))
        if record.get("source"):
            form.addRow(button("Open clip source", lambda: QDesktopServices.openUrl(QUrl(record["source"]))))
        remove = button("Remove sound", lambda: dialog.done(2), "stop")
        form.addRow(remove)
        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Save | QDialogButtonBox.StandardButton.Cancel)
        def save():
            try:
                self.validate_shortcut(key.trigger(), record["id"])
                if not name.text().strip():
                    raise ValueError("Enter a name")
                dialog.accept()
            except ValueError as exc:
                QMessageBox.warning(dialog, "Check sound settings", str(exc))
        buttons.accepted.connect(save)
        buttons.rejected.connect(dialog.reject)
        form.addRow(buttons)
        result = self.exec_shortcut_dialog(dialog)
        if result == 2:
            self.playback.remove(record["id"])
            self.library.remove(record["id"])
        elif result == QDialog.DialogCode.Accepted:
            record["name"], record["shortcut"] = name.text().strip(), key.trigger()
            self.library.save()
        else:
            return
        self.render()
        self.bind_shortcuts()

    def settings_dialog(self):
        dialog = QDialog(self)
        dialog.setWindowTitle("Audio & global shortcuts")
        dialog.setMinimumWidth(620)
        form = QFormLayout(dialog)
        combos = {}
        for key, title, devices in [("microphone", "Physical microphone", self.sources), ("output", "Headphones / preview output", self.sinks)]:
            combo = QComboBox()
            combo.addItem("Choose a device…", "")
            for device in devices:
                combo.addItem(device.get("description", device["name"]), device["name"])
            selected = self.library.settings[key]
            index = combo.findData(selected)
            if selected and index < 0:
                combo.addItem("Disconnected: " + selected, selected)
                index = combo.count() - 1
            combo.setCurrentIndex(max(0, index))
            form.addRow(title, combo)
            combos[key] = combo
        stop = ShortcutRecorder(self.library.settings["stop_shortcut"])
        form.addRow("Stop All keybind", stop)
        normal = ShortcutRecorder(self.library.settings["normal_shortcut"])
        form.addRow("Return to Normal keybind", normal)
        info = QLabel("Assign sound keybinds using each card’s Edit button.\n"
                      "Select Soundboard Microphone inside your game/Discord.\n"
                      "Game push-to-talk still applies; noise suppression may filter clips.\n"
                      "Closing the panel stops clips and shortcuts; the microphone stays available.")
        info.setWordWrap(True)
        form.addRow(info)
        form.addRow(button("Configure approved shortcuts in desktop", self.configure_desktop))
        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Save | QDialogButtonBox.StandardButton.Cancel)
        def save():
            try:
                self.validate_shortcut(stop.trigger(), "stop-all")
                self.validate_shortcut(normal.trigger(), "voice-normal")
                if stop.trigger() and normalized_trigger(stop.trigger()) == normalized_trigger(normal.trigger()):
                    raise ValueError("Stop All and Return to Normal need different shortcuts")
                dialog.accept()
            except ValueError as exc:
                QMessageBox.warning(dialog, "Check shortcut", str(exc))
        buttons.accepted.connect(save)
        buttons.rejected.connect(dialog.reject)
        form.addRow(buttons)
        if self.exec_shortcut_dialog(dialog):
            for key, combo in combos.items():
                self.library.settings[key] = combo.currentData()
            self.library.settings["stop_shortcut"] = stop.trigger()
            self.library.settings["normal_shortcut"] = normal.trigger()
            self.library.save()
            if self.router:
                self.router.update(self.library.settings)
            self.bind_shortcuts()

    def configure_desktop(self):
        if self.shortcuts:
            self.shortcuts.configure()

    def import_files(self):
        paths, _ = QFileDialog.getOpenFileNames(self, "Import sounds", "", "Audio (*.wav *.mp3 *.ogg *.flac)")
        for path in paths:
            self.submit(lambda p=path: self.library.prepare(p))

    def submit(self, function, seed=""):
        self.pending += 1
        if seed:
            self.pending_seeds.add(seed)
        self.notice(f"Importing… {self.pending} remaining")
        future = self.executor.submit(function)
        def completed(f):
            try:
                result = f.result()
            except Exception as exc:
                result = exc
            self.import_signals.finished.emit(result, seed)
        future.add_done_callback(completed)

    def import_done(self, result, seed):
        self.pending -= 1
        self.pending_seeds.discard(seed)
        if isinstance(result, Exception):
            self.notice(f"Import failed: {result} · use Get starter clips to retry downloads")
        else:
            self.library.commit(result)
            self.playback.refresh_devices()
            self.render()
            self.notice(f'Imported {result["name"]}' + (f" · {self.pending} remaining" if self.pending else ""))

    def seed(self):
        for entry in STARTERS:
            if entry[0] not in self.library.seeded and entry[0] not in self.pending_seeds:
                self.submit(lambda e=entry: download_starter(self.library, e), entry[0])

    def dragEnterEvent(self, event):
        if event.mimeData().hasUrls():
            event.acceptProposedAction()

    def dropEvent(self, event):
        for url in event.mimeData().urls():
            if url.isLocalFile():
                self.submit(lambda p=url.toLocalFile(): self.library.prepare(p))
        event.acceptProposedAction()

    def closeEvent(self, event):
        if self.pending:
            self.notice("Please wait for imports to finish before closing.")
            event.ignore()
            return
        self.playback.stop_all()
        self.executor.shutdown(wait=False)
        if self.shortcuts:
            self.shortcuts.stop()
        if self.router:
            self.router.stop()
        self.library.save()
        event.accept()
