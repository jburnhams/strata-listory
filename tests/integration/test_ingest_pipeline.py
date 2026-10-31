"""Synthetic audio through staged ingest, exact-file proposal and processing."""
import json
import shutil
import subprocess
from pathlib import Path

import pytest
from app import catalogue
from app.ingest import pipeline as P,metadata as M
from app.process import queue as Q


@pytest.mark.skipif(not shutil.which('ffmpeg'),reason='ffmpeg required')
@pytest.mark.parametrize('online',[False,True])
def test_offline_ingest_then_explicit_approval_processes_selected_manifest(tmp_path,monkeypatch,online):
    source=tmp_path/'source.wav';output=tmp_path/'final';monkeypatch.setenv('APP_FINAL',str(output))
    p=subprocess.run(['ffmpeg','-nostdin','-v','error','-f','lavfi','-i','sine=frequency=300:sample_rate=24000:duration=10','-c:a','pcm_s16le',str(source)],capture_output=True)
    assert p.returncode==0,p.stderr.decode()
    book=catalogue.add_book(title='Synthetic book',author='Test Author',narrator='Test Reader',path=str(source),year=2000)
    if online:
        from app.providers.base import candidate
        def collect(identity,inv,folder,names):
            folder.mkdir(parents=True,exist_ok=True);raw=folder/'response.json';raw.write_text(json.dumps(dict(unknown_field='preserved forever',source='test')))
            c=candidate('audible',title='Synthetic book',authors=['Test Author'],narrators=['Test Reader'],runtime_minutes=10/60,language='english',abridged=False)
            return dict(providers=dict(audible=dict(candidates=[c])),archives=[str(raw)]),{'metadata.provider_attempts':1}
        monkeypatch.setattr(M,'collect',collect)
    run=P.run([book['id']],online=online)[0]
    assert run['state']=='review'
    assert [e['stage'] for e in run['events'] if e['status']=='done']==list(P.STAGES)
    assert P.metrics(run)['quality.complete']==1
    assert catalogue.get_plan(book['id']) is None
    assert P.enqueue(run['id'])==dict(book_id=book['id'],approved=False)
    plan=catalogue.get_plan(book['id'])['plan']
    assert plan['source_paths']==[str(source)]
    assert plan['ingest']['manifest'][0]['sha256']
    assert Q.decide([book['id']],True)==[book['id']]
    assert Q.run_approved(ids=[book['id']])==dict(done=1,failed=0)
    assert catalogue.get_processed(book['id'])['status']=='done'
    assert list(output.rglob('*.mp3'))
    final_meta=json.loads(next(output.rglob('metadata.json')).read_text())
    assert final_meta['metadata']['resolved']['title']=='Synthetic book'
    assert final_meta['ingest']['run_id']==run['id']
    if online:
        assert final_meta['evidence_files']
        archived=next(output.rglob('evidence/0000-response.json'))
        assert json.loads(archived.read_text())['unknown_field']=='preserved forever'
    finished=P.store.get(run['id']);assert finished['state']=='processed'
    assert P.metrics(finished)['processing.true_peak_after']<=-1
    assert source.is_file()


@pytest.mark.skipif(not shutil.which('ffmpeg'),reason='ffmpeg required')
def test_stereo_pilot_normalises_measured_mono_output(tmp_path,monkeypatch):
    from app.process import probe
    source=tmp_path/'stereo.wav';output=tmp_path/'final';monkeypatch.setenv('APP_FINAL',str(output))
    subprocess.run(['ffmpeg','-nostdin','-v','error','-f','lavfi','-i','sine=frequency=300:duration=12','-af','volume=1.1','-ac','2',str(source)],check=True)
    book=catalogue.add_book(title='Stereo pilot',author='Test Author',path=str(source),year=2000)
    run=P.run([book['id']],online=False)[0];plan=P.stage_data(run,'planning')['plan']
    assert -21<=plan['loudness']['I']<=-16
    assert plan['loudness']['normalisation_I']<-21 and plan['loudness']['gain_db']>3
    P.enqueue(run['id']);Q.decide([book['id']],True)
    assert Q.run_approved(ids=[book['id']])==dict(done=1,failed=0)
    result=catalogue.get_processed(book['id'])
    assert result['loudness_after']>=-21 and result['true_peak_after']<=-1
    assert probe.probe_file(str(next(output.rglob('*.mp3'))))['channels']==1
