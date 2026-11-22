"""Explicit starter downloads; no third-party audio is bundled with source code."""
import html
import re
import tempfile
import subprocess
import sys
from pathlib import Path
from urllib.parse import urljoin, urlparse
from urllib.request import Request, urlopen
from .library import ffmpeg_binary

STARTERS = [
    ("bomb-planted", "Bomb has been planted", "https://www.myinstants.com/en/instant/bomb-has-been-planted-csgo-cs2-75763/"),
    ("kaboom", "Kaboom!", "https://www.youtube.com/watch?v=UO9JSE3aRtw"),
    ("oh-yeah", "Oh Yeah!", "https://www.myinstants.com/en/instant/oh-yeah-kool-aid-man-35548/"),
    ("this-guy-stinks", "Oh brother, this guy stinks!", "https://www.myinstants.com/en/instant/oh-brother-this-guy-stinks/"),
]


def fetch(url, limit):
    req = Request(url, headers={"User-Agent": "Mozilla/5.0 (GameSoundboard personal audio import)"})
    with urlopen(req, timeout=30) as response:
        data = response.read(limit + 1)
        if len(data) > limit:
            raise ValueError("Download exceeds size limit")
        return data


def download_starter(library, entry):
    seed, name, page = entry
    if urlparse(page).hostname == "www.youtube.com":
        return download_youtube_starter(library, entry)
    content = fetch(page, 2_000_000).decode("utf-8")
    candidates = re.findall(r'''(?:https://[^\s"'<>]+)?/media/sounds/[^\s"'<>]+\.mp3''', content)
    if not candidates:
        raise ValueError(f"No downloadable MP3 found for {name}. Open the source page and import manually.")
    url = urljoin(page, html.unescape(candidates[0]))
    if urlparse(url).hostname not in {"www.myinstants.com", "myinstants.com"}:
        raise ValueError("Unexpected audio host")
    with tempfile.TemporaryDirectory(prefix="soundboard-download-") as tmp:
        file = Path(tmp) / f"{seed}.mp3"
        file.write_bytes(fetch(url, 20_000_000))
        record = library.prepare(file, name, page, seed)
        record["download_url"] = url
        return record


def download_youtube_starter(library, entry):
    """Import the user-selected video audio; never substitute a similarly named clip."""
    seed, name, page = entry
    with tempfile.TemporaryDirectory(prefix="soundboard-youtube-") as tmp:
        root = Path(tmp)
        result = subprocess.run([sys.executable, "-m", "yt_dlp", "--no-playlist", "--no-cache-dir",
            "--socket-timeout", "20", "--retries", "2", "--max-filesize", "20M",
            "-f", "bestaudio", "-o", str(root / "audio.%(ext)s"), page],
            capture_output=True, text=True, timeout=120)
        media = [p for p in root.glob("audio.*") if p.suffix not in {".part", ".ytdl"}]
        if result.returncode or len(media) != 1:
            raise ValueError("Could not download the selected YouTube recording: " + result.stderr[-500:])
        wav = root / "decoded.wav"
        result = subprocess.run([ffmpeg_binary(), "-nostdin", "-v", "error", "-y", "-i", str(media[0]),
            "-vn", "-ar", "48000", "-ac", "2", "-c:a", "pcm_s16le", str(wav)],
            capture_output=True, text=True, timeout=60)
        if result.returncode:
            raise ValueError("Could not decode the selected video audio: " + result.stderr[-500:])
        record = library.prepare(wav, name, page, seed)
        record["download_url"] = page
        return record
