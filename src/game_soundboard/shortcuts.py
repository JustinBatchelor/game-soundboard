"""Portal messages use dbus-next: PySide does not expose custom D-Bus type registration.
The worker emits Qt signals; the GUI and audio objects stay on the Qt main thread.
"""
import asyncio
import concurrent.futures
import threading
import uuid
from dbus_next import Message, MessageType, Variant
from dbus_next.aio import MessageBus
from PySide6.QtCore import QObject, Signal
from . import APP_ID

DEST = "org.freedesktop.portal.Desktop"
PATH = "/org/freedesktop/portal/desktop"
IFACE = "org.freedesktop.portal.GlobalShortcuts"


class Shortcuts(QObject):
    activated = Signal(str)
    status = Signal(str)
    bindings = Signal(dict)

    def __init__(self, parent=None):
        super().__init__(parent)
        self.loop = asyncio.new_event_loop()
        self.thread = threading.Thread(target=self._run, daemon=True)
        self.bus = None
        self.session = None
        self.responses = {}
        self.held = set()
        self.gate = None
        self.thread.start()

    def _run(self):
        asyncio.set_event_loop(self.loop)
        self.loop.run_forever()
        self.loop.close()

    async def call(self, interface, member, signature="", body=None, path=PATH):
        reply = await self.bus.call(Message(destination=DEST, path=path, interface=interface,
                                            member=member, signature=signature, body=body or []))
        if reply.message_type == MessageType.ERROR:
            raise RuntimeError(f'{reply.error_name}: {reply.body}')
        return reply.body

    async def connect(self):
        if self.bus and self.bus.connected:
            return
        self.bus = await MessageBus().connect()
        self.session = None
        self.bus.add_message_handler(self.message)
        reply = await self.bus.call(Message(destination="org.freedesktop.DBus", path="/org/freedesktop/DBus",
            interface="org.freedesktop.DBus", member="AddMatch", signature="s",
            body=["type='signal',sender='org.freedesktop.portal.Desktop'"]))
        if reply.message_type == MessageType.ERROR:
            raise RuntimeError(str(reply.body))
        # Host-app registration provides a stable identity outside Flatpak.
        await self.call("org.freedesktop.host.portal.Registry", "Register", "sa{sv}", [APP_ID, {}])

    def message(self, msg):
        if msg.message_type != MessageType.SIGNAL:
            return
        if msg.interface == "org.freedesktop.portal.Request" and msg.member == "Response":
            future = self.responses.get(msg.path)
            if future and not future.done():
                future.set_result(msg.body)
        elif msg.interface == IFACE:
            if not msg.body or msg.body[0] != self.session:
                return
            if msg.member == "Activated":
                ident = msg.body[1]
                if ident not in self.held:
                    self.held.add(ident)
                    self.activated.emit(ident)
            elif msg.member == "Deactivated":
                self.held.discard(msg.body[1])
            elif msg.member == "ShortcutsChanged":
                self.publish(msg.body[1])
        elif msg.interface == "org.freedesktop.portal.Session" and msg.member == "Closed":
            if msg.path == self.session:
                self.session = None
                self.held.clear()
                self.bindings.emit({})
                self.status.emit("Shortcut session closed · click Configure shortcuts to reconnect")

    def publish(self, shortcuts):
        labels = {ident: values.get("trigger_description", Variant("s", "")).value
                  for ident, values in shortcuts}
        self.bindings.emit(labels)

    async def request(self, method, signature, body, options):
        token = "r" + uuid.uuid4().hex
        options["handle_token"] = Variant("s", token)
        sender = self.bus.unique_name[1:].replace(".", "_")
        path = f"/org/freedesktop/portal/desktop/request/{sender}/{token}"
        future = self.loop.create_future()
        self.responses[path] = future
        try:
            await self.call(IFACE, method, signature, body)
            code, result = await asyncio.wait_for(future, timeout=180)
            if code != 0:
                raise RuntimeError("Shortcut configuration cancelled or denied")
            return result
        finally:
            self.responses.pop(path, None)

    def bind(self, entries):
        return asyncio.run_coroutine_threadsafe(self._bind(entries), self.loop)

    async def _bind(self, entries):
        if self.gate is None:
            self.gate = asyncio.Lock()
        if self.gate.locked():
            self.status.emit("Finish the open shortcut dialog first")
            return
        async with self.gate:
            try:
                self.status.emit("Connecting global shortcuts…")
                await self.connect()
                await self.close_session()
                self.bindings.emit({})
                if not entries:
                    self.status.emit("No global shortcuts assigned")
                    return
                options = {"session_handle_token": Variant("s", "s" + uuid.uuid4().hex)}
                result = await self.request("CreateSession", "a{sv}", [options], options)
                self.session = result["session_handle"].value
                shortcuts = [[ident, {"description": Variant("s", label),
                                      "preferred_trigger": Variant("s", trigger)}]
                             for ident, label, trigger in entries]
                opts = {}
                self.status.emit("Approve shortcuts in the desktop dialog")
                result = await self.request("BindShortcuts", "oa(sa{sv})sa{sv}",
                                            [self.session, shortcuts, "", opts], opts)
                values = result.get("shortcuts", Variant("a(sa{sv})", [])).value
                self.publish(values)
                self.status.emit(f"Global shortcuts active: {len(values)}/{len(entries)} · desktop controls conflicts")
            except Exception as exc:
                self.status.emit("Global shortcuts unavailable: " + str(exc))
                await self.close_session()
                self.bindings.emit({})

    async def close_session(self):
        old, self.session = self.session, None
        self.held.clear()
        if old and self.bus and self.bus.connected:
            try:
                await self.call("org.freedesktop.portal.Session", "Close", path=old)
            except Exception:
                pass

    def stop(self):
        async def shutdown():
            await self.close_session()
            if self.bus:
                self.bus.disconnect()
            for task in asyncio.all_tasks():
                if task is not asyncio.current_task():
                    task.cancel()
        try:
            asyncio.run_coroutine_threadsafe(shutdown(), self.loop).result(timeout=3)
        except (concurrent.futures.TimeoutError, RuntimeError):
            pass
        self.loop.call_soon_threadsafe(self.loop.stop)
        self.thread.join(timeout=3)

    def configure(self):
        async def show():
            try:
                if not self.session:
                    raise RuntimeError("Assign at least one shortcut and save first")
                await self.call(IFACE, "ConfigureShortcuts", "osa{sv}", [self.session, "", {}])
            except Exception as exc:
                self.status.emit("Desktop shortcut settings: " + str(exc))
        asyncio.run_coroutine_threadsafe(show(), self.loop)
