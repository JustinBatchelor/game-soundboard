"""Opt-in real restricted-checkpoint test; run in the isolated AI environment."""
from pathlib import Path
import sys
import tempfile
import torch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'src/game_soundboard'))
from ai_validate import validate

with tempfile.TemporaryDirectory() as tmp:
    marker = Path(tmp) / 'must-not-exist'
    class Executable:
        def __reduce__(self):
            return (eval, (f"__import__('pathlib').Path({str(marker)!r}).touch()",))
    checkpoint = Path(tmp) / 'executable.pth'
    torch.save({'unexpected': Executable()}, checkpoint)
    try:
        validate(checkpoint)
    except Exception:
        pass
    else:
        raise AssertionError('Executable checkpoint was accepted')
    assert not marker.exists(), 'Checkpoint code executed'
    checkpoint.write_bytes(b'not a model')
    try:
        validate(checkpoint)
    except Exception:
        pass
    else:
        raise AssertionError('Corrupt checkpoint was accepted')
print('PASS: executable and corrupt checkpoints rejected without running checkpoint code')
