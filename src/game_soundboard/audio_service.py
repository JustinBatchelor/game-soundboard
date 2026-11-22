"""One audio-graph owner per library, independent of panel lifetime."""
from __future__ import annotations
import argparse
import fcntl
import hashlib
import json
import os
from pathlib import Path
import signal
import socket
import subprocess
import sys
import threading
import time

from .library import DEFAULTS, data_directory
from .routing import Routing

UNIT = "game-soundboard-audio.service"
MAX_MESSAGE = 256 * 1024


def socket_path(root):
    runtime = os.environ.get("XDG_RUNTIME_DIR")
    if not runtime:
        raise RuntimeError("A logged-in desktop session (XDG_RUNTIME_DIR) is required.")
    ident = hashlib.sha256(str(Path(root).absolute()).encode()).hexdigest()[:16]
    return Path(runtime) / f"game-soundboard-audio-{ident}.sock"


def receive(sock):
    data = bytearray()
    while b"\n" not in data:
        chunk = sock.recv(8192)
        if not chunk:
            raise RuntimeError("Audio service connection closed")
        data.extend(chunk)
        if len(data) > MAX_MESSAGE:
            raise ValueError("Audio service message too large")
    return json.loads(data.split(b"\n", 1)[0])


def send(sock, value):
    sock.sendall(json.dumps(value).encode() + b"\n")


class AudioClient:
    def __init__(self, root):
        self.root = Path(root).absolute()
        self.path = socket_path(self.root)

    def request(self, command="status", **values):
        with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as sock:
            sock.settimeout(10)
            sock.connect(str(self.path))
            send(sock, {"command": command, **values})
            response = receive(sock)
        if response.get("error"):
            raise RuntimeError(response["error"])
        return response["result"]

    def ensure_running(self):
        # A server with a routing error is still running; don't start competitors.
        try:
            self.request("ping")
            return
        except (OSError, RuntimeError):
            pass
        config = Path(os.environ.get("XDG_CONFIG_HOME", Path.home() / ".config"))
        installed = config / "systemd/user" / UNIT
        if "SOUNDBOARD_DATA_DIR" not in os.environ and self.root == data_directory().absolute() and installed.is_file():
            result = subprocess.run(["systemctl", "--user", "start", UNIT],
                                    capture_output=True, text=True, timeout=10)
            if result.returncode:
                raise RuntimeError("Could not start microphone service: " + result.stderr.strip())
        else:
            # Source checkouts and isolated test libraries also keep a daemon after
            # panel exit. Explicit service shutdown (SIGTERM) removes their graph.
            self.root.mkdir(parents=True, exist_ok=True)
            with (self.root / "audio-service.log").open("ab") as log:
                subprocess.Popen([sys.executable, "-m", "game_soundboard.audio_service",
                                  "--data-dir", str(self.root)], stdin=subprocess.DEVNULL,
                                 stdout=log, stderr=subprocess.STDOUT, start_new_session=True)
        deadline = time.monotonic() + 8
        while time.monotonic() < deadline:
            try:
                self.request("ping")
                return
            except (OSError, RuntimeError):
                time.sleep(.1)
        raise RuntimeError("Microphone service did not start. See audio-service.log or the user journal.")


class AudioService:
    def __init__(self, root, router=None):
        self.root = Path(root).absolute()
        self.router = router or Routing(self.root)
        self.state = None
        self.error = "Audio routing is starting"

    def refresh(self):
        try:
            path = self.root / "library.json"
            document = json.loads(path.read_text()) if path.exists() else {"version": 1}
            if document.get("version") != 1:
                raise ValueError("Unsupported library version")
            settings = dict(DEFAULTS, **document.get("settings", {}))
            initial = None
            if not settings.get("devices_initialized"):
                sources, sinks = self.router.devices()
                mic = self.router.call("get-default-source")
                out = self.router.call("get-default-sink")
                settings["microphone"] = mic if mic in {d["name"] for d in sources} else ""
                settings["output"] = out if out in {d["name"] for d in sinks} else ""
                settings["devices_initialized"] = True
                initial = settings.copy()
            self.state = self.router.reconcile(settings)
            self.state["initial_settings"] = initial
            self.state["persistent"] = True
            self.state["message"] += " · microphone stays on when panel closes"
            self.error = ""
        except Exception as exc:
            self.error = str(exc)

    def handle(self, request):
        command = request.get("command")
        if command == "ping":
            return {"pid": os.getpid()}
        if command == "refresh":
            self.refresh()
        elif command == "connect":
            self.router.connect_app(request["app"])
            self.refresh()
        elif command != "status":
            raise ValueError("Unknown audio service command")
        if self.error:
            raise RuntimeError(self.error)
        return self.state

    def serve(self, stopped):
        path = socket_path(self.root)
        # The lock stays on a stable inode; only its owner may replace the socket
        # or touch this library's audio graph. A second service exits harmlessly.
        with path.with_suffix(".lock").open("a") as lock:
            try:
                fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
            except BlockingIOError:
                return
            path.unlink(missing_ok=True)
            with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as server:
                server.bind(str(path))
                path.chmod(0o600)
                server.listen(8)
                server.settimeout(.5)
                try:
                    next_refresh = 0
                    while not stopped.is_set():
                        if time.monotonic() >= next_refresh:
                            self.refresh()
                            next_refresh = time.monotonic() + 3
                        try:
                            connection, _ = server.accept()
                        except socket.timeout:
                            continue
                        with connection:
                            connection.settimeout(2)
                            try:
                                result = self.handle(receive(connection))
                                send(connection, {"result": result})
                            except (ValueError, KeyError, RuntimeError, OSError, TypeError) as exc:
                                try:
                                    send(connection, {"error": str(exc)})
                                except OSError:
                                    pass
                finally:
                    path.unlink(missing_ok=True)
                    self.router.cleanup()


def main():
    parser = argparse.ArgumentParser(description="Persistent Game Soundboard microphone")
    parser.add_argument("--data-dir", type=Path, default=data_directory())
    args = parser.parse_args()
    stopped = threading.Event()
    signal.signal(signal.SIGTERM, lambda *_: stopped.set())
    signal.signal(signal.SIGINT, lambda *_: stopped.set())
    AudioService(args.data_dir).serve(stopped)


if __name__ == "__main__":
    main()
