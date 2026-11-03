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
    assert run['state']=='metadata_review' and P.stage_data(run,'alignment') is None
    P.approve_review(run['id'],'metadata');run=P.continue_review(run['id'],propose=False)
    assert run['state']=='audiobook_review' and P.stage_data(run,'alignment') is None
    P.approve_review(run['id'],'audiobook');run=P.continue_review(run['id'],propose=False)
    assert run['state']=='quality_review' and P.stage_data(run,'planning') is None
    P.approve_review(run['id'],'quality');run=P.continue_review(run['id'],propose=False)
    assert run['state']=='review'
    assert [e['stage'] for e in run['events'] if e['status']=='done' and e['stage'] in P.STAGES]==list(P.STAGES)
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
    run=P.run([book['id']],online=False)[0]
    P.approve_review(run['id'],'metadata');P.continue_review(run['id'],propose=False)
    P.approve_review(run['id'],'audiobook');P.continue_review(run['id'],propose=False)
    P.approve_review(run['id'],'quality');run=P.continue_review(run['id'],propose=False)
    plan=P.stage_data(run,'planning')['plan']
    assert -21<=plan['loudness']['I']<=-16
    assert plan['loudness']['normalisation_I']<-21 and plan['loudness']['gain_db']>3
    P.enqueue(run['id']);Q.decide([book['id']],True)
    assert Q.run_approved(ids=[book['id']])==dict(done=1,failed=0)
    result=catalogue.get_processed(book['id'])
    assert result['loudness_after']>=-21 and result['true_peak_after']<=-1
    assert probe.probe_file(str(next(output.rglob('*.mp3'))))['channels']==1


def test_listening_previews_generate_in_background_and_cache_selected_audio(client,tmp_path):
    import math
    import struct
    import wave
    from app.ingest import previews,store
    from app.process import probe
    paths=[];files=[]
    for i in range(2):
        p=tmp_path/f'part{i}.wav'
        with wave.open(str(p),'wb') as wav:
            wav.setparams((1,2,16000,0,'NONE','not compressed'))
            wav.writeframes(b''.join(struct.pack('<h',int(2000*math.sin(2*math.pi*440*n/16000))) for n in range(16000*15)))
        stat=p.stat();paths.append(str(p));files.append(dict(path=str(p),size=stat.st_size,mtime_ns=stat.st_mtime_ns,sha256=str(i),probe=probe.probe_file(str(p))))
    rid=store.create(1,{});store.event(rid,'inventory','done',{},dict(files=files));store.event(rid,'selection','done',{},dict(paths=paths))
    before=[p.read_bytes() for p in map(__import__('pathlib').Path,paths)]
    assert client.post(f'/api/ingest/runs/{rid}/samples').status_code==200
    with previews.LOCK:jobs=list(previews.JOBS.values())
    for job in jobs:job.result(timeout=60)
    samples=client.get(f'/api/ingest/runs/{rid}/samples').json()['samples']
    assert len(samples)==5 and all(s['status']=='ready' for s in samples)
    assert all(0<=s['start_seconds']<=24 and s['duration_seconds']==6 for s in samples)
    for s in samples:
        response=client.get(s['url']);assert response.status_code==200 and response.content
        assert abs(probe.probe_file(str(previews.audio(rid,s['index'])))['duration']-6)<.1
    # Exercise a sample crossing the file boundary as well as cache reuse.
    ctx=previews.context(rid);ctx[2][0].update(start_seconds=13,duration_seconds=6)
    previews.audio(rid,0).unlink();cross=previews.generate(ctx,0)
    assert abs(probe.probe_file(str(cross))['duration']-6)<.1
    mtime=cross.stat().st_mtime_ns
    client.post(f'/api/ingest/runs/{rid}/samples')
    assert cross.stat().st_mtime_ns==mtime
    assert [p.read_bytes() for p in map(__import__('pathlib').Path,paths)]==before
    assert client.get(f'/api/ingest/runs/{rid}/samples/5').status_code==400


@pytest.mark.skipif(not shutil.which('ffmpeg'),reason='ffmpeg required')
def test_mixed_flat_folder_creates_independent_book_plans_and_scoped_manifests(tmp_path,monkeypatch):
    root=tmp_path/'mixed';root.mkdir();monkeypatch.setenv('APP_FINAL',str(tmp_path/'final'))
    for title in ('Alpha Novel','Beta Novel'):
        for part in (1,2):
            path=root/f'{title}-Part{part:02d}.mp3'
            result=subprocess.run(['ffmpeg','-nostdin','-v','error','-f','lavfi','-i','sine=frequency=300:duration=3','-metadata',f'album={title}','-metadata','artist=Test Writer','-metadata',f'track={part}/2',str(path)],capture_output=True)
            assert result.returncode==0,result.stderr.decode()
    container=catalogue.add_book(title='Mixed sources',path=str(root))
    results=P.run([container['id']],online=False,limit=1)
    assert [r['state'] for r in results]==['split','metadata_review','metadata_review']
    selections=[]
    for run in results[1:]:
        for phase in ('metadata','audiobook','quality'):
            P.approve_review(run['id'],phase);run=P.continue_review(run['id'],propose=False)
        assert run['state']=='review'
        P.enqueue(run['id']);plan=catalogue.get_plan(run['book_id'])['plan']
        assert len(plan['source_paths'])==2 and plan['ingest']['source_scope']['scope_paths']
        assert Q._unchanged(catalogue.get_book(run['book_id']),plan)
        selections.append(set(plan['source_paths']))
    assert not selections[0] & selections[1]
    assert len(list(root.glob('*.mp3')))==4


@pytest.mark.skipif(not shutil.which('ffmpeg'),reason='ffmpeg required')
def test_multi_disc_anthologies_keep_stories_as_chapters_and_show_grouping_evidence(tmp_path,client):
    from app import source_files
    root=tmp_path/'Recordings';before={}
    for label,album in [('A','Stories Vol Five CD1'),('B','Stories VOLUME FIVE CD2'),('C','Stories Vol Six CD1'),('D','Stories Vol Six CD2')]:
        folder=root/label;folder.mkdir(parents=True)
        for n in (1,2):
            path=folder/f'{n:02} - Different Story {n}.mp3'
            command=['ffmpeg','-nostdin','-v','error','-f','lavfi','-i','sine=frequency=300:sample_rate=24000:duration=0.5','-metadata',f'album={album}','-metadata','artist=Test Writer','-metadata',f'track={n}/2',str(path)]
            subprocess.run(command,check=True,capture_output=True)
            before[str(path)]=path.read_bytes()
    book=catalogue.add_book(title='Recordings',path=str(root))
    runs=P.run([book['id']],online=False,limit=1)
    assert [r['state'] for r in runs]==['split','metadata_review','metadata_review']
    for child in runs[1:]:
        inv=P.stage_data(child,'inventory')
        assert len(inv['files'])==4 and len(inv['representations'])==1
        assert inv['representations'][0]['missing_tracks']==0 and inv['representations'][0]['duplicate_tracks']==0
        location=source_files.describe(catalogue.get_book(child['book_id']))
        assert location['count']==4 and location['grouping']==['Album identity and ordered track sequence']
        response=client.get(f"/api/books/{child['book_id']}/source")
        assert response.status_code==200 and response.json()==location
    assert {str(path):path.read_bytes() for path in root.rglob('*.mp3')}==before
