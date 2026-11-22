import asyncio
from dbus_next import Message, Variant
from game_soundboard.shortcuts import Shortcuts, IFACE


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
