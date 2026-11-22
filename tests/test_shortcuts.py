import asyncio
from dbus_next import Message, Variant
from game_soundboard.shortcuts import BindingRegistry, Shortcuts, IFACE


class Emitter:
    def __init__(self):
        self.values = []
    def emit(self, *args):
        self.values.append(args)


class Client:
    message = Shortcuts.message
    publish = Shortcuts.publish
    def __init__(self):
        self.session = '/session/current'
        self.held = set()
        self.responses = {}
        self.actions = {'sound1': 'sound1'}
        self.activated = Emitter()
        self.bindings = Emitter()
        self.status = Emitter()


def event(member, session='/session/current'):
    return Message.new_signal('/org/freedesktop/portal/desktop', IFACE, member,
                              'osta{sv}', [session, 'sound1', 100, {}])


def test_global_key_hold_deduplicated_and_release_rearms():
    c = Client()
    c.message(event('Activated'))
    c.message(event('Activated'))
    c.message(event('Activated', '/session/other'))
    assert c.activated.values == [('sound1',)]
    c.message(event('Deactivated'))
    c.message(event('Activated'))
    assert c.activated.values == [('sound1',), ('sound1',)]


def test_desktop_binding_labels_and_session_revocation():
    c = Client()
    c.message(Message.new_signal('/org/freedesktop/portal/desktop', IFACE, 'ShortcutsChanged',
        'oa(sa{sv})', ['/session/current', [['sound1', {'trigger_description': Variant('s', 'Ctrl+Alt+K')}]]]))
    assert c.bindings.values == [({'sound1': 'Ctrl+Alt+K'},)]
    c.message(event('Activated'))
    c.message(Message.new_signal('/session/current', 'org.freedesktop.portal.Session', 'Closed', ''))
    assert c.session is None
    assert not c.held
    assert c.bindings.values[-1] == ({},)


def test_request_cancel_returns_failure_and_removes_listener():
    async def run():
        class Bus:
            unique_name = ':1.234'
        class Request:
            request = Shortcuts.request
            bus = Bus()
            responses = {}
            loop = asyncio.get_running_loop()
            async def call(self, *_):
                next(iter(self.responses.values())).set_result([1, {}])
        r = Request()
        opts = {}
        try:
            await r.request('CreateSession', 'a{sv}', [opts], opts)
            assert False, 'cancellation must not succeed'
        except RuntimeError as exc:
            assert 'cancelled or denied' in str(exc)
        assert r.responses == {}
    asyncio.run(run())


def test_rebinding_replaces_cached_desktop_trigger_and_survives_relaunch(tmp_path):
    path = tmp_path / 'shortcuts.json'
    desktop = {}
    def approve(trigger, label='Play sound'):
        shortcuts, actions = BindingRegistry(path).prepare([('sound1', label, trigger)])
        ident, props = shortcuts[0]
        # Reproduce GNOME: an existing ID keeps its desktop-approved key.
        desktop.setdefault(ident, props['preferred_trigger'].value)
        assert actions == {ident: 'sound1'}
        return ident, desktop[ident]
    first, key = approve('F5')
    assert key == 'F5'
    second, key = approve('F4')
    assert second != first and key == 'F4'
    assert approve('F4', 'Renamed sound') == (second, 'F4')
    third, key = approve('F5')
    assert third not in {first, second} and key == 'F5'


def test_clearing_binding_invalidates_old_registration_without_changing_others(tmp_path):
    registry = BindingRegistry(tmp_path / 'shortcuts.json')
    _, first = registry.prepare([('sound1', 'Play', 'F5'), ('stop-all', 'Stop', 'F8')])
    _, cleared = registry.prepare([('stop-all', 'Stop', 'F8')])
    assert list(cleared) == [key for key, value in first.items() if value == 'stop-all']
    _, restored = registry.prepare([('sound1', 'Play', 'F5'), ('stop-all', 'Stop', 'F8')])
    assert set(first) & set(restored) == set(cleared)
    registry.prepare([])
    assert BindingRegistry(registry.path).prepare([]) == ([], {})


def test_portal_revision_events_translate_to_sound_and_ignore_stale_ids():
    c = Client()
    c.actions = {'revision-new': 'sound1'}
    def revised_event(member, ident, session='/session/current'):
        return Message.new_signal('/org/freedesktop/portal/desktop', IFACE, member,
                                  'osta{sv}', [session, ident, 100, {}])
    c.message(revised_event('Activated', 'revision-old'))
    c.message(revised_event('Activated', 'revision-new', '/session/old'))
    c.message(revised_event('Activated', 'revision-new'))
    c.message(revised_event('Activated', 'revision-new'))
    assert c.activated.values == [('sound1',)]
    c.message(revised_event('Deactivated', 'revision-new'))
    c.message(revised_event('Activated', 'revision-new'))
    assert c.activated.values == [('sound1',), ('sound1',)]
    c.publish([['revision-old', {'trigger_description': Variant('s', 'F5')}],
               ['revision-new', {'trigger_description': Variant('s', 'F4')}]])
    assert c.bindings.values[-1] == ({'sound1': 'F4'},)


def test_save_during_portal_dialog_is_applied_after_dialog_finishes(tmp_path):
    async def run():
        class Binder(Client):
            _bind = Shortcuts._bind
            def __init__(self):
                super().__init__()
                self.gate = None
                self.registry = BindingRegistry(tmp_path / 'shortcuts.json')
                self.opened = asyncio.Event()
                self.release = asyncio.Event()
                self.triggers = []
            async def connect(self): pass
            async def close_session(self):
                self.session = None
                self.actions.clear()
                self.held.clear()
            async def request(self, method, signature, body, options):
                if method == 'CreateSession':
                    return {'session_handle': Variant('s', '/session/current')}
                shortcuts = body[1]
                self.triggers.append(shortcuts[0][1]['preferred_trigger'].value)
                if len(self.triggers) == 1:
                    self.opened.set()
                    await self.release.wait()
                return {'shortcuts': Variant('a(sa{sv})', [[ident, {
                    'trigger_description': props['preferred_trigger']}] for ident, props in shortcuts])}
        c = Binder()
        first = asyncio.create_task(c._bind([('sound1', 'Play sound', 'F5')]))
        await c.opened.wait()
        second = asyncio.create_task(c._bind([('sound1', 'Play sound', 'F4')]))
        await asyncio.sleep(0)
        c.release.set()
        await asyncio.gather(first, second)
        assert c.triggers == ['F5', 'F4']
        assert c.bindings.values[-1] == ({'sound1': 'F4'},)
    asyncio.run(run())
