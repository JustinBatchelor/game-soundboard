from __future__ import annotations
import hashlib
import json
import os
from pathlib import Path
import shutil
import subprocess
from .voice_effects import PRESETS, effect_arguments


def description_property(kind, text):
    # Module arguments and property lists are parsed separately by the server.
    return f'{kind}_properties=\'device.description="{text}"\''


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
        self.voice = self.prefix + "_voice"
        self.effect = self.prefix + "_effect"
        self.ai_input = self.prefix + "_ai_input"
        self.ai_return = self.prefix + "_ai_return"
        self.run_override = run
        self.modules = {}
        self.config = None
        self.monitor_config = None
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
        sinks = (self.clips, self.mix, self.voice, self.effect, self.ai_input, self.ai_return)
        owned_tokens = [f"{key}={sink}" for sink in sinks for key in ("sink_name", "sink")]
        owned_tokens += [f"source_name={self.source}", f"source={self.clips}.monitor",
                         f"source={self.voice}.monitor", f"source={self.ai_return}.monitor"]
        return any(t in tokens for t in owned_tokens)

    def cleanup(self):
        try:
            owned = [m for m in self.listing("modules") if self.owned(m)]
            # Disconnect streams before effects, and effects before buses.
            owned.sort(key=lambda m: {"module-ladspa-sink": 1, "module-null-sink": 2}.get(m["name"], 0))
            for module in owned:
                try:
                    self.call("unload-module", str(module["index"]))
                except RuntimeError:
                    pass
        finally:
            self.modules.clear()
            self.config = None
            self.monitor_config = None

    def adopt(self):
        """Recover a complete owned core after a crash without replacing its source.

        Only exact expected module types and connections qualify. Incomplete or
        ambiguous graphs use the existing cleanup/rebuild path.
        """
        import shlex
        modules = [m for m in self.listing("modules") if self.owned(m)]
        expected = {
            "clips": ("module-null-sink", {f"sink_name={self.clips}", "rate=48000", "channels=2"}),
            "mix": ("module-null-sink", {f"sink_name={self.mix}", "rate=48000", "channels=2"}),
            "voice": ("module-null-sink", {f"sink_name={self.voice}", "rate=48000", "channels=2"}),
            "source": ("module-remap-source", {f"source_name={self.source}", f"master={self.mix}.monitor"}),
            "clips_mix": ("module-loopback", {f"source={self.clips}.monitor", f"sink={self.mix}"}),
            "voice_mix": ("module-loopback", {f"source={self.voice}.monitor", f"sink={self.mix}"}),
        }
        found = {}
        for key, (kind, tokens) in expected.items():
            matches = [m for m in modules if m["name"] == kind and tokens.issubset(set(shlex.split(m.get("argument", ""))))]
            if len(matches) != 1:
                return False
            found[key] = int(matches[0]["index"])
        self.modules = found
        # Normal is the service startup mode. Remove stale effects/AI routes,
        # preserving core buses, source and the applications attached to them.
        extras = [m for m in modules if int(m["index"]) not in found.values()]
        extras.sort(key=lambda m: {"module-ladspa-sink": 1, "module-null-sink": 2}.get(m["name"], 0))
        for module in extras:
            self.call("unload-module", str(module["index"]))
        return True

    def load(self, key, module, *args):
        self.modules[key] = int(self.call("load-module", module, *args))

    def devices(self):
        sources = [s for s in self.listing("sources") if s.get("monitor_of_sink") in (None, 4294967295)
                   and not s.get("monitor_source") and not s["name"].endswith(".monitor")
                   and not s["name"].startswith("gsb_")]
        sinks = [s for s in self.listing("sinks") if not s["name"].startswith("gsb_")]
        return sources, sinks

    def unload(self, *keys):
        for key in keys:
            if key in self.modules:
                self.call("unload-module", str(self.modules[key]))
                del self.modules[key]

    def loopback(self, key, source, sink):
        self.load(key, "module-loopback", f"source={source}", f"sink={sink}",
                  "source_dont_move=true", "sink_dont_move=true", "latency_msec=20")

    def capture_apps(self):
        sources = {str(s["index"]): s["name"] for s in self.listing("sources") if "index" in s}
        apps = []
        for stream in self.listing("source-outputs"):
            props = stream.get("properties", {})
            # Module loopbacks/remaps are routing infrastructure, not game inputs.
            if stream.get("owner_module") not in (None, "", 4294967295, "4294967295"):
                continue
            if props.get("resample.peaks") in (True, "true") or props.get("stream.monitor") in (True, "true"):
                continue
            name = props.get("application.name")
            if not name or name == "Game Soundboard AI":
                continue
            apps.append({"index": str(stream["index"]), "name": name,
                         "pid": str(props.get("application.process.id", "")),
                         "source": sources.get(str(stream.get("source")), ""),
                         "requested_source": str(props.get("target.object", "")),
                         "connected": sources.get(str(stream.get("source"))) == self.source})
        return apps

    def connect_app(self, requested, *, restore=False):
        # Revalidate identity: an input can disappear while the dialog is open.
        current = next((a for a in self.capture_apps() if all(a[k] == requested[k]
                       for k in ("index", "name", "pid"))), None)
        if not current:
            raise RuntimeError("That application's input closed. Open its microphone test and try again.")
        if restore and (current.get("requested_source") != self.source or current["connected"]):
            return
        self.call("move-source-output", current["index"], self.source)

    def reconcile(self, settings):
        if not self.initialized:
            if not self.adopt():
                self.cleanup()
            self.initialized = True
        modules = {int(m["index"]): m for m in self.listing("modules")}
        if any(i not in modules or not self.owned(modules[i]) for i in self.modules.values()):
            self.cleanup()
        sources, sinks = self.devices()
        mic = settings["microphone"] if settings["microphone"] in {s["name"] for s in sources} else ""
        out = settings["output"] if settings["output"] in {s["name"] for s in sinks} else ""
        preset = settings.get("voice_effect", "normal")
        ai_mode = settings.get("_ai_mode", "normal")
        signature = (mic, preset, ai_mode)
        monitors = (out if settings["monitor"] else "", out if settings.get("voice_monitor") else "")
        voice_error = ""
        rebuilt = False
        try:
            if "clips" not in self.modules:
                rebuilt = True
                for key, name, description in [("clips", self.clips, "Soundboard Clips"),
                                                ("mix", self.mix, "Soundboard Voice Mix"),
                                                ("voice", self.voice, "Soundboard Processed Voice"),
                                                ("ai_input", self.ai_input, "Soundboard AI Input"),
                                                ("ai_return", self.ai_return, "Soundboard AI Return")]:
                    self.load(key, "module-null-sink", f"sink_name={name}", "rate=48000", "channels=2",
                              description_property("sink", description))
                self.load("source", "module-remap-source", f"master={self.mix}.monitor",
                          f"source_name={self.source}", description_property("source", "Soundboard Microphone"),
                          "channels=2", "channel_map=front-left,front-right")
                self.loopback("clips_mix", self.clips + ".monitor", self.mix)
                self.loopback("voice_mix", self.voice + ".monitor", self.mix)
            # A pre-AI service's core can be adopted in place during upgrade.
            for key, name, description in [("ai_input", self.ai_input, "Soundboard AI Input"),
                                            ("ai_return", self.ai_return, "Soundboard AI Return")]:
                if key not in self.modules:
                    self.load(key, "module-null-sink", f"sink_name={name}", "rate=48000", "channels=2",
                              description_property("sink", description))
            if signature != self.config:
                self.unload("mic", "effect", "ai_mic", "ai_voice")
                self.config = None
                try:
                    if mic:
                        target = self.voice
                        if ai_mode in ("loading", "active"):
                            self.loopback("ai_mic", mic, self.ai_input)
                        if ai_mode == "active":
                            self.loopback("ai_voice", self.ai_return + ".monitor", self.voice)
                        elif preset != "normal":
                            self.load("effect", "module-ladspa-sink", f"sink_name={self.effect}",
                                      f"master={self.voice}", *effect_arguments(preset),
                                      "rate=48000", "channels=1", "channel_map=mono",
                                      description_property("sink", "Soundboard Voice Effect"))
                            target = self.effect
                        if ai_mode != "active":
                            self.loopback("mic", mic, target)
                    self.config = signature
                except Exception as exc:
                    # Keep clips available, but never substitute an unprocessed mic.
                    self.unload("mic", "effect", "ai_mic", "ai_voice")
                    voice_error = "Voice suspended: " + str(exc) + " · select Normal to bypass"
            if monitors != self.monitor_config:
                self.unload("monitor", "voice_monitor")
                if monitors[0]:
                    self.loopback("monitor", self.clips + ".monitor", out)
                if monitors[1]:
                    self.loopback("voice_monitor", self.voice + ".monitor", out)
                self.monitor_config = monitors
            # Gain and mute are post-effect, affecting chat and optional self-monitoring.
            voice_modules = {str(self.modules[k]) for k in ("voice_mix", "voice_monitor") if k in self.modules}
            for stream in self.listing("sink-inputs"):
                if str(stream.get("owner_module")) in voice_modules:
                    self.call("set-sink-input-volume", str(stream["index"]), f'{round(settings["mic_volume"] * 100)}%')
                    self.call("set-sink-input-mute", str(stream["index"]), "1" if settings["mic_muted"] else "0")
        except Exception:
            self.cleanup()
            raise
        apps = self.capture_apps()
        reconnect_errors = []
        if rebuilt:
            # Native PipeWire clients (including CS2/SDL) can keep requesting our
            # named device while WirePlumber parks them on a fallback after exit.
            # Restore that request when the device returns. Do not police moves
            # on later health checks or take over apps requesting another input.
            for app in apps:
                if app["requested_source"] == self.source and not app["connected"]:
                    try:
                        self.connect_app(app, restore=True)
                    except RuntimeError as exc:
                        reconnect_errors.append(f'Could not reconnect {app["name"]}: {exc} · use Connect app')
            apps = self.capture_apps()
        connected = sorted({a["name"] for a in apps if a["connected"]})
        mic_label = next((s.get("description", s["name"]) for s in sources if s["name"] == mic), "Disconnected")
        messages = ["Mic: " + mic_label,
                    "Effect: " + PRESETS.get(preset, (preset,))[0],
                    "Receiving apps: " + ", ".join(connected) if connected else
                    "No app is using Soundboard Microphone · select it in your game or use Connect app"]
        if voice_error:
            messages.append(voice_error)
        if settings["mic_muted"]:
            messages.append("Voice muted")
        if not out:
            messages.append("Headphones disconnected")
        messages.extend(reconnect_errors)
        return {"sources": sources, "sinks": sinks, "clip_sink": self.clips,
                "output": out, "microphone": mic, "capture_apps": apps,
                "voice_error": voice_error, "message": " · ".join(messages)}
