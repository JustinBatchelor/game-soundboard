"""No UI imports, network listeners, or system default audio devices."""
import collections
import json
import os
from pathlib import Path
import queue
import select
import signal
import subprocess
import sys
import threading
import time

from ai_backend import Backend


def run(config, emit):
    def watch_parent():
        while True:
            if os.getppid() != config["parent_pid"]:
                os.killpg(os.getpgrp(), signal.SIGKILL)
            time.sleep(.25)
    threading.Thread(target=watch_parent, daemon=True).start()
    loading = threading.Event()
    loading.set()
    def loading_heartbeat():
        while loading.is_set():
            emit({"event": "heartbeat", "phase": "loading"})
            time.sleep(.3)
    threading.Thread(target=loading_heartbeat, daemon=True).start()
    record = config["model"]
    backend = Backend(config["runtime"], config["directory"], record,
                      config["pitch"], config["profile"])
    import numpy as np
    # Warm up pitch extraction, CUDA and SOLA before replacing normal speech.
    warmup = (np.sin(np.arange(backend.frames) * 2 * np.pi * 220 / 48000) * 1800).astype(np.int16)
    for _ in range(4):
        backend.convert(warmup)
    loading.clear()
    capture, output = None, None
    try:
        common = ["--raw", "--format=s16le", "--rate=48000", "--channels=1",
                  "--latency-msec=20", "--process-time-msec=10", "--client-name=Game Soundboard AI",
                  "--property=node.dont-fallback=true", "--property=node.dont-reconnect=true"]
        capture = subprocess.Popen([config["pacat"], "--record", "--device=" + config["input"], *common],
                                   stdout=subprocess.PIPE, stdin=subprocess.DEVNULL, pipesize=4096)
        output = subprocess.Popen([config["pacat"], "--playback", "--device=" + config["output"], *common],
                                  stdin=subprocess.PIPE, stdout=subprocess.DEVNULL, pipesize=4096)
        latest = queue.Queue(maxsize=1)
        drops = [0]
        frames = backend.frames
        def read_audio():
            try:
                while True:
                    data = bytearray()
                    while len(data) < frames * 2:
                        chunk = capture.stdout.read(frames * 2 - len(data))
                        if not chunk:
                            raise RuntimeError("AI input audio stream closed")
                        data.extend(chunk)
                    item = (time.monotonic(), bytes(data))
                    try:
                        latest.put_nowait(item)
                    except queue.Full:
                        try:
                            latest.get_nowait()
                        except queue.Empty:
                            pass
                        drops[0] += 1
                        latest.put_nowait(item)
            except Exception:
                pass  # The main thread times out and reports stream failure.
        threading.Thread(target=read_audio, daemon=True).start()
        os.set_blocking(output.stdin.fileno(), False)
        timings = collections.deque(maxlen=250)
        ready, last_report, late = False, 0., 0
        while True:
            stamp, data = latest.get(timeout=1)
            if time.monotonic() - stamp > .2:
                drops[0] += 1
                continue
            start = time.monotonic()
            converted = backend.convert(np.frombuffer(data, dtype="<i2"))
            elapsed = time.monotonic() - start
            timings.append(elapsed * 1000)
            late = late + 1 if elapsed > frames / 48000 else 0
            if late >= 20:
                raise RuntimeError("AI cannot keep up with live audio; use Normal or Balanced")
            if capture.poll() is not None or output.poll() is not None:
                raise RuntimeError("AI audio device disconnected")
            payload = memoryview(converted.astype("<i2").tobytes())
            deadline = time.monotonic() + frames / 48000
            while payload:
                remaining = deadline - time.monotonic()
                if remaining <= 0 or not select.select([], [output.stdin], [], remaining)[1]:
                    raise RuntimeError("AI output buffer stalled; restored Normal to avoid growing delay")
                try:
                    written = os.write(output.stdin.fileno(), payload)
                    payload = payload[written:]
                except BlockingIOError:
                    continue
            if not ready:
                emit({"event": "ready"})
                ready = True
            if time.monotonic() - last_report >= .2:
                emit({"event": "heartbeat", "phase": "active", "processing_ms": round(elapsed * 1000, 1),
                      "processing_p95_ms": round(float(np.percentile(timings, 95)), 1),
                      "overruns": drops[0], "block_ms": frames / 48})
                last_report = time.monotonic()
    finally:
        loading.clear()
        for process in (capture, output):
            if process and process.poll() is None:
                process.kill()
                process.wait(timeout=2)


if __name__ == "__main__":
    protocol = sys.stdout
    sys.stdout = sys.stderr  # Upstream debug prints must never corrupt IPC.
    lock = threading.Lock()
    def emit(value):
        with lock:
            protocol.write(json.dumps(value) + "\n")
            protocol.flush()
    try:
        run(json.loads(sys.stdin.readline()), emit)
    except Exception as exc:
        emit({"event": "error", "reason": str(exc) or type(exc).__name__})
        import traceback
        traceback.print_exc()
        sys.exit(1)
