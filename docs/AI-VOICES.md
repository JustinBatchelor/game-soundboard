# Local live AI impressions

The panel can select a local RVC model, adjust pitch (−12 to +12 semitones), and use Responsive or Balanced processing. **Normal is always the audio service's startup mode**, even if a preferred AI model was saved. Use **Hear my voice** to audition through the selected headphones. **Return to Normal** also cancels loading and releases the worker's GPU memory; its optional global shortcut is configured under Audio & shortcuts. Global shortcuts require the panel to remain running (minimized is fine).

The two personally installed presets are **Donald Trump — AI impression** and **Elon Musk — AI impression**. The user preferred alternative checkpoints in a same-sentence listening comparison on 2026-11-22; those replace the initial candidates while retaining the preset IDs and tuning. They are experimental for gaming: end-to-end latency, matched CS2 FPS impact, and the 30-minute gameplay acceptance test remain unverified. Joe Rogan remains unavailable because the discovered model had no documented usage terms. There is no substitute voice behind an unavailable entry. No celebrity weights or speech recordings are distributed in this public repository.

## Setup and model management

Install the ordinary soundboard first, then opt into the separate CUDA environment:

```bash
python3 scripts/setup-ai.py
```

This needs `uv`, Git, network access, an NVIDIA driver compatible with the pinned PyTorch CUDA runtime, and several GB of disk space. It installs Python 3.10 and pinned dependencies into `.runtime/ai-venv`; the panel's Python 3.12 environment is unchanged. Backend source is pinned to [w-okada/voice-changer commit 7e2ce5c](https://github.com/w-okada/voice-changer/tree/7e2ce5c4800010bbc7604968bd29b802534fac05). The installer downloads only the fixed HuBERT/RMVPE feature weights referenced by upstream, verifies SHA-256, and does not download celebrity models. Conversion uses the backend directly; no web server or cloud service runs.

In **AI models… → Import model**, choose a standard RVC v1/v2 inference `.pth` or a ZIP containing exactly one `.pth`, then enter the source and documented usage terms. Imports run off the UI thread, copy weights locally, validate with `torch.load(weights_only=True)` in the isolated environment, and store versioned metadata and checksums under the library's `models/<stable-id>/` directory. Duplicate weights reuse their existing ID. Archives are never extracted as a directory, scripts are ignored, and weights remain outside Git. Retrieval indexes are deliberately ignored in this first version; inference uses index ratio zero. ONNX, custom model architectures and training checkpoints are not supported.

The only unrestricted legacy checkpoint load is the fixed, checksum-verified upstream HuBERT feature extractor, whose fairseq metadata is incompatible with restricted tensor-only loading. User-imported voice checkpoints never use that exception. This validation is not a general-purpose sandbox or a guarantee that arbitrary native libraries can parse every malformed file safely.

Pitch/profile changes take effect through **Apply / Retry AI** and are stored per model. Removing the requested/active model first returns the service to Normal. Failed selections remain failed until an explicit Retry or another selection; saved preferences never cause an automatic reload after failure or restart.

## Routing and recovery

```text
Physical mic -> Normal / generic DSP / private AI input -> voice bus -> voice mix
                                                       |                ^
                                                       v                |
                                                 headphones          clip bus
                                                    optional            |
                                                                     headphones
                                                                       optional
Preview -----------------------------------------------------------> headphones
Voice mix -> persistent Soundboard Microphone -> CS2 / Discord
```

During loading, the normal mic stays connected while the worker sends its output into a private staging sink. Only a ready worker is connected to the voice bus. Normal/generic/AI are mutually exclusive; clips never enter the AI input. Gain and mute are applied after voice processing to both game and self-monitor streams. System defaults are never changed. Disconnected selected devices are not substituted.

The service supervises one worker process group, cancels loads, enforces a 60-second load timeout, and falls back on worker exit, inference failure (including CUDA errors), two seconds without a heartbeat, disconnected microphone, or a stalled output buffer. The worker has one pending input block, discards stale blocks, and bounds output writes. It does not build an unbounded speech queue. Repeated processing deadline misses also return to Normal. The panel displays phase, reason, per-block processing time, rolling processing p95, and dropped input blocks. **These processing figures are not end-to-end latency.**

Closing/reopening the panel does not stop AI conversion. Selecting Normal terminates the whole worker group, including its audio streams, to release GPU resources. A service restart starts Normal while remembering the preferred model. A complete application-owned core graph can be adopted after a service crash without replacing the public microphone. Explicit graceful service shutdown still removes its modules. PipeWire restarts necessarily replace devices and can interrupt a game's capture session.

Existing `ping`, `status`, `refresh`, and `connect` IPC commands remain available. New commands are `ai_activate` (model ID, pitch, profile), `ai_cancel`, `voice_normal`, `voice_effect`, and `ai_remove`. Status adds `ai` (requested, active, phase, reason, timing) and `active_effect`. Heavy model loading/inference happens only in the child process. The service never writes the sound library manifest; model metadata is separate. Logs: `ai-worker.log` and `ai-worker.previous.log` in the library directory.

## Model evidence and listening

| Voice | Source and documented terms | Observation |
|---|---|---|
| Trump | [Coolwowsocoolwow/Donald-J.-Trump](https://huggingface.co/Coolwowsocoolwow/Donald-J.-Trump), repository declares OpenRAIL; detailed training provenance is incomplete | User preferred this alternative to the initial HaruNova checkpoint. Installed locally with the previous checkpoint backed up. |
| Musk | [anonymous12345678910/ElonMuskV2](https://huggingface.co/anonymous12345678910/ElonMuskV2), repository declares CC BY 4.0 and identifies 14 minutes of a Lex Fridman interview as training material | User preferred this alternative to the initial UnidentifiedPerson checkpoint. Original creator attribution retained in the local model record. |
| Rogan | [GarrGarr/RVC_Models](https://huggingface.co/GarrGarr/RVC_Models), JoeRogan-48k.zip found, no model card/usage terms found | Unavailable; weights not installed. |

Observation date: 2026-11-22. A repository license label is recorded evidence, not independent verification of training-data rights or identity accuracy. Actual local records retain the source, terms, SHA-256, listening result, and tuning. Reimporting elsewhere does not inherit the personal listening approval automatically.

## Repeatable acceptance procedure

The same 48 kHz mono PCM16 speech WAV can be processed without connecting any physical audio device:

```bash
.runtime/ai-venv/bin/python scripts/benchmark-ai.py /path/to/models/MODEL_ID \
  --input /path/to/reference.wav --output /tmp/converted.wav --report /tmp/processing.json
PYTHONPATH=src .venv/bin/python scripts/test-ai-live.py /path/to/models/MODEL_ID \
  --seconds 90 --crash-worker
```

The benchmark reports loading/warm-up time and processing distribution only. The live test uses synthetic private devices, checks stable microphone/capture identity, and can kill its own AI worker to check mute-preserving fallback. It never routes its test audio into a game or physical headphones.

For the gaming acceptance check:

1. Use the same CS2 replay/benchmark, resolution, graphics, frame cap, GPU power settings, and background apps for all runs. Warm up first. Alternate Normal/AI and collect three matched pairs of per-frame timing logs with the same instrumentation. Compare the average of the three 1% low-FPS results; target AI reduction ≤5%. Do not infer FPS impact from GPU utilization or processing time.
2. Record a repeatable speech test through both a direct reference channel and the converted local output using a synchronized recorder/external loopback. Measure matched audible onset times over at least 100 events during gameplay; report p50/p95 and test setup. Target p95 ≤200 ms, excluding network/chat delay. Processing time alone cannot pass this check.
3. Play for 30 minutes with the chosen AI preset. Record dropped-block counters, input/output timing, sustained dropouts, and whether delay grows. Repeat listening through CS2's own test, with its push-to-talk/noise settings held constant.
4. While recording from Soundboard Microphone, close/reopen the panel, change models, cancel loading, and terminate the AI worker. Confirm the source ID and game's capture stream remain unchanged, mute stays muted, voice returns on failure, clips stay unprocessed, and Preview never reaches the mix.

Do not promote a model as gaming-verified until these measurements pass. If a target fails, use Normal and report the actual latency/FPS/quality tradeoff before expanding presets.

## Measurements so far

RTX 4070 Ti SUPER, driver 595.99.02. Short reference-speech test, Responsive (80 ms blocks; 16,384 samples of past context), 2026-11-22:

| Model | Processing p50 | Processing p95 | Missed processing deadlines |
|---|---:|---:|---:|
| Trump, original candidate | 47.5 ms | 54.3 ms | 0 / 74 blocks |
| Musk, original candidate | 48.4 ms | 51.8 ms | 0 / 74 blocks |
| Trump, preferred alternative | 47.2 ms | 51.4 ms | 0 / 74 blocks |
| Musk, preferred alternative | 46.4 ms | 51.7 ms | 0 / 74 blocks |

Load plus warm-up was about 7.0–7.5 seconds. CS2 was running, but these were not matched gameplay benchmarks. End-to-end latency, FPS impact and a 30-minute gameplay run remain unmeasured. These are observations on one machine, not performance guarantees.

A 90-second private-device stream test observed processing p95 63.5 ms and one dropped input block. Killing its AI worker restored Normal within three seconds with mute retained; the microphone node and native capture connection stayed unchanged. A separate panel lifecycle test verified two closes/reopens and a service crash/restart retained the same source and capture IDs. Existing synthetic clip/preview/generic-effect/mute tests passed.

The user confirmed usable live voice through the updated soundboard and reception in CS2 on 2026-11-22. The production service handoff preserved the existing microphone and CS2 capture IDs. An additional isolated test switched from Trump to Musk, closed its panel while AI continued, then crashed the worker and verified mute-preserving fallback. Restricted-loading tests rejected both a corrupt checkpoint and a checkpoint containing executable pickle instructions without running those instructions. These checks do not replace the remaining controlled gaming benchmarks.
