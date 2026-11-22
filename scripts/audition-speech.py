#!/usr/bin/env python3
"""Generate matched, offline auditions; never connects to audio devices."""
import argparse
import json
import os
from pathlib import Path
import subprocess
import sys
import time

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
from game_soundboard.library import ffmpeg_binary

PHRASES = [
    "We are going to win this round. Watch the flank and stay together.",
    "The bomb is planted. Cover me while I defuse it.",
    "That was an incredible shot. I cannot believe you hit that!",
    "Save your money this round. We can buy together next round.",
    "There are two players on the left. Wait for my flash, then push.",
]


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("directory", type=Path, help="Contains trump-voice.json and musk-voice.json")
    args = parser.parse_args()
    folder = args.directory.resolve()
    reports = []
    for voice in ("trump", "musk"):
        for engine in ("f5", "chatterbox"):
            profile = json.loads((folder / (voice + "-voice.json")).read_text())
            profile["engine"] = engine
            for index, text in enumerate(PHRASES, 1):
                ident = f"{voice}-{engine}-{index}"
                job = {"job_id": ident, "text": text, "voice": profile,
                       "assets": str(ROOT / ".runtime/tts-assets"),
                       "output": str(folder / (ident + ".wav")), "ffmpeg": ffmpeg_binary(), "seed": 42}
                path = folder / ("job-" + ident + ".json")
                path.write_text(json.dumps(job, indent=2))
                events = folder / (ident + ".events.jsonl")
                ready = []
                if events.exists() and Path(job["output"]).exists():
                    ready = [e for line in events.read_text().splitlines()
                             if (e := json.loads(line)).get("phase") == "ready"]
                if not ready:
                    start = time.monotonic()
                    env = dict(os.environ, HF_HOME=str(ROOT / ".runtime/tts-hf-cache"),
                               MPLCONFIGDIR=str(folder / "mpl"),
                               PATH=str(folder / "bin") + os.pathsep + os.environ.get("PATH", ""))
                    with events.open("w") as out, (folder / (ident + ".log")).open("w") as log:
                        result = subprocess.run([str(ROOT / f".runtime/tts-{engine}-venv/bin/python"),
                            str(ROOT / "src/game_soundboard/speech_worker.py"), str(path)],
                            stdout=out, stderr=log, env=env, timeout=180)
                    ready = [e for line in events.read_text().splitlines()
                             if (e := json.loads(line)).get("phase") == "ready"]
                    if result.returncode or not ready:
                        raise RuntimeError(f"{ident} failed: {events.read_text()}")
                    ready[-1]["wall_seconds"] = time.monotonic() - start
                reports.append(dict(ready[-1], text=text))
                (folder / "comparison.json").write_text(json.dumps(reports, indent=2))
                print(ident, round(ready[-1]["total_seconds"], 2), "seconds", flush=True)


if __name__ == "__main__":
    main()
