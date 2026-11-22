"""Local microphone effects supported by PipeWire's LADSPA sink module."""
from pathlib import Path
import shlex


PRESETS = {
    "normal": ("Normal", "", "", ""),
    # Rubber Band's Wet-Dry Mix is the DRY fraction: zero is fully processed.
    "baby": ("Baby / Helium", "ladspa-rubberband.so", "rubberband-live-pitchshifter-mono", "0,8,0,0,0"),
    "deep": ("Deep voice", "ladspa-rubberband.so", "rubberband-live-pitchshifter-mono", "0,-6,0,0,0"),
    "robot": ("Robot", "ringmod_1188.so", "ringmod_1i1o1l", "2,45,1,0,0,0"),
    "radio": ("Radio", "bandpass_iir_1892.so", "bandpass_iir", "1750,2900,4"),
}


def effect_arguments(preset):
    if preset not in PRESETS:
        raise RuntimeError("Unknown voice effect. Select Normal or another preset.")
    _, filename, label, control = PRESETS[preset]
    if not filename:
        return []
    roots = [Path(__file__).resolve().parents[2] / ".runtime/usr/lib/ladspa",
             Path("/usr/lib/ladspa"), Path("/usr/local/lib/ladspa")]
    roots.extend(Path("/usr/lib").glob("*-linux-gnu/ladspa"))
    plugin = next((p / filename for p in roots if (p / filename).is_file()), None)
    if not plugin:
        raise RuntimeError("Voice effects need rubberband-ladspa (4.0+) and swh-plugins. "
                           "Install them or select Normal.")
    return [f"plugin={shlex.quote(str(plugin))}", f"label={label}", f"control={control}"]
