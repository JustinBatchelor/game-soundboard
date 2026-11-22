"""Adapter for one pinned w-okada RVC implementation (isolated Python 3.10)."""
import hashlib
from pathlib import Path
import subprocess
import sys

COMMIT = "7e2ce5c4800010bbc7604968bd29b802534fac05"
ASSETS = {
    "hubert_base.pt": ("https://huggingface.co/ddPn08/rvc-webui-models/resolve/main/embeddings/hubert_base.pt",
                       "f54b40fd2802423a5643779c4861af1e9ee9c1564dc9d32f54f20b5ffba7db96"),
    "rmvpe.pt": ("https://huggingface.co/wok000/weights/resolve/main/rmvpe/rmvpe_20231006.pt",
                 "6d62215f4306e3ca278246188607209f09af3dc77ed4232efdd069798c4ec193"),
}


def digest(path):
    h = hashlib.sha256()
    with Path(path).open("rb") as f:
        while chunk := f.read(1024 * 1024):
            h.update(chunk)
    return h.hexdigest()


class Backend:
    def __init__(self, runtime, directory, record, pitch=0, profile="responsive"):
        runtime, directory = Path(runtime), Path(directory)
        upstream = runtime / "voice-changer"
        actual = subprocess.check_output(["git", "-C", str(upstream), "rev-parse", "HEAD"], text=True).strip()
        if actual != COMMIT:
            raise RuntimeError("AI backend revision differs from the tested pin; rerun setup-ai.py")
        if digest(directory / "model.pth") != record["sha256"]:
            raise RuntimeError("Model checksum mismatch; reimport the model")
        from ai_validate import validate
        info = validate(directory / "model.pth")
        assets = runtime / "ai-assets"
        for name, (_, expected) in ASSETS.items():
            if digest(assets / name) != expected:
                raise RuntimeError("AI feature weights failed verification: " + name)
        import torch
        if not torch.cuda.is_available():
            raise RuntimeError("CUDA is unavailable; keeping Normal voice")
        torch.set_num_threads(2)
        # Upstream calls torch.load without specifying safety. Only the pinned,
        # checksum-verified HuBERT feature extractor needs legacy fairseq objects.
        # User-supplied voice checkpoints ALWAYS retain weights_only=True.
        original_load = torch.load
        trusted = (assets / "hubert_base.pt").resolve()
        def restricted_load(path, *args, **kwargs):
            name = path if isinstance(path, (str, Path)) else getattr(path, "name", None)
            candidate = Path(name).resolve() if isinstance(name, (str, Path)) else None
            kwargs["weights_only"] = candidate != trusted
            return original_load(path, *args, **kwargs)
        torch.load = restricted_load
        sys.path.insert(0, str(upstream / "server"))
        from mods.log_control import VoiceChangaerLogger
        import logging
        logger = VoiceChangaerLogger.get_instance().getLogger()
        if not logger.handlers:
            logger.addHandler(logging.StreamHandler(sys.stderr))
        logger.setLevel(logging.WARNING)
        from voice_changer.utils.VoiceChangerParams import VoiceChangerParams
        from data.ModelSlot import RVCModelSlot
        from const import EnumInferenceTypes
        from voice_changer.RVC.RVCr2 import RVCr2
        from voice_changer.VoiceChangerV2 import VoiceChangerV2
        params = VoiceChangerParams(**{key: "" for key in VoiceChangerParams.__dataclass_fields__})
        params.model_dir = str(directory.parent)
        params.hubert_base = str(trusted)
        params.rmvpe = str(assets / "rmvpe.pt")
        params.content_vec_500_onnx_on = False
        v2 = info["version"] == "v2"
        kind = (EnumInferenceTypes.pyTorchRVCv2 if v2 else EnumInferenceTypes.pyTorchRVC) if info["f0"] else (
            EnumInferenceTypes.pyTorchRVCv2Nono if v2 else EnumInferenceTypes.pyTorchRVCNono)
        slot = RVCModelSlot(slotIndex=directory.name, modelFile="model.pth", indexFile="",
                            samplingRate=info["sample_rate"], modelType=kind.value, f0=info["f0"],
                            embChannels=768 if v2 else 256, embOutputLayer=12 if v2 else 9,
                            useFinalProj=not v2, defaultTune=int(pitch), defaultIndexRatio=0)
        model = RVCr2(params, slot)
        model.settings.gpu = 0
        model.settings.f0Detector = "rmvpe"
        model.settings.extraConvertSize = 16384 if profile == "responsive" else 32768
        # RMVPE reflection padding needs >=16 mel frames. Keep past context
        # for short live blocks; this is look-back, not additional look-ahead.
        model.settings.silenceFront = 0
        model.initialize()
        if model.pipeline is None:
            raise RuntimeError("RVC model pipeline failed to initialize; see AI worker log")
        self.inference_error = ""
        inference = model.inference
        def checked_inference(*args, **kwargs):
            try:
                return inference(*args, **kwargs)
            except Exception as exc:
                self.inference_error = str(exc) or type(exc).__name__
                raise
        model.inference = checked_inference
        self.engine = VoiceChangerV2(params)
        self.engine.setModel(model)
        self.engine.update_settings("crossFadeOverlapSize", 480 if profile == "responsive" else 960)
        self.frames = 3840 if profile == "responsive" else 5760
        self.torch = torch
        self.first_block = True

    def convert(self, samples):
        self.inference_error = ""
        with self.torch.inference_mode():
            result, performance = self.engine.on_request(samples)
        if self.inference_error:
            raise RuntimeError("RVC inference failed: " + self.inference_error)
        if self.first_block and len(result) == 4096:
            # Pinned upstream emits a fixed 4096-sample silent SOLA primer.
            # This call is warm-up only and never reaches the voice bus.
            import numpy as np
            result = np.zeros(self.frames, dtype=np.int16)
        self.first_block = False
        if len(result) != self.frames:
            raise RuntimeError("RVC inference failed (invalid output); see AI worker log")
        return result
