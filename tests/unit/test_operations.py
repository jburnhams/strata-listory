"""Operations include unfinished batches and durable ingest outcomes across pages."""
from app import catalogue
from app.ingest import operations, pipeline as P, store
from app.process.queue import Worker


def saved(book,state='metadata_review'):
    rid=store.create(book['id'],{})
    store.event(rid,'inventory','done',data=dict(primary_id=book['id'],members=[book]))
    store.finish(rid,state)
    return rid


def test_operations_include_queued_batch_members_current_failures_and_completed_toggle():
    a=catalogue.add_book(title='Waiting',author='Author');b=catalogue.add_book(title='Queued',author='Author');c=catalogue.add_book(title='Broken',author='Author');d=catalogue.add_book(title='Finished',author='Author')
    old=saved(a);current=saved(a);broken=saved(c,'failed');finished=saved(d,'processed')
    store.event(broken,'providers','failed',error='provider unavailable')
    tasks=[dict(kind='run',options=dict(ids=[a['id'],b['id'],99999]),state='running',started='1900-01-01')]
    rows=operations.view(tasks,[])['rows']
    assert next(r for r in rows if r['book_id']==b['id'])['state']=='queued'
    assert next(r for r in rows if r['id']==current)['state']=='waiting'
    assert next(r for r in rows if r['id']==broken)['error']=='provider unavailable'
    assert old not in [r['id'] for r in rows] and finished not in [r['id'] for r in rows]
    assert any(r['id']==finished and r['state']=='completed' for r in operations.view([],[],True)['rows'])
    queued=operations.view([dict(kind='continue',options=dict(run_id=current),state='queued',started='')],[])['rows']
    assert next(r for r in queued if r['book_id']==a['id'])['state']=='queued'
    assert current not in [r['id'] for r in queued]


def test_operations_report_active_runs_and_worker_tasks_without_duplicate_processing_plans():
    a=catalogue.add_book(title='Analysing');rid=saved(a,'running')
    b=catalogue.add_book(title='Processing');processed=saved(b,'processed');catalogue.set_plan(b['id'],'done')
    tasks=[dict(kind='run',options=dict(ids=[a['id']]),state='running',started='1900-01-01'),dict(kind='auto_metadata',options={},state='queued',started=''),dict(kind='run',options={},state='queued',started='')]
    process=[dict(kind='propose',state='queued'),dict(kind='run',state='running')]
    value=operations.view(tasks,process,True);rows=value['rows']
    assert value['busy']
    assert next(r for r in rows if r['id']==rid)['state']=='running'
    assert processed not in [r['id'] for r in rows]
    assert {'Metadata approvals','Discover ingest candidates','Build processing proposals','Process approved books'}<={r['title'] for r in rows}
    assert [r['state'] for r in rows][:2]==['running','running']


def test_worker_exposes_active_and_pending_tasks(monkeypatch):
    w=Worker();w.inline=True;seen=[]
    monkeypatch.setattr('app.process.queue.propose',lambda **kw:seen.extend(w.tasks()) or {})
    w.submit('propose',limit=5)
    assert seen[0]['state']=='running' and seen[0]['options']==dict(limit=5)
    assert not w.tasks()
    w.q.put(('run',dict(ids=[1,2])))
    assert w.tasks()==[dict(kind='run',options=dict(ids=[1,2]),state='queued',started='')]


def test_review_pages_filter_before_pagination_and_hide_running_failed_and_superseded():
    a=catalogue.add_book(title='Metadata');old=saved(a);metadata=saved(a)
    b=catalogue.add_book(title='Quality');quality=saved(b,'quality_review')
    store.event(quality,'metadata_approval','done',data=dict(signature=P.review_signature(store.get(quality),'metadata'),automatic=False))
    saved(catalogue.add_book(title='Running'),'running');saved(catalogue.add_book(title='Failed'),'failed')
    assert [r['id'] for r in P.dashboard(phase='metadata')['runs']]==[metadata]
    assert P.dashboard(offset=1,limit=1,phase='metadata')['runs']==[]
    assert P.dashboard(phase='metadata')['total']==1
    assert [r['id'] for r in P.dashboard(phase='quality')['runs']]==[quality]
    assert old not in [r['id'] for r in P.dashboard(phase='metadata')['runs']]


def test_library_membership_covers_current_reviews_plans_finished_and_queued_books():
    books=[catalogue.add_book(title=str(i)) for i in range(8)]
    book_run=saved(books[0]);audio_run=saved(books[1],'audiobook_review');quality_run=saved(books[2],'quality_review')
    for rid in (audio_run,quality_run):
        store.event(rid,'metadata_approval','done',data=dict(scope='book',automatic=False,signature=P.review_signature(store.get(rid),'metadata')))
    store.event(quality_run,'audiobook_approval','done',data=dict(scope='audiobook',automatic=False,signature=P.review_signature(store.get(quality_run),'audiobook')))
    saved(books[3],'failed')
    catalogue.set_plan(books[4]['id'],'proposed')
    catalogue.set_processed(books[5]['id'],dict(status='done',finished='2026-11-01'))
    tasks=[dict(state='queued',started='',options=dict(ids=[books[6]['id']]))]
    result=operations.library(tasks,[])['books']
    assert result[str(books[0]['id'])]==['metadata']
    assert result[str(books[1]['id'])]==['audiobook']
    assert result[str(books[2]['id'])]==['quality']
    assert result[str(books[3]['id'])]==['processing']
    assert result[str(books[4]['id'])]==['proposed']
    assert result[str(books[5]['id'])]==['finished']
    assert result[str(books[6]['id'])]==['processing']
    assert str(books[7]['id']) not in result
    # A batch has already reached these reviews; they are not still queued.
    active=[dict(state='running',started='1900-01-01',options=dict(ids=[books[0]['id']]))]
    assert operations.library(active,[])['books'][str(books[0]['id'])]==['metadata']
    assert store.get(book_run)['state']=='metadata_review'


def test_library_membership_tracks_latest_runs_all_group_members_and_processing_plans():
    a=catalogue.add_book(title='A');b=catalogue.add_book(title='B');old=saved(a,'processed');store.link(old,[b['id']])
    assert operations.library([],[])['books'][str(b['id'])]==['finished']
    fresh=saved(b)
    stages=operations.library([],[])['books']
    assert stages[str(a['id'])]==['finished'] and stages[str(b['id'])]==['metadata']
    catalogue.set_plan(b['id'],'approved')
    assert 'processing' in operations.library([],[])['books'][str(b['id'])]
    queued=[dict(state='queued',options=dict(run_id=fresh),started='')]
    assert 'processing' in operations.library(queued,[])['books'][str(b['id'])]
    assert 'processing' in operations.library([],[dict(options=dict(ids=[a['id']]))])['books'][str(a['id'])]


def test_review_pagination_loads_full_evidence_only_for_selected_page(monkeypatch):
    ids=[saved(catalogue.add_book(title=f'Book {i}')) for i in range(9)]
    original=store.get;loaded=[]
    def get(rid):
        loaded.append(rid)
        return original(rid)
    monkeypatch.setattr(store,'get',get)
    page=P.dashboard(offset=3,limit=2,phase='metadata',sort='newest')
    assert page['total']==9 and [r['id'] for r in page['runs']]==list(reversed(ids))[3:5]
    assert loaded==list(reversed(ids))[3:5]


def test_book_identification_sorts_all_matches_before_loading_page_evidence(monkeypatch):
    def ranked(title,score,families,complete=False):
        bid=catalogue.add_book(title=title);rid=saved(bid)
        store.event(rid,'identity','done',data=dict(kind='book',book=bid))
        measure={'work.score':score,'metadata.work_families':families,'metadata.original_work_link':1}
        work=dict(title=title,authors=['Writer'],year=1985 if complete else None,publisher='Publisher' if complete else '')
        store.event(rid,'metadata','done',metrics=measure,data=dict(version=999,analysis_metrics=measure,work=dict(resolved=work)))
        return rid
    strong=ranked('Z Strong',.99,3)
    rich=ranked('A Rich',.95,2,True)
    weak=ranked('Weak',.4,1)
    # Enough newer, weak matches to push the strongest off an unsorted first page.
    for i in range(52):ranked(f'Weak {i:02}',.2,1)
    original=store.get;loaded=[]
    def get(rid):
        loaded.append(rid)
        return original(rid)
    monkeypatch.setattr(store,'get',get)
    page=P.dashboard(limit=2,phase='metadata')
    assert page['total']==55 and [r['id'] for r in page['runs']]==[strong,rich] and loaded==[strong,rich]
    assert P.dashboard(limit=1,phase='metadata',sort='metadata')['runs'][0]['id']==rich
    assert P.dashboard(limit=1,phase='metadata',sort='title')['runs'][0]['id']==rich
    assert P.dashboard(limit=1,phase='metadata',sort='oldest')['runs'][0]['id']==strong
    assert P.dashboard(limit=1,phase='metadata',sort='newest')['runs'][0]['id'] not in (strong,rich,weak)
    # A newer recording stage can refine book evidence; ranking uses its latest analysis.
    measure={'work.score':1,'metadata.work_families':4,'metadata.original_work_link':1}
    store.event(weak,'recording','done',data=dict(version=999,analysis_metrics=measure,work=dict(resolved=dict(title='Weak',authors=['Writer']))))
    assert P.dashboard(limit=1,phase='metadata')['runs'][0]['id']==weak


def test_book_review_explains_saved_provider_failure_without_fetching_again():
    rid=saved(catalogue.add_book(title='Outer Dark',author='Cormac McCarthy'))
    store.event(rid,'providers','done',data=dict(providers={'hardcover':dict(status='ok',candidates=[]),'openlibrary':dict(status='error',reason='Connection reset by peer',candidates=[])}))
    store.event(rid,'metadata','done',data=dict(version=999,work=dict(records={'hardcover':{'title':'Outer Dark'}},rows=[])))
    work=P.dashboard(phase='metadata')['runs'][0]['metadata']['work']
    assert work['lookups']==[dict(source='hardcover',status='confirmed',reason=''),dict(source='openlibrary',status='error',reason='Connection reset by peer')]


def test_current_step_and_recent_activity_include_live_file_progress():
    book=catalogue.add_book(title='Inspecting');rid=saved(book,'running')
    store.event(rid,'inventory','running')
    store.event(rid,'inventory','progress',data=dict(message='Inspecting chapter 2.mp3',current=2,total=10))
    row=next(r for r in operations.view([],[])['rows'] if r['id']==rid)
    assert row['current_step']=='Inspecting chapter 2.mp3' and row['step_elapsed']>=0
    assert row['logs'][-1]['message']=='Inspecting chapter 2.mp3'


def test_batch_ingest_yields_between_books_and_skips_members_of_the_completed_group(monkeypatch):
    from app.ingest.worker import Worker as IngestWorker
    a=catalogue.add_book(title='First');alias=catalogue.add_book(title='Alias');b=catalogue.add_book(title='Next');rid=saved(a)
    store.event(rid,'inventory','done',data=dict(members=[a,alias]));calls=[]
    monkeypatch.setattr(P,'run',lambda **kw:calls.append(kw) or [store.get(rid)])
    worker=IngestWorker();worker.inline=False
    monkeypatch.setattr(worker,'submit',lambda kind,**kw:worker.q.put((kind,kw)))
    worker.q.put(('continue',dict(run_id=rid)))
    worker._do('run',dict(ids=[a['id'],alias['id'],b['id']],limit=3,online=True))
    assert calls==[dict(ids=[a['id']],limit=1,online=True)]
    assert worker.q.get()[0]=='continue'
    assert worker.q.get()==('run',dict(ids=[b['id']],limit=1,online=True))
