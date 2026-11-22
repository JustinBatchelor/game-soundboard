"""One offline TTS job per process; no microphone or playback access."""
from pathlib import Path
import contextlib
import ctypes
import hashlib
import json
import os
import signal
import subprocess
import sys
import time


def emit(job, phase, **values):
    print(json.dumps({"job_id": job["job_id"], "phase": phase, **values}),
          file=sys.__stdout__, flush=True)


def verify_assets(root, name):
    manifest = json.loads((root / (name + "-manifest.json")).read_text())
    for relative, expected in manifest["files"].items():
        path = (root / name / relative).resolve()
        if not path.is_relative_to((root / name).resolve()):
            raise ValueError("Invalid model asset path")
        with path.open("rb") as stream:
            actual = hashlib.file_digest(stream, "sha256").hexdigest()
        if actual != expected:
            raise ValueError("Speech model checksum mismatch: " + relative)


def generate(job):
    start = time.monotonic()
    emit(job, "loading")
    text = job["text"].strip()
    if not 1 <= len(text) <= 500:
        raise ValueError("Enter between 1 and 500 characters")
    engine = job["voice"]["engine"]
    if engine not in {"f5", "chatterbox"}:
        raise ValueError("Unsupported speech engine")
    reference = Path(job["voice"]["reference"])
    with reference.open("rb") as stream:
        if hashlib.file_digest(stream, "sha256").hexdigest() != job["voice"]["reference_sha256"]:
            raise ValueError("Voice reference checksum mismatch")
    assets = Path(job["assets"])
    verify_assets(assets, engine)
    if engine == "f5":
        verify_assets(assets, "vocos")
    import torch
    import numpy as np
    import soundfile as sf
    if not torch.cuda.is_available():
        raise RuntimeError("CUDA is unavailable; normal microphone and saved sounds remain usable")
    torch.set_num_threads(4)
    torch.manual_seed(job.get("seed", 42))
    torch.cuda.reset_peak_memory_stats()
    if engine == "f5":
        from f5_tts.api import F5TTS
        model = F5TTS(device="cuda", ckpt_file=str(assets / "f5/F5TTS_v1_Base/model_1250000.safetensors"),
                      vocab_file=str(assets / "f5/F5TTS_v1_Base/vocab.txt"),
                      vocoder_local_path=str(assets / "vocos"))
    else:
        from chatterbox.tts import ChatterboxTTS
        model = ChatterboxTTS.from_local(assets / "chatterbox", "cuda")
    torch.cuda.synchronize()
    load_seconds = time.monotonic() - start
    emit(job, "generating", load_seconds=load_seconds)
    inference_start = time.monotonic()
    if engine == "f5":
        wav, rate, _ = model.infer(ref_file=str(reference), ref_text=job["voice"]["reference_text"],
                                 gen_text=text, nfe_step=32, seed=job.get("seed", 42),
                                 show_info=lambda *_: None)
    else:
        wav = model.generate(text, audio_prompt_path=str(reference), exaggeration=0.65, cfg_weight=0.3)
        wav, rate = wav.detach().cpu().numpy().reshape(-1), model.sr
    torch.cuda.synchronize()
    inference_seconds = time.monotonic() - inference_start
    wav = np.asarray(wav).reshape(-1)
    if not 0 < len(wav) / rate <= 60 or not np.isfinite(wav).all():
        raise ValueError("Generated audio is invalid or exceeds 60 seconds")
    if np.max(np.abs(wav)) < 1e-5:
        raise ValueError("The speech engine returned silence")
    # Avoid introducing digital clipping; never strip an engine's watermark.
    wav = wav / max(1.0, float(np.max(np.abs(wav))) / 0.98)
    output = Path(job["output"])
    raw = output.with_name("raw.wav")
    sf.write(raw, wav, rate, subtype="PCM_16")
    subprocess.run([job["ffmpeg"], "-nostdin", "-v", "error", "-y", "-i", str(raw),
                    "-ar", "48000", "-ac", "2", "-c:a", "pcm_s16le", str(output)],
                   check=True, timeout=20)
    raw.unlink()
    emit(job, "ready", output=str(output), load_seconds=load_seconds,
         inference_seconds=inference_seconds, total_seconds=time.monotonic() - start,
         duration_seconds=len(wav) / rate,
         peak_gpu_mib=torch.cuda.max_memory_allocated() / (1024 * 1024))


def main():
    job = json.loads(Path(sys.argv[1]).read_text())
    if parent := job.get("parent_pid"):
        # Linux: a crashed panel must not leave a GPU worker behind.
        if ctypes.CDLL(None).prctl(1, signal.SIGKILL, 0, 0, 0) != 0:
            raise RuntimeError("Could not attach speech worker to panel lifetime")
        if os.getppid() != parent:
            return 1
    os.environ.update(HF_HUB_OFFLINE="1", TRANSFORMERS_OFFLINE="1", HF_HUB_DISABLE_TELEMETRY="1")
    try:
        with contextlib.redirect_stdout(sys.stderr):
            generate(job)
        return 0
    except Exception as exc:
        emit(job, "failed", reason=str(exc))
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
