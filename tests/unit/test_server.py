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
    new_dir, lib = str(tmp_path / 'custom_data'), str(tmp_path / 'books')
    monkeypatch.delenv('APP_DATA', raising=False)
    res = client.put('/api/settings', json=dict(data_dir=new_dir, library_dir=lib)).json()
    assert res == dict(data_dir=new_dir, library_dir=lib)
    assert client.get('/api/settings').json() == res
    assert client.put('/api/settings', json=dict(library_dir='')).json()['library_dir'] == ''      # empty resets

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
    from app import matcher
    assert client.post('/api/scan', json={}).status_code == 400
    assert client.post('/api/scan', json=dict(folder=str(tmp_path / 'nope'))).status_code == 400
    lib = tmp_path / 'lib'; lib.mkdir(); (lib / 'Dune by Frank Herbert.m4b').write_text('')
    assert client.post('/api/scan', json=dict(folder=str(lib))).json() == dict(found=1, added=1, existing=0)
    client.put('/api/settings', json=dict(library_dir=str(lib)))
    assert client.post('/api/scan', json={}).json()['existing'] == 1

    class Source:
        def search(self, t): return [matcher.candidate(dict(id='1', title='Dune', people=[dict(person=dict(name='Frank Herbert'), role=dict(name='Author'))]))]
        def by_asin(self, a): return None
    monkeypatch.setattr(matcher, 'AudiobookDB', Source)
    assert client.post('/api/match', params=dict(limit=5)).json() == dict(tried=1, suggested=1, none=0, remaining=0)
    [b] = client.get('/api/books', params=dict(review='suggested', kind='book')).json()
    assert b['suggestion']['author'] == 'Frank Herbert' and b['match_score'] == 1.0
    assert client.post(f"/api/books/{b['id']}/confirm").json()['review'] == 'confirmed'
    assert client.post(f"/api/books/{b['id']}/ignore").json()['review'] == 'ignored'
    assert client.post('/api/books/99/confirm').status_code == 404

    def broken(limit): raise matcher.MatchError('no AudiobookDB API key')
    monkeypatch.setattr(matcher, 'match_pending', broken)
    r = client.post('/api/match'); assert r.status_code == 502 and 'API key' in r.json()['detail']
