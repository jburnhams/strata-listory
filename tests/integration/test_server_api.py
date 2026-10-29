"""The HTTP API over real on-disk files: health check, catalogue, and settings."""


def test_health_endpoint(client):
    assert client.get('/api/health').json() == dict(ok=True, version='0.1.0')


def test_books_created_via_http_are_shared_with_the_cli(client, cli):
    assert client.get('/api/books').json() == []
    client.post('/api/books', json=dict(title='Dune', author='Frank Herbert'))
    cli('add', 'Emma', '--author', 'Jane Austen')
    assert [b['title'] for b in client.get('/api/books').json()] == ['Emma', 'Dune'] or [b['title'] for b in client.get('/api/books').json()] == ['Dune', 'Emma']
    assert 'Dune' in cli('list').stdout
    assert client.delete('/api/books/1').status_code == 200 and len(client.get('/api/books').json()) == 1


def test_settings_changes_persist(client, tmp_path):
    assert client.put('/api/settings', json=dict(library_dir=str(tmp_path / 'books'))).json()['library_dir'].endswith('books')      # absolute on every OS (a bare '/srv' is not on Windows)
    assert client.put('/api/settings', json=dict(library_dir='relative')).status_code == 400
