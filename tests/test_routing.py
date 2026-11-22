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
    assert any(f'source=physical-mic sink={router.mix}' in a for a in args)
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
    assert len(pulse.modules) == 5


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
    assert len(pulse.modules) == 5
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
