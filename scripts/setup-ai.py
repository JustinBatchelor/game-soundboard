#!/usr/bin/env python3
"""Opt-in, isolated Linux/CUDA runtime. Does not download celebrity models."""
import os
from pathlib import Path
import shutil
import subprocess
import sys
import urllib.request

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src/game_soundboard"))
from ai_backend import ASSETS, COMMIT, digest


def run(*args):
    subprocess.run(list(map(str, args)), cwd=ROOT, check=True)


def main():
    runtime = ROOT / ".runtime"
    runtime.mkdir(exist_ok=True)
    uv = shutil.which("uv") or str(Path.home() / ".local/bin/uv")
    upstream = runtime / "voice-changer"
    if not upstream.exists():
        run("git", "clone", "--no-checkout", "--filter=blob:none", "https://github.com/w-okada/voice-changer.git", upstream)
    if subprocess.check_output(["git", "-C", str(upstream), "status", "--porcelain"], text=True).strip():
        raise RuntimeError("Backend checkout has local edits; preserve them before running setup")
    run("git", "-C", upstream, "fetch", "--depth=1", "origin", COMMIT)
    run("git", "-C", upstream, "checkout", "--detach", COMMIT)
    python = runtime / "ai-venv/bin/python"
    if not python.exists():
        run(uv, "venv", "--python", "3.10", runtime / "ai-venv")
    run(uv, "pip", "sync", "--python", python, ROOT / "requirements-ai.lock")
    assets = runtime / "ai-assets"
    assets.mkdir(exist_ok=True)
    for name, (url, expected) in ASSETS.items():
        target = assets / name
        if target.exists() and digest(target) == expected:
            continue
        part = target.with_suffix(".download")
        try:
            with urllib.request.urlopen(url, timeout=60) as response, part.open("wb") as output:
                shutil.copyfileobj(response, output)
            if digest(part) != expected:
                raise RuntimeError("Downloaded feature weights failed SHA-256 verification: " + name)
            part.replace(target)
        finally:
            part.unlink(missing_ok=True)
    run(python, "-c", "import torch; print('CUDA available:', torch.cuda.is_available()); print('Torch:', torch.__version__)")
    print("AI runtime installed. Import a documented RVC model in AI models… and audition before gaming.")


if __name__ == "__main__":
    main()
