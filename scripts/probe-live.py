"""Noninteractive live integration probe: temporary graph + portal session, no key grabs."""
import asyncio
import json
import sys
import time
from pathlib import Path
from PySide6.QtWidgets import QApplication
from PySide6.QtMultimedia import QMediaDevices
from dbus_next import Variant
from game_soundboard.library import DEFAULTS
from game_soundboard.routing import Routing
from game_soundboard.shortcuts import Shortcuts

app = QApplication([])
routing = Routing('game-soundboard-integration-probe')
before = (routing.call('get-default-source'), routing.call('get-default-sink'))
try:
    state = routing.reconcile(dict(DEFAULTS))
    deadline = time.monotonic() + 3
    devices = []
    while time.monotonic() < deadline:
        app.processEvents()
        devices = [bytes(d.id()).decode() for d in QMediaDevices.audioOutputs()]
        if routing.clips in devices:
            break
        time.sleep(.05)
    assert routing.clips in devices, devices
    assert routing.source in [s['name'] for s in routing.listing('sources')]
    assert before == (routing.call('get-default-source'), routing.call('get-default-sink'))
    print('PASS: virtual microphone, Qt output discovery, default devices preserved', flush=True)
finally:
    routing.cleanup()
assert not any(routing.owned(m) for m in routing.listing('modules'))
print('PASS: temporary audio modules cleaned up', flush=True)

shortcuts = Shortcuts()
async def probe():
    await shortcuts.connect()
    options = {'session_handle_token': Variant('s', 'probe_session')}
    result = await shortcuts.request('CreateSession', 'a{sv}', [options], options)
    shortcuts.session = result['session_handle'].value
    print('PASS: host app registration and GlobalShortcuts session', flush=True)
    await shortcuts.close_session()
try:
    asyncio.run_coroutine_threadsafe(probe(), shortcuts.loop).result(timeout=15)
finally:
    shortcuts.stop()
