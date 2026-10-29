import os
import pytest
from app import settings


def test_defaults_and_env_overrides(tmp_path, monkeypatch):
    monkeypatch.delenv('APP_DATA', raising=False)
    monkeypatch.delenv('APP_HOME', raising=False)
    assert settings.home() == os.path.join(os.path.expanduser('~'), '.app')
    assert settings.library_dir() == ''
    assert settings.data_dir() == settings.DEFAULT_DATA_DIR

    custom_home = tmp_path / 'custom_home'
    custom_data = tmp_path / 'custom_data'
    monkeypatch.setenv('APP_HOME', str(custom_home))
    monkeypatch.setenv('APP_DATA', str(custom_data))
    monkeypatch.setenv('APP_LIBRARY', '  ~/books  ')
    assert settings.home() == str(custom_home)
    assert settings.data_dir() == str(custom_data) and custom_data.is_dir()
    assert settings.library_dir() == os.path.abspath(os.path.expanduser('~/books'))


def test_save_load_and_reset():
    assert settings.load() == {}
    saved = settings.save(dict(library_dir='/tmp/books', data_dir='/tmp/appdata'))
    assert saved == dict(library_dir='/tmp/books', data_dir='/tmp/appdata')
    assert settings.load() == saved
    assert settings.library_dir() == os.path.abspath('/tmp/books')

    after = settings.save(dict(library_dir=None))
    assert 'library_dir' not in after
    assert settings.library_dir() == ''

    with pytest.raises(KeyError, match='unknown setting'):
        settings.save(dict(unknown_key='val'))


def test_corrupt_or_non_dict_settings_file():
    os.makedirs(settings.home(), exist_ok=True)
    p = os.path.join(settings.home(), 'settings.json')
    with open(p, 'w', encoding='utf8') as f: f.write('{not valid json')
    assert settings.load() == {}
    with open(p, 'w', encoding='utf8') as f: f.write('[1, 2, 3]')
    assert settings.load() == {}
    assert settings.all_settings()['library_dir'] == ''
