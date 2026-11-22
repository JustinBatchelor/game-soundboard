"""Opt-in restart recovery check with a native client and silent virtual sources.

Uses node.dont-fallback to prevent the test recorder from selecting a real mic.
No recording is retained and no audio is sent to physical outputs.
"""
import subprocess
import time
from game_soundboard.library import DEFAULTS
from game_soundboard.routing import Routing


router = Routing('soundboard-reconnect-test')
extras = []
recorder = None
name = 'SoundboardReconnectTest'


def wait_for(predicate):
    deadline = time.monotonic() + 5
    while time.monotonic() < deadline:
        apps = [a for a in router.capture_apps() if a['name'] == name]
        if apps and predicate(apps[0]):
            return apps[0]
        if recorder.poll() is not None:
            raise AssertionError('Test recorder exited: ' + recorder.stderr.read().decode())
        time.sleep(.1)
    raise AssertionError(f'Capture did not reach expected state: {apps}')


try:
    before = (router.call('get-default-source'), router.call('get-default-sink'))
    extras.append(router.call('load-module', 'module-null-sink', 'sink_name=gsbtest_reconnect_feed'))
    extras.append(router.call('load-module', 'module-remap-source',
                             'master=gsbtest_reconnect_feed.monitor',
                             'source_name=synthetic_reconnect_mic'))
    settings = dict(DEFAULTS, microphone='synthetic_reconnect_mic', devices_initialized=True)
    router.reconcile(settings)
    recorder = subprocess.Popen(['pw-record', '--target', router.source,
        '--properties', f'{{ application.name = {name} node.dont-fallback = true }}',
        '--raw', '--format=s16', '--rate=48000', '--channels=1', '-'],
        stdout=subprocess.DEVNULL, stderr=subprocess.PIPE)
    app = wait_for(lambda a: a['connected'])
    assert app['requested_source'] == router.source
    # Simulate the observed fallback, using a silent synthetic source only.
    router.call('move-source-output', app['index'], 'synthetic_reconnect_mic')
    app = wait_for(lambda a: a['source'] == 'synthetic_reconnect_mic')
    assert app['requested_source'] == router.source
    router.cleanup()
    router = Routing('soundboard-reconnect-test')
    router.reconcile(settings)
    wait_for(lambda a: a['connected'])
    print('PASS: native capture requesting the virtual mic reconnects after app restart', flush=True)
    # Ordinary health checks should respect a manual input change afterward.
    router.call('move-source-output', app['index'], 'synthetic_reconnect_mic')
    wait_for(lambda a: a['source'] == 'synthetic_reconnect_mic')
    router.reconcile(settings)
    wait_for(lambda a: a['source'] == 'synthetic_reconnect_mic')
    assert before == (router.call('get-default-source'), router.call('get-default-sink'))
    print('PASS: manual changes respected; system defaults unchanged', flush=True)
finally:
    if recorder is not None:
        recorder.terminate()
        recorder.wait(timeout=5)
    router.cleanup()
    for module in reversed(extras):
        router.call('unload-module', module)
