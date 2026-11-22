"""Panel-owned generation lifecycle, independent of the persistent audio service."""
from pathlib import Path
import json
import os
import shutil
import signal
import subprocess
import threading
import time
import uuid
import wave

from PySide6.QtCore import QObject, Signal
from .library import atomic_json, ffmpeg_binary

PROJECT = Path(__file__).resolve().parents[2]
RUNTIME = PROJECT / ".runtime"
MAX_TEXT = 500
JOB_TIMEOUT = 180


def validate_text(text):
    text = text.strip()
    if not 1 <= len(text) <= MAX_TEXT:
        raise ValueError("Enter between 1 and 500 characters")
    return text


def validate_audio(path):
    with wave.open(str(path)) as wav:
        if wav.getnchannels() != 2 or wav.getframerate() != 48000 or wav.getsampwidth() != 2:
            raise ValueError("Generated audio must be 48 kHz stereo PCM16")
        frames = wav.getnframes()
        if not 0 < frames <= 48000 * 60:
            raise ValueError("Generated audio must be between 0 and 60 seconds")
        if len(wav.readframes(frames)) != frames * 4:
            raise ValueError("Generated audio is incomplete")


class SpeechVoices:
    def __init__(self, root):
        self.root = Path(root) / "speech"
        self.path = self.root / "voices.json"

    def records(self):
        if not self.path.exists():
            return []
        data = json.loads(self.path.read_text())
        if data.get("version") != 1:
            raise ValueError("Unsupported speech voice catalog")
        return data["voices"]

    def get(self, ident):
        record = next((r for r in self.records() if r["id"] == ident), None)
        if not record or not record.get("approved"):
            raise ValueError("This voice has not passed its listening check")
        if record["engine"] not in {"f5", "chatterbox"}:
            raise ValueError("Unsupported speech engine")
        if not Path(record["reference"]).is_file():
            raise ValueError("Voice reference is missing; reinstall this preset")
        return dict(record)


class SpeechGenerator(QObject):
    changed = Signal(object)
    ready = Signal(object)
    _event = Signal(object)

    def __init__(self, root, parent=None, *, command_factory=None, timeout=JOB_TIMEOUT):
        super().__init__(parent)
        self.root = Path(root)
        self.voices = SpeechVoices(root)
        self.jobs = self.voices.root / "jobs"
        self.command_factory = command_factory
        self.timeout = timeout
        self.job_id = None
        self.phase = "idle"
        self.process = None
        self.thread = None
        self.cancelled = threading.Event()
        self.closed = False
        self._lock = threading.Lock()
        self._event.connect(self._receive)

    @property
    def busy(self):
        return self.job_id is not None

    def start(self, text, voice_id):
        if self.closed or self.busy:
            raise ValueError("Wait for the current generation to finish or cancel it")
        text = validate_text(text)
        voice = self.voices.get(voice_id)
        executable = RUNTIME / ("tts-" + voice["engine"] + "-venv/bin/python")
        if not self.command_factory and not executable.is_file():
            raise ValueError("Install the speech runtime first: scripts/setup-speech.py")
        ident = uuid.uuid4().hex
        folder = self.jobs / ident
        folder.mkdir(parents=True)
        job = {"job_id": ident, "text": text, "voice": voice,
               "assets": str(RUNTIME / "tts-assets"), "output": str(folder / "speech.wav"),
               "ffmpeg": ffmpeg_binary(), "seed": int.from_bytes(os.urandom(4), "little"),
               "parent_pid": os.getpid()}
        atomic_json(folder / "job.json", job)
        command = (self.command_factory(job, folder) if self.command_factory else
                   [str(executable), str(Path(__file__).with_name("speech_worker.py")), str(folder / "job.json")])
        self.job_id = ident
        self.cancelled.clear()
        self.phase = "loading"
        self.changed.emit({"phase": self.phase, "job_id": ident})
        self.thread = threading.Thread(target=self._run, args=(job, folder, command), daemon=True)
        self.thread.start()

    def _run(self, job, folder, command):
        start = time.monotonic()
        result = None
        reason = "Speech process exited without returning audio"
        events = folder / "events.jsonl"
        try:
            env = dict(os.environ, HF_HUB_OFFLINE="1", TRANSFORMERS_OFFLINE="1",
                       HF_HUB_DISABLE_TELEMETRY="1", MPLCONFIGDIR=str(folder / "mpl"),
                       HF_HOME=str(RUNTIME / "tts-hf-cache"), PYTHONUNBUFFERED="1")
            # pydub discovers ffmpeg by executable name.
            binary_dir = folder / "bin"
            binary_dir.mkdir()
            (binary_dir / "ffmpeg").symlink_to(job["ffmpeg"])
            env["PATH"] = str(binary_dir) + os.pathsep + env.get("PATH", "")
            with events.open("w") as out, (folder / "worker.log").open("w") as log:
                with self._lock:
                    if self.cancelled.is_set():
                        return
                    process = subprocess.Popen(command, stdout=out, stderr=log, stdin=subprocess.DEVNULL,
                                               env=env, start_new_session=True)
                    self.process = process
                with events.open() as incoming:
                    pending = ""
                    while True:
                        pending += incoming.read()
                        lines = pending.split("\n")
                        pending = lines.pop()
                        for line in lines:
                            if len(line) > 16000:
                                raise ValueError("Invalid speech worker response")
                            event = json.loads(line)
                            if event.get("job_id") != job["job_id"]:
                                continue
                            if event.get("phase") == "ready":
                                result = event
                            elif event.get("phase") == "failed":
                                reason = event.get("reason", reason)
                            elif event.get("phase") in {"loading", "generating"}:
                                self._event.emit(event)
                        if self.cancelled.is_set():
                            self._kill(process)
                            break
                        if time.monotonic() - start > self.timeout:
                            reason = "Generation timed out; try a shorter line"
                            self._kill(process)
                            result = None
                            break
                        if process.poll() is not None:
                            # Read the final flushed event after observing process exit.
                            tail = incoming.read()
                            if tail:
                                pending += tail
                                continue
                            break
                        time.sleep(0.04)
                process.wait(timeout=3)
            if process.returncode != 0:
                result = None
            if result and not self.cancelled.is_set():
                output = Path(job["output"])
                validate_audio(output)
                result.update(text=job["text"], voice=job["voice"], output=str(output),
                              elapsed_seconds=time.monotonic() - start)
        except Exception as exc:
            reason = str(exc)
            result = None
        finally:
            with self._lock:
                if self.process:
                    self._kill(self.process)
                    self.process.wait(timeout=3)
                self.process = None
            if self.cancelled.is_set() or not result:
                shutil.rmtree(folder, ignore_errors=True)
                self._event.emit({"job_id": job["job_id"],
                                  "phase": "cancelled" if self.cancelled.is_set() else "failed",
                                  "reason": reason})
            else:
                self._event.emit(result)

    @staticmethod
    def _kill(process):
        if process.poll() is None:
            try:
                os.killpg(process.pid, signal.SIGKILL)
            except ProcessLookupError:
                pass

    def _receive(self, event):
        if self.closed or event.get("job_id") != self.job_id:
            return
        phase = event["phase"]
        if self.cancelled.is_set() and phase != "cancelled":
            if phase == "ready":
                self.discard(event)
                phase = "cancelled"
                event = {"phase": phase}
            elif phase in {"loading", "generating"}:
                return
        self.phase = phase
        if phase in {"ready", "failed", "cancelled"}:
            self.job_id = None
        self.changed.emit(event)
        if phase == "ready":
            self.ready.emit(event)

    def cancel(self):
        self.cancelled.set()
        with self._lock:
            if self.process:
                self._kill(self.process)

    def discard(self, result):
        folder = Path(result["output"]).parent
        if folder.parent == self.jobs:
            shutil.rmtree(folder, ignore_errors=True)

    def close(self):
        self.closed = True
        self.cancel()
        if self.thread:
            self.thread.join(timeout=4)
        if not self.thread or not self.thread.is_alive():
            shutil.rmtree(self.jobs, ignore_errors=True)
