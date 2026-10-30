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
    assert [p['name'] for p in client.get('/api/providers').json()] == ['audnexus', 'audible', 'hardcover', 'audiobookdb']


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
    monkeypatch.setattr(providers, 'enabled', lambda names=None: [HC()])
    b = client.post('/api/books', json=dict(title='Dune', author='Frank Herbert')).json()
    assert client.get(f"/api/books/{b['id']}/details").json()['extras'] == {}
    r = client.post(f"/api/books/{b['id']}/enrich").json()
    assert r['found'] is True and r['extras']['hardcover']['data']['rating'] == 4.0
    assert client.get(f"/api/books/{b['id']}/details").json()['extras']['hardcover']['data']['id'] == '42'
    assert client.post('/api/books/99/enrich').status_code == 404
    other = client.post('/api/books', json=dict(title='Other')).json()
    assert client.post('/api/enrich', params=dict(limit=5)).json() == dict(tried=1, enriched=0, not_found=1, remaining=0)      # 'Other' is not on Hardcover
    monkeypatch.setattr(providers, 'enabled', lambda names=None: [])
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
