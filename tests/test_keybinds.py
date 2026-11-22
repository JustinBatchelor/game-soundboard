import os
os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')
import pytest
from PySide6.QtCore import Qt
from PySide6.QtGui import QKeySequence
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication, QDialog, QVBoxLayout
from game_soundboard.keybinds import ShortcutRecorder, from_xdg, to_xdg, normalized_trigger


@pytest.fixture(scope='module')
def app():
    instance = QApplication.instance() or QApplication([])
    yield instance


@pytest.mark.parametrize('trigger', [
    'CTRL+ALT+k', 'SHIFT+F8', 'CTRL+BackSpace', 'LOGO+space',
    'CTRL+plus', 'CTRL+comma', 'CTRL+bracketleft', 'KP_1', 'CTRL+KP_Enter',
    'ALT+Left', 'CTRL+Prior', 'Tab', 'SHIFT+Tab', 'Escape', 'CTRL+eacute',
])
def test_portal_round_trip(trigger):
    assert to_xdg(from_xdg(trigger)) == trigger


@pytest.mark.parametrize(('key', 'modifiers', 'expected'), [
    (Qt.Key_K, Qt.ControlModifier | Qt.AltModifier, 'CTRL+ALT+k'),
    (Qt.Key_F8, Qt.NoModifier, 'F8'),
    (Qt.Key_Backspace, Qt.ControlModifier, 'CTRL+BackSpace'),
    (Qt.Key_Comma, Qt.ControlModifier, 'CTRL+comma'),
    (Qt.Key_1, Qt.KeypadModifier, 'KP_1'),
    (Qt.Key_Tab, Qt.NoModifier, 'Tab'),
    (Qt.Key_Return, Qt.ControlModifier, 'CTRL+Return'),
    (Qt.Key_Escape, Qt.NoModifier, 'Escape'),
])
def test_records_actual_key_events_without_accepting_dialog(app, key, modifiers, expected):
    dialog = QDialog()
    recorder = ShortcutRecorder('CTRL+ALT+o')
    layout = QVBoxLayout(dialog)
    layout.addWidget(recorder)
    dialog.show()
    recorder.editor.setFocus()
    QTest.keyClick(recorder.editor, key, modifiers)
    app.processEvents()
    assert recorder.trigger() == expected
    assert dialog.isVisible()
    assert recorder.editor.keySequence().count() == 1
    dialog.close()


def test_clear_and_reopen_preserve_legacy_settings(app):
    recorder = ShortcutRecorder('ALT+CTRL+k')
    assert recorder.trigger() == 'ALT+CTRL+k'
    recorder.clear_button.click()
    assert recorder.trigger() == ''
    assert recorder.editor.keySequence().isEmpty()
    legacy = ShortcutRecorder('NUM+F8')
    assert legacy.trigger() == 'NUM+F8'
    legacy.clear_button.click()
    assert legacy.trigger() == ''


def test_modifier_only_does_not_erase_existing_binding(app):
    recorder = ShortcutRecorder('CTRL+ALT+k')
    recorder.show()
    recorder.editor.setFocus()
    QTest.keyClick(recorder.editor, Qt.Key_Control)
    app.processEvents()
    assert recorder.trigger() == 'CTRL+ALT+k'
    recorder.close()


def test_duplicate_detection_uses_normalized_combinations():
    from game_soundboard.ui import Window
    class Board:
        def entries(self):
            return [('first', 'Play one', 'ALT+CTRL+k'), ('stop-all', 'Stop', 'F8')]
    assert normalized_trigger('ALT+CTRL+K') == 'CTRL+ALT+k'
    with pytest.raises(ValueError, match='already assigned'):
        Window.validate_shortcut(Board(), 'CTRL+ALT+k', 'second')
    with pytest.raises(ValueError, match='already assigned'):
        Window.validate_shortcut(Board(), 'F8', 'second')
    assert Window.validate_shortcut(Board(), 'CTRL+ALT+k', 'first') == 'CTRL+ALT+k'


def test_global_playback_is_suppressed_while_editing():
    from game_soundboard.ui import Window
    class Playback:
        def __init__(self): self.actions = []
        def play(self, ident): self.actions.append(ident)
        def stop_all(self): self.actions.append('stop')
    class Board:
        shortcuts_paused = True
        playback = Playback()
    board = Board()
    Window.trigger(board, 'sound')
    Window.trigger(board, 'stop-all')
    assert not board.playback.actions
    board.shortcuts_paused = False
    Window.trigger(board, 'sound')
    assert board.playback.actions == ['sound']
