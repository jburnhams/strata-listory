"""Ingest HTTP contracts and background task dispatch."""
import pytest
from fastapi.testclient import TestClient
from app import catalogue
from app.ingest import pipeline,policy,store
from app.ingest.worker import Worker
from app.server.app import create_app


@pytest.fixture
def client(monkeypatch):
    monkeypatch.setattr(Worker,'inline',True)
    with TestClient(create_app()) as c:yield c


def test_ingest_dashboard_and_versioned_policy(client):
    r=client.get('/api/ingest').json();assert r['runs']==[] and r['policy']['auto_process'] is False
    p=r['policy'];p['gates']['work.score']={'min':.95}
    assert client.put('/api/ingest/policy',json=p).json()['revision']==1
    assert client.put('/api/ingest/policy',json={'auto_process':True}).status_code==400
    assert client.get('/api/ingest/runs/missing').status_code==400
    assert client.get('/api/ingest?offset=50').json()['offset']==50
    assert client.get('/api/ingest?offset=-1').status_code==422


def test_ingest_dispatch_evaluation_and_resume(client,monkeypatch):
    b=catalogue.add_book(title='T');rid=store.create(b['id'],{});store.link(rid,[b['id']]);called=[]
    monkeypatch.setattr(pipeline,'run',lambda **kw:called.append(kw) or [store.get(rid)])
    assert client.post('/api/ingest/run',json=dict(ids=[b['id']],online=False)).status_code==200
    assert called[-1]['ids']==[b['id']] and not called[-1]['online']
    assert client.post('/api/ingest/run',json=dict(ids=[])).status_code==400
    assert client.post('/api/ingest/run',json=dict(ids=[999])).status_code==404
    assert client.post('/api/ingest/run',json=dict(limit=0)).status_code==422
    assert client.post(f'/api/ingest/runs/{rid}/resume').status_code==200
    assert called[-1]['resume']==rid
    assert client.get(f'/api/ingest/runs/{rid}').json()['id']==rid
    assert not client.post(f'/api/ingest/runs/{rid}/evaluate').json()['eligible']
    assert client.post(f'/api/ingest/runs/{rid}/enqueue').status_code==400
    assert client.get('/api/ingest').json()['runs'][0]['id']==rid


def test_explicit_library_selection_dispatches_every_id(client,monkeypatch):
    ids=list(range(1,1682));called=[];validated=[]
    monkeypatch.setattr(catalogue,'get_book',lambda bid: validated.append(bid) or dict(id=bid))
    monkeypatch.setattr(pipeline,'run',lambda **kw:called.append(kw) or [])
    response=client.post('/api/ingest/run',json=dict(ids=ids,limit=len(ids),online=False))
    assert response.status_code==200
    assert validated==ids
    assert called==[dict(ids=ids,limit=1681,online=False)]
    # A high limit alone must not expand an automatic-discovery batch.
    assert client.post('/api/ingest/run',json=dict(limit=201)).status_code==422
    assert client.post('/api/ingest/run',json=dict(ids=[1],limit=201)).status_code==422
    assert len(called)==1


def test_stage_approval_dispatches_continuation_only_once(client,monkeypatch):
    book=catalogue.add_book(title='T');rid=store.create(book['id'],{});called=[];approved=[]
    def approve(run_id,phase):
        if phase not in ('metadata','quality'):raise ValueError('invalid review stage')
        changed=phase not in approved
        if changed:approved.append(phase)
        return dict(changed=changed,phase=phase)
    monkeypatch.setattr(pipeline,'approve_review',approve)
    monkeypatch.setattr(pipeline,'continue_review',lambda run_id:called.append(run_id) or store.get(run_id))
    assert client.post(f'/api/ingest/runs/{rid}/approve/metadata').json()['queued']
    assert not client.post(f'/api/ingest/runs/{rid}/approve/metadata').json()['queued']
    assert client.post(f'/api/ingest/runs/{rid}/approve/quality').json()['queued']
    assert called==[rid,rid]
    assert client.post(f'/api/ingest/runs/{rid}/approve/bad').status_code==400


def test_enabling_metadata_automation_queues_existing_reviews_without_enabling_quality(client,monkeypatch):
    book=catalogue.add_book(title='T');rid=store.create(book['id'],{});called=[]
    monkeypatch.setattr(pipeline,'approve_passing_metadata',lambda:[rid])
    monkeypatch.setattr(pipeline,'continue_review',lambda run_id:called.append(run_id) or store.get(run_id))
    value=client.get('/api/ingest').json()['policy'];value['auto_approve_metadata']=True
    result=client.put('/api/ingest/policy',json=value).json()
    assert result['auto_approve_metadata'] and not result['auto_approve_quality']
    assert not result['auto_approve'] and not result['auto_process'] and called==[rid]


def test_review_pages_and_combined_operations_endpoint(client):
    b=catalogue.add_book(title='Waiting');rid=store.create(b['id'],{})
    store.finish(rid,'metadata_review')
    value=client.get('/api/operations').json()
    assert not value['busy'] and value['rows'][0]['run_id']==rid
    assert client.get('/api/operations?completed=true').status_code==200
    assert client.get('/api/ingest?phase=metadata').json()['total']==1
    assert client.get('/api/ingest?phase=quality').json()['total']==0
    assert client.get('/api/ingest?phase=other').status_code==422
    assert client.get('/api/ingest?phase=metadata&sort=oldest').status_code==200
    assert client.get('/api/ingest?phase=metadata&sort=other').status_code==422


def test_source_endpoint_lists_sample_filenames_without_ingesting(client,tmp_path):
    folder=tmp_path/'Chapters';folder.mkdir()
    for n in range(5):(folder/f'{n+1:03}.mp3').write_bytes(b'audio')
    book=catalogue.add_book(title='A book',path=str(folder))
    value=client.get(f'/api/books/{book["id"]}/source').json()
    assert value==dict(folder=str(folder),files=['001.mp3','002.mp3','003.mp3'],count=5,available=True)
    assert client.get('/api/books/99999/source').status_code==404
    assert not store.run_count()


def test_listening_preview_http_contract(client,monkeypatch,tmp_path):
    from app.ingest import previews
    sample=tmp_path/'sample.mp3';sample.write_bytes(b'mp3')
    monkeypatch.setattr(previews,'prepare',lambda rid:dict(samples=[dict(index=0,status='generating')]))
    monkeypatch.setattr(previews,'status',lambda rid:dict(samples=[dict(index=0,status='ready')]))
    monkeypatch.setattr(previews,'audio',lambda rid,index:sample)
    assert client.post('/api/ingest/runs/example/samples').json()['samples'][0]['status']=='generating'
    assert client.get('/api/ingest/runs/example/samples').json()['samples'][0]['status']=='ready'
    r=client.get('/api/ingest/runs/example/samples/0')
    assert r.content==b'mp3' and r.headers['content-type']=='audio/mpeg'
    assert client.get('/api/ingest/runs/example/samples/word').status_code==422


def test_audiobook_identification_is_a_separate_queue_and_approval(client,monkeypatch):
    assert client.get('/api/ingest?phase=audiobook').status_code==200
    book=catalogue.add_book(title='T');rid=store.create(book['id'],{});calls=[]
    monkeypatch.setattr(pipeline,'approve_review',lambda run_id,phase:calls.append(phase) or dict(changed=True,phase=phase))
    monkeypatch.setattr(pipeline,'continue_review',lambda run_id:store.get(run_id))
    assert client.post(f'/api/ingest/runs/{rid}/approve/audiobook').json()['queued']
    assert calls==['audiobook']


def test_startup_reconciles_enabled_identification_automation(monkeypatch):
    value=policy.current();value['auto_approve_audiobook']=True;policy.save(value);calls=[]
    monkeypatch.setattr(Worker,'inline',True)
    monkeypatch.setattr(pipeline,'approve_passing_metadata',lambda:calls.append('identification') or [])
    with TestClient(create_app()) as client:
        assert client.get('/api/health').status_code==200
    assert calls==['identification']


def test_library_workflow_endpoint_includes_reviews_and_queued_work(client,monkeypatch):
    from app.ingest import operations
    monkeypatch.setattr(operations,'library',lambda ingest,process:dict(books={'1':['metadata'],'2':['processing']}))
    assert client.get('/api/library/workflow').json()==dict(books={'1':['metadata'],'2':['processing']})


def test_startup_restores_only_current_queued_collection_children(monkeypatch):
    monkeypatch.setattr(Worker,'inline',True);calls=[]
    book=catalogue.add_book(title='Queued child');old=store.create(book['id'],dict(collection_run='parent'));store.finish(old,'queued')
    latest=store.create(book['id'],dict(collection_run='parent'));store.finish(latest,'queued')
    monkeypatch.setattr(pipeline,'continue_review',lambda rid:calls.append(rid) or store.get(rid))
    with TestClient(create_app()) as client:assert client.get('/api/health').status_code==200
    assert calls==[latest]


def test_startup_queues_regrouping_for_pending_mixed_collections(monkeypatch,tmp_path):
    monkeypatch.setattr(Worker,'inline',True);root=tmp_path/'collection';root.mkdir()
    book=catalogue.add_book(title='Collection',path=str(root));rid=store.create(book['id'],{});files=[];called=[]
    for title in ('Alpha Novel','Beta Novel'):
        p=root/f'{title}.mp3';p.write_bytes(b'audio')
        files.append(dict(path=str(p),probe=dict(tags=dict(album=title,artist='Test Writer'))))
    store.event(rid,'inventory','done',data=dict(root=str(root),files=files));store.finish(rid,'metadata_review')
    monkeypatch.setattr(pipeline,'regroup_collection',lambda run_id:called.append(run_id) or [])
    with TestClient(create_app()) as client:assert client.get('/api/health').status_code==200
    assert called==[rid]


def test_recording_choice_endpoint_passes_the_saved_candidate_key(client,monkeypatch):
    calls=[]
    monkeypatch.setattr(pipeline,'choose_recording',lambda rid,key:calls.append((rid,key)) or dict(changed=True,key=key))
    assert client.put('/api/ingest/runs/run/recording',json=dict(key='audible:B000000001')).json()['changed']
    assert calls==[('run','audible:B000000001')]


def test_library_covers_returns_confirmed_local_ids(client,monkeypatch):
    from app import details
    monkeypatch.setattr(details,'available_cover_ids',lambda books:[12,34])
    assert client.get('/api/library/covers').json()==dict(ids=[12,34])
