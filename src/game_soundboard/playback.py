from PySide6.QtCore import QObject, QUrl, Signal, Slot
from PySide6.QtMultimedia import QMediaDevices, QSoundEffect
from pathlib import Path
import uuid
from .speech import validate_audio


class Playback(QObject):
    changed = Signal(str, bool)
    error = Signal(str)

    def __init__(self, library, parent=None):
        super().__init__(parent)
        self.library = library
        self.effects = {}
        self.preview_effects = {}
        self.temporary = {}
        self.clip_device = None
        self.output_device = None
        self.media = QMediaDevices(self)
        self.media.audioOutputsChanged.connect(self.refresh_devices)
        self.clip_name = ""
        self.output_name = ""

    def routes(self, clip_name, output_name):
        self.clip_name, self.output_name = clip_name, output_name
        self.refresh_devices()

    def refresh_devices(self):
        devices = {bytes(d.id()).decode(): d for d in self.media.audioOutputs()}
        self.clip_device = devices.get(self.clip_name)
        self.output_device = devices.get(self.output_name)
        for cache, device in [(self.effects, self.clip_device), (self.preview_effects, self.output_device)]:
            for effect in cache.values():
                if device is None:
                    effect.stop()
                elif effect.audioDevice() != device:
                    effect.stop()
                    effect.setAudioDevice(device)
        if self.clip_device:
            for record in self.library.sounds:
                self.effect(record, False)

    def effect(self, record, preview):
        cache = self.preview_effects if preview else self.effects
        device = self.output_device if preview else self.clip_device
        if record["id"] not in cache:
            effect = QSoundEffect(device, self)
            effect.setLoopCount(1)
            effect.setSource(QUrl.fromLocalFile(str(self.library.root / record["cache"])))
            effect.setProperty("sound_id", record["id"])
            effect.setProperty("sound_name", record["name"])
            if not preview:
                effect.playingChanged.connect(self.effect_changed)
            effect.statusChanged.connect(self.effect_status)
            cache[record["id"]] = effect
        effect = cache[record["id"]]
        effect.setVolume(record["volume"] * self.library.settings["master"])
        return effect

    @Slot()
    def effect_changed(self):
        effect = self.sender()
        if effect:
            self.changed.emit(effect.property("sound_id"), effect.isPlaying())

    @Slot()
    def effect_status(self):
        effect = self.sender()
        if effect and effect.status() == QSoundEffect.Status.Error:
            self.error.emit(f'Cannot load {effect.property("sound_name")}. Remove it and import the original again.')

    def play(self, ident, preview=False):
        record = self.temporary.get(ident) or next((s for s in self.library.sounds if s["id"] == ident), None)
        if record is None:
            return
        if (self.output_device if preview else self.clip_device) is None:
            self.error.emit("Headphones unavailable" if preview else "Audio routing is not ready")
            return
        effect = self.effect(record, preview)
        effect.stop()
        effect.play()

    def volumes(self):
        for record in list(self.library.sounds) + list(self.temporary.values()):
            for cache in (self.effects, self.preview_effects):
                if effect := cache.get(record["id"]):
                    effect.setVolume(record["volume"] * self.library.settings["master"])

    def remove(self, ident):
        for cache in (self.effects, self.preview_effects):
            if effect := cache.pop(ident, None):
                effect.stop()
                effect.deleteLater()
        self.temporary.pop(ident, None)

    def add_temporary(self, path, name):
        path = Path(path).resolve()
        if not path.is_relative_to((self.library.root / "speech/jobs").resolve()):
            raise ValueError("Generated clip is outside the speech job directory")
        validate_audio(path)
        ident = "speech-" + uuid.uuid4().hex
        self.temporary[ident] = {"id": ident, "name": name, "volume": .5,
                                 "cache": str(path.relative_to(self.library.root.resolve()))}
        return ident

    def stop_all(self):
        for effect in list(self.effects.values()) + list(self.preview_effects.values()):
            effect.stop()
