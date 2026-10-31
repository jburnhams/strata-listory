import os
import pytest
import uvicorn
from fastapi.testclient import TestClient
from app.server import app as server_app
from app.server.app import create_app


@pytest.fixture
def client():
    return TestClient(create_app())


def test_landing_page_and_built_static_index(tmp_path, monkeypatch):
    monkeypatch.setattr(server_app, 'STATIC', str(tmp_path / 'no_static'))
    c = TestClient(create_app())
    assert 'strata-listory' in c.get('/').text and 'Add book' in c.get('/').text
    assert c.get('/api/health').json()['ok'] is True

    static = tmp_path / 'static'; (static / 'assets').mkdir(parents=True)
    (static / 'index.html').write_text('<h1>built react app</h1>', encoding='utf8')
    (static / 'assets' / 'main.js').write_text('console.log(1)', encoding='utf8')
    monkeypatch.setattr(server_app, 'STATIC', str(static))
    c2 = TestClient(create_app())
    assert c2.get('/').text == '<h1>built react app</h1>'
    assert 'console.log(1)' in c2.get('/assets/main.js').text


def test_book_lifecycle(client):
    assert client.get('/api/books').json() == []
    b = client.post('/api/books', json=dict(title='Dune', author='Frank Herbert')).json()
    assert b['id'] == 1 and b['status'] == 'unlistened'
    assert client.post('/api/books', json=dict(title='')).status_code == 400
    assert client.get('/api/books/1').json()['title'] == 'Dune'
    assert client.patch('/api/books/1', json=dict(status='finished')).json()['status'] == 'finished'
    assert client.patch('/api/books/1', json=dict(status='lost')).status_code == 400
    assert [x['title'] for x in client.get('/api/books', params=dict(q='herbert', status='finished')).json()] == ['Dune']
    assert client.get('/api/books', params=dict(status='unlistened')).json() == []
    assert client.delete('/api/books/1').json()['title'] == 'Dune'
    for call in (client.get, client.delete): assert call('/api/books/1').status_code == 404
    assert client.patch('/api/books/1', json=dict(title='x')).status_code == 404


def test_settings_roundtrip_and_server_main(client, tmp_path, monkeypatch):
    assert client.put('/api/settings', json=dict(data_dir='relative/path')).status_code == 400
    assert client.put('/api/settings', json=dict(library_dir='relative/path')).status_code == 400
    assert client.put('/api/settings', json=dict(backup_dir='relative/path')).status_code == 400
    new_dir, lib = str(tmp_path / 'custom_data'), str(tmp_path / 'books')
    monkeypatch.delenv('APP_DATA', raising=False)
    res = client.put('/api/settings', json=dict(data_dir=new_dir, library_dir=lib)).json()
    assert res == dict(data_dir=new_dir, library_dir=lib, backup_dir=os.path.join(new_dir, 'backups'))
    assert client.get('/api/settings').json() == res
    assert client.put('/api/settings', json=dict(library_dir='')).json()['library_dir'] == ''      # empty resets
    elsewhere = str(tmp_path / 'share' / 'backups')
    assert client.put('/api/settings', json=dict(backup_dir=elsewhere)).json()['backup_dir'] == elsewhere

    runs = []
    monkeypatch.setattr(uvicorn, 'run', lambda app, host, port, log_level: runs.append((host, port, log_level)))
    server_app.main(['--host', '127.0.0.1', '--port', '8360'])
    assert runs == [('127.0.0.1', 8360, 'warning')]


def test_corrupt_database_gives_503(client, monkeypatch):
    from app import db
    client.post('/api/books', json=dict(title='a'))
    monkeypatch.setattr(db, '_checked', set())
    with open(db.path(), 'wb') as f: f.write(b'junk' * 200)
    r = client.get('/api/books')
    assert r.status_code == 503 and 'restore' in r.json()['detail']


def test_scan_match_confirm_endpoints(client, tmp_path, monkeypatch):
    from app import matcher, providers
    from app.providers.base import candidate
    assert client.post('/api/scan', json={}).status_code == 400
    assert client.post('/api/scan', json=dict(folder=str(tmp_path / 'nope'))).status_code == 400
    lib = tmp_path / 'lib'; lib.mkdir(); (lib / 'Dune by Frank Herbert.m4b').write_text('')
    assert client.post('/api/scan', json=dict(folder=str(lib))).json() == dict(found=1, added=1, existing=0, replaced=0)
    client.put('/api/settings', json=dict(library_dir=str(lib)))
    assert client.post('/api/scan', json={}).json()['existing'] == 1

    class Source:
        name = 'fake'
        def search(self, t, a=''): return [candidate('fake', id='1', title='Dune', authors=['Frank Herbert'])]
        def by_asin(self, a): return None
    monkeypatch.setattr(providers, 'enabled', lambda names=None: [Source()])
    assert client.post('/api/match', params=dict(limit=5)).json() == dict(tried=1, suggested=1, none=0, remaining=0, providers=['fake'])
    [b] = client.get('/api/books', params=dict(review='suggested', kind='book')).json()
    assert b['suggestion']['author'] == 'Frank Herbert' and b['match_score'] == 1.0
    assert client.post(f"/api/books/{b['id']}/confirm").json()['review'] == 'confirmed'
    assert client.post(f"/api/books/{b['id']}/ignore").json()['review'] == 'ignored'
    assert client.post('/api/books/99/confirm').status_code == 404

    def broken(limit, plist): raise matcher.MatchError('no AudiobookDB API key')
    monkeypatch.setattr(matcher, 'match_pending', broken)
    r = client.post('/api/match'); assert r.status_code == 502 and 'API key' in r.json()['detail']
    monkeypatch.undo()
    assert client.post('/api/match', params=dict(providers='nope')).status_code == 400
    assert [p['name'] for p in client.get('/api/providers').json()] == ['audnexus', 'audible', 'hardcover', 'audiobookdb', 'openlibrary', 'applebooks', 'googlebooks']


def test_book_details_and_cover_endpoints(client, tmp_path, monkeypatch):
    from app import details
    d = tmp_path / 'Book'; d.mkdir(); (d / '01.mp3').write_bytes(b'x'); (d / 'cover.jpg').write_bytes(b'\xff\xd8\xff\xe0' + b'0' * 10)
    monkeypatch.setattr(details, 'probe', lambda files: dict(available=True, files_probed=1, complete=True, duration_seconds=3700))
    b = client.post('/api/books', json=dict(title='Book', path=str(d))).json()
    r = client.get(f"/api/books/{b['id']}/details").json()
    assert r['book']['duration_seconds'] == 3700 and r['files']['count'] == 1 and r['path_exists'] is True       # the duration is filled in from the probe
    img = client.get(f"/api/books/{b['id']}/cover")
    assert img.status_code == 200 and img.content.startswith(b'\xff\xd8\xff') and 'max-age' in img.headers['cache-control']
    manual = client.post('/api/books', json=dict(title='By hand')).json()
    assert client.get(f"/api/books/{manual['id']}/details").json()['path_exists'] is False
    assert client.get(f"/api/books/{manual['id']}/cover").status_code == 404
    monkeypatch.setattr(details, 'probe', lambda files: dict(available=True, files_probed=0))
    d2 = tmp_path / 'Book2'; d2.mkdir(); (d2 / '01.mp3').write_bytes(b'x'); (d2 / 'x.metadata.json').write_text('{"runtime_length_min": 2}')
    b2 = client.post('/api/books', json=dict(title='Book2', path=str(d2))).json()
    assert client.get(f"/api/books/{b2['id']}/details").json()['book']['duration_seconds'] == 120               # no usable probe: Audible's runtime
    assert client.get('/api/books/999/details').status_code == 404 and client.get('/api/books/999/cover').status_code == 404


def test_enrich_endpoints_and_extras_in_details(client, monkeypatch):
    from app import providers
    from app.providers.base import candidate
    class HC:
        name = 'hardcover'
        def unavailable(self): return None
        def search(self, t, a=''): return [candidate('hardcover', id='42', title='Dune', authors=['Frank Herbert'])]
        def by_asin(self, a): return None
        def details(self, i): return dict(source='hardcover', id=i, rating=4.0, ratings_count=3, reviews_count=1, reviews=[])
    from app import catalogue, matcher, enrich as enrich_mod
    monkeypatch.setattr(providers, 'enabled', lambda names=None: [HC()])
    monkeypatch.setattr(enrich_mod, 'enrich_openlibrary', lambda book, ol=None: catalogue.set_extra(book['id'], 'openlibrary', dict(first_publish_year=1965)) or dict(first_publish_year=1965))
    b = client.post('/api/books', json=dict(title='Dune', author='Frank Herbert')).json()
    assert client.get(f"/api/books/{b['id']}/details").json()['extras'] == {}
    r = client.post(f"/api/books/{b['id']}/enrich").json()
    assert r['found'] is True and r['extras']['hardcover']['data']['rating'] == 4.0 and r['extras']['openlibrary']['data']['first_publish_year'] == 1965      # both sources
    assert client.get(f"/api/books/{b['id']}/details").json()['extras']['hardcover']['data']['id'] == '42'
    assert client.post('/api/books/99/enrich').status_code == 404
    other = client.post('/api/books', json=dict(title='Other')).json()
    assert client.post('/api/enrich', params=dict(limit=5)).json() == dict(tried=1, enriched=0, not_found=1, remaining=0)      # 'Other' is not on Hardcover
    monkeypatch.setattr(providers, 'enabled', lambda names=None: [])
    monkeypatch.setattr(enrich_mod, 'enrich_openlibrary', lambda book, ol=None: (_ for _ in ()).throw(matcher.MatchError('Open Library down')))
    assert client.post(f"/api/books/{other['id']}/enrich").status_code == 502


def test_scan_reports_a_down_share_with_503(client, tmp_path, monkeypatch):
    import errno
    from app import fs, scan
    lib = tmp_path / 'lib'; lib.mkdir()
    monkeypatch.setattr(fs, 'reachable', lambda p: False)
    r = client.post('/api/scan', json=dict(folder=str(lib))); assert r.status_code == 503 and 'not reachable' in r.json()['detail']
    monkeypatch.setattr(fs, 'reachable', lambda p: True)
    monkeypatch.setattr(scan, 'scan', lambda root: (_ for _ in ()).throw(OSError(errno.EHOSTDOWN, 'Host is down')))
    r = client.post('/api/scan', json=dict(folder=str(lib))); assert r.status_code == 503 and 'interrupted' in r.json()['detail']


def test_processing_queue_endpoints(client, monkeypatch, tmp_path):
    from app.process import queue as pq, run as R
    monkeypatch.setattr(pq.Worker, 'inline', True)                                      # run tasks on the spot instead of in a thread
    d = tmp_path / 'Book'; d.mkdir(); (d / '01.mp3').write_bytes(b'x' * 100)
    b = client.post('/api/books', json=dict(title='Book', author='Au', path=str(d))).json()
    plan = dict(book_id=b['id'], title='Book', author='Au', out_dir=str(tmp_path / 'final' / 'Book'), chapter_source='x', downmix='average', duration=60.0, channels=1, in_files=1, in_bytes=100, est_bytes=50, warnings=[],
                loudness=dict(I=-20.0, TP=-3.0, LRA=4.0, target=-18.0, gain_db=2.0, apply_db=1.5, gain_steps=1, decision='x', reencoded=False), chapters=[dict(title='C', file='01 - C.mp3', start=0, end=60, duration=60.0, mode='copy', tier='copy', kbps=64, segments=[(str(d / '01.mp3'), 0, None)])])
    monkeypatch.setattr(pq, '_plan_one', lambda book, final: plan)
    monkeypatch.setattr(R, 'execute', lambda book, p, force=False: dict(status='done', out_dir=p['out_dir'], finished='2026-10-30T00:00:00+00:00', in_bytes=100, out_bytes=50, loudness_after=-18.2))
    assert client.get('/api/process').json()['rows'] == [] and client.post('/api/process/run').status_code == 400                    # nothing approved yet
    v = client.post('/api/process/propose', params=dict(limit=3)).json()
    assert [r['state'] for r in v['rows']] == ['proposed'] and v['rows'][0]['summary']['chapters'] == 1 and v['worker']['last']['propose']['planned'] == 1
    assert client.post('/api/process/reject', json=dict(ids=[b['id']])).json() == dict(changed=[b['id']])
    assert client.post('/api/process/approve', json=dict(ids=[b['id']])).json() == dict(changed=[b['id']]) and client.post('/api/process/approve', json=dict(ids=[999])).json() == dict(changed=[])
    v = client.post('/api/process/run').json()
    assert v['rows'][0]['state'] == 'done' and v['result'][str(b['id'])]['out_bytes'] == 50 and v['worker']['counts'] == {'done': 1}
    assert client.get(f"/api/books/{b['id']}/details").json()['processed']['status'] == 'done'
    assert client.post('/api/process/repropose', json=dict(ids=[b['id']])).json() == dict(changed=[]) and client.post('/api/process/propose').json()['rows'][0]['state'] == 'done'      # done work is kept and not proposed again
