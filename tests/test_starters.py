import pytest
from game_soundboard import starters


def test_download_failure_does_not_seed_library(tmp_path, monkeypatch):
    from game_soundboard.library import Library
    lib = Library(tmp_path)
    def fail(*_):
        raise TimeoutError('network unavailable')
    monkeypatch.setattr(starters, 'fetch', fail)
    with pytest.raises(TimeoutError):
        starters.download_starter(lib, starters.STARTERS[0])
    assert lib.seeded == [] and lib.sounds == []


def test_missing_download_reports_replacement(tmp_path, monkeypatch):
    from game_soundboard.library import Library
    monkeypatch.setattr(starters, 'fetch', lambda *_: b'<html>No download</html>')
    with pytest.raises(ValueError, match='import manually'):
        starters.download_starter(Library(tmp_path), starters.STARTERS[0])
