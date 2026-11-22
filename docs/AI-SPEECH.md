# Local typed AI impressions

Open **AI Speech**, choose a voice, enter up to 500 characters of English, and click **Generate**. A complete result appears with its original text and voice. Draft edits do not change an existing result. **Preview** goes only to the selected headphones; **Play** goes to the clip bus and persistent Soundboard Microphone. Generation never triggers playback automatically. Game push-to-talk and noise filtering still apply.

**Save as sound** imports the generated WAV through the normal managed library at 50% volume with no shortcut. Saved clips work offline without any speech runtime and support the same renaming, volume, overlap/restart, and keybind behavior as other clips. Microphone mute affects voice, not sound clips; the Clips slider controls generated speech too.

Only one job runs at a time. Each starts its own process, loads the selected model, generates, and exits to release GPU memory. Cancel or Stop All kills pending generation; Stop All also stops previews and clips. A failed job preserves the previous successful result. Closing the panel cancels generation and removes unsaved results; minimizing permits it to finish. The worker is tied to the panel's Linux process lifetime, including panel crashes. Jobs time out after 180 seconds and reject outputs longer than 60 seconds.

## Setup and local records

```bash
python3 scripts/setup-speech.py --engine both
```

The optional installer creates separate Python 3.11 environments in `.runtime/tts-f5-venv` and `.runtime/tts-chatterbox-venv`, installs the corresponding frozen requirements, and downloads revision-pinned, checksum-verified model assets. `--engine f5` or `--engine chatterbox` installs only one. The existing panel and audio-service environments are unchanged. Generation is offline; network access is needed only for setup. CUDA is required for this version.

F5 uses release 1.1.10 at `9ae46c8360303417489d2c1071f29972cd8ab171`; later upstream code had a device-selection incompatibility with the selected PyTorch 2.6 runtime. Chatterbox uses `ab55923c27757c90cfcc407baa57dc25a7d25217`. Dependency compatibility checks pass in both environments. Model revisions and individual file hashes are in `scripts/speech-assets.json`; workers verify them before loading. Chatterbox's built-in watermark remains enabled.

The installer does **not** install celebrity references or automatically approve voices. Local preset records live in `$XDG_DATA_HOME/game-soundboard/speech/voices.json` (version 1, `voices` array). Each record includes `id` (`trump` or `musk`), display `name`, `engine` (`f5` or `chatterbox`), `approved`, absolute `reference` WAV path, `reference_sha256`, exact `reference_text`, `source`, and `terms`. Personal records also retain model/backend revisions, approval date, and reference edits. References are copied into `speech/references/`; unapproved/missing presets remain unavailable. Do not copy the personal catalog or recordings into the public repository.

F5 model weights declare CC BY-NC 4.0; Chatterbox declares MIT. These are recorded upstream terms, not independent verification of training-data rights. References and generated clips remain personal local data. No custom training or RVC conversion is involved.

## Approved personal presets and evidence

The user compared the same five gaming lines on 2026-11-22 and selected **Chatterbox for Trump** and **F5-TTS for Musk**.

- Trump reference: [Trump White House Archived, November 5, 2020](https://www.youtube.com/watch?v=-jpH2jjDrMk); [Commons source description](https://commons.wikimedia.org/wiki/File:Donald_Trump_voice.ogg) identifies the government recording as public domain. A short mono speech excerpt is used solely as a voice reference.
- Musk reference: [World Economic Forum conversation](https://www.youtube.com/watch?v=IgifEgm1-e0), credited to World Economic Forum under [CC BY 4.0](https://creativecommons.org/licenses/by/4.0/); [Commons reviewed source](https://commons.wikimedia.org/wiki/File:Conversation_with_Elon_Musk_-_World_Economic_Forum_Annual_Meeting_2026.webm). The excerpt is cropped, resampled, and transcribed to condition generation; neither the speaker nor publisher endorses the generated audio.

Initial five-line comparison on an RTX 4070 Ti SUPER (16 GB), with CS2 running:

| Voice / engine | Mean worker total | Mean model load | Mean inference | Peak PyTorch allocation |
|---|---:|---:|---:|---:|
| Trump / F5 | 10.65 s | 8.75 s | 1.86 s | 1,990 MiB |
| Trump / Chatterbox | 20.03 s | 14.66 s | 5.32 s | 3,465 MiB |
| Musk / F5, initial reference | 11.13 s | 9.04 s | 2.05 s | 1,990 MiB |
| Musk / Chatterbox | 19.15 s | 14.65 s | 4.46 s | 3,556 MiB |

Every line used a fresh worker, with model files already downloaded. The first Chatterbox inference had extra first-use overhead. Worker totals include asset verification and loading but exclude a small amount of process startup/teardown; the panel displays its full observed elapsed time. GPU figures are PyTorch allocation peaks, not total driver usage. These are short-phrase observations, not guarantees for 500-character text.

Speech recognition caught reference words leaking into initial Musk/F5 outputs because the downloaded excerpt differed from its transcript. The corrected reference removes the interviewer and aligns the transcript to decoded speech. Five regenerated checks no longer contained that reference passage, averaging 10.79 seconds total (8.66 seconds loading, 2.09 seconds inference). The user approved the corrected audition on 2026-11-22; the personal Musk preset is enabled with that corrected reference. Speech recognition is a diagnostic, not proof of pronunciation or resemblance; minor wording variations can still occur.

## Recovery, compatibility, and validation

The panel confirms live RVC conversion is stopped via the existing `voice_normal` command before enabling TTS generation. Existing `ping`, `status`, `refresh`, `connect`, and legacy AI commands remain compatible. No public microphone device is replaced and no service restart is required. Generic voice effects, microphone mute/gain, clips, IDs, and portal registrations are preserved.

`speech.py` owns one worker with job-scoped JSON events. Only a matching job's validated PCM WAV becomes a result, after process exit. Late events are ignored; cancellation discards partial output. Successful unsaved files live under `speech/jobs/`; saving copies them into normal originals/cache storage and adds optional `generation` metadata to the existing version-1 sound record. That metadata is descriptive and is not needed for playback.

Automated coverage includes text/duration limits, missing/unapproved references, worker crashes, corrupt output, simulated GPU-memory failure, cancellation, timeout, late events, repeated generation, panel close, and saving a result independently of later draft edits. The synthetic live-audio test exercises temporary speech Preview/Play/Stop All alongside generic effects and microphone passthrough without using a physical microphone or output. Run it with:

```bash
PYTHONPATH=src QT_QPA_PLATFORM=offscreen .venv/bin/python scripts/test-live-audio.py
PYTHONPATH=src QT_QPA_PLATFORM=offscreen .venv/bin/python scripts/test-speech-live.py --voice trump
```

`scripts/audition-speech.py DIRECTORY` reproduces the five-phrase comparison from local `trump-voice.json` and `musk-voice.json` records. It writes audition WAVs and timing reports without routing audio.

On 2026-11-22, 86 automated tests passed, both pinned environments were reproduced by the installer, and real panel/controller inference for Trump completed in 18.1 seconds. Stop All and panel closure reaped real workers, with no automatic playback. Synthetic device testing verified temporary speech isolation and microphone passthrough. Deployment preserved all personal sound records/settings, the running audio-service PID, and CS2's capture connection. Final generated-speech listening inside CS2 still requires a user check.

No instrumented CS2 frame-time benchmark has been collected. Generation uses GPU resources while running; its FPS impact remains unmeasured. Saved-clip playback requires no inference. Listening tests, synthetic routing tests, and GPU-memory readings do not establish a gameplay FPS guarantee.
