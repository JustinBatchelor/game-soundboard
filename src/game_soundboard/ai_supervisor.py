"""Nonblocking worker lifecycle; the audio service remains the graph owner."""
from __future__ import annotations
import json
import os
from pathlib import Path
import queue
import shutil
import signal
import subprocess
import threading
import time

from .ai_models import AI_PYTHON, PROJECT, RUNTIME, ModelLibrary, tuning


class Worker:
    def __init__(self, config, root):
        self.events = queue.Queue(maxsize=32)
        root = Path(root)
        root.mkdir(parents=True, exist_ok=True)
        log = root / "ai-worker.log"
        if log.exists() and log.stat().st_size > 2 * 1024 * 1024:
            log.replace(root / "ai-worker.previous.log")
        env = dict(os.environ, OMP_NUM_THREADS="2", MKL_NUM_THREADS="2", OPENBLAS_NUM_THREADS="2")
        with log.open("ab") as output:
            self.process = subprocess.Popen([str(AI_PYTHON), str(PROJECT / "src/game_soundboard/ai_worker.py")],
                stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=output, text=True,
                start_new_session=True, cwd=root, env=env)
        self.process.stdin.write(json.dumps(config) + "\n")
        self.process.stdin.close()
        threading.Thread(target=self.read, daemon=True).start()

    def read(self):
        try:
            while line := self.process.stdout.readline(16384):
                event = json.loads(line)
                self.events.put(event, timeout=2)
        except (ValueError, OSError, queue.Full):
            pass
        finally:
            self.process.stdout.close()

    def poll(self):
        return self.process.poll()

    def stop(self):
        # One process group includes the bridge subprocesses. Kill even if the
        # leader exited first, so orphan audio writers cannot keep speaking.
        try:
            os.killpg(self.process.pid, signal.SIGKILL)
        except ProcessLookupError:
            pass
        threading.Thread(target=self.process.wait, daemon=True).start()


class AISupervisor:
    def __init__(self, root, worker_factory=Worker, clock=time.monotonic):
        self.root = Path(root)
        self.models = ModelLibrary(root)
        self.worker_factory, self.clock = worker_factory, clock
        self.worker = None
        self.phase, self.requested, self.active = "normal", "normal", "normal"
        self.reason = ""
        self.started = self.last_heartbeat = 0.
        self.metrics = {}
        self.microphone = ""

    def status(self):
        return {"phase": self.phase, "requested": self.requested, "active": self.active,
                "reason": self.reason, **self.metrics}

    def stop(self):
        if self.worker:
            self.worker.stop()
            self.worker = None

    def normal(self):
        self.stop()
        self.phase = self.requested = self.active = "normal"
        self.reason = ""
        self.metrics = {}

    def fail(self, reason):
        self.stop()
        self.phase, self.active, self.reason = "failed", "normal", reason

    def activate(self, ident, pitch, profile, microphone, input_bus, output_bus):
        values = tuning(pitch, profile)
        self.stop()
        self.requested, self.active, self.phase = ident, "normal", "loading"
        self.reason, self.metrics = "", {}
        self.microphone = microphone
        self.started = self.last_heartbeat = self.clock()
        try:
            if not microphone:
                raise ValueError("Select a connected physical microphone first")
            record = self.models.get(ident)
            pacat = shutil.which("pacat") or str(RUNTIME / "usr/bin/pacat")
            config = {"model": record, "directory": str(self.models.directory(ident).absolute()),
                      "runtime": str(RUNTIME), "input": input_bus, "output": output_bus,
                      "pacat": pacat, "parent_pid": os.getpid(), **values}
            self.worker = self.worker_factory(config, self.root)
        except Exception as exc:
            self.fail(str(exc))

    def tick(self):
        before = (self.phase, self.active)
        worker = self.worker
        if worker:
            for _ in range(32):
                try:
                    event = worker.events.get_nowait()
                except queue.Empty:
                    break
                kind = event.get("event")
                if kind == "error":
                    self.fail(event.get("reason", "AI worker failed"))
                    break
                if kind in ("ready", "heartbeat"):
                    self.last_heartbeat = self.clock()
                if kind == "ready" and self.phase == "loading":
                    self.phase, self.active = "active", self.requested
                if kind == "heartbeat":
                    self.metrics.update({k: event[k] for k in ("processing_ms", "processing_p95_ms", "overruns", "block_ms") if k in event})
            if self.worker:
                if worker.poll() is not None:
                    self.fail("AI worker exited; Normal voice restored. See ai-worker.log")
                elif self.phase == "loading" and self.clock() - self.started >= 60:
                    self.fail("Model loading exceeded 60 seconds")
                elif self.clock() - self.last_heartbeat >= 2:
                    self.fail("AI worker missed its two-second heartbeat")
        return before != (self.phase, self.active)
