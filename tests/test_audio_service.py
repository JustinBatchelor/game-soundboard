import json
import socket
import pytest
from game_soundboard.audio_service import AudioService, receive, socket_path
from game_soundboard.library import Library


class Router:
    def __init__(self):
        self.settings = []
        self.connected = []

    def devices(self):
        return [{'name': 'mic'}], [{'name': 'headphones'}]

    def call(self, command):
        return 'mic' if command == 'get-default-source' else 'headphones'

    def reconcile(self, settings):
        self.settings.append(dict(settings))
        return {'message': 'Ready', 'capture_apps': []}

    def connect_app(self, app):
        self.connected.append(app)


def test_service_reads_atomic_settings_without_rewriting_library(tmp_path):
    lib = Library(tmp_path)
    lib.settings.update(devices_initialized=True, microphone='saved-mic', mic_muted=True)
    lib.save()
    before = lib.path.read_bytes()
    router = Router()
    service = AudioService(tmp_path, router)
    state = service.handle({'command': 'refresh'})
    assert state['persistent'] and state['initial_settings'] is None
    assert router.settings[-1]['mic_muted']
    assert router.settings[-1]['microphone'] == 'saved-mic'
    assert lib.path.read_bytes() == before
    lib.settings['mic_muted'] = False
    lib.save()
    service.refresh()
    assert not router.settings[-1]['mic_muted']


def test_first_device_choice_returned_for_gui_to_persist(tmp_path):
    service = AudioService(tmp_path, Router())
    state = service.handle({'command': 'refresh'})
    assert state['initial_settings']['microphone'] == 'mic'
    assert state['initial_settings']['output'] == 'headphones'
    assert not (tmp_path / 'library.json').exists()


def test_manifest_error_does_not_start_competing_service_or_overwrite(tmp_path):
    path = tmp_path / 'library.json'
    path.write_text('{"version": 99}')
    service = AudioService(tmp_path, Router())
    service.refresh()
    assert service.handle({'command': 'ping'})['pid'] > 0
    with pytest.raises(RuntimeError, match='Unsupported'):
        service.handle({'command': 'status'})
    assert json.loads(path.read_text())['version'] == 99


def test_service_connects_through_owner_and_rejects_unknown_commands(tmp_path):
    router = Router()
    service = AudioService(tmp_path, router)
    service.handle({'command': 'connect', 'app': {'index': '5'}})
    assert router.connected == [{'index': '5'}]
    with pytest.raises(ValueError, match='Unknown'):
        service.handle({'command': 'shell', 'value': 'anything'})


def test_socket_names_isolate_libraries(tmp_path, monkeypatch):
    monkeypatch.setenv('XDG_RUNTIME_DIR', str(tmp_path))
    assert socket_path(tmp_path / 'one') != socket_path(tmp_path / 'two')
    monkeypatch.delenv('XDG_RUNTIME_DIR')
    with pytest.raises(RuntimeError, match='desktop'):
        socket_path(tmp_path)


def test_ipc_handles_split_messages_and_rejects_oversize(monkeypatch):
    class Socket:
        def __init__(self, parts): self.parts = iter(parts)
        def recv(self, _): return next(self.parts)
    assert receive(Socket([b'{"command":', b'"ping"}\n'])) == {'command': 'ping'}
    monkeypatch.setattr('game_soundboard.audio_service.MAX_MESSAGE', 4)
    with pytest.raises(ValueError, match='too large'):
        receive(Socket([b'12345']))
