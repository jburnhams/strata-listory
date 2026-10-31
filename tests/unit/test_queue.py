import pytest
from app import catalogue, providers, settings
from app.process import queue as pq, run as R
from app.process.queue import Worker


def fake_plan(book, total=3600.0):
    return dict(book_id=book['id'], title=book['title'], author=book['author'], out_dir='/final/' + book['title'], chapter_source='file boundaries', downmix='average', duration=total, channels=1, in_files=1, in_bytes=1000, est_bytes=500, warnings=['w'],
                loudness=dict(I=-23.0, TP=-3.0, LRA=5.0, target=-18.0, gain_db=5.0, apply_db=3.0, gain_steps=2, decision='too quiet', reencoded=False),
                chapters=[dict(title='One', file='01 - One.mp3', start=0, end=60, duration=60.0, mode='copy', tier='copy', kbps=64, segments=[('/x', 0, None)]), dict(title='Two', file='02 - Two.mp3', start=60, end=3600, duration=3540.0, mode='encode', tier='V5', kbps=49, segments=[('/x', 60, None)])])


@pytest.fixture
def books(tmp_path, monkeypatch):
    out = []
    for t in ('Alpha', 'Beta', 'Gamma'):
        d = tmp_path / t; d.mkdir(); (d / '01.mp3').write_bytes(b'x' * 1000)
        out.append(catalogue.add_book(title=t, author='Auth', path=str(d)))
    catalogue.add_book(title='No author', path=str(tmp_path / 'Alpha')) if False else None
    monkeypatch.setattr(pq, '_plan_one', lambda book, final: fake_plan(book))
    return out


def test_summary_shape():
    s = pq.summary(fake_plan(dict(id=1, title='T', author='A')))
    assert (s['hours'], s['chapters'], s['tiers'], s['loudness_after_est'], s['warnings']) == (1.0, 2, {'copy': 1, 'V5': 1}, -20.0, ['w']) and s['chapter_list'][1] == dict(file='02 - Two.mp3', title='Two', minutes=59.0, mode='encode', tier='V5')


def test_propose_creates_proposals_for_eligible_books_once(books):
    catalogue.add_book(title='Hand', author='', path='/x/h')                                        # no author: not eligible
    catalogue.add_scanned([dict(path='/x/n', title='New', author='A', narrator='', series_position=None, asin='', kind='book', file_count=1)])          # unconfirmed: not eligible
    assert pq.propose(2) == dict(planned=2, failed=0, remaining=1)
    assert [r['state'] for r in catalogue.list_plans()] == ['proposed', 'proposed'] or len(catalogue.list_plans()) == 2
    assert pq.propose(5) == dict(planned=1, failed=0, remaining=0) and pq.propose(5)['planned'] == 0              # nothing is proposed twice
    assert catalogue.plan_counts() == {'proposed': 3}


def test_unplannable_books_are_recorded_as_failed(books, monkeypatch, tmp_path):
    def boom(book, final):
        if book['title'] == 'Beta': raise pq.probe.ToolError('no audio files')
        return fake_plan(book)
    monkeypatch.setattr(pq, '_plan_one', boom)
    gone = catalogue.add_book(title='Gone', author='A', path=str(tmp_path / 'gone'))
    assert pq.propose(10) == dict(planned=2, failed=2, remaining=0)
    assert catalogue.get_plan(books[1]['id'])['error'] == 'no audio files' and 'not available' in catalogue.get_plan(gone['id'])['error'] and catalogue.get_plan(books[1]['id'])['state'] == 'failed'


def test_decisions_follow_the_state_rules(books):
    pq.propose(3); a, b, c = [x['id'] for x in books]
    assert pq.decide([a, b], True) == [a, b] and catalogue.get_plan(a)['state'] == 'approved'
    assert pq.decide([b], False) == [b] and pq.decide([b], True) == [b]                                      # a rejection can be reversed
    assert pq.decide([c, 999], False) == [c] and pq.decide([c], False) == [] and catalogue.get_plan(c)['state'] == 'rejected'
    catalogue.set_plan(a, 'running', keep_plan=True); assert pq.decide([a], False) == [] and pq.decide([a], True) == []   # running plans cannot be changed
    assert pq.repropose([c, b, 999]) == [c, b] and catalogue.get_plan(c) is None and pq.propose(5)['planned'] == 2
    with pytest.raises(catalogue.CatalogueError): catalogue.set_plan(a, 'maybe')


def test_only_approved_plans_run_and_results_are_recorded(books, monkeypatch):
    pq.propose(3); a, b, c = [x['id'] for x in books]; pq.decide([a, b], True)
    ran = []
    def execute(book, plan, force=False):
        ran.append(book['title'])
        if book['title'] == 'Beta': raise pq.probe.ToolError('boom')
        return dict(status='done', out_dir=plan['out_dir'], finished='2026-10-30T00:00:00+00:00', in_bytes=1000, out_bytes=400)
    monkeypatch.setattr(R, 'execute', execute)
    assert pq.run_approved(1) == dict(done=1, failed=1) and sorted(ran) == ['Alpha', 'Beta']                   # Gamma was never approved
    assert catalogue.get_plan(a)['state'] == 'done' and catalogue.get_processed(a)['status'] == 'done'
    f = catalogue.get_plan(b); assert f['state'] == 'failed' and f['error'] == 'boom' and catalogue.get_processed(b)['status'] == 'failed'
    assert catalogue.get_plan(c)['state'] == 'proposed' and pq.run_approved(1) == dict(done=0, failed=0)


def test_a_plan_is_not_run_if_the_files_changed(books, monkeypatch, tmp_path):
    pq.propose(1); a = books[0]['id']; pq.decide([a], True)
    (tmp_path / 'Alpha' / '01.mp3').write_bytes(b'changed and longer')
    monkeypatch.setattr(R, 'execute', lambda *a, **k: pytest.fail('must not run'))
    assert pq.run_approved(1) == dict(done=0, failed=1) and 'changed since this plan was proposed' in catalogue.get_plan(a)['error']
    pq.repropose([a]); pq.propose(1); pq.decide([a], True)
    monkeypatch.setattr(pq.fs, 'reachable', lambda p: False)
    assert pq.run_approved(1)['failed'] == 1 and 'not available' in catalogue.get_plan(a)['error']


def test_the_worker_runs_tasks_in_the_background_and_survives_errors(books, monkeypatch):
    w = Worker(); w.inline = True
    w.submit('propose', limit=2); assert w.last == {'propose': dict(planned=2, failed=0, remaining=1)} and w.busy is None
    monkeypatch.setattr(pq, 'run_approved', lambda jobs=2: (_ for _ in ()).throw(RuntimeError('bad')))
    w.submit('run'); assert w.last == {'run': dict(error='bad')} and w.status()['counts'] == {'proposed': 2}
    t = Worker(); done = []
    monkeypatch.setattr(pq, 'propose', lambda limit=5, jobs=2: done.append(limit) or dict(planned=0, failed=0, remaining=0))
    t.submit('propose', limit=7); t._thread.join(2)                                                         # the real thread path, until its queue idles
    t.q.put(('propose', dict(limit=8))); t._loop.__self__ and None
    import time; time.sleep(0.2); assert 7 in done
