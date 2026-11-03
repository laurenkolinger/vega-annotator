"""Tests for settings.py."""

import json

import pytest

from vega_annotator import paths, settings


def test_load_defaults_when_missing():
    assert settings.load() == settings.DEFAULTS


def test_save_and_reload_normalizes_initials():
    saved = settings.save({"initials": "at", "server_folder": " /Volumes/home/x ", "device": "cpu"})
    assert saved == {"initials": "AT", "server_folder": "/Volumes/home/x", "device": "cpu"}
    assert settings.load() == saved


@pytest.mark.parametrize("bad", ["", "ABCDE", "A1", "A B", "../x", "<b>", 12])
def test_normalize_initials_rejects(bad):
    with pytest.raises(ValueError):
        settings.normalize_initials(bad)


def test_save_rejects_unknown_key_and_bad_device():
    with pytest.raises(ValueError):
        settings.save({"nope": 1})
    with pytest.raises(ValueError):
        settings.save({"device": "quantum"})
    with pytest.raises(ValueError):
        settings.save({"server_folder": 5})


def test_corrupt_file_is_moved_aside_and_defaults_used():
    path = paths.settings_path()
    path.write_text("{not json", encoding="utf-8")
    assert settings.load() == settings.DEFAULTS
    assert not path.exists()
    assert path.with_name("settings.json.bad").exists()


def test_wrong_types_fall_back_field_by_field():
    paths.settings_path().write_text(json.dumps({"server_folder": 3, "initials": "toolong", "device": "gpu"}), encoding="utf-8")
    assert settings.load() == {"server_folder": "", "initials": "", "device": "gpu"}


def test_computer_name_is_text():
    assert settings.computer_name()
