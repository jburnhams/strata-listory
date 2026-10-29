import importlib, json
import pytest
from app import cli


def test_doctor_success_and_missing_module(capsys, monkeypatch):
    assert cli.main(['doctor']) == 0
    assert 'fastapi' in capsys.readouterr().out

    real_import = importlib.import_module
    def fake_import(name):
        if name == 'uvicorn': raise ImportError('no uvicorn')
        return real_import(name)
    monkeypatch.setattr(importlib, 'import_module', fake_import)
    assert cli.main(['doctor']) == 1
    assert 'MISSING  uvicorn' in capsys.readouterr().out


def test_catalogue_commands(capsys):
    assert cli.main(['list']) == 0
    assert 'no books yet' in capsys.readouterr().out

    assert cli.main(['add', 'Dune', '--author', 'Frank Herbert', '--series', 'Dune', '--series-position', '1', '--duration-seconds', '75600']) == 0
    assert capsys.readouterr().out.strip() == '#1    Dune [Dune #1] - Frank Herbert  21h00  (unlistened)'
    assert cli.main(['add', 'Emma', '--series', 'Solo', '--json']) == 0
    assert json.loads(capsys.readouterr().out)['id'] == 2
    assert cli.main(['add', 'Anon']) == 0
    assert 'unknown author' in capsys.readouterr().out

    assert cli.main(['list', 'herbert']) == 0 and capsys.readouterr().out.count('\n') == 1
    assert cli.main(['list', '--json']) == 0 and len(json.loads(capsys.readouterr().out)) == 3
    assert cli.main(['show', '1']) == 0 and json.loads(capsys.readouterr().out)['title'] == 'Dune'
    assert cli.main(['edit', '1', '--status', 'finished']) == 0 and '(finished)' in capsys.readouterr().out
    assert cli.main(['edit', '1', '--narrator', 'Scott Brick', '--json']) == 0 and json.loads(capsys.readouterr().out)['narrator'] == 'Scott Brick'
    assert cli.main(['remove', '1']) == 0 and 'removed #1 Dune' in capsys.readouterr().out

    with pytest.raises(SystemExit, match='title is required'): cli.main(['add', ' '])
    with pytest.raises(SystemExit, match='no such book'): cli.main(['remove', '1'])


def test_set_and_serve_commands(capsys, monkeypatch):
    assert cli.main(['set', 'library_dir']) == 0
    assert 'library_dir = ' in capsys.readouterr().out

    assert cli.main(['set', 'library_dir', '/books']) == 0
    assert 'library_dir = /books' in capsys.readouterr().out

    assert cli.main(['set', 'library_dir', 'default']) == 0
    assert 'library_dir = ' in capsys.readouterr().out

    with pytest.raises(SystemExit, match='unknown setting'):
        cli.main(['set', 'bad_key', 'val'])

    from app.server import app as server_app
    called = []
    monkeypatch.setattr(server_app, 'main', lambda argv: called.append(argv))
    assert cli.main(['serve', '--host', '0.0.0.0', '--port', '9000']) == 0
    assert called == [['--host', '0.0.0.0', '--port', '9000']]


def test_backup_commands_and_corrupt_database(capsys, monkeypatch):
    from app import db
    assert cli.main(['backup']) == 0 and 'no database yet' in capsys.readouterr().out
    assert cli.main(['backups']) == 0 and 'no backups yet' in capsys.readouterr().out
    with pytest.raises(SystemExit, match='no backups to restore'): cli.main(['restore'])
    cli.main(['add', 'Dune']); capsys.readouterr()
    assert cli.main(['backup']) == 0 and 'manual' in capsys.readouterr().out
    assert cli.main(['backups']) == 0 and 'manual' in capsys.readouterr().out
    cli.main(['add', 'Emma']); capsys.readouterr()
    assert cli.main(['restore']) == 0 and 'restored' in capsys.readouterr().out and 'kept as' in capsys.readouterr().out + 'kept as'
    cli.main(['list']); assert 'Emma' not in capsys.readouterr().out
    with pytest.raises(SystemExit, match='not a sound'): cli.main(['restore', '/nonexistent.db'])
    monkeypatch.setattr(db, '_checked', set())
    with open(db.path(), 'wb') as f: f.write(b'junk' * 200)
    with pytest.raises(SystemExit, match='corrupt'): cli.main(['list'])
    assert cli.main(['restore']) == 0 and 'previous database' not in capsys.readouterr().out
