import json
import wave
import pytest
from game_soundboard.library import Library


def sample(path):
    with wave.open(str(path), 'wb') as f:
        f.setnchannels(1)
        f.setsampwidth(2)
        f.setframerate(22050)
        f.writeframes(b'\0\0' * 2205)
    return path


def test_import_survives_original_removal_and_persists(tmp_path):
    source = sample(tmp_path / 'source.wav')
    lib = Library(tmp_path / 'library')
    record = lib.prepare(source, 'Example', 'https://example.com', 'starter')
    lib.commit(record)
    source.unlink()
    restored = Library(lib.root)
    assert restored.sounds[0]['source'] == 'https://example.com'
    with wave.open(str(lib.root / record['cache'])) as f:
        assert (f.getframerate(), f.getnchannels(), f.getsampwidth()) == (48000, 2, 2)
    assert (lib.root / record['original']).exists()
    lib.remove(record['id'])
    assert Library(lib.root).seeded == ['starter']
    assert not (lib.root / record['cache']).exists()


def test_invalid_import_has_no_files_or_record(tmp_path):
    path = tmp_path / 'bad.mp3'
    path.write_text('not audio')
    lib = Library(tmp_path / 'library')
    with pytest.raises(ValueError):
        lib.prepare(path)
    assert lib.sounds == []
    assert list((lib.root / 'cache').iterdir()) == []
    assert list((lib.root / 'originals').iterdir()) == []


def test_duplicate_seed_and_order(tmp_path):
    path = sample(tmp_path / 'source.wav')
    lib = Library(tmp_path / 'library')
    lib.commit(lib.prepare(path, 'first', seed='one'))
    lib.commit(lib.prepare(path, 'duplicate', seed='one'))
    assert len(lib.sounds) == 1
    second = lib.prepare(path, 'second')
    lib.commit(second)
    lib.move(second['id'], -1)
    assert Library(lib.root).sounds[0]['name'] == 'second'
    assert len(list((lib.root / 'cache').iterdir())) == 2


def test_unknown_manifest_is_not_overwritten(tmp_path):
    path = tmp_path / 'library.json'
    path.write_text('{"version": 99}')
    with pytest.raises(ValueError):
        Library(tmp_path)
    assert json.loads(path.read_text()) == {'version': 99}


def test_audio_replacement_preserves_button_identity_settings_and_position(tmp_path):
    path = sample(tmp_path / 'source.wav')
    lib = Library(tmp_path / 'library')
    lib.commit(lib.prepare(path, 'first', seed='kaboom'))
    lib.commit(lib.prepare(path, 'second'))
    original = dict(lib.sounds[0])
    lib.sounds[0].update(volume=0.23, shortcut='CTRL+ALT+k', name='My Kaboom')
    lib.settings['monitor'] = True
    lib.save()
    prepared = lib.prepare(path, 'replacement', 'https://www.youtube.com/watch?v=UO9JSE3aRtw')
    lib.replace_audio(original['id'], prepared)
    restored = Library(lib.root)
    sound = restored.sounds[0]
    assert sound['id'] == original['id']
    assert (sound['name'], sound['volume'], sound['shortcut'], sound['seed']) == (
        'My Kaboom', 0.23, 'CTRL+ALT+k', 'kaboom')
    assert sound['cache'] == prepared['cache']
    assert sound['source'].endswith('UO9JSE3aRtw')
    assert restored.settings['monitor'] is True
    assert restored.seeded == ['kaboom']
    assert not (lib.root / original['cache']).exists()
    assert (lib.root / sound['cache']).exists()
    assert restored.sounds[1]['name'] == 'second'
