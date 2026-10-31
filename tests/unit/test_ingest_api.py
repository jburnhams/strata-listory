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
    p=r['policy'];p['gates']['recording.score']={'min':.95}
    assert client.put('/api/ingest/policy',json=p).json()['revision']==1
    assert client.put('/api/ingest/policy',json={'auto_process':True}).status_code==400
    assert client.get('/api/ingest/runs/missing').status_code==400


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
