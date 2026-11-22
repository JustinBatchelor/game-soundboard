from __future__ import annotations
import hashlib
import json
import os
from pathlib import Path
import shutil
import subprocess


def pactl_binary():
    if path := os.environ.get("SOUNDBOARD_PACTL") or shutil.which("pactl"):
        return path
    local = Path(__file__).resolve().parents[2] / ".runtime/usr/bin/pactl"
    if local.exists():
        return str(local)
    raise RuntimeError("pactl is missing. Install pulseaudio-utils (see README).")


class Routing:
    """Synchronous control plane, run exclusively in the routing worker thread."""
    def __init__(self, identity, run=None):
        self.prefix = "gsb_" + hashlib.sha256(str(identity).encode()).hexdigest()[:12]
        self.clips = self.prefix + "_clips"
        self.mix = self.prefix + "_mix"
        self.source = self.prefix + "_microphone"
        self.run_override = run
        self.modules = {}
        self.config = None
        self.initialized = False

    def call(self, *args):
        if self.run_override:
            return self.run_override(*args)
        result = subprocess.run([pactl_binary(), *map(str, args)], capture_output=True,
                                text=True, timeout=5)
        if result.returncode:
            raise RuntimeError(result.stderr.strip() or "PulseAudio connection failed")
        return result.stdout.strip()

    def listing(self, kind):
        values = json.loads(self.call("-f", "json", "list", kind))
        if kind == "modules" and any("index" not in m for m in values):
            # Ubuntu pactl 17 JSON omits module indices; the short format retains them.
            values = []
            for line in self.call("list", "short", "modules").splitlines():
                fields = line.split("\t")
                if len(fields) >= 3 and fields[0].isdigit():
                    values.append({"index": int(fields[0]), "name": fields[1], "argument": fields[2]})
        return values

    def owned(self, module):
        # Ownership is an exact token, never a broad substring of another app's config.
        import shlex
        try:
            tokens = shlex.split(module.get("argument", ""))
        except ValueError:
            return False
        return any(t in tokens for t in [f"sink_name={self.clips}", f"sink_name={self.mix}",
            f"source_name={self.source}", f"sink={self.clips}", f"sink={self.mix}",
            f"source={self.clips}.monitor"])

    def cleanup(self):
        try:
            owned = [m for m in self.listing("modules") if self.owned(m)]
            # Connections and remap source go before the two sinks.
            owned.sort(key=lambda m: m["name"] == "module-null-sink")
            for module in owned:
                try:
                    self.call("unload-module", str(module["index"]))
                except RuntimeError:
                    pass
        finally:
            self.modules.clear()
            self.config = None

    def load(self, key, module, *args):
        self.modules[key] = int(self.call("load-module", module, *args))

    def devices(self):
        sources = [s for s in self.listing("sources") if s.get("monitor_of_sink") in (None, 4294967295)
                   and not s.get("monitor_source") and not s["name"].endswith(".monitor")
                   and not s["name"].startswith("gsb_")]
        sinks = [s for s in self.listing("sinks") if not s["name"].startswith("gsb_")]
        return sources, sinks

    def reconcile(self, settings):
        if not self.initialized:
            self.cleanup()
            self.initialized = True
        modules = {int(m["index"]): m for m in self.listing("modules")}
        if any(i not in modules or not self.owned(modules[i]) for i in self.modules.values()):
            self.cleanup()
        sources, sinks = self.devices()
        mic = settings["microphone"] if settings["microphone"] in {s["name"] for s in sources} else ""
        out = settings["output"] if settings["output"] in {s["name"] for s in sinks} else ""
        signature = (mic, out if settings["monitor"] else "")
        try:
            if "clips" not in self.modules:
                for key, name, description in [("clips", self.clips, "Soundboard Clips"),
                                                ("mix", self.mix, "Soundboard Voice Mix")]:
                    self.load(key, "module-null-sink", f"sink_name={name}", "rate=48000", "channels=2",
                              f"sink_properties=device.description='{description}'")
                self.load("source", "module-remap-source", f"master={self.mix}.monitor",
                          f"source_name={self.source}", "source_properties=device.description='Soundboard Microphone'",
                          "channels=2", "channel_map=front-left,front-right")
                self.load("clips_mix", "module-loopback", f"source={self.clips}.monitor", f"sink={self.mix}",
                          "source_dont_move=true", "sink_dont_move=true", "latency_msec=20")
            if signature != self.config:
                for key in ("mic", "monitor"):
                    if key in self.modules:
                        self.call("unload-module", str(self.modules.pop(key)))
                if mic:
                    self.load("mic", "module-loopback", f"source={mic}", f"sink={self.mix}",
                              "source_dont_move=true", "sink_dont_move=true", "latency_msec=20")
                if signature[1]:
                    self.load("monitor", "module-loopback", f"source={self.clips}.monitor", f"sink={out}",
                              "source_dont_move=true", "sink_dont_move=true", "latency_msec=20")
                self.config = signature
            for stream in self.listing("sink-inputs"):
                if "mic" in self.modules and str(stream.get("owner_module")) == str(self.modules["mic"]):
                    self.call("set-sink-input-volume", str(stream["index"]), f'{round(settings["mic_volume"] * 100)}%')
                    self.call("set-sink-input-mute", str(stream["index"]), "1" if settings["mic_muted"] else "0")
        except Exception:
            self.cleanup()
            raise
        return {"sources": sources, "sinks": sinks, "clip_sink": self.clips,
                "output": out, "microphone": mic,
                "message": "Virtual microphone ready" + (" · microphone disconnected" if not mic else "")
                           + (" · headphones disconnected" if not out else "")}
