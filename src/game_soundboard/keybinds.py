"""Record one Qt key combination and translate it to XDG/XKB shortcut syntax."""
import ctypes
from functools import lru_cache
from PySide6.QtCore import Qt
from PySide6.QtGui import QKeySequence
from PySide6.QtWidgets import QWidget, QHBoxLayout, QKeySequenceEdit, QPushButton, QLineEdit

MODIFIERS = [("CTRL", Qt.ControlModifier), ("ALT", Qt.AltModifier),
             ("SHIFT", Qt.ShiftModifier), ("LOGO", Qt.MetaModifier)]
SPECIAL = {
    Qt.Key_Escape: "Escape", Qt.Key_Tab: "Tab", Qt.Key_Backtab: "ISO_Left_Tab",
    Qt.Key_Backspace: "BackSpace", Qt.Key_Return: "Return", Qt.Key_Enter: "KP_Enter",
    Qt.Key_Insert: "Insert", Qt.Key_Delete: "Delete", Qt.Key_Pause: "Pause",
    Qt.Key_Print: "Print", Qt.Key_SysReq: "Sys_Req", Qt.Key_Clear: "Clear",
    Qt.Key_Home: "Home", Qt.Key_End: "End", Qt.Key_Left: "Left", Qt.Key_Up: "Up",
    Qt.Key_Right: "Right", Qt.Key_Down: "Down", Qt.Key_PageUp: "Prior", Qt.Key_PageDown: "Next",
    Qt.Key_CapsLock: "Caps_Lock", Qt.Key_NumLock: "Num_Lock", Qt.Key_ScrollLock: "Scroll_Lock",
    Qt.Key_VolumeDown: "XF86AudioLowerVolume", Qt.Key_VolumeUp: "XF86AudioRaiseVolume",
    Qt.Key_VolumeMute: "XF86AudioMute", Qt.Key_MediaPlay: "XF86AudioPlay",
    Qt.Key_MediaStop: "XF86AudioStop", Qt.Key_MediaPrevious: "XF86AudioPrev",
    Qt.Key_MediaNext: "XF86AudioNext",
}
SPECIAL.update({int(Qt.Key_F1) + n: f"F{n+1}" for n in range(35)})
KEYPAD = {ord(str(n)): f"KP_{n}" for n in range(10)}
KEYPAD.update({Qt.Key_Plus: "KP_Add", Qt.Key_Minus: "KP_Subtract", Qt.Key_Asterisk: "KP_Multiply",
    Qt.Key_Slash: "KP_Divide", Qt.Key_Period: "KP_Decimal", Qt.Key_Comma: "KP_Separator",
    Qt.Key_Enter: "KP_Enter", Qt.Key_Return: "KP_Enter", Qt.Key_Equal: "KP_Equal",
    Qt.Key_Home: "KP_Home", Qt.Key_End: "KP_End", Qt.Key_Up: "KP_Up", Qt.Key_Down: "KP_Down",
    Qt.Key_Left: "KP_Left", Qt.Key_Right: "KP_Right", Qt.Key_PageUp: "KP_Prior",
    Qt.Key_PageDown: "KP_Next", Qt.Key_Insert: "KP_Insert", Qt.Key_Delete: "KP_Delete"})


@lru_cache(maxsize=1)
def xkb():
    lib = ctypes.CDLL("libxkbcommon.so.0")
    lib.xkb_utf32_to_keysym.argtypes = [ctypes.c_uint32]
    lib.xkb_utf32_to_keysym.restype = ctypes.c_uint32
    lib.xkb_keysym_to_utf32.argtypes = [ctypes.c_uint32]
    lib.xkb_keysym_to_utf32.restype = ctypes.c_uint32
    lib.xkb_keysym_from_name.argtypes = [ctypes.c_char_p, ctypes.c_int]
    lib.xkb_keysym_from_name.restype = ctypes.c_uint32
    lib.xkb_keysym_get_name.argtypes = [ctypes.c_uint32, ctypes.c_char_p, ctypes.c_size_t]
    lib.xkb_keysym_get_name.restype = ctypes.c_int
    return lib


def symbol_name(symbol):
    result = ctypes.create_string_buffer(128)
    if xkb().xkb_keysym_get_name(symbol, result, len(result)) <= 0:
        raise ValueError("This key cannot be used as a global shortcut. Choose another key.")
    return result.value.decode("ascii")


def to_xdg(sequence):
    if sequence.isEmpty():
        return ""
    if sequence.count() != 1:
        raise ValueError("Use one key combination, not a sequence of several shortcuts.")
    combo = sequence[0]
    key, mods = int(combo.key()), combo.keyboardModifiers()
    if mods & Qt.GroupSwitchModifier:
        raise ValueError("AltGr shortcuts are not supported. Use Ctrl, Alt, Shift or Super instead.")
    if mods & Qt.KeypadModifier:
        name = KEYPAD.get(key)
        if name is None:
            raise ValueError("This keypad key is not supported. Choose another key.")
    elif key in SPECIAL:
        name = SPECIAL[key]
    elif 0x20 <= key <= 0x10FFFF:
        char = chr(key).lower()
        if len(char) != 1:
            raise ValueError("This key cannot be represented as one shortcut.")
        name = symbol_name(xkb().xkb_utf32_to_keysym(ord(char)))
    else:
        raise ValueError("Press a letter, number, function key or navigation key with any modifiers.")
    return "+".join([label for label, modifier in MODIFIERS if mods & modifier] + [name])


def from_xdg(trigger):
    if not trigger:
        return QKeySequence()
    parts = trigger.split("+")
    mods = Qt.NoModifier
    mapping = dict(MODIFIERS)
    for part in parts[:-1]:
        if part not in mapping:
            raise ValueError("Saved modifier is not supported by the recorder")
        mods |= mapping[part]
    symbol = xkb().xkb_keysym_from_name(parts[-1].encode("ascii"), 0)
    if not symbol:
        raise ValueError("Unknown saved key")
    name = symbol_name(symbol)
    reverse_pad = {v: k for k, v in KEYPAD.items()}
    reverse_special = {v: k for k, v in SPECIAL.items()}
    if name in reverse_pad:
        key = reverse_pad[name]
        if name == "KP_Enter":
            key = Qt.Key_Enter
        mods |= Qt.KeypadModifier
    elif name in reverse_special:
        key = reverse_special[name]
    else:
        char = xkb().xkb_keysym_to_utf32(symbol)
        if char < 0x20:
            raise ValueError("Saved key cannot be displayed by the recorder")
        text = chr(char).upper()
        if len(text) != 1:
            raise ValueError("Saved key cannot be displayed by the recorder")
        key = ord(text)
    return QKeySequence(int(key) | mods.value)


def normalized_trigger(trigger):
    """Canonicalize existing text preferences for duplicate detection."""
    try:
        return to_xdg(from_xdg(trigger))
    except (ValueError, UnicodeError):
        return trigger


class SingleKeyEdit(QKeySequenceEdit):
    """Keep keypad identity and never erase a binding on a modifier-only press.

    QKeySequenceEdit's default recorder drops KeypadModifier. Capture one key
    directly while retaining its native Qt display and accessibility support.
    """
    def keyPressEvent(self, event):
        if not event.isAutoRepeat() and event.key() not in (
                Qt.Key_Control, Qt.Key_Alt, Qt.Key_Shift, Qt.Key_Meta, Qt.Key_AltGr):
            self.setKeySequence(QKeySequence(event.key() | event.modifiers().value))
        event.accept()

    def keyReleaseEvent(self, event):
        event.accept()
        if not event.isAutoRepeat():
            self.editingFinished.emit()


class ShortcutRecorder(QWidget):
    def __init__(self, trigger="", parent=None):
        super().__init__(parent)
        self.original = trigger
        self.changed = False
        self.editor = SingleKeyEdit(self)
        self.editor.setMaximumSequenceLength(1)
        # Tab and Shift+Tab are recordable; click Save or another field to leave.
        self.editor.setFinishingKeyCombinations([])
        self.editor.setAccessibleName("Press a key combination")
        field = self.editor.findChild(QLineEdit)
        if field:
            field.setPlaceholderText("Click here, then press your shortcut")
        try:
            self.editor.setKeySequence(from_xdg(trigger))
        except (ValueError, UnicodeError):
            # Preserve legacy preferences when opening/saving unrelated settings.
            if field:
                field.setPlaceholderText(f"Saved: {trigger} (press keys to replace)")
        self.editor.keySequenceChanged.connect(self.recorded)
        self.clear_button = QPushButton("Clear", self)
        self.clear_button.clicked.connect(self.clear)
        layout = QHBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.addWidget(self.editor, 1)
        layout.addWidget(self.clear_button)
        self.setFocusProxy(self.editor)

    def recorded(self, _):
        self.changed = True

    def clear(self):
        self.changed = True
        self.editor.clear()

    def trigger(self):
        return to_xdg(self.editor.keySequence()) if self.changed else self.original
