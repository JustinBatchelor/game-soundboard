import json
from pathlib import Path
import queue
import zipfile
import pytest

from game_soundboard.ai_models import ModelLibrary, tuning
from game_soundboard.ai_supervisor import AISupervisor
from game_soundboard.audio_service import AudioService
from game_soundboard.library import Library
from test_routing import Pulse, settings
from game_soundboard.routing import Routing


class Worker:
    def __init__(self, config, root):
        self.config = config
        self.events = queue.Queue()
        self.code = None
        self.stopped = False
    def poll(self): return self.code
    def stop(self): self.stopped = True


@pytest.fixture
def model(tmp_path):
    source = tmp_path / 'voice.pth'
    source.write_bytes(b'fake tensors validated by test stub')
    library = ModelLibrary(tmp_path)
    return library.import_model(source, 'Test voice', 'https://example.org/source', 'test license',
                                validator=lambda p: {'version': 'v2', 'sample_rate': 40000, 'f0': True})


def supervisor(tmp_path, model):
    clock = [10.]
    ai = AISupervisor(tmp_path, Worker, lambda: clock[0])
    ai.activate(model['id'], 0, 'responsive', 'mic', 'ai.monitor', 'return')
    return ai, clock


def test_loading_keeps_normal_then_ready_switches(tmp_path, model):
    ai, clock = supervisor(tmp_path, model)
    assert ai.phase == 'loading' and ai.active == 'normal'
    assert ai.worker.config['input'] == 'ai.monitor'
    ai.worker.events.put({'event': 'ready'})
    assert ai.tick() and ai.active == model['id'] and ai.phase == 'active'
    worker = ai.worker
    ai.normal()
    assert worker.stopped and ai.active == ai.requested == 'normal'


@pytest.mark.parametrize('failure', ['exit', 'heartbeat', 'timeout', 'oom'])
def test_failure_latches_without_automatic_retry(tmp_path, model, failure):
    ai, clock = supervisor(tmp_path, model)
    worker = ai.worker
    if failure == 'exit': worker.code = 9
    elif failure == 'heartbeat': clock[0] += 2
    elif failure == 'timeout':
        clock[0] += 60
        worker.events.put({'event': 'heartbeat'})
    else: worker.events.put({'event': 'error', 'reason': 'CUDA out of memory'})
    assert ai.tick()
    assert worker.stopped and ai.worker is None
    assert ai.phase == 'failed' and ai.active == 'normal' and ai.requested == model['id']
    clock[0] += 999
    assert not ai.tick() and ai.worker is None
    assert ai.reason


def test_cancel_ignores_late_ready_and_switch_kills_old_worker(tmp_path, model):
    ai, clock = supervisor(tmp_path, model)
    old = ai.worker
    ai.normal()
    old.events.put({'event': 'ready'})
    ai.tick()
    assert ai.active == 'normal'
    ai.activate(model['id'], 2, 'balanced', 'mic', 'input', 'output')
    newer = ai.worker
    ai.activate(model['id'], 1, 'responsive', 'mic', 'input', 'output')
    assert old.stopped and newer.stopped and ai.worker is not newer


def test_invalid_and_missing_models_fall_back(tmp_path):
    ai = AISupervisor(tmp_path, Worker)
    ai.activate('../outside', 0, 'responsive', 'mic', 'input', 'output')
    assert ai.phase == 'failed' and ai.worker is None
    ai.activate('a' * 32, 0, 'responsive', 'mic', 'input', 'output')
    assert ai.phase == 'failed' and ai.active == 'normal'


def test_models_are_managed_deduplicated_and_preserve_metadata(tmp_path, model):
    lib = ModelLibrary(tmp_path)
    original = tmp_path / 'voice.pth'
    duplicate = lib.import_model(original, 'Other label', 'other source', 'other terms', validator=lambda p: {})
    assert duplicate['id'] == model['id']
    original.unlink()
    assert lib.get(model['id'])['sha256'] == model['sha256']
    lib.set_tuning(model['id'], -3, 'balanced')
    assert lib.get(model['id'])['tuning'] == {'pitch': -3, 'profile': 'balanced'}
    lib.remove(model['id'])
    assert lib.records() == []


def test_zip_never_extracts_scripts_or_paths(tmp_path):
    archive = tmp_path / 'model.zip'
    with zipfile.ZipFile(archive, 'w') as z:
        z.writestr('../../model.pth', b'weights')
        z.writestr('../../evil.py', b'raise Exception()')
    lib = ModelLibrary(tmp_path)
    record = lib.import_model(archive, 'Voice', 'source', 'terms', validator=lambda p: {})
    assert (lib.directory(record['id']) / 'model.pth').read_bytes() == b'weights'
    assert not (tmp_path / 'evil.py').exists()
    with zipfile.ZipFile(archive, 'w') as z:
        z.writestr('one.pth', b'x')
        z.writestr('two.pth', b'x')
    with pytest.raises(ValueError, match='exactly one'):
        lib.import_model(archive, 'Voice', 'source', 'terms', validator=lambda p: {})


def test_rejected_validation_is_not_committed(tmp_path):
    source = tmp_path / 'bad.pth'; source.write_bytes(b'bad')
    lib = ModelLibrary(tmp_path)
    def reject(path): raise ValueError('corrupt checkpoint')
    with pytest.raises(ValueError, match='corrupt'):
        lib.import_model(source, 'Voice', 'source', 'terms', validator=reject)
    assert lib.records() == [] and list(lib.root.iterdir()) == []
    with pytest.raises(ValueError): tuning(13, 'responsive')
    with pytest.raises(ValueError): tuning(0, 'unknown')


def test_ai_route_switches_preserve_source_and_post_effect_mute():
    pulse = Pulse(); router = Routing('test-ai', pulse)
    config = settings(); config.update(mic_muted=True, mic_volume=.37)
    router.reconcile(config)
    source = router.modules['source']
    config['_ai_mode'] = 'loading'
    router.reconcile(config)
    assert 'mic' in router.modules and 'ai_mic' in router.modules and 'ai_voice' not in router.modules
    config['_ai_mode'] = 'active'
    router.reconcile(config)
    assert 'mic' not in router.modules and 'ai_voice' in router.modules
    assert router.modules['source'] == source
    config['_ai_mode'] = 'failed'
    router.reconcile(config)
    assert 'mic' in router.modules and 'ai_voice' not in router.modules and 'ai_mic' not in router.modules
    assert router.modules['source'] == source
    voice = str(router.modules['voice_mix'] + 100)
    assert ('set-sink-input-mute', voice, '1') in pulse.calls
    assert ('set-sink-input-volume', voice, '37%') in pulse.calls


def test_crash_adopts_existing_core_and_returns_normal():
    pulse = Pulse(); old = Routing('stable-ai', pulse)
    old.reconcile(settings(_ai_mode='active'))
    source = old.modules['source']
    replacement = Routing('stable-ai', pulse)
    replacement.reconcile(settings())
    assert replacement.modules['source'] == source
    assert 'mic' in replacement.modules and 'ai_voice' not in replacement.modules
    assert len(pulse.modules) == 9


def test_service_starts_normal_remembers_preference_and_preserves_mute(tmp_path, model):
    lib = Library(tmp_path)
    lib.settings.update(settings(), devices_initialized=True, ai_model=model['id'], voice_effect='baby', mic_muted=True, mic_volume=.35)
    lib.save()
    original = lib.path.read_bytes()
    pulse = Pulse(); router = Routing(tmp_path, pulse)
    ai = AISupervisor(tmp_path, Worker)
    service = AudioService(tmp_path, router, ai)
    assert service.handle({'command': 'refresh'})['active_effect'] == 'normal'
    state = service.handle({'command': 'ai_activate', 'model': model['id']})
    assert state['ai']['phase'] == 'loading' and 'mic' in router.modules
    worker = ai.worker
    worker.events.put({'event': 'ready'})
    service.refresh()
    assert 'mic' not in router.modules
    worker.events.put({'event': 'error', 'reason': 'GPU failure'})
    service.refresh()
    assert service.state['ai']['active'] == 'normal'
    assert 'mic' in router.modules and service.settings['mic_muted'] and service.settings['mic_volume'] == .35
    assert lib.path.read_bytes() == original
    pulse.sources = []
    service.handle({'command': 'ai_activate', 'model': model['id']})
    assert ai.phase == 'failed' and not ai.worker
