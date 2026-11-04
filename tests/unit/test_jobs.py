"""Shared durable queue: bounded claims, replay, deduplication and visible outcomes."""
from concurrent.futures import ThreadPoolExecutor
import threading
from app import db
from app.jobs import JobQueue


def test_jobs_persist_deduplicate_and_record_success_or_failure():
    calls=[]
    def handle(domain,kind,options):
        calls.append((domain,kind,options))
        if kind=='bad':raise ValueError('source changed')
        return dict(done=True)
    q=JobQueue(handle)
    ident=q.submit('system','scan',folder='/source')
    assert q.submit('system','scan',folder='/source')==ident
    assert q.get(ident)['state']=='queued' and calls==[]
    assert q.tasks()[0]['options']==dict(folder='/source')
    assert q.run_one() and q.get(ident)['result']==dict(done=True)
    assert q.status('system')['last']=={'scan':dict(done=True)}
    bad=q.submit('system','bad');assert q.run_one()
    assert q.get(bad)['state']=='failed' and q.get(bad)['error']=='source changed'
    assert not q.run_one() and not q.tasks()
    assert JobQueue(handle).get(ident)['state']=='done'
    assert q.submit('system','scan',folder='/source')!=ident


def test_shared_cap_bounds_different_kinds_even_with_concurrent_claimers():
    entered=[threading.Event(),threading.Event()];release=threading.Event();lock=threading.Lock();active=[]
    def handle(domain,kind,options):
        with lock:
            active.append(kind)
            if len(active)<=2:entered[len(active)-1].set()
        assert release.wait(5)
        return kind
    q=JobQueue(handle,workers=2)
    for kind in ('scan','quality','samples'):q.submit(kind,kind)
    with ThreadPoolExecutor(max_workers=3) as pool:
        futures=[pool.submit(q.run_one) for _ in range(2)]
        try:
            assert all(event.wait(5) for event in entered)
            assert pool.submit(q.run_one).result(timeout=5) is False
            assert len([t for t in q.tasks() if t['state']=='running'])==2
            assert len([t for t in q.tasks() if t['state']=='queued'])==1
        finally:release.set()
        assert all(f.result(timeout=5) for f in futures)
    assert q.run_one() and not q.tasks()


def test_dead_owner_is_requeued_but_live_owner_is_preserved(monkeypatch):
    q=JobQueue(lambda *a:None,workers=1)
    dead=q.submit('ingest','run',ids=[1]);live=q.submit('process','run')
    c=db.connect()
    with c:
        c.execute("UPDATE jobs SET state='running',owner_pid=11 WHERE id=?",(dead,))
        c.execute("UPDATE jobs SET state='running',owner_pid=12 WHERE id=?",(live,))
    c.close()
    monkeypatch.setattr('app.jobs.oslib.pid_alive',lambda pid:pid==12)
    class Thread:
        def __init__(self,**kw):pass
        def start(self):pass
        def join(self,**kw):pass
    monkeypatch.setattr('app.jobs.threading.Thread',Thread)
    q.start();q.start()
    assert len(q.threads)==1
    assert q.get(dead)['state']=='queued' and q.get(live)['state']=='running'
    q.stop()


def test_jobs_for_the_same_book_do_not_overlap_across_domains():
    q=JobQueue(lambda *a:None,workers=2)
    first=q.submit('ingest','run',ids=[15]);second=q.submit('system','inspect',book_id=15)
    third=q.submit('system','inspect',book_id=16)
    c=db.connect()
    with c:c.execute("UPDATE jobs SET state='running' WHERE id=?",(first,))
    c.close()
    assert q.run_one() and q.get(third)['state']=='done'
    assert not q.run_one() and q.get(second)['state']=='queued'


def test_worker_loop_exits_after_finishing_its_job_and_serialization_errors_are_visible():
    from app.jobs import JobQueue
    q=JobQueue(lambda *a:q.stopping.set() or {'invalid':float('nan')})
    ident=q.submit('system','invalid');q._loop()
    assert q.get(ident)['state']=='failed' and 'JSON' in q.get(ident)['error']


def test_recovery_resumes_an_interrupted_batch_from_its_saved_run(monkeypatch):
    from app import catalogue
    from app.ingest import store
    q=JobQueue(lambda *a:None)
    book=catalogue.add_book(title='First');rid=store.create(book['id'],{})
    ident=q.submit('ingest','run',ids=[book['id'],99],limit=2)
    c=db.connect()
    with c:c.execute("UPDATE jobs SET state='running',owner_pid=123,started='1900' WHERE id=?",(ident,))
    c.close()
    monkeypatch.setattr('app.jobs.oslib.pid_alive',lambda pid:False)
    class Thread:
        def __init__(self,**kw):pass
        def start(self):pass
        def join(self,**kw):pass
    monkeypatch.setattr('app.jobs.threading.Thread',Thread)
    q.start()
    options=q.tasks('ingest')[0]['options']
    assert options['resume']==rid and options['ids']==[book['id'],99] and options['_recovered']
    q.stop()
