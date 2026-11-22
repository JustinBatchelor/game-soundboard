"""Local model records. Never import Python from a model download."""
from __future__ import annotations
import hashlib
import json
from pathlib import Path
import re
import shutil
import subprocess
import tempfile
import uuid
import zipfile

from .library import atomic_json

BACKEND_COMMIT = "7e2ce5c4800010bbc7604968bd29b802534fac05"
PROJECT = Path(__file__).resolve().parents[2]
RUNTIME = PROJECT / ".runtime"
AI_PYTHON = RUNTIME / "ai-venv/bin/python"
NAMED_PRESETS = (
    ("trump", "Donald Trump", "https://huggingface.co/Coolwowsocoolwow/Donald-J.-Trump", "Local model required; alternative preferred in listening comparison, gameplay checks pending"),
    ("musk", "Elon Musk", "https://huggingface.co/anonymous12345678910/ElonMuskV2", "Local model required; alternative preferred in listening comparison, gameplay checks pending"),
    ("rogan", "Joe Rogan", "https://huggingface.co/GarrGarr/RVC_Models", "No documented model usage terms found"),
)
MAX_MODEL = 512 * 1024 * 1024


def checksum(path):
    with Path(path).open("rb") as f:
        return hashlib.file_digest(f, "sha256").hexdigest()


def tuning(pitch=0, profile="responsive"):
    pitch = int(pitch)
    if not -12 <= pitch <= 12 or profile not in ("responsive", "balanced"):
        raise ValueError("Pitch must be −12…12 and processing must be Responsive or Balanced")
    return {"pitch": pitch, "profile": profile}


class ModelLibrary:
    def __init__(self, root):
        self.root = Path(root) / "models"

    def directory(self, ident):
        if not re.fullmatch(r"[a-f0-9]{32}", ident):
            raise ValueError("Invalid model ID")
        return self.root / ident

    def get(self, ident):
        directory = self.directory(ident)
        record = json.loads((directory / "model.json").read_text())
        if record.get("version") != 1 or record.get("id") != ident:
            raise ValueError("Invalid model record")
        if not (directory / "model.pth").is_file():
            raise ValueError("Model file is missing; reimport it")
        return record

    def records(self):
        records = []
        for path in sorted(self.root.glob("*/model.json")):
            try:
                records.append(self.get(path.parent.name))
            except (OSError, ValueError, KeyError):
                continue
        return records

    def import_model(self, source, name, attribution, terms, *, validator=None):
        if not all(str(v).strip() for v in (name, attribution, terms)):
            raise ValueError("Enter a model name, source and documented usage terms")
        if validator is None and not AI_PYTHON.exists():
            raise ValueError("Install the isolated AI runtime first: scripts/setup-ai.py")
        source = Path(source)
        if not 0 < source.stat().st_size <= MAX_MODEL:
            raise ValueError("Model must be between 1 byte and 512 MiB")
        self.root.mkdir(parents=True, exist_ok=True)
        ident = uuid.uuid4().hex
        with tempfile.TemporaryDirectory(prefix=".import-", dir=self.root) as tmp:
            stage = Path(tmp)
            target = stage / "model.pth"
            if source.suffix.lower() == ".pth":
                shutil.copyfile(source, target)
            elif source.suffix.lower() == ".zip":
                with zipfile.ZipFile(source) as archive:
                    # Read one exact member into a fixed destination. No extractall,
                    # executable files, paths, symlinks, or FAISS deserialization.
                    models = [m for m in archive.infolist() if m.filename.lower().endswith(".pth")]
                    if len(models) != 1 or not 0 < models[0].file_size <= MAX_MODEL:
                        raise ValueError("Archive must contain exactly one .pth model under 512 MiB")
                    with archive.open(models[0]) as src, target.open("wb") as dst:
                        remaining = MAX_MODEL + 1
                        while chunk := src.read(min(1024 * 1024, remaining)):
                            dst.write(chunk)
                            remaining -= len(chunk)
                            if remaining <= 0:
                                raise ValueError("Expanded model is too large")
            else:
                raise ValueError("Import an RVC .pth checkpoint or a .zip containing one")
            if validator:
                info = validator(target)
            else:
                result = subprocess.run([str(AI_PYTHON), str(PROJECT / "src/game_soundboard/ai_validate.py"), str(target)],
                                        capture_output=True, text=True, timeout=60)
                if result.returncode:
                    raise ValueError("Model validation failed: " + result.stderr[-800:])
                info = json.loads(result.stdout)
            digest = checksum(target)
            for existing in self.records():
                if existing["sha256"] == digest:
                    return existing
            record = {"version": 1, "id": ident, "name": str(name).strip()[:160],
                      "source": str(attribution).strip()[:2048], "terms": str(terms).strip()[:4096],
                      "sha256": digest, "format": info, "tuning": tuning(),
                      "auditioned": False, "benchmark_passed": False}
            atomic_json(stage / "model.json", record)
            stage.rename(self.directory(ident))
        return record

    def remove(self, ident):
        shutil.rmtree(self.directory(ident))

    def set_tuning(self, ident, pitch, profile):
        record = self.get(ident)
        record["tuning"] = tuning(pitch, profile)
        atomic_json(self.directory(ident) / "model.json", record)
