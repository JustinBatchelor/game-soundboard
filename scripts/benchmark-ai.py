"""Offline processing benchmark, never claims microphone-to-output latency.

Run with .runtime/ai-venv/bin/python. Optional input must be 48 kHz mono PCM16.
No physical audio devices are accessed. Output files remain local.
"""
import argparse
import contextlib
import json
from pathlib import Path
import sys
import time
import wave

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src/game_soundboard"))
from ai_backend import Backend

parser = argparse.ArgumentParser()
parser.add_argument("model_directory", type=Path)
parser.add_argument("--input", type=Path)
parser.add_argument("--output", type=Path)
parser.add_argument("--report", type=Path)
parser.add_argument("--profile", choices=["responsive", "balanced"], default="responsive")
parser.add_argument("--seconds", type=int, default=15)
args = parser.parse_args()
import numpy as np
record = json.loads((args.model_directory / "model.json").read_text())
with contextlib.redirect_stdout(sys.stderr):
    start = time.monotonic()
    backend = Backend(ROOT / ".runtime", args.model_directory.absolute(), record, profile=args.profile)
    load_seconds = time.monotonic() - start
    frames = backend.frames
    signal = (np.sin(np.arange(frames) * 2 * np.pi * 220 / 48000) * 1800).astype(np.int16)
    warmup_start = time.monotonic()
    for _ in range(4):
        backend.convert(signal)
    warmup_seconds = time.monotonic() - warmup_start
    samples = np.tile(signal, max(1, int(args.seconds * 48000 / frames)))
    if args.input:
        with wave.open(str(args.input)) as f:
            if (f.getframerate(), f.getnchannels(), f.getsampwidth()) != (48000, 1, 2):
                raise ValueError("Input must be 48 kHz mono PCM16 WAV")
            samples = np.frombuffer(f.readframes(f.getnframes()), dtype="<i2")
    timings, output = [], []
    for offset in range(0, len(samples), frames):
        block = samples[offset:offset + frames]
        if len(block) < frames:
            block = np.pad(block, (0, frames - len(block)))
        start = time.monotonic()
        converted = backend.convert(block)
        timings.append((time.monotonic() - start) * 1000)
        output.append(converted)
report = {"model_sha256": record["sha256"], "profile": args.profile, "blocks": len(timings),
          "load_seconds": load_seconds, "warmup_seconds": warmup_seconds,
          "block_ms": frames / 48, "processing_p50_ms": float(np.percentile(timings, 50)),
          "processing_p95_ms": float(np.percentile(timings, 95)),
          "blocks_over_deadline": sum(t > frames / 48 for t in timings),
          "end_to_end_latency_ms": None, "cs2_fps_impact": None,
          "input": str(args.input) if args.input else "synthetic 220 Hz tone"}
print(json.dumps(report, indent=2))
if args.report:
    args.report.write_text(json.dumps(report, indent=2))
if args.output:
    with wave.open(str(args.output), "wb") as f:
        f.setnchannels(1); f.setsampwidth(2); f.setframerate(48000)
        f.writeframes(np.concatenate(output).astype("<i2").tobytes())
