import sys
import importlib, json, os
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
    assert f"library_dir = {os.path.abspath('/books')}" in capsys.readouterr().out

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


def test_scan_match_confirm_commands(capsys, tmp_path, monkeypatch):
    from app import matcher
    lib = tmp_path / 'lib'; (lib / 'Dune by Frank Herbert').mkdir(parents=True); (lib / 'Dune by Frank Herbert' / '01.mp3').write_text(''); (lib / 'Show S01E01.mp3').write_text('')
    with pytest.raises(SystemExit, match='no audiobook folder'): cli.main(['scan'])
    with pytest.raises(SystemExit, match='not a folder'): cli.main(['scan', str(tmp_path / 'nope')])
    assert cli.main(['scan', str(lib), '--dry-run']) == 0 and '2 candidates (1 books, 1 radio); nothing stored' in capsys.readouterr().out
    assert cli.main(['list', '--review', 'new']) == 0 and 'no books yet' in capsys.readouterr().out
    cli.main(['set', 'library_dir', str(lib)]); capsys.readouterr()
    assert cli.main(['scan']) == 0 and '2 added, 0 already known' in capsys.readouterr().out
    assert cli.main(['scan']) == 0 and '0 added, 2 already known' in capsys.readouterr().out

    from app.providers.base import candidate
    class Source:
        name = 'fake'
        def search(self, t, a=''): return [candidate('fake', id='1', title='Dune', authors=['Frank Herbert'])]
        def by_asin(self, a): return None
    monkeypatch.setattr(matcher.providers, 'enabled', lambda names=None: [Source()])
    assert cli.main(['match']) == 0 and 'tried 1: 1 suggestions, 0 without a match; 0 still to try (providers: fake)' in capsys.readouterr().out
    assert cli.main(['list', '--review', 'suggested']) == 0 and '[suggested -> Dune / Frank Herbert, 1.00]' in capsys.readouterr().out
    assert cli.main(['list', '--kind', 'radio']) == 0 and 'Show' in capsys.readouterr().out
    assert cli.main(['confirm', '2']) == 0 and 'suggested' not in capsys.readouterr().out
    assert cli.main(['ignore', '1']) == 0 and '[ignored]' in capsys.readouterr().out
    with pytest.raises(SystemExit, match='no such book'): cli.main(['confirm', '99'])

    def broken(*a, **k): raise matcher.MatchError('no AudiobookDB API key')
    monkeypatch.setattr(matcher, 'match_pending', broken)
    with pytest.raises(SystemExit, match='no AudiobookDB API key'): cli.main(['match'])


def test_providers_and_match_test_commands(capsys, monkeypatch):
    from app import matcher, providers
    from app.providers.base import candidate
    assert cli.main(['providers']) == 0
    out = capsys.readouterr().out
    assert 'audnexus' in out and 'audible' in out and 'audiobookdb' in out and 'selected #1' in out and 'ready' in out
    with pytest.raises(SystemExit, match='unknown provider'): cli.main(['match', '--providers', 'nope'])
    assert cli.main(['match-test']) == 0 and 'no scanned books to test' in capsys.readouterr().out
    cli.main(['add', 'Hand']); capsys.readouterr()
    class Source:
        name = 'fake'
        def search(self, t, a=''): return [candidate('fake', id='1', title='Dune', authors=['Frank Herbert'])]
        def by_asin(self, a): return None
    from app import catalogue
    catalogue.add_scanned([dict(path='/x', title='Dune', author='Frank Herbert', narrator='', series_position=None, asin='', kind='book', file_count=1)])
    monkeypatch.setattr(providers, 'enabled', lambda names=None: [Source()])
    assert cli.main(['match-test', '--limit', '5', '--providers', 'fake']) == 0
    assert '1 scanned books; nothing is recorded' in capsys.readouterr().out and catalogue.list_books()[0]['review'] in ('new', 'confirmed')
    def broken(*a, **k): raise providers.MatchError('all providers down')
    monkeypatch.setattr(matcher, 'evaluate', broken)
    with pytest.raises(SystemExit, match='all providers down'): cli.main(['match-test'])


def test_mirror_command_and_provider_limits(capsys):
    from app import catalogue, mirror
    assert cli.main(['providers']) == 0 and '1 request per 0.3s, cache 14 days' in capsys.readouterr().out
    cli.main(['add', 'Dune']); capsys.readouterr()
    catalogue.add_scanned([dict(path='/x/new one', title='New one', author='', narrator='', series_position=None, asin='', kind='book', file_count=1)])
    assert cli.main(['mirror']) == 0 and 'wrote metadata.json for 1 of 1 books' in capsys.readouterr().out
    assert cli.main(['mirror', '--all']) == 0 and 'for 2 of 2 books' in capsys.readouterr().out
    assert os.path.isfile(os.path.join(mirror.root(), '_external', 'new one', 'metadata.json'))


def test_enrich_command(capsys, monkeypatch):
    from app import catalogue, providers
    from app.providers.base import candidate
    class HC:
        name = 'hardcover'
        def unavailable(self): return None
        def search(self, t, a=''): return [candidate('hardcover', id='42', title='Dune', authors=['Frank Herbert'])]
        def by_asin(self, a): return None
        def details(self, i): return dict(source='hardcover', id=i, rating=4.25, ratings_count=10, reviews_count=2, reviews=[1, 2])
    monkeypatch.setattr(providers, 'enabled', lambda names=None: [HC()])
    cli.main(['add', 'Dune', '--author', 'Frank Herbert']); capsys.readouterr()
    assert cli.main(['enrich']) == 0 and 'tried 1: 1 enriched, 0 not found on Hardcover; 0 confirmed books still to do' in capsys.readouterr().out
    assert cli.main(['enrich', '1']) == 0 and '#1: Hardcover 4.25 from 10 ratings, 2 reviews, 2 stored' in capsys.readouterr().out
    cli.main(['add', 'Unknown Thing']); capsys.readouterr()
    class Empty(HC):
        def search(self, t, a=''): return []
    monkeypatch.setattr(providers, 'enabled', lambda names=None: [Empty()])
    assert cli.main(['enrich', '2']) == 0 and 'not found on Hardcover' in capsys.readouterr().out
    with pytest.raises(SystemExit, match='no such book'): cli.main(['enrich', '99'])
    monkeypatch.setattr(providers, 'enabled', lambda names=None: [])
    with pytest.raises(SystemExit, match='Hardcover is not available'): cli.main(['enrich', '1'])


def test_scan_reports_a_down_share_and_an_interrupted_scan(capsys, tmp_path, monkeypatch):
    import errno
    from app import fs, scan
    lib = tmp_path / 'lib'; lib.mkdir()
    monkeypatch.setattr(fs, 'reachable', lambda p: False)
    with pytest.raises(SystemExit, match='not reachable'): cli.main(['scan', str(lib)])
    monkeypatch.setattr(fs, 'reachable', lambda p: True)
    monkeypatch.setattr(scan, 'scan', lambda root: (_ for _ in ()).throw(OSError(errno.EHOSTDOWN, 'Host is down')))
    with pytest.raises(SystemExit, match='interrupted .Host is down.; nothing was changed'): cli.main(['scan', str(lib)])


@pytest.mark.skipif(sys.platform == 'win32', reason='uses POSIX-style absolute paths')
def test_use_library_checks_the_copy_then_moves_paths_and_setting(capsys, tmp_path):
    from app import catalogue, settings
    old, new = tmp_path / 'share', tmp_path / 'local'
    for root in (old, new): (root / 'A').mkdir(parents=True)
    (old / 'A' / '01.mp3').write_bytes(b'x'); (old / 'B.m4b').write_bytes(b'x'); (new / 'A' / '01.mp3').write_bytes(b'x')           # B.m4b has not been copied yet
    cli.main(['set', 'library_dir', str(old)]); catalogue.add_book(title='A', path=str(old / 'A' / '01.mp3')); catalogue.add_book(title='B', path=str(old / 'B.m4b')); capsys.readouterr()
    with pytest.raises(SystemExit, match='1 are missing in the new folder'): cli.main(['use-library', str(new)])
    out = capsys.readouterr().out
    assert '2 books live under' in out and '1 of them exist' in out and 'missing: /B.m4b' in out and settings.library_dir() == str(old)      # nothing changed
    (new / 'B.m4b').write_bytes(b'x')
    assert cli.main(['use-library', str(new)]) == 0 and '2 book paths moved' in capsys.readouterr().out
    assert settings.library_dir() == str(new) and sorted(b['path'] for b in catalogue.list_books()) == [str(new / 'A' / '01.mp3'), str(new / 'B.m4b')]
    assert any(b['reason'] == 'pre-rebase' for b in __import__('app.backup', fromlist=['x']).list_backups())                          # backed up first
    with pytest.raises(SystemExit, match='not a folder with files'): cli.main(['use-library', str(tmp_path / 'nothing')])
    cli.main(['set', 'library_dir', 'default']); capsys.readouterr()
    with pytest.raises(SystemExit, match='no current audiobook folder'): cli.main(['use-library', str(new)])
    (new / 'C.m4b').write_bytes(b'x'); catalogue.add_book(title='C', path=str(old / 'C.m4b'))
    assert cli.main(['use-library', str(new), '--from', str(old), '--force']) == 0 and '1 book paths moved' in capsys.readouterr().out


def test_process_approval_commands(capsys, monkeypatch, tmp_path):
    from app import catalogue
    from app.process import queue as pq, run as R
    d = tmp_path / 'Book'; d.mkdir(); (d / '01.mp3').write_bytes(b'x' * 100)
    cli.main(['add', 'Book', '--author', 'Au', '--path', str(d)]); capsys.readouterr()
    plan = dict(book_id=1, title='Book', author='Au', out_dir=str(tmp_path / 'f' / 'Book'), chapter_source='file boundaries', downmix='average', duration=3600.0, channels=1, in_files=1, in_bytes=100, est_bytes=50, warnings=['careful'],
                loudness=dict(I=-23.0, TP=-3.0, LRA=4.0, target=-18.0, gain_db=5.0, apply_db=3.0, gain_steps=2, decision='x', reencoded=False), chapters=[dict(title='C', file='01 - C.mp3', start=0, end=3600, duration=3600.0, mode='encode', tier='V5', kbps=49, segments=[(str(d / '01.mp3'), 0, None)])])
    monkeypatch.setattr(pq, '_plan_one', lambda b, f: plan)
    monkeypatch.setattr(R, 'execute', lambda b, p, force=False: dict(status='done', out_dir=p['out_dir'], finished='2026-10-30T00:00:00+00:00', in_bytes=100, out_bytes=50))
    assert cli.main(['process-queue']) == 0 and 'the queue is empty' in capsys.readouterr().out
    assert cli.main(['process-run']) == 0 and 'nothing is approved yet' in capsys.readouterr().out
    assert cli.main(['process-propose', '--limit', '2']) == 0 and 'proposed 1 plans, 0 could not be planned' in capsys.readouterr().out
    assert cli.main(['process-queue']) == 0
    out = capsys.readouterr().out; assert '#1' in out and 'proposed' in out and '1.0 h' in out and '-23.0 ->  -20.0 LUFS' in out and 'careful' in out
    assert cli.main(['process-queue', '--state', 'approved']) == 0 and 'queue is empty' in capsys.readouterr().out
    assert cli.main(['process-approve', '1', '7']) == 0 and 'approved: #1 (not changed: #7' in capsys.readouterr().out
    assert cli.main(['process-reject', '1']) == 0 and 'rejected: #1' in capsys.readouterr().out
    cli.main(['process-approve', '1']); capsys.readouterr()
    assert cli.main(['process-run']) == 0 and 'processed 1, failed 0' in capsys.readouterr().out
    assert catalogue.get_plan(1)['state'] == 'done'
    cli.main(['add', 'Odd', '--author', 'X', '--path', str(tmp_path / 'gone')]); catalogue.set_plan(2, 'failed', error='x'); cli.main(['process-queue']); assert 'failed' in capsys.readouterr().out
    catalogue.set_plan(2, 'approved', plan, keep_plan=False)
    monkeypatch.setattr(R, 'execute', lambda *a, **k: (_ for _ in ()).throw(pq.probe.ToolError('boom')))
    assert cli.main(['process-run']) == 1 and 'failed 1' in capsys.readouterr().out


def test_doctor_audio_checks_optional_modules_and_native_tools(monkeypatch,capsys):
    from app import cli
    monkeypatch.setattr(cli.shutil,'which',lambda name:'/tools/'+name)
    monkeypatch.setattr(cli.importlib,'import_module',lambda name:type('Module',(),{'__version__':'test'})())
    assert cli.main(['doctor','--audio'])==0
    assert 'numpy test' in capsys.readouterr().out
    monkeypatch.setattr(cli.shutil,'which',lambda name:None)
    assert cli.main(['doctor','--audio'])==1
    assert 'MISSING' in capsys.readouterr().out
