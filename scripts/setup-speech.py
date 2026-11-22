#!/usr/bin/env python3
"""Install pinned local TTS runtimes and verified weights, not celebrity references."""
import argparse
import json
import os
from pathlib import Path
import shutil
import subprocess

ROOT = Path(__file__).resolve().parents[1]


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--engine", choices=("f5", "chatterbox", "both"), default="both")
    args = parser.parse_args()
    runtime = ROOT / ".runtime"
    runtime.mkdir(exist_ok=True)
    uv = shutil.which("uv") or str(Path.home() / ".local/bin/uv")
    engines = ("f5", "chatterbox") if args.engine == "both" else (args.engine,)
    env = dict(os.environ, HF_HUB_DISABLE_TELEMETRY="1", HF_HUB_DISABLE_XET="1",
               HF_HOME=str(runtime / "tts-hf-cache"))
    for engine in engines:
        venv = runtime / ("tts-" + engine + "-venv")
        if not (venv / "bin/python").exists():
            subprocess.run([uv, "venv", "--python", "3.11", str(venv)], check=True, env=env)
        subprocess.run([uv, "pip", "sync", "--python", str(venv / "bin/python"),
                        str(ROOT / ("requirements-speech-" + engine + ".lock"))], check=True, env=env)
        subprocess.run([str(venv / "bin/python"), "-c", DOWNLOAD,
                        str(ROOT / "scripts/speech-assets.json"), str(runtime / "tts-assets"), engine],
                       check=True, env=env)
    print("Speech runtimes installed. Voices remain unavailable until their local listening check is approved.")


DOWNLOAD = '''
import hashlib,json,sys
from pathlib import Path
from huggingface_hub import snapshot_download
catalog=json.loads(Path(sys.argv[1]).read_text()); root=Path(sys.argv[2]); engine=sys.argv[3]
root.mkdir(parents=True,exist_ok=True)
for name in ([engine,'vocos'] if engine=='f5' else [engine]):
    entry=catalog[name]; folder=root/name
    snapshot_download(entry['repo'],revision=entry['revision'],allow_patterns=list(entry['files']),local_dir=folder)
    for relative,expected in entry['files'].items():
        with (folder/relative).open('rb') as stream:
            if hashlib.file_digest(stream,'sha256').hexdigest()!=expected:
                raise RuntimeError('Downloaded speech asset failed checksum: '+relative)
    temp=root/(name+'-manifest.tmp')
    temp.write_text(json.dumps(entry,indent=2));temp.replace(root/(name+'-manifest.json'))
    print('Verified',name,entry['revision'])
'''


if __name__ == "__main__":
    main()
