"""Executed only in the isolated AI environment; uses restricted torch loading."""
import json
import math
import sys


def validate(path):
    import torch
    # No safe_globals additions and no unrestricted pickle fallback.
    checkpoint = torch.load(path, map_location="cpu", weights_only=True)
    if not isinstance(checkpoint, dict):
        raise ValueError("Expected an RVC checkpoint dictionary")
    config, weights = checkpoint.get("config"), checkpoint.get("weight")
    if not isinstance(config, (list, tuple)) or len(config) != 18:
        raise ValueError("Only standard RVC v1/v2 inference checkpoints are supported")
    def numeric(value):
        if isinstance(value, (list, tuple)):
            return len(value) < 128 and all(numeric(v) for v in value)
        return type(value) in (int, float) and math.isfinite(value) and abs(value) <= 192000
    if config[9] not in ("1", "2") or not numeric(list(config[:9]) + list(config[10:])) or config[-1] not in (32000, 40000, 48000):
        raise ValueError("Unsupported model configuration/sample rate")
    version = checkpoint.get("version", "v1")
    if version not in ("v1", "v2") or checkpoint.get("f0") not in (0, 1):
        raise ValueError("Unsupported model version or pitch configuration")
    if not isinstance(weights, dict) or not weights or len(weights) > 5000:
        raise ValueError("Invalid model weights")
    if not all(isinstance(k, str) and isinstance(v, torch.Tensor) and v.numel() <= 100_000_000
               for k, v in weights.items()):
        raise ValueError("Expected plain tensor weights")
    embedding = weights.get("emb_g.weight")
    if embedding is None or embedding.ndim != 2 or not 1 <= embedding.shape[0] <= 10000:
        raise ValueError("Missing speaker embedding")
    if config[-3] != embedding.shape[0]:
        raise ValueError("Speaker configuration does not match checkpoint")
    return {"version": version, "sample_rate": config[-1], "f0": bool(checkpoint["f0"])}


if __name__ == "__main__":
    try:
        print(json.dumps(validate(sys.argv[1])))
    except Exception as exc:
        print(str(exc), file=sys.stderr)
        sys.exit(1)
