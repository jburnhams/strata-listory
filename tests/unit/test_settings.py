import os

import pytest

from app import settings


def test_defaults_and_env_overrides(tmp_path, monkeypatch):
    monkeypatch.delenv("APP_DATA", raising=False)
    monkeypatch.delenv("APP_HOME", raising=False)
    assert settings.home() == os.path.join(os.path.expanduser("~"), ".app")
    assert settings.library_dir() == ""
    assert settings.data_dir() == settings.DEFAULT_DATA_DIR

    custom_home = tmp_path / "custom_home"
    custom_data = tmp_path / "custom_data"
    monkeypatch.setenv("APP_HOME", str(custom_home))
    monkeypatch.setenv("APP_DATA", str(custom_data))
    monkeypatch.setenv("APP_LIBRARY", "  ~/books  ")
    assert settings.home() == str(custom_home)
    assert settings.data_dir() == str(custom_data) and custom_data.is_dir()
    assert settings.library_dir() == os.path.abspath(os.path.expanduser("~/books"))


def test_save_load_and_reset():
    assert settings.load() == {}
    saved = settings.save(dict(library_dir="/tmp/books", data_dir="/tmp/appdata"))
    assert saved == dict(library_dir="/tmp/books", data_dir="/tmp/appdata")
    assert settings.load() == saved
    assert settings.library_dir() == os.path.abspath("/tmp/books")

    after = settings.save(dict(library_dir=None))
    assert "library_dir" not in after
    assert settings.library_dir() == ""

    with pytest.raises(KeyError, match="unknown setting"):
        settings.save(dict(unknown_key="val"))


def test_corrupt_or_non_dict_settings_file():
    os.makedirs(settings.home(), exist_ok=True)
    p = os.path.join(settings.home(), "settings.json")
    with open(p, "w", encoding="utf8") as f:
        f.write("{not valid json")
    assert settings.load() == {}
    with open(p, "w", encoding="utf8") as f:
        f.write("[1, 2, 3]")
    assert settings.load() == {}
    assert settings.all_settings()["library_dir"] == ""


def test_backup_dir_defaults_to_the_data_folder_and_can_be_moved(tmp_path, monkeypatch):
    assert settings.backup_dir() == os.path.join(settings.data_dir(), "backups")
    settings.save(dict(backup_dir=str(tmp_path / "share")))
    assert settings.backup_dir() == str(tmp_path / "share")
    monkeypatch.setenv("APP_BACKUP", str(tmp_path / "env"))
    assert settings.backup_dir() == str(tmp_path / "env")


def test_library_roots_are_library_dir_then_extra_folders_without_overlaps(monkeypatch, tmp_path):
    monkeypatch.delenv("APP_LIBRARY", raising=False)
    assert settings.library_roots() == []
    audio, more, inside = (
        str(tmp_path / "a" / "audio"),
        str(tmp_path / "b" / "more"),
        str(tmp_path / "a" / "audio" / "inside"),
    )
    settings.save(dict(library_dir=audio, library_dirs=[more, audio, inside, more]))
    assert settings.library_roots() == [audio, more]
    settings.save(
        dict(library_dirs=[str(tmp_path / "a")])
    )  # a folder holding the library_dir replaces it: a file may be in one root only
    assert settings.library_roots() == [str(tmp_path / "a")]
    settings.save(dict(library_dirs=None))
    assert settings.library_roots() == [audio]
