import json
import sqlite3
from job_client import JobClient as TestClient
from app.identify import context, queue, service
from app.server.app import create_app


def write_cache(items):
    service.folder().joinpath('queue.json').write_text(json.dumps(dict(summary=dict(queue=len(items), accepted=0), queue=items)))


def entry(n, impact):
    item = dict(title=f'Book {n}', author='A B', path=f'/x/{n}.m4b')
    return dict(group=queue.group_key(item), item=item, files=n, work='/works/OL1W', title=f'Book {n}', authors=['A B'], impact=impact, score=.5, decision='review')


def test_page_orders_skips_decided_and_handles_missing_cache():
    assert service.page() == dict(summary=None, items=[])
    first, second = entry(1, 9), entry(2, 5)
    write_cache([first, second])
    assert [i['title'] for i in service.page(5)['items']] == ['Book 1', 'Book 2']
    queue.decide(first['item'], 'approve', first['work'], 1, 'Book 1', 'A B')
    got = service.page(5)
    assert [i['title'] for i in got['items']] == ['Book 2'] and got['summary']['decided'] == 1 and got['summary']['remaining'] == 1


def test_page_after_and_find_reach_beyond_the_first_page():
    entries = [entry(n, 100 - n) for n in range(1, 61)]; write_cache(entries)
    assert [i['title'] for i in service.page(3, entries[9]['group'])['items']] == ['Book 11', 'Book 12', 'Book 13']
    queue.decide(entries[10]['item'], 'approve', entries[10]['work'], 1, 'Book 11', 'A B')
    assert [i['title'] for i in service.page(2, entries[9]['group'])['items']] == ['Book 12', 'Book 13'] and service.page(2, 'unknown')['items'][0]['title'] == 'Book 1'
    assert service.find(entries[59]['group'])['title'] == 'Book 60' and service.find('nope') is None
    queue.decide(entries[59]['item'], 'reject', entries[59]['work'], 1, 'Book 60', 'A B')
    assert service.find(entries[59]['group']) is None


def test_api_queue_and_decision_roundtrip():
    first, second = entry(1, 9), entry(2, 5)
    write_cache([first, second]); client = TestClient(create_app())
    assert len(client.get('/api/identify/queue?limit=1').json()['items']) == 1
    out = client.post('/api/identify/decisions', json=dict(group=first['group'], decision='approve')).json()
    assert out['recorded'] == 'approve' and [i['title'] for i in out['next']['items']] == ['Book 2']
    assert client.post('/api/identify/decisions', json=dict(group='nope', decision='reject')).status_code == 404
    assert client.post('/api/identify/decisions', json=dict(group=second['group'], decision='maybe')).status_code == 422
    with sqlite3.connect(queue.labels_path()) as c: assert c.execute('SELECT decision, work FROM labels').fetchall() == [('approve', '/works/OL1W')]


def test_library_items_reads_latest_book_runs(tmp_path):
    db = tmp_path / 'catalogue.db'; c = sqlite3.connect(db)
    c.executescript('CREATE TABLE ingest_runs(id TEXT, book_id INTEGER, created TEXT); CREATE TABLE ingest_events(id INTEGER PRIMARY KEY, run_id TEXT, stage TEXT, status TEXT, data TEXT); CREATE TABLE ingest_members(run_id TEXT, book_id INTEGER);')
    for run, book, created, kind, title in (('r1', 1, '1', 'book', 'Old'), ('r2', 1, '2', 'book', 'The Hobbit CD01 01'), ('r3', 2, '3', 'radio', 'Show')):
        c.execute('INSERT INTO ingest_runs VALUES(?,?,?)', (run, book, created)); c.execute('INSERT INTO ingest_members VALUES(?,?)', (run, book))
        c.execute('INSERT INTO ingest_events(run_id,stage,status,data) VALUES(?,?,?,?)', (run, 'identity', 'done', json.dumps(dict(kind=kind, book=dict(title=title, author='J. Tolkien', path=f'/a/{title}.mp3')))))
    c.commit(); c.close()
    items = service.library_items(db)
    assert [i['title'] for i in items] == ['The Hobbit CD01 01'] and items[0]['numbered'] == 1 and items[0]['members'] == 1 and items[0]['run_id'] == 'r2'


def test_current_runs_follow_membership_when_a_group_is_reingested(tmp_path):
    c = sqlite3.connect(':memory:')
    c.executescript('CREATE TABLE ingest_runs(id TEXT, book_id INTEGER, created TEXT); CREATE TABLE ingest_members(run_id TEXT, book_id INTEGER);')
    for run, book, created, members in (('old', 1, '1', (1, 2)), ('new', 2, '2', (1, 2)), ('solo', 3, '3', ())):
        c.execute('INSERT INTO ingest_runs VALUES(?,?,?)', (run, book, created)); c.executemany('INSERT INTO ingest_members VALUES(?,?)', [(run, m) for m in members])
    assert context.current_runs(c) == ['new', 'solo']
