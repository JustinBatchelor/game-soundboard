# Game Soundboard

A native Linux soundboard for Ubuntu/GNOME on Wayland. Trigger imported clips from a button grid or desktop-approved global keybinds, and combine them with your microphone for games and Discord.

## Install and launch

Requires Linux with PipeWire/PulseAudio compatibility, Python 3.12 or later, and a desktop implementing the XDG GlobalShortcuts portal. The initial target is Ubuntu with GNOME on Wayland.

```bash
sudo apt install git python3-venv pulseaudio-utils libxkbcommon0
git clone https://github.com/JustinBatchelor/game-soundboard.git
cd game-soundboard
./setup.sh
./run.sh
```

Setup creates an isolated `.venv` and installs a per-user **Game Soundboard** desktop launcher. You can subsequently launch it from your applications menu. Keep the checkout in place because the launcher points to it; rerun `scripts/install-desktop.py` with the virtual environment's Python if you move it.

The setup script uses `uv` when available, otherwise Python's venv/pip. FFmpeg is supplied by `imageio-ffmpeg` if a system FFmpeg is unavailable. The application does not start automatically at login.

## First use

1. Launch the app. On first launch it selects your current system microphone and output, then remembers their specific device names. Change them in **Audio & shortcuts**.
2. In your game or Discord, select **Soundboard Microphone** as the input. System defaults are never changed by this app.
3. Click a sound button to send it to voice chat. Enable **Hear clips locally** to also hear triggered clips in your headset.
4. **Preview** plays only to your selected headset, never into voice chat.
5. Use a card's **Edit** button, click the keybind field, and press your desired combination (for example **Ctrl+Alt+K** or **F8**). Use **Clear** to disable it. Save and approve the desktop's shortcut dialog. The card displays the desktop's accepted binding.
6. Record the Stop All keybind the same way in settings. Global shortcut playback is paused while either settings dialog is open, so recording keys does not trigger sounds. Stop All stops clips and previews; your voice remains connected. **Mute voice** mutes only the app's microphone route.

Different clips overlap. Repeating a clip restarts it. Minimize to keep running; close to quit and remove the virtual devices. Games can require reselecting their microphone after restarting the app. Game push-to-talk still applies, and noise suppression can filter clips. The app does not press a game's push-to-talk key.

The desktop owns global binding decisions. Rebinding a preferred key may require **Configure approved shortcuts in desktop**; the desktop may retain earlier approved bindings. Clearing a sound's keybind removes it from the active session. Duplicate preferences within the app are rejected; desktop conflicts/denials appear in the status area. Shortcuts work only while the app is running in your logged-in desktop session.

## Starter recordings

First launch imports missing starters; **Get starter clips** retries failed downloads. Successful starter imports are recorded permanently, so removing a starter does not restore it on every launch. Clips are stored at 50% individual volume (master volume defaults to 70%), with no keybind assigned.

- [COD Zombies — Kaboom!](https://www.youtube.com/watch?v=UO9JSE3aRtw)
- [Kool-Aid Man — Oh Yeah!](https://www.myinstants.com/en/instant/oh-yeah-kool-aid-man-35548/)
- [CSGO/CS2 — Bomb has been planted](https://www.myinstants.com/en/instant/bomb-has-been-planted-csgo-cs2-75763/)
- [SpongeBob — Oh brother, this guy stinks!](https://www.myinstants.com/en/instant/oh-brother-this-guy-stinks/)
- [Fahh — meme sound effect](https://www.youtube.com/watch?v=VP6eZu3SAak)

These third-party recordings are downloaded into your local library, not included in this repository. Kaboom uses the linked Black Ops video; yt-dlp retrieves its audio. Original source and download URLs are retained in each record. Audio is not bundled in source distributions. Import your preferred recording if an upload differs from the version you want. Downloads are bounded and time out; failures do not prevent local imports or playback. All imported audio works offline afterward.

## Library and architecture

Data lives in `$XDG_DATA_HOME/game-soundboard` (normally `~/.local/share/game-soundboard`): a versioned atomic JSON manifest, managed originals, and decoded 48 kHz stereo PCM WAV cache. Set `SOUNDBOARD_DATA_DIR` for an isolated test library. Back up the entire directory. Removing a sound deletes its managed copies, never the source file you imported.

- `library`: asynchronous preparation, atomic persistence and starter history.
- `playback`: reusable Qt sound effects; shared click/shortcut playback path.
- `routing`: PipeWire/PulseAudio modules controlled in a worker thread; three-second health checks and recovery.
- `shortcuts`: XDG GlobalShortcuts portal in an asyncio worker, delivering Qt signals.
- `ui`: searchable grid, import workers, controls and device settings.

The D-Bus transport uses `dbus-next` because PySide6 does not expose the custom-type registration needed for the portal's `a(sa{sv})` messages. It registers the installed desktop ID with the host portal Registry. No raw keyboard hooks, root input access, network listener, or background autostart service is used.

```text
Clips -> Clip bus -> Voice mix -> Soundboard Microphone -> Game/Discord
           |            ^
           v            |
       Headphones   Physical microphone
Preview -> Headphones only
```

Owned module names include a hash of the library path. Normal exit removes them; next launch reconciles leftovers after a crash. Recovery touches only that library's modules. Unplugged devices are not silently replaced; their routes suspend until the same named device returns. Global shortcut session loss is reported and can be reconnected from settings. An unsupported or malformed manifest is left untouched and produces an error.

## Verification

```bash
.venv/bin/python -m pytest -q
PYTHONPATH=src QT_QPA_PLATFORM=offscreen .venv/bin/python scripts/probe-live.py
PYTHONPATH=src QT_QPA_PLATFORM=offscreen .venv/bin/python scripts/test-live-audio.py
./run.sh --screenshot /tmp/soundboard.png
```

Live probes require access to your real desktop session, PipeWire, and the installed launcher. The audio test uses temporary null sinks and synthetic tones, never the physical microphone or headphones. Run live probes outside a restrictive sandbox. `parec` from `pulseaudio-utils` is needed for the audio test.

The remaining user acceptance checks are approving chosen keybinds, triggering them while a fullscreen game has focus, selecting the virtual input in that game, checking perceived volume, and measuring end-to-end trigger latency under gaming load. The under-100-ms figure is a target, not a guarantee. Native Linux games and other applications may handle device changes and voice processing differently.

### Validation results

- 44 automated tests pass, covering key capture and conversion, binding preservation, duplicate detection, playback suppression while editing, library imports and persistence, routing recovery, and shortcut session handling.
- Live portal registration and session creation succeeded. The test did not assign/grab any keyboard combination.
- Live synthetic audio verified overlapping clips plus voice in the virtual microphone, preview isolation, monitoring without voice feedback, microphone mute, and Stop All preserving voice.
- Warm trigger-to-capture measured about 42 ms in the synthetic test on the current desktop. Fullscreen-game keybind timing and network/voice-chat processing remain unmeasured.
- Live UI, device discovery, search, single-instance activation, graceful shutdown, and module cleanup passed.
- All three starter recordings downloaded and decoded. Local speech recognition confirmed “Oh yeah” and “Bomb has been planted.” Kaboom uses audio from the linked YouTube video (UO9JSE3aRtw).

![Soundboard panel](docs/soundboard.png)
