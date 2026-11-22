from __future__ import annotations
import copy
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
from .routing import Routing
from .shortcuts import Shortcuts
from .starters import STARTERS, download_starter


class RoutingWorker(QObject):
    result = Signal(dict)
    error = Signal(str)

    def __init__(self, library):
        super().__init__()
        self.router = Routing(library.root)
        self.settings = copy.deepcopy(library.settings)
        self.lock = threading.Lock()
        self.wake = threading.Event()
        self.stopping = threading.Event()
        self.thread = threading.Thread(target=self.run, daemon=True)

    def update(self, settings):
        with self.lock:
            self.settings = copy.deepcopy(settings)
        self.wake.set()

    def run(self):
        while not self.stopping.is_set():
            try:
                with self.lock:
                    settings = copy.deepcopy(self.settings)
                initial = None
                if not settings.get("devices_initialized"):
                    sources, sinks = self.router.devices()
                    mic = self.router.call("get-default-source")
                    out = self.router.call("get-default-sink")
                    settings["microphone"] = mic if mic in {d["name"] for d in sources} else ""
                    settings["output"] = out if out in {d["name"] for d in sinks} else ""
                    settings["devices_initialized"] = True
                    initial = settings
                    with self.lock:
                        self.settings.update({k: settings[k] for k in ("microphone", "output", "devices_initialized")})
                state = self.router.reconcile(settings)
                state["initial_settings"] = initial
                self.result.emit(state)
            except Exception as exc:
                self.error.emit(str(exc))
            self.wake.wait(3)
            self.wake.clear()
        try:
            self.router.cleanup()
        except Exception as exc:
            self.error.emit("Audio cleanup failed: " + str(exc))

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
QLineEdit, QComboBox { background: #172130; padding: 9px; border: 1px solid #40516c; border-radius: 6px; }
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
        self.pads = {}
        self.sources, self.sinks = [], []
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
        layout.addWidget(hint)
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

    def routed(self, state):
        if state.get("initial_settings") and not self.library.settings.get("devices_initialized"):
            for key in ("microphone", "output", "devices_initialized"):
                self.library.settings[key] = state["initial_settings"][key]
            self.library.save()
        self.sources, self.sinks = state["sources"], state["sinks"]
        self.audio_status.setText(state["message"])
        self.playback.routes(state["clip_sink"], state["output"])

    def routing_error(self, text):
        self.audio_status.setText("Audio unavailable: " + text)
        self.playback.routes("", "")

    def trigger(self, ident):
        if ident == "stop-all":
            self.playback.stop_all()
        else:
            self.playback.play(ident)

    def binding_labels(self, labels):
        self.accepted = labels
        self.render()

    def entries(self):
        result = [(s["id"], "Play " + s["name"], s["shortcut"]) for s in self.library.sounds if s["shortcut"]]
        if self.library.settings["stop_shortcut"]:
            result.append(("stop-all", "Stop all sounds", self.library.settings["stop_shortcut"]))
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
            raise ValueError("Use XDG syntax, e.g. CTRL+ALT+k or F8. Leave empty to disable.")
        if any(trigger == value and ident != current for ident, _, trigger in self.entries()):
            raise ValueError("That shortcut is already assigned in this board.")
        return value

    def edit(self, record):
        dialog = QDialog(self)
        dialog.setWindowTitle("Edit sound")
        form = QFormLayout(dialog)
        name = QLineEdit(record["name"])
        key = QLineEdit(record["shortcut"])
        key.setPlaceholderText("CTRL+ALT+k")
        form.addRow("Name", name)
        form.addRow("Preferred global keybind", key)
        form.addRow(QLabel("The desktop approves the final binding. Blank disables it."))
        if record.get("source"):
            form.addRow(button("Open clip source", lambda: QDesktopServices.openUrl(QUrl(record["source"]))))
        remove = button("Remove sound", lambda: dialog.done(2), "stop")
        form.addRow(remove)
        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Save | QDialogButtonBox.StandardButton.Cancel)
        def save():
            try:
                self.validate_shortcut(key.text(), record["id"])
                if not name.text().strip():
                    raise ValueError("Enter a name")
                dialog.accept()
            except ValueError as exc:
                QMessageBox.warning(dialog, "Check sound settings", str(exc))
        buttons.accepted.connect(save)
        buttons.rejected.connect(dialog.reject)
        form.addRow(buttons)
        result = dialog.exec()
        if result == 2:
            self.playback.remove(record["id"])
            self.library.remove(record["id"])
        elif result == QDialog.DialogCode.Accepted:
            record["name"], record["shortcut"] = name.text().strip(), key.text().strip()
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
        stop = QLineEdit(self.library.settings["stop_shortcut"])
        stop.setPlaceholderText("e.g. CTRL+ALT+BackSpace")
        form.addRow("Stop All keybind", stop)
        info = QLabel("Assign sound keybinds using each card’s Edit button.\n"
                      "Select Soundboard Microphone inside your game/Discord.\n"
                      "Game push-to-talk still applies; noise suppression may filter clips.\n"
                      "Closing the window quits; minimize to keep shortcuts active.")
        info.setWordWrap(True)
        form.addRow(info)
        form.addRow(button("Configure approved shortcuts in desktop", self.configure_desktop))
        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Save | QDialogButtonBox.StandardButton.Cancel)
        def save():
            try:
                self.validate_shortcut(stop.text(), "stop-all")
                dialog.accept()
            except ValueError as exc:
                QMessageBox.warning(dialog, "Check shortcut", str(exc))
        buttons.accepted.connect(save)
        buttons.rejected.connect(dialog.reject)
        form.addRow(buttons)
        if dialog.exec():
            for key, combo in combos.items():
                self.library.settings[key] = combo.currentData()
            self.library.settings["stop_shortcut"] = stop.text().strip()
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
