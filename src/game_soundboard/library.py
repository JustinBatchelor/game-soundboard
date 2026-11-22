from __future__ import annotations

import json
import os
import shutil
import subprocess
import tempfile
import uuid
from pathlib import Path

FORMATS = {".wav", ".mp3", ".ogg", ".flac"}
DEFAULTS = {"master": 0.7, "mic_volume": 1.0, "mic_muted": False,
            "monitor": False, "microphone": "", "output": "", "stop_shortcut": ""}


def data_directory():
    return Path(os.environ.get("SOUNDBOARD_DATA_DIR", str(
        Path(os.environ.get("XDG_DATA_HOME", Path.home() / ".local/share")) / "game-soundboard")))


def ffmpeg_binary():
    if path := shutil.which("ffmpeg"):
        return path
    import imageio_ffmpeg
    return imageio_ffmpeg.get_ffmpeg_exe()


def atomic_json(path, value):
    path = Path(path)
    fd, tmp = tempfile.mkstemp(prefix=".save-", dir=path.parent)
    try:
        with os.fdopen(fd, "w") as f:
            json.dump(value, f, indent=2)
            f.flush()
            os.fsync(f.fileno())
        os.replace(tmp, path)
    finally:
        Path(tmp).unlink(missing_ok=True)


class Library:
    """Only the GUI thread mutates the manifest; workers return prepared records."""
    def __init__(self, root=None):
        self.root = Path(root or data_directory())
        self.root.mkdir(parents=True, exist_ok=True)
        self.path = self.root / "library.json"
        self.sounds = []
        self.settings = dict(DEFAULTS)
        self.seeded = []
        if self.path.exists():
            doc = json.loads(self.path.read_text())
            if doc.get("version") != 1:
                raise ValueError("Unsupported library version. Your library has been left intact.")
            self.sounds = doc["sounds"]
            self.settings.update(doc.get("settings", {}))
            self.seeded = doc.get("seeded", [])
        for folder in ("originals", "cache"):
            (self.root / folder).mkdir(exist_ok=True)

    def save(self):
        atomic_json(self.path, {"version": 1, "sounds": self.sounds,
                               "settings": self.settings, "seeded": self.seeded})

    def prepare(self, source, name=None, attribution="", seed=None):
        source = Path(source)
        if source.suffix.lower() not in FORMATS:
            raise ValueError("Supported formats: WAV, MP3, OGG, FLAC")
        ident = uuid.uuid4().hex
        original = self.root / "originals" / (ident + source.suffix.lower())
        cache = self.root / "cache" / (ident + ".wav")
        try:
            shutil.copyfile(source, original)
            result = subprocess.run([ffmpeg_binary(), "-nostdin", "-v", "error", "-y",
                "-i", str(original), "-vn", "-ar", "48000", "-ac", "2", "-c:a", "pcm_s16le",
                str(cache)], capture_output=True, text=True, timeout=300)
            if result.returncode or not cache.exists() or cache.stat().st_size <= 44:
                raise ValueError("Could not decode audio: " + result.stderr[-500:])
        except BaseException:
            original.unlink(missing_ok=True)
            cache.unlink(missing_ok=True)
            raise
        return {"id": ident, "name": name or source.stem, "original": str(original.relative_to(self.root)),
                "cache": str(cache.relative_to(self.root)), "volume": 0.5, "shortcut": "",
                "source": attribution, "seed": seed}

    def commit(self, record):
        if record.get("seed") in self.seeded:
            self.discard_files(record)
            return
        self.sounds.append(record)
        if record.get("seed"):
            self.seeded.append(record["seed"])
        self.save()

    def replace_audio(self, ident, prepared):
        """Replace audio/source only, retaining button ID, position and user settings."""
        record = next(s for s in self.sounds if s["id"] == ident)
        old = dict(record)
        for key in ("original", "cache", "source", "download_url"):
            record.pop(key, None)
            if key in prepared:
                record[key] = prepared[key]
        try:
            self.save()
        except Exception:
            record.clear()
            record.update(old)
            self.discard_files(prepared)
            raise
        self.discard_files(old)

    def discard_files(self, record):
        for key in ("original", "cache"):
            path = (self.root / record[key]).resolve()
            if path.is_relative_to(self.root.resolve()):
                path.unlink(missing_ok=True)

    def remove(self, ident):
        record = next(s for s in self.sounds if s["id"] == ident)
        self.sounds.remove(record)
        self.save()
        self.discard_files(record)

    def move(self, ident, delta):
        index = next(i for i, s in enumerate(self.sounds) if s["id"] == ident)
        target = max(0, min(len(self.sounds) - 1, index + delta))
        self.sounds.insert(target, self.sounds.pop(index))
        self.save()
