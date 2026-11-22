import json
import pytest
from game_soundboard.routing import Routing
from game_soundboard.library import DEFAULTS


class Pulse:
    def __init__(self):
        self.modules = {}
        self.index = 0
        self.calls = []
        self.sources = [{'name': 'physical-mic', 'monitor_of_sink': None}]
        self.sinks = [{'name': 'headphones'}]
        self.fail = ''

    def __call__(self, *args):
        self.calls.append(args)
        if args[0] == '-f':
            kind = args[-1]
            if kind == 'modules':
                return json.dumps(list(self.modules.values()))
            if kind == 'sources':
                return json.dumps(self.sources)
            if kind == 'sinks':
                return json.dumps(self.sinks)
            if kind == 'source-outputs':
                return '[]'
            if kind == 'sink-inputs':
                return json.dumps([{'index': i + 100, 'owner_module': str(i)} for i, m in self.modules.items()
                                  if m['name'] == 'module-loopback'])
        if args[0] == 'load-module':
            if self.fail == args[1]:
                raise RuntimeError('injected load failure')
            self.index += 1
            self.modules[self.index] = {'index': self.index, 'name': args[1], 'argument': ' '.join(args[2:])}
            return str(self.index)
        if args[0] == 'unload-module':
            del self.modules[int(args[1])]
        return ''


def settings(**changes):
    return dict(DEFAULTS, microphone='physical-mic', output='headphones', **changes)


def test_graph_monitor_is_clip_only_and_mic_controls_are_stream_local():
    pulse = Pulse()
    router = Routing('test', pulse)
    router.reconcile(settings(monitor=True))
    args = [m['argument'] for m in pulse.modules.values()]
    assert any(f'source={router.clips}.monitor sink=headphones' in a for a in args)
    assert any(f'source=physical-mic sink={router.voice}' in a for a in args)
    assert not any(f'source={router.mix}.monitor sink=headphones' in a for a in args)
    assert not any(c[0] in {'set-default-source', 'set-default-sink', 'set-source-volume'} for c in pulse.calls)
    assert any(c[0] == 'set-sink-input-volume' for c in pulse.calls)


def test_unplug_does_not_switch_mic_and_reconnect_rebuilds():
    pulse = Pulse()
    router = Routing('test', pulse)
    router.reconcile(settings())
    pulse.sources = [{'name': 'other-mic'}]
    state = router.reconcile(settings())
    assert state['microphone'] == ''
    assert 'mic' not in router.modules
    pulse.sources = [{'name': 'physical-mic'}]
    router.reconcile(settings())
    assert 'mic' in router.modules
    pulse.modules.clear()  # server restart
    router.reconcile(settings())
    assert len(pulse.modules) == 7


def test_load_failure_rolls_back_only_owned_modules():
    pulse = Pulse()
    pulse.modules[50] = {'index': 50, 'name': 'module-null-sink', 'argument': 'sink_name=unrelated'}
    router = Routing('test', pulse)
    pulse.fail = 'module-remap-source'
    with pytest.raises(RuntimeError):
        router.reconcile(settings())
    assert list(pulse.modules) == [50]


def test_crash_leftovers_cleaned_but_other_instances_preserved():
    pulse = Pulse()
    first = Routing('test', pulse)
    first.reconcile(settings())
    other = Routing('other', pulse)
    other.reconcile(settings())
    second = Routing('test', pulse)
    second.reconcile(settings())
    second.cleanup()
    assert len(pulse.modules) == 7
    assert all(other.owned(m) for m in pulse.modules.values())


def test_pactl17_modules_without_json_index():
    def run(*args):
        if args[0] == '-f':
            return '[{"name":"module-null-sink","argument":"sink_name=test"}]'
        return '536870916\tmodule-null-sink\tsink_name=test\t\n'
    assert Routing('test', run).listing('modules') == [
        {'index': 536870916, 'name': 'module-null-sink', 'argument': 'sink_name=test'}]


def test_monitor_sources_excluded_from_microphone_choices():
    pulse = Pulse()
    pulse.sources += [{'name': 'desktop.monitor', 'monitor_source': 'desktop'},
                      {'name': 'also-monitor', 'monitor_source': 'desktop'}]
    sources, _ = Routing('test', pulse).devices()
    assert [s['name'] for s in sources] == ['physical-mic']


def test_no_microphone_does_not_change_unrelated_stream_volume():
    class WithGame(Pulse):
        def __call__(self, *args):
            if args[-1] == 'sink-inputs':
                return json.dumps([{'index': 77, 'owner_module': None}])
            return super().__call__(*args)
    pulse = WithGame()
    Routing('test', pulse).reconcile(dict(DEFAULTS))
    assert not any(c[0].startswith('set-') for c in pulse.calls)


def test_effect_switch_preserves_public_source_and_clip_routes(monkeypatch):
    monkeypatch.setattr('game_soundboard.routing.effect_arguments', lambda p: ['plugin=test', 'label=test'])
    pulse = Pulse()
    router = Routing('test', pulse)
    router.reconcile(settings())
    source, clips = router.modules['source'], router.modules['clips_mix']
    router.reconcile(settings(voice_effect='baby', voice_monitor=True))
    assert router.modules['source'] == source and router.modules['clips_mix'] == clips
    args = [m['argument'] for m in pulse.modules.values()]
    assert any(f'source=physical-mic sink={router.effect}' in a for a in args)
    assert any(f'source={router.voice}.monitor sink=headphones' in a for a in args)
    assert any(f'master={router.voice}' in a for a in args)
    router.reconcile(settings(voice_effect='normal'))
    assert 'effect' not in router.modules and 'voice_monitor' not in router.modules
    assert router.modules['source'] == source


def test_failed_effect_suspends_voice_preserves_clips_and_recovers(monkeypatch):
    monkeypatch.setattr('game_soundboard.routing.effect_arguments', lambda p: ['plugin=test'])
    pulse = Pulse()
    router = Routing('test', pulse)
    router.reconcile(settings())
    source = router.modules['source']
    pulse.fail = 'module-ladspa-sink'
    state = router.reconcile(settings(voice_effect='robot'))
    assert state['voice_error'] and 'mic' not in router.modules
    assert 'clips_mix' in router.modules and router.modules['source'] == source
    pulse.fail = ''
    assert not router.reconcile(settings(voice_effect='robot'))['voice_error']
    assert 'mic' in router.modules


def test_voice_gain_and_mute_do_not_change_clips(monkeypatch):
    monkeypatch.setattr('game_soundboard.routing.effect_arguments', lambda p: ['plugin=test'])
    pulse = Pulse()
    router = Routing('test', pulse)
    router.reconcile(settings(voice_effect='deep', monitor=True, voice_monitor=True, mic_muted=True, mic_volume=.4))
    controlled = {int(c[1])-100 for c in pulse.calls if c[0] == 'set-sink-input-mute'}
    assert controlled == {router.modules['voice_mix'], router.modules['voice_monitor']}
    assert all(c[2] == '1' for c in pulse.calls if c[0] == 'set-sink-input-mute')


def test_connect_app_checks_stream_identity_and_never_changes_defaults(monkeypatch):
    pulse = Pulse()
    router = Routing('test', pulse)
    app = {'index': '42', 'name': 'cs2', 'pid': '999'}
    monkeypatch.setattr(router, 'capture_apps', lambda: [app])
    router.connect_app(app)
    assert pulse.calls == [('move-source-output', '42', router.source)]
    with pytest.raises(RuntimeError, match='closed'):
        router.connect_app(dict(app, pid='998'))
    assert len(pulse.calls) == 1


def test_capture_status_distinguishes_game_from_module_streams():
    class Capturing(Pulse):
        def __call__(self, *args):
            if args[-1] == 'source-outputs':
                return json.dumps([
                    {'index': 10, 'source': 2, 'owner_module': None, 'properties': {'application.name': 'cs2'}},
                    {'index': 11, 'source': 1, 'owner_module': None, 'properties': {'application.name': 'Discord'}},
                    {'index': 12, 'source': 2, 'owner_module': '123', 'properties': {'application.name': 'Loopback'}},
                    {'index': 13, 'source': 2, 'owner_module': None, 'properties': {'application.name': 'Settings', 'resample.peaks': 'true'}}])
            return super().__call__(*args)
    pulse = Capturing()
    router = Routing('test', pulse)
    pulse.sources = [{'index': 1, 'name': 'physical-mic'}, {'index': 2, 'name': router.source}]
    apps = router.capture_apps()
    assert [(a['name'], a['connected']) for a in apps] == [('cs2', True), ('Discord', False)]
    assert 'Receiving apps: cs2' in router.reconcile(settings())['message']
