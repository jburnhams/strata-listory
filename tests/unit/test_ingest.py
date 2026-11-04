"""Staged ingest invariants: source retention, fail-closed gates and exact manifests."""
from copy import deepcopy
from array import array
import json
import math
from pathlib import Path
import random
from types import SimpleNamespace

import pytest

from app import catalogue, contributors, db, details, providers, sources
from app.ingest import audio, inventory as I, metadata as M, pipeline as P, policy, store
from app.ingest.observe import capture
from app.ingest.worker import Worker
from app.providers.base import candidate


def recording(source='audible', **kw):
    return candidate(source,title='Dune',authors=['Frank Herbert'],narrators=['Reader'],runtime_minutes=60,language='english',abridged=False,asin='B000000001',release_date='2000-01-01',**kw)


def finish_reviews(run):
    while run['state'] in ('metadata_review','audiobook_review','quality_review'):
        P.approve_review(run['id'],run['state'].split('_')[0])
        run=P.continue_review(run['id'],propose=False)
    return run


def complete_run(*args,**kwargs):
    return [finish_reviews(run) for run in P.run(*args,**kwargs)]


def test_new_runs_pause_for_metadata_then_quality_approval(pipeline_fakes):
    book=pipeline_fakes;run=P.run([book['id']])[0];rid=run['id']
    assert run['state']=='metadata_review' and P.stage_data(run,'alignment') is None
    assert P.dashboard()['runs'][0]['review_phase']=='metadata'
    with pytest.raises(ValueError,match='approve metadata'):P.approve_review(rid,'quality')
    with pytest.raises(ValueError,match='approve metadata and quality'):P.enqueue(rid)
    with pytest.raises(ValueError,match='only interrupted'):P.run(resume=rid)
    assert P.approve_review(rid,'metadata')['changed']
    assert not P.approve_review(rid,'metadata')['changed']
    run=P.continue_review(rid,propose=False)
    assert run['state']=='audiobook_review' and P.stage_data(run,'alignment') is None
    assert P.dashboard()['runs'][0]['review_phase']=='audiobook'
    with pytest.raises(ValueError,match='approve audiobook'):P.approve_review(rid,'quality')
    P.approve_review(rid,'audiobook');run=P.continue_review(rid,propose=False)
    assert run['state']=='quality_review' and P.stage_data(run,'planning') is None
    assert P.approval_valid(run,'metadata') and not P.approval_valid(run,'quality')
    P.rate_quality(rid,'good');assert P.approve_review(rid,'quality')['changed']
    with pytest.raises(ValueError,match='awaiting quality approval'):P.rate_quality(rid,'medium')
    run=P.continue_review(rid)
    assert run['state']=='eligible' and catalogue.get_plan(book['id'])['state']=='proposed'
    assert catalogue.get_processed(book['id']) is None
    assert P.dashboard()['runs'][0]['review_phase']=='processing'


def test_automatic_metadata_approval_does_not_bypass_quality_checks(pipeline_fakes,monkeypatch):
    value=policy.current();value.update(auto_approve_metadata=True,auto_approve_audiobook=True,auto_approve_quality=True);policy.save(value)
    monkeypatch.setattr(audio.probe,'measure_book',lambda *a:dict(I=-20,TP=1,LRA=5,silences=[],peak_p99=-4))
    run=P.run([pipeline_fakes['id']])[0]
    assert run['state']=='quality_review'
    assert P.stage_data(run,'metadata_approval')['automatic']
    assert P.stage_data(run,'quality_approval') is None and catalogue.get_plan(pipeline_fakes['id']) is None


def test_book_only_automation_stops_at_audiobook_review(pipeline_fakes):
    value=policy.current();value['auto_approve_metadata']=True;policy.save(value)
    run=P.run([pipeline_fakes['id']])[0]
    assert run['state']=='audiobook_review' and P.stage_data(run,'metadata_approval')['automatic']
    assert P.stage_data(run,'quality_approval') is None and P.stage_data(run,'planning') is None
    assert catalogue.get_plan(pipeline_fakes['id']) is None


@pytest.mark.parametrize('passes',[True,False])
def test_existing_metadata_reviews_are_automated_only_when_all_checks_pass(pipeline_fakes,passes):
    old=P.run([pipeline_fakes['id']])[0]
    current=P.run([pipeline_fakes['id']])[0]
    value=policy.current();value['auto_approve_metadata']=True
    if not passes:value['gates']['work.score']={'min':1.1}
    policy.save(value)
    approved=P.approve_passing_metadata()
    assert approved==([current['id']] if passes else [])
    assert not P.approval_valid(store.get(old['id']),'metadata')
    if passes:
        run=P.continue_review(current['id'])
        assert run['state']=='audiobook_review' and P.stage_data(run,'metadata_approval')['automatic']
        assert not P.approval_valid(run,'quality') and catalogue.get_plan(pipeline_fakes['id']) is None
    else:assert store.get(current['id'])['state']=='metadata_review'
    assert P.approve_passing_metadata()==[]


def test_automatic_metadata_approval_revalidates_sources_and_policy(pipeline_fakes):
    run=P.run([pipeline_fakes['id']])[0]
    assert P.approve_passing_metadata()==[]
    with pytest.raises(ValueError,match='current thresholds'):P.approve_review(run['id'],'metadata',automatic=True)
    value=policy.current();value['auto_approve_metadata']=True;policy.save(value)
    Path(pipeline_fakes['path']).write_bytes(b'changed')
    assert P.approve_passing_metadata()==[]
    assert not P.approval_valid(store.get(run['id']),'metadata')
    assert 'source files changed' in store.get(run['id'])['events'][-1]['error']


def test_legacy_automation_policies_keep_their_stage_behavior(monkeypatch):
    legacy={k:v for k,v in policy.current().items() if k not in ('auto_approve_metadata','auto_approve_quality')}
    legacy.update(auto_approve=True,revision=7)
    monkeypatch.setattr(store,'policy_latest',lambda:legacy)
    value=policy.current()
    assert value['auto_approve_metadata'] and value['auto_approve_quality'] and value['revision']==7
    for field in ('auto_approve_metadata','auto_approve_quality'):
        with pytest.raises(ValueError,match='booleans'):policy.validate(dict(value,**{field:'yes'}))


def test_changed_evidence_invalidates_stage_approvals_and_execution(pipeline_fakes):
    run=complete_run([pipeline_fakes['id']])[0];P.enqueue(run['id']);plan=catalogue.get_plan(pipeline_fakes['id'])['plan']
    assert P.Q._unchanged(pipeline_fakes,plan)
    store.event(run['id'],'metadata','done',data=P.stage_data(run,'metadata'))
    changed=store.get(run['id'])
    assert not P.approval_valid(changed,'metadata') and not P.approval_valid(changed,'quality')
    assert not P.Q._unchanged(pipeline_fakes,plan)
    with pytest.raises(catalogue.CatalogueError,match='approve metadata and quality'):P.Q.decide([pipeline_fakes['id']],True)


def test_review_actions_reject_incomplete_and_invalid_stages(pipeline_fakes):
    rid=store.create(pipeline_fakes['id'],{})
    for phase in ('bad',):
        with pytest.raises(ValueError,match='metadata or quality'):P.approve_review(rid,phase)
        with pytest.raises(ValueError,match='metadata or quality'):P.review_evaluation(store.get(rid),phase)
    with pytest.raises((ValueError,OSError)):P.approve_review(rid,'metadata')


def test_legacy_finished_output_does_not_return_to_metadata_review(pipeline_fakes):
    rid=store.create(pipeline_fakes['id'],{});store.finish(rid,'processed')
    row=next(r for r in P.dashboard()['runs'] if r['id']==rid)
    assert row['review_phase']=='processing' and not row['processing_proposed']


def test_review_history_pages_keep_older_runs_accessible_and_superseded(pipeline_fakes):
    old=P.run([pipeline_fakes['id']])[0];new=P.run([pipeline_fakes['id']])[0]
    first=P.dashboard(limit=1);second=P.dashboard(offset=1,limit=1)
    assert first['total']==2 and first['runs'][0]['id']==new['id'] and not first['runs'][0]['superseded']
    assert second['total']==2 and second['offset']==1 and second['runs'][0]['id']==old['id'] and second['runs'][0]['superseded']
    pending=store.create(pipeline_fakes['id'],{})
    assert store.latest_id(pipeline_fakes['id'])==pending


@pytest.fixture
def book(tmp_path,monkeypatch):
    p=tmp_path/'Dune.m4b';p.write_bytes(b'unchanged audio')
    b=catalogue.add_book(title='Dune',author='Frank Herbert',path=str(p))
    def probe(path):return dict(path=path,duration=3600,codec='aac',channels=1,sample_rate=24000,bit_rate=64,chapters=[],tags=dict(album='Dune (Unabridged)',artist='Frank Herbert',composer='Reader',language='english',track='1/1'))
    monkeypatch.setattr(I.probe,'probe_file',probe);monkeypatch.setattr(details,'_ffprobe',lambda p:dict(format=dict(tags=dict(unknown='keep'))))
    return b


@pytest.fixture
def pipeline_fakes(book,monkeypatch):
    def collect(identity,inv,folder,names):
        return dict(providers=dict(audible=dict(status='ok',candidates=[recording()]),openlibrary=dict(status='ok',candidates=[candidate('openlibrary',title='Dune',authors=['Frank Herbert'],original_year=1965,id='/works/OL1W')])),archives=[]),{'metadata.provider_errors':0}
    monkeypatch.setattr(M,'collect',collect)
    def alignment(inv,folder):
        reps=[dict(r,coverage=1,matched_fraction=1,max_gap_seconds=0,decode_messages=[],windows=[]) for r in inv['representations']]
        return dict(reference_id=reps[0]['id'],representations=reps),{'alignment.coverage':1,'alignment.max_gap_seconds':0,'alignment.unmatched_fraction':0,'alignment.missing_tracks':0,'alignment.unresolved_representations':0}
    monkeypatch.setattr(audio,'alignment',alignment)
    monkeypatch.setattr(audio.probe,'measure_book',lambda *a:dict(I=-20,TP=-2,LRA=5,silences=[],peak_p99=-4))
    monkeypatch.setattr(audio,'band_sample',lambda *a:-20)
    monkeypatch.setattr(audio.acoustics,'assess',lambda *a: {'medians': {}, 'samples': [{'result': {}}] * 3})
    def plan(book,final,*args,**kw):
        assert kw['input_paths']==[book['path']]
        return dict(book_id=book['id'],title='Dune',author='Frank Herbert',out_dir=final+'/Dune',warnings=[],in_files=1,in_bytes=15,chapters=[],duration=3600)
    monkeypatch.setattr(P.P,'make_plan',plan)
    return book


def test_complete_run_persists_every_stage_and_replays_policy(pipeline_fakes):
    runs=complete_run([pipeline_fakes['id']]);r=runs[0]
    assert r['state']=='eligible'
    assert [e['stage'] for e in r['events'] if e['status']=='done' and e['stage'] in P.STAGES]==list(P.STAGES)
    assert all(e['elapsed']>=0 for e in r['events'])
    assert P.metrics(r)['metadata.original_work_link']==1
    assert P.evaluate(r['id'])['eligible']
    changed=deepcopy(policy.current());changed['gates']['work.score']['min']=1.01
    assert not P.evaluate(r['id'],changed)['eligible']
    assert len([e for e in store.get(r['id'])['events'] if e['stage']=='evaluation'])==2
    assert P.dashboard()['runs'][0]['members']==[pipeline_fakes['id']]
    assert complete_run()==[]
    assert catalogue.get_plan(pipeline_fakes['id']) is None


def test_enqueue_freezes_inputs_and_catches_same_size_edit(pipeline_fakes):
    r=complete_run([pipeline_fakes['id']])[0];P.enqueue(r['id']);plan=catalogue.get_plan(pipeline_fakes['id'])['plan']
    assert P.Q._unchanged(pipeline_fakes,plan)
    with pytest.raises(ValueError,match='already has'):P.enqueue(r['id'])
    p=Path(pipeline_fakes['path']);old=p.read_bytes();p.write_bytes(b'x'*len(old))
    assert not P.Q._unchanged(pipeline_fakes,plan)
    with pytest.raises(ValueError,match='changed'):P.enqueue(r['id'])


def test_manual_metadata_edit_invalidates_plan(pipeline_fakes):
    r=complete_run([pipeline_fakes['id']])[0];plan=P.stage_data(r,'planning')['plan'];catalogue.update_book(pipeline_fakes['id'],title='Different')
    assert not P.Q._unchanged(pipeline_fakes,plan)
    with pytest.raises(ValueError,match='metadata changed'):P.enqueue(r['id'])


def test_stage_failure_resumes_without_repeating_completed_stages(pipeline_fakes,monkeypatch):
    original=audio.quality
    monkeypatch.setattr(audio,'quality',lambda *a:(_ for _ in ()).throw(ValueError('quality failed')))
    r=complete_run([pipeline_fakes['id']])[0];assert r['state']=='failed'
    assert not P.evaluate(r['id'])['eligible']
    monkeypatch.setattr(audio,'quality',original)
    monkeypatch.setattr(M,'collect',lambda *a:pytest.fail('must reuse completed provider stage'))
    resumed=complete_run(resume=r['id'])[0]
    assert resumed['state']=='eligible'
    assert len([e for e in resumed['events'] if e['stage']=='quality' and e['status']=='failed'])==1
    with pytest.raises(ValueError,match='only interrupted'):complete_run(resume=r['id'])


def test_metadata_refresh_preserves_prior_history_and_reuses_verified_audio(pipeline_fakes,monkeypatch):
    previous=complete_run([pipeline_fakes['id']])[0];count=len(previous['events']);calls=[];collect=M.collect
    monkeypatch.setattr(M,'collect',lambda *a: calls.append(a[0]['book']['id']) or collect(*a))
    monkeypatch.setattr(audio,'alignment',lambda *a:pytest.fail('unchanged alignment must be reused'))
    monkeypatch.setattr(audio,'quality',lambda *a:pytest.fail('unchanged quality must be reused'))
    fresh=finish_reviews(P.refresh_metadata(previous['id']))
    assert fresh['id']!=previous['id'] and fresh['state']=='eligible'
    assert fresh['options']['metadata_refresh_of']==previous['id'] and fresh['options']['online'] is True
    assert calls==[pipeline_fakes['id'],pipeline_fakes['id']]
    for stage in ('inventory','alignment','quality','selection'):assert P.stage_data(fresh,stage)['reused_from']==previous['id']
    assert len(store.get(previous['id'])['events'])==count
    assert store.latest(pipeline_fakes['id'])['id']==fresh['id']


@pytest.mark.parametrize('problem,message', [('failed','completed'),('active','active'),('plan','replan'),('output','replan'),('metadata','metadata changed'),('files','source files changed'),('incomplete','incomplete')])
def test_metadata_refresh_refuses_unsafe_reuse(pipeline_fakes,problem,message):
    book=pipeline_fakes;previous=complete_run([book['id']])[0]
    if problem=='failed':store.finish(previous['id'],'failed')
    elif problem=='active':
        active=store.create(book['id'],{});store.link(active,[book['id']])
    elif problem=='plan':catalogue.set_plan(book['id'],'proposed',dict(x=1))
    elif problem=='output':catalogue.set_processed(book['id'],dict(status='done',finished='now'))
    elif problem=='metadata':catalogue.update_book(book['id'],title='Changed')
    elif problem=='files':Path(book['path']).write_bytes(b'changed audio')
    elif problem=='incomplete':
        incomplete=store.create(book['id'],previous['options']);store.link(incomplete,[book['id']])
        store.event(incomplete,'inventory','done',data=P.stage_data(previous,'inventory'));store.finish(incomplete,'review');previous=store.get(incomplete)
    with pytest.raises(ValueError,match=message):P.refresh_metadata(previous['id'])


def test_changed_input_cannot_resume(pipeline_fakes,monkeypatch):
    monkeypatch.setattr(audio,'quality',lambda *a:(_ for _ in ()).throw(ValueError('fail')))
    r=complete_run([pipeline_fakes['id']])[0];Path(pipeline_fakes['path']).write_bytes(b'new')
    with pytest.raises(ValueError,match='changed'):complete_run(resume=r['id'])


def test_automation_explicit_policy_and_only_selected_job_runs(pipeline_fakes,monkeypatch):
    p=policy.current();p.update(auto_approve_metadata=True,auto_approve_quality=True,auto_approve=True,auto_process=True);policy.save(p);called=[]
    monkeypatch.setattr(P.Q,'run_approved',lambda **kw:called.append(kw))
    r=complete_run([pipeline_fakes['id']])[0]
    assert catalogue.get_plan(pipeline_fakes['id'])['state']=='approved'
    assert called==[dict(ids=[pipeline_fakes['id']])]
    plan=catalogue.get_plan(pipeline_fakes['id'])['plan'];policy.save(dict(policy.current(),auto_process=False))
    assert not P.Q._unchanged(pipeline_fakes,plan)


def test_threshold_failure_proposes_for_manual_review_only(pipeline_fakes):
    p=policy.current();p['gates']['work.score']={'min':1.1};policy.save(p)
    r=complete_run([pipeline_fakes['id']])[0];assert r['state']=='review';P.enqueue(r['id'])
    row=catalogue.get_plan(pipeline_fakes['id']);assert row['state']=='proposed'
    assert any('work.score' in w for w in row['plan']['warnings'])
    with pytest.raises(ValueError,match='thresholds'):P.enqueue(r['id'],automatic=True)


def test_offline_missing_measurements_fail_closed(pipeline_fakes):
    r=complete_run([pipeline_fakes['id']],online=False)[0]
    assert r['state']=='review'
    checks=P.evaluate(r['id'])['checks']
    assert next(c for c in checks if c['metric']=='recording.runtime_error')['reason']=='missing measurement'


def test_bad_run_and_policy_arguments(pipeline_fakes):
    with pytest.raises(ValueError):store.get('missing')
    with pytest.raises(ValueError):complete_run(limit=0)
    p=policy.current();assert p['revision']==0
    for change in [dict(extra=True),dict(auto_process=True),dict(auto_approve=1),dict(providers=['bad']),dict(allowed_kinds=['bad']),dict(gates={})]:
        with pytest.raises(ValueError):policy.save(dict(p,**change))
    for value in (math.nan,math.inf,True):
        q=deepcopy(p);q['gates']['work.score']={'min':value}
        with pytest.raises(ValueError):policy.save(q)
    for gate in ({'min':2,'max':1},{'bad':0},{}):
        q=deepcopy(p);q['gates']['work.score']=gate
        with pytest.raises(ValueError):policy.save(q)
    assert not policy.evaluate({},'unknown')['eligible']


def test_group_discovery_and_sidecar_change_detection(book,monkeypatch):
    p=Path(book['path']);parent=p.parent/'Parts';parent.mkdir();one=parent/'Dune - 001.m4b';two=parent/'Dune - 002.m4b';one.write_bytes(b'1');two.write_bytes(b'2')
    a=catalogue.add_book(title='001',path=str(one));b=catalogue.add_book(title='002',path=str(two))
    original=I.probe.probe_file
    def probe(path):
        x=original(path);x['tags']['track']='1/2' if '001' in path else '2/2';return x
    monkeypatch.setattr(I.probe,'probe_file',probe)
    side=parent/'d.metadata.json';side.write_text(json.dumps(dict(title='Dune',authors=[dict(name='Frank Herbert')],format_type='unabridged')))
    inv,metrics=I.discover(a)
    assert len(inv['files'])==2 and inv['primary_id']==a['id'] and {m['id'] for m in inv['members']}=={a['id'],b['id']}
    assert len(inv['representations'])==1 and inv['representations'][0]['missing_tracks']==0
    assert I.snapshot_unchanged(inv)
    side.write_text('{}');assert not I.snapshot_unchanged(inv)
    side.unlink();assert not I.snapshot_unchanged(inv)
    inv['sidecars']=[];three=parent/'003.mp3';three.write_bytes(b'3');assert not I.snapshot_unchanged(inv)


@pytest.mark.parametrize('entry', ['folder','chapter'])
@pytest.mark.parametrize('numbers,missing', [([1,2,3],0),([1,3],1)])
def test_numbered_chapters_without_track_tags_share_a_timeline(book,monkeypatch,entry,numbers,missing):
    folder=Path(book['path']).parent/'Anniversary';folder.mkdir()
    paths=[folder/f'{n:03} American Gods 10th Anniversary.mp3' for n in numbers]
    for path in paths:path.write_bytes(b'chapter')
    original=I.probe.probe_file
    def probe(path):
        info=original(path);info['tags'].pop('track');return info
    monkeypatch.setattr(I.probe,'probe_file',probe)
    b=catalogue.add_book(title='American Gods',path=str(folder if entry=='folder' else paths[0]))
    inv,_=I.discover(b)
    assert inv['root']==str(folder) and len(inv['files'])==len(numbers)
    assert len(inv['representations'])==1
    rep=inv['representations'][0]
    assert rep['duration']==3600*len(numbers) and rep['files']==[str(p) for p in paths]
    assert rep['missing_tracks']==missing and rep['duplicate_tracks']==0


def test_numbered_filenames_do_not_combine_distinct_titles_or_whole_copies(book,monkeypatch):
    folder=Path(book['path']).parent/'Collection';folder.mkdir()
    names=['001 Dune.mp3','002 Dune.mp3','Dune.m4b','001 Dune Messiah.mp3','002 Dune Messiah.mp3','1984 Novel.mp3','1985 Novel.mp3','001 Lone.mp3']
    for name in names:(folder/name).write_bytes(b'audio')
    original=I.probe.probe_file
    def probe(path):
        info=original(path);info['tags'].pop('track');return info
    monkeypatch.setattr(I.probe,'probe_file',probe)
    b=catalogue.add_book(title='Collection',path=str(folder))
    inv,_=I.discover(b)
    assert sorted(len(r['files']) for r in inv['representations'])==[1,1,1,1,2,2]
    part=catalogue.add_book(title='Lone',path=str(folder/'001 Lone.mp3'))
    # Distinct sibling titles prevent expanding a loose file to this folder.
    single,_=I.discover(part)
    assert single['root']==part['path'] and len(single['files'])==1


@pytest.mark.parametrize('style',['total','counter','bytes'])
def test_reingesting_one_legacy_fragment_reunites_members_without_reusing_approvals(book,monkeypatch,style):
    from app.ingest import grouping as G
    root=Path(book['path']).parent/'Example';root.mkdir();count=5 if style=='bytes' else 3
    names=[f'Writer Person - Example {n:02} of 3.mp3' if style=='total' else f'1 ({n}).mp3' if style=='counter' else f'Example {n}.mp3' for n in range(1,count+1)]
    if style=='counter':names.append('Example by Writer Person - Listen Online.mp3')
    paths=[root/name for name in names]
    for i,path in enumerate(paths):path.write_bytes(bytes([65+i])*(6000 if style=='bytes' and i==count-1 else 8192))
    before={str(p):p.read_bytes() for p in paths}
    parent=catalogue.add_book(title='Example',path=str(root));aliases=[];old=[];measured=[]
    original=I.probe.probe_file
    def probe(path):
        info=original(path);info.update(tags={},codec='mp3',duration=999)
        return info
    monkeypatch.setattr(I.probe,'probe_file',probe)
    monkeypatch.setattr(I.probe,'decoded_duration',lambda path:(measured.append(path) or dict(duration=10,decode_messages='')))
    for path in paths:
        alias=catalogue.add_book(title='Old fragment',path=str(path));aliases.append(alias)
        catalogue.set_extra(alias['id'],'source_group',dict(version=G.VERSION-1,root=str(root),paths=[str(path)],scope_paths=[str(p) for p in paths]))
        catalogue.set_extra(alias['id'],'source_group_ref',dict(primary_id=alias['id']))
        rid=store.create(alias['id'],{});store.event(rid,'metadata_approval','done',data=dict(signature='old fragment consent',automatic=False));store.finish(rid,'metadata_review');old.append(rid)
    # A batch of all old entries still analyses the repaired work exactly once.
    runs=P.run([a['id'] for a in aliases]+[parent['id']],limit=len(aliases)+1,online=False)
    assert len(runs)==1 and runs[0]['state']=='metadata_review'
    run=runs[0];inv=P.stage_data(run,'inventory')
    assert inv['primary_id']==parent['id'] and {m['id'] for m in inv['members']}=={parent['id'],*(a['id'] for a in aliases)}
    assert {f['path'] for f in inv['files']}==set(before)
    assert sorted(len(r['files']) for r in inv['representations'])==([1,3] if style=='counter' else [count])
    assert all(store.latest_id(a['id'])==run['id'] for a in aliases)
    assert all(not P.approval_valid(run,phase) for phase in P.REVIEW_STAGES)
    assert all(store.get(rid)['state']=='metadata_review' for rid in old)
    assert all(catalogue.get_extras(a['id'])['source_group_ref']['data']['primary_id']==parent['id'] for a in aliases)
    if style=='bytes':
        assert set(measured)==set(before) and inv['representations'][0]['duration']==50
        assert all(f['probe']['reported_duration']==999 and f['probe']['duration_method']=='decoded audio clock' for f in inv['files'])
    else:assert measured==[]
    assert {str(p):p.read_bytes() for p in paths}==before


def test_split_mp3_duration_failure_keeps_files_but_blocks_automatic_book_consent(book,monkeypatch):
    root=Path(book['path']).parent/'Example';root.mkdir()
    for n,size in enumerate((8192,8192,8192,6000),1):(root/f'Example {n}.mp3').write_bytes(b'a'*size)
    original=I.probe.probe_file
    def probe(path):
        info=original(path);info.update(tags={},codec='mp3');return info
    monkeypatch.setattr(I.probe,'probe_file',probe)
    def fail(path):raise I.probe.ToolError('Cannot establish decoded timeline')
    monkeypatch.setattr(I.probe,'decoded_duration',fail)
    selected=catalogue.add_book(title='Example',path=str(root))
    inv,metrics=I.discover(selected)
    assert len(inv['files'])==4 and metrics['inventory.probe_errors']==4
    assert all('Cannot establish decoded timeline' in e['error'] for e in inv['errors'])


def test_input_change_during_split_duration_measurement_is_reported(book,monkeypatch):
    root=Path(book['path']).parent/'Example';root.mkdir()
    for n,size in enumerate((8192,8192,8192,6000),1):(root/f'Example {n}.mp3').write_bytes(b'a'*size)
    original=I.probe.probe_file
    def probe(path):
        info=original(path);info.update(tags={},codec='mp3');return info
    def measure(path):
        p=Path(path);p.write_bytes(p.read_bytes()+b'changed')
        return dict(duration=10,decode_messages='')
    monkeypatch.setattr(I.probe,'probe_file',probe);monkeypatch.setattr(I.probe,'decoded_duration',measure)
    selected=catalogue.add_book(title='Example',path=str(root))
    inv,metrics=I.discover(selected)
    assert metrics['inventory.probe_errors']==4
    assert all(e['error']=='input changed during duration measurement' for e in inv['errors'])
    assert not I.snapshot_unchanged(inv)


def test_anniversary_metadata_selects_the_complete_recording_and_links_the_work(book,monkeypatch,tmp_path):
    folder=tmp_path/'American Gods';folder.mkdir()
    for n in range(1,4):(folder/f'{n:03} American Gods 10th Anniversary.mp3').write_bytes(b'chapter')
    def probe(path):return dict(path=path,duration=2400,codec='mp3',channels=1,sample_rate=24000,bit_rate=64,chapters=[],tags=dict(album='American Gods 10th Anniversary Edition',artist='Neil Gaiman'))
    monkeypatch.setattr(I.probe,'probe_file',probe)
    b=catalogue.add_book(title='American Gods 10th Anniversary',author='Neil Gaiman',path=str(folder))
    inv,_=I.discover(b);identity,im=I.recover(b,inv);calls=[]
    full=candidate('audible',id='B007ZT4U46',asin='B007ZT4U46',title='American Gods: The Tenth Anniversary Edition (A Full Cast Production)',authors=['Neil Gaiman'],narrators=['Neil Gaiman','Dennis Boutskiaris','Sarah Jones'],runtime_minutes=120,language='english',abridged=False,release_date='2012-04-12')
    sparse=candidate('audiobookdb',title='American Gods',subtitle='The Tenth Anniversary Edition',authors=['Neil Gaiman'])
    work=candidate('openlibrary',title='American Gods',authors=['Neil Gaiman'],original_year=2001)
    class Fake:
        def __init__(self,name):self.name=name
        def unavailable(self):return None
        def by_asin(self,asin):
            calls.append((self.name,asin));return dict(full,source=self.name) if self.name=='audnexus' else None
        def search(self,title,author):
            if self.name=='openlibrary':return [work]
            if self.name=='audiobookdb':return [sparse]
            if self.name=='applebooks':
                assert title=='American Gods'
                return [candidate('applebooks',id='other',title='American Gods',authors=['Neil Gaiman'],release_date='2018-05-16'),candidate('applebooks',id='matching',title='American Gods',authors=['Neil Gaiman'],release_date='2012-04-12T07:00:00Z')]
            return [dict(full,source=self.name)]
        def details(self,c):
            assert c['id']=='matching';return dict(full,source='applebooks',title='American Gods',narrators=['Neil Gaiman','Dennis Boutsikaris','Sarah Jones'])
    monkeypatch.setattr(providers,'get',lambda n:Fake(n))
    data,_=M.collect(identity,inv,tmp_path/'providers',['audible','audnexus','audiobookdb','openlibrary','applebooks'])
    result,mm=M.resolve(identity,inv,data)
    assert ('audnexus','B007ZT4U46') in calls
    assert result['recording']['asin']=='B007ZT4U46' and result['resolved']['year']==2001
    assert mm['metadata.original_work_link']==1 and mm['metadata.work_families']==4
    assert mm['recording.runtime_error']==0 and mm['recording.narrator_agrees']==1 and mm['recording.language_agrees']==1
    assert result['records']['applebooks']['narrators']==['Neil Gaiman','Dennis Boutsikaris','Sarah Jones']
    evaluation=policy.evaluate({'inventory.files':3,'inventory.probe_errors':0,**im,**mm},'book')
    assert all(c['passed'] for c in evaluation['checks'] if c['metric'].startswith(('inventory.','identity.','metadata.','recording.')))
    # A shorter title cannot hide a genuinely different performance.
    data['providers']['applebooks']['details']['narrators']=['Different Reader']
    other,changed=M.resolve(identity,inv,data)
    assert changed['recording.ambiguous']==1 and changed['recording.narrator_agrees'] is None
    assert 'applebooks' not in other['performance']['records']
    assert 'applebooks' in other['work']['records']


@pytest.mark.parametrize('actual,expected', [('Dennis Boutsikaris',True),('Dennis Boutskiaris',True),('Dennis Boutsakaris',False),('David Boutsikaris',False),('Dennis Boutsiarik',False),('D Boutsikaris',False),('',False)])
def test_cast_name_comparison_accepts_only_an_adjacent_surname_transposition(actual,expected):
    reference=M.names(['Dennis Boutskiaris'])
    assert (M.narrator_names([actual],reference)==reference)==expected
    assert M.narrator_names(['Neil Gaiman'],reference)!=reference
    assert M.narrator_names(['Neil Gaiman'],set())==M.names(['Neil Gaiman'])
    assert M.narrator_names(['Amy Smthi'],M.names(['Amy Smith']))!=M.names(['Amy Smith'])
    # An ambiguous spelling cannot silently choose one of two credited people.
    both=M.names(['Dennis Boutskiaris','Dennis Boutsikaris'])
    assert M.narrator_names(['Dennis Boutsikaris'],both)==M.names(['Dennis Boutsikaris'])


def test_work_name_variants_accept_shared_authors_and_reject_ambiguous_identity():
    book=dict(title='All That Remains',author='Sue Black')
    apple=candidate('applebooks',title=book['title'],authors=['Professor Dame Sue Black'])
    assert M.compatible_work(book,apple)
    assert M.compatible_work(dict(book,author='Susan Brown'),dict(apple,authors=['Sue Brown']))
    assert not M.compatible_work(book,dict(apple,authors=['Susan Brown']))
    assert M.compatible_work(book,dict(apple,authors=['Sue Black','Another Writer']))
    assert M.compatible_work(book,dict(apple,authors=['Sue Black','Susan Black']))
    assert not M.compatible_work(dict(book,author='Sue Brown, Susan Brown'),dict(apple,authors=['S. Brown']))
    assert M.narrator_names(['S. Brown'],M.names(['Sue Brown','Susan Brown']))==M.names(['S. Brown'])


def test_inventory_errors_and_identity_conflicts(book,monkeypatch):
    inv,_=I.discover(book);identity,metrics=I.recover(book,inv)
    assert identity['book']['narrator']=='Reader' and identity['abridged'] is False and metrics['identity.conflicts']==0
    inv['files'].append(deepcopy(inv['files'][0]));inv['files'][1]['probe']['tags'].update(album='Other',artist='Other author',language='French',audible_asin='B000000002')
    inv['files'][0]['probe']['tags']['audible_asin']='B000000001'
    assert I.recover(book,inv)[1]['identity.conflicts']>=3
    monkeypatch.setattr(I.probe,'probe_file',lambda p:(_ for _ in ()).throw(ValueError('invalid')))
    inv,metrics=I.discover(book);assert metrics['inventory.probe_errors']==1
    Path(book['path']).unlink()
    with pytest.raises(ValueError,match='unavailable'):I.discover(book)


def test_metadata_collection_archives_errors_and_candidate_rejections(book,monkeypatch,tmp_path):
    inv,_=I.discover(book);identity,_=I.recover(book,inv)
    class Fake:
        def __init__(self,name):self.name=name
        def unavailable(self):return 'no key' if self.name=='googlebooks' else None
        def by_asin(self,a):return None
        def search(self,t,a):
            from app.ingest.observe import emit
            emit(source=self.name,raw=dict(unknown='retained'))
            if self.name=='audnexus':raise providers.MatchError('down')
            return [recording(self.name)]
        def details(self,c):return recording('applebooks')
    monkeypatch.setattr(providers,'get',lambda n:Fake(n))
    data,metrics=M.collect(identity,inv,tmp_path/'raw',['audible','audnexus','googlebooks','applebooks'])
    assert metrics['metadata.provider_errors']==1 and data['providers']['googlebooks']['status']=='unavailable'
    assert len(data['archives'])==3
    assert json.load(open(data['archives'][0]))['raw']==dict(unknown='retained')
    data['providers']['audible']['candidates'].append(candidate('audible',title='Other',authors=['Wrong'],runtime_minutes=90,narrators=['Other']))
    resolved,metrics=M.resolve(identity,inv,data)
    assert resolved['recording']['title']=='Dune'
    assert resolved['rejected']
    assert metrics['recording.runtime_error']==0


def test_metadata_rejects_language_narrator_and_work_conflicts(book):
    inv,_=I.discover(book);identity,_=I.recover(book,inv);r=recording();r['narrators']=['Wrong'];r['language']='French';r['abridged']=True
    data=dict(providers=dict(audible=dict(candidates=[r]),hardcover=dict(candidates=[candidate('hardcover',title='Dune',authors=['Other'])])))
    resolved,metrics=M.resolve(identity,inv,data)
    assert metrics['recording.narrator_agrees']==0 and metrics['recording.language_agrees']==0 and metrics['recording.abridgement_known']==0
    assert metrics['metadata.critical_conflicts']>=2 and metrics['metadata.original_work_link']==0
    assert resolved['rejected']


@pytest.mark.parametrize('tags,expected', [
    ({'WOAS':'http://www.audible.com/pd/B01MU5XTWY'}, {'B01MU5XTWY'}),
    ({'url':'https://www.audible.co.uk/pd/A-Book/B000000001?x=1'}, {'B000000001'}),
    ({'audible_asin':'b000000001','asin':'B000000002'}, {'B000000001','B000000002'}),
    ({'woas':'https://audible.com.evil.test/pd/B000000001'}, set()),
    ({'woas':'https://evil.test/audible.com/pd/B000000001'}, set()),
    ({'woas':'https://[invalid/pd/B000000001','asin':'invalid','title':'B000000001','url':None}, set()),
])
def test_embedded_product_identifiers_are_validated(tags,expected):
    assert I.embedded_asins(tags)==expected


def test_recovered_product_link_and_narrator_keep_original_evidence(book):
    inv,_=I.discover(book);tags=inv['files'][0]['probe']['tags']
    tags.update(woas='http://www.audible.com/pd/B01MU5XTWY',artist='Writer, Helper - foreword',composer='Composer',narratedby='Actual Reader')
    identity,metrics=I.recover(dict(book,locked=[]),inv)
    assert identity['book']['asin']=='B01MU5XTWY' and identity['book']['narrator']=='Actual Reader'
    assert identity['contributors']==[dict(name='Writer',role='author'),dict(name='Helper',role='foreword')]
    assert identity['evidence'][0]['tags']['artist']=='Writer, Helper - foreword'
    conflict,m=I.recover(dict(book,asin='B000000001',locked=['asin']),inv)
    assert conflict['book']['asin']=='B000000001' and m['identity.conflicts']==1


def test_missing_provider_fields_are_unknown_instead_of_conflicts(book):
    inv,_=I.discover(book);identity,_=I.recover(book,inv)
    sparse=candidate('audiobookdb',title='Dune',authors=['Frank Herbert'])
    _,metrics=M.resolve(identity,inv,dict(providers=dict(audiobookdb=dict(candidates=[sparse]))))
    assert metrics['metadata.critical_conflicts']==0
    assert metrics['recording.narrator_agrees'] is None and metrics['recording.language_agrees'] is None


def test_unknown_local_language_requires_corroboration_from_distinct_families(book):
    inv,_=I.discover(book);identity,_=I.recover(book,inv);identity['language']='';identity['book']['narrator']=''
    data=dict(providers=dict(audible=dict(candidates=[recording('audible')]),audnexus=dict(candidates=[recording('audnexus')])))
    _,metrics=M.resolve(identity,inv,data)
    assert metrics['recording.language_agrees'] is None and metrics['recording.narrator_agrees'] is None
    data['providers']['applebooks']=dict(candidates=[dict(recording('applebooks'),language='en-GB')])
    value,metrics=M.resolve(identity,inv,data)
    assert metrics['recording.language_agrees']==1 and metrics['recording.narrator_agrees']==1
    assert metrics['metadata.critical_conflicts']==0
    assert set(value['corroboration']['language'])=={'audible','audnexus','applebooks'}


def test_recording_identity_links_work_metadata_with_short_titles_and_roles(book,monkeypatch,tmp_path):
    inv,_=I.discover(book)
    long_title='A Man for All Markets From Las Vegas to Wall Street, How I Beat the Dealer and the Market'
    inv['files'][0]['probe']['tags'].update(album=long_title,artist='Edward O. Thorp, Nassim Nicholas Taleb - foreword',narratedby='Edward O. Thorp',woas='http://www.audible.com/pd/B01MU5XTWY')
    identity,_=I.recover(dict(book,locked=[]),inv);calls=[]
    audio_record=candidate('audnexus',title='A Man for All Markets',subtitle='From Las Vegas to Wall Street, How I Beat the Dealer and the Market',authors=['Edward O. Thorp','Nassim Nicholas Taleb - introduction'],narrators=['Edward O. Thorp'],runtime_minutes=60,language='english',abridged=False,asin='B01MU5XTWY')
    work=candidate('openlibrary',title='A Man for All Markets',authors=['Edward O. Thorp'],original_year=2017,id='/works/OL1W')
    class Fake:
        def __init__(self,name):self.name=name
        def unavailable(self):return None
        def by_asin(self,asin):
            calls.append((self.name,'asin',asin));return audio_record if self.name=='audnexus' else None
        def search(self,title,author):
            calls.append((self.name,'search',title,author))
            return [work] if self.name=='openlibrary' else [dict(audio_record,source='applebooks',title='A Man for All Markets: From Las Vegas to Wall Street, How I Beat the Dealer and the Market (Unabridged)')]
        def details(self,c):return c
    monkeypatch.setattr(providers,'get',lambda name:Fake(name))
    collected,_=M.collect(identity,inv,tmp_path,['audnexus','openlibrary','applebooks'])
    assert ('audnexus','asin','B01MU5XTWY') in calls
    assert ('openlibrary','search','A Man for All Markets','Edward O. Thorp') in calls
    result,metrics=M.resolve(identity,inv,collected)
    assert result['resolved']['year']==2017 and result['resolved']['year_source']=='Open Library (first published)'
    assert result['resolved']['authors']==['Edward O. Thorp']
    assert metrics['recording.score']==1 and metrics['metadata.critical_conflicts']==0 and metrics['metadata.original_work_link']==1
    wrong=dict(work,authors=['Edward O. Thorp','Unknown Coauthor'])
    assert M.compatible_work(dict(identity['book'],title=work['title']),wrong)


def test_audio_aligns_copies_and_selects_safe_complete_representation(book,monkeypatch,tmp_path):
    inv,_=I.discover(book);rep=inv['representations'][0];other=dict(rep,id='other',files=['second.mp3']);inv['representations'].append(other)
    inv['files'].append(dict(inv['files'][0],path='second.mp3'))
    values=array('f',(-40+random.Random(i).random()*30 for i in range(1000)))
    def extract(path,target):return values,dict(sha256=inv['files'][0]['sha256'],decode_messages='')
    monkeypatch.setattr(A:=audio.A,'extract',extract)
    aligned,metrics=audio.alignment(inv,tmp_path/'fingerprints');assert metrics['alignment.coverage']==1
    monkeypatch.setattr(audio.probe,'measure_book',lambda *a:dict(I=-20,TP=-1,LRA=5,silences=[]))
    monkeypatch.setattr(audio,'band_sample',lambda *a:-20)
    monkeypatch.setattr(audio.acoustics,'assess',lambda *a: {'medians': {}, 'samples': []})
    quality,metrics=audio.quality(inv,aligned);assert metrics['quality.complete']==1
    quality['representations'][0]['measurements']['TP']=1
    selected,metrics=audio.select(inv,aligned,quality)
    assert selected['selected_id']=='other' and metrics['quality.true_peak']==-1
    quality['representations'][0]['measurements']['TP']=-1
    assert audio.select(inv,aligned,quality)[1]['selection.ambiguous']==1


def test_worker_and_cli_commands(pipeline_fakes,monkeypatch,capsys):
    from app import cli
    w=Worker();w.inline=True;w.submit('run',ids=[pipeline_fakes['id']]);assert w.status()['busy'] is None
    rid=w.last['run'][0]
    for argv in [['ingest-status'],['ingest-policy'],['ingest-show',rid],['ingest-evaluate',rid]]:
        assert cli.main(argv)==0;assert json.loads(capsys.readouterr().out)
    assert cli.main(['ingest-refresh',rid])==0
    fresh=json.loads(capsys.readouterr().out);assert fresh!=rid
    assert cli.main(['ingest-approve',fresh,'metadata'])==0
    assert json.loads(capsys.readouterr().out)['state']=='audiobook_review'
    assert cli.main(['ingest-approve',fresh,'audiobook'])==0
    assert json.loads(capsys.readouterr().out)['state']=='quality_review'
    assert cli.main(['ingest-approve',fresh,'quality'])==0
    assert json.loads(capsys.readouterr().out)['state']=='eligible'
    assert catalogue.get_plan(pipeline_fakes['id'])['state']=='proposed'
    w._do('bad',{});assert 'error' in w.last['bad']
    with pytest.raises(SystemExit):cli.main(['ingest-show','bad'])


def test_plan_claim_is_atomic(book):
    catalogue.set_plan(book['id'],'approved',dict(x=1))
    assert catalogue.claim_plan(book['id']) and not catalogue.claim_plan(book['id'])


def test_band_measurements_and_failure_are_observable(monkeypatch):
    levels=iter([-20,-40])
    monkeypatch.setattr(audio.probe,'run',lambda *a,**kw:SimpleNamespace(returncode=0,stderr='Overall\nRMS level dB: '+str(next(levels))))
    assert audio.band_sample([('file',0,30)])==-20
    monkeypatch.setattr(audio.probe,'run',lambda *a,**kw:SimpleNamespace(returncode=1,stderr='error'))
    assert audio.band_sample([('file',0,30)]) is None


def test_single_source_nonfinite_quality_stays_unknown(book,monkeypatch):
    inv,_=I.discover(book);rep=dict(inv['representations'][0],coverage=1,matched_fraction=1,max_gap_seconds=0,decode_messages=[])
    aligned=dict(representations=[rep]);monkeypatch.setattr(audio.probe,'measure_book',lambda *a:dict(I=-math.inf,TP=-math.inf,LRA=0,silences=[]))
    monkeypatch.setattr(audio,'band_sample',lambda *a:None)
    data,m=audio.quality(inv,aligned);assert m['quality.complete']==0 and m['quality.true_peak'] is None
    selection,m=audio.select(inv,aligned,data);assert m['selection.ambiguous']==1 and m['quality.complete']==0
    json.dumps(data,allow_nan=False)


def test_full_copy_can_be_selected_over_verified_partial(book,monkeypatch,tmp_path):
    inv,_=I.discover(book);rep=inv['representations'][0]
    full=dict(rep,coverage=1,matched_fraction=1,max_gap_seconds=0,decode_messages=[])
    part=dict(full,id='part',files=['part.mp3'],coverage=.1,max_gap_seconds=100,missing_tracks=2)
    quality=dict(representations=[dict(id=r['id'],measurements=dict(I=-20,TP=-1,LRA=5),band4k_relative_db=-20,decode_errors=0,phase_loss_db=0) for r in [full,part]])
    selected,m=audio.select(inv,dict(representations=[full,part]),quality)
    assert selected['selected_id']==full['id'] and m['alignment.coverage']==1 and m['alignment.missing_tracks']==0


def test_raw_response_observer_captures_retries_cache_and_text_without_auth(fake_urlopen,monkeypatch):
    from app.providers.base import Http,cached
    from fakes import http_error
    events=[];monkeypatch.setattr('app.providers.base.time.sleep',lambda x:None)
    fake_urlopen.reply(http_error(429,{'limited':True}),{'unknown':'keep'},'<p>public page</p>')
    with capture(events.append):
        http=Http('test',0)
        assert cached('test',lambda:http.request('https://example.test',headers={'Authorization':'secret'}))=={'unknown':'keep'}
        assert cached('test',lambda:pytest.fail('cache miss'))=={'unknown':'keep'}
        assert http.request('https://example.test/page',text=True)=='<p>public page</p>'
    assert events[0]['status']==429 and events[2]['cached'] is True
    assert 'secret' not in json.dumps(events)


def test_apple_and_google_adapters_preserve_detail_fields(fake_urlopen,monkeypatch):
    from app.providers.applebooks import AppleBooks
    from app.providers.googlebooks import GoogleBooks
    apple=AppleBooks();monkeypatch.setattr('app.providers.base.time.sleep',lambda n:None)
    html='<div>NARRATOR</div><div>R</div><div>Reader</div><div>LANGUAGE</div><div>EN</div><div>English</div><div>LENGTH</div><div>01:00</div><div>RELEASED</div><script>ignore</script>'
    fake_urlopen.reply({'results':[dict(wrapperType='audiobook',collectionId=1,collectionName='Dune (Unabridged)',artistName='Frank Herbert',collectionViewUrl='https://books.apple.com/gb/audiobook/id1')]},html)
    c=apple.search('Dune','Frank Herbert')[0];d=apple.details(c)
    assert d['narrators']==['Reader'] and d['runtime_minutes']==60 and d['abridged'] is False
    with pytest.raises(ValueError):apple.details(dict(id='x',url='https://evil.example'))
    google=GoogleBooks();assert google.unavailable();monkeypatch.setenv('APP_GOOGLE_BOOKS_KEY','secret')
    fake_urlopen.reply({'items':[dict(id='x',volumeInfo=dict(title='Dune',authors=['Frank Herbert'],industryIdentifiers=[dict(identifier='123')],averageRating=4.5,ratingsCount=10))]})
    c=google.search('Dune','Frank Herbert')[0];assert c['rating']==4.5 and c['isbns']==['123']
    assert 'secret' not in fake_urlopen.calls[-1]['url']


@pytest.mark.parametrize('encoded',[False,True])
def test_apple_full_cast_comes_from_the_matching_structured_product(fake_urlopen,monkeypatch,encoded):
    from app.providers.applebooks import AppleBooks
    monkeypatch.setattr('app.providers.base.time.sleep',lambda n:None)
    product=dict(id='1',attributes=dict(narratorNames='Neil Gaiman, Dennis Boutsikaris & Sarah Jones'))
    data=dict(d=[dict(id='other',attributes=dict(narratorNames='Unrelated Reader')),dict(id='1',attributes={}),product])
    payload=json.dumps(dict(cache=json.dumps(data) if encoded else data))
    html='<div>NARRATOR</div><div>SJ</div><div>Sarah Jones</div><div>LANGUAGE</div><div>EN</div><div>English</div><div>LENGTH</div><div>19:36</div><div>RELEASED</div>'
    html+='<script id="shoebox-media-api-cache-amp-books">bad json</script>'
    html+=f'<script id="shoebox-media-api-cache-amp-books">{payload}</script>'
    fake_urlopen.reply(html)
    d=AppleBooks().details(dict(id='1',title='American Gods',url='https://books.apple.com/gb/audiobook/id1'))
    assert d['narrators']==['Neil Gaiman','Dennis Boutsikaris','Sarah Jones']
    assert d['runtime_minutes']==1176 and d['language']=='english'


def test_sidecar_recovery_locked_fields_and_radio_classification(book):
    inv,_=I.discover(book);inv['sidecars']=[dict(raw=dict(title='Dune',authors=[dict(name='Frank Herbert')],narrators=[dict(name='Reader')],asin='B000000001',format_type='abridged'))]
    b=dict(book,title='My title',locked=['title']);out,m=I.recover(b,inv)
    assert out['book']['title']=='My title' and out['book']['asin']=='B000000001' and m['identity.conflicts']==1
    inv['files'][0]['probe']['tags'].update(genre='Radio Comedy',album='Unabridged 06:16',title='Episode title',artist='Name/Name')
    assert I.recover(book,inv)[0]['kind']=='radio'
    inv['files'][0]['probe']['tags']['genre']='';inv['files'][0]['probe']['tags']['album']='Ep. 3: III';inv['sidecars']=[]
    assert I.recover(book,inv)[0]['kind']=='episode'
    inv['files'][0]['probe']['tags']['album']='Stand-up Live'
    assert I.recover(book,inv)[0]['kind']=='standup'


def test_auto_queue_failure_is_persisted_and_processed_groups_not_overwritten(pipeline_fakes,monkeypatch):
    p=policy.current();p['auto_approve']=True;policy.save(p)
    catalogue.set_processed(pipeline_fakes['id'],dict(status='done',finished='2026-10-31'))
    r=complete_run([pipeline_fakes['id']])[0];assert r['state']=='failed'
    assert r['events'][-1]['stage']=='queue' and 'already processed' in r['events'][-1]['error']


def test_ignored_and_nonbook_content_cannot_auto_process(pipeline_fakes,monkeypatch):
    catalogue.ignore(pipeline_fakes['id']);r=complete_run([pipeline_fakes['id']])[0]
    assert not P.evaluate(r['id'])['eligible']
    inv=P.stage_data(r,'inventory');identity=P.stage_data(r,'identity');identity['kind']='radio'
    data,metrics=P._plan(pipeline_fakes,inv,P.stage_data(r,'selection'),P.stage_data(r,'metadata'),identity,r['id'])
    assert data['plan'] is None and metrics['planning.warning_count'] is None


def test_cli_policy_files_resume_and_explicit_ids(pipeline_fakes,monkeypatch,capsys,tmp_path):
    from app import cli
    assert cli.main(['ingest-run',str(pipeline_fakes['id']),'--offline'])==0
    rid=json.loads(capsys.readouterr().out)[0]
    p=tmp_path/'policy.json';p.write_text(json.dumps(policy.current()))
    assert cli.main(['ingest-policy',str(p)])==0;capsys.readouterr()
    assert cli.main(['ingest-evaluate',rid,'--policy',str(p)])==0;capsys.readouterr()
    with pytest.raises(SystemExit):cli.main(['ingest-resume',rid])
    w=Worker();w.inline=True;w.submit('resume',run_id=rid);assert 'error' in w.last['resume']


def test_legacy_proposal_path_uses_ingest_selection_and_blocks_group_duplicates(pipeline_fakes):
    r=complete_run([pipeline_fakes['id']])[0]
    assert P.Q._plan_one(pipeline_fakes,'/final')['source_paths']==[pipeline_fakes['path']]
    other=catalogue.add_book(title='Other',path='/different')
    store.link(r['id'],[other['id']])
    with pytest.raises(P.Q.probe.ToolError,match='belongs'):P.Q._plan_one(other,'/final')
    store.finish(r['id'],'running')
    with pytest.raises(P.Q.probe.ToolError,match='no completed'):P.Q._plan_one(pipeline_fakes,'/final')
    store.finish(r['id'],'eligible');Path(pipeline_fakes['path']).write_bytes(b'changed')
    with pytest.raises(P.Q.probe.ToolError,match='changed'):P.Q._plan_one(pipeline_fakes,'/final')


def test_new_sidecar_invalidates_single_file_snapshot(book):
    inv,_=I.discover(book);assert I.snapshot_unchanged(inv)
    Path(book['path']).with_suffix('.metadata.json').write_text('{}')
    assert not I.snapshot_unchanged(inv)


def test_batch_coalesces_duplicates_and_honours_limit(pipeline_fakes):
    other=catalogue.add_book(title='Later',path='/not-visited')
    assert len(complete_run([pipeline_fakes['id'],pipeline_fakes['id']],limit=2))==1
    assert len(complete_run([pipeline_fakes['id'],other['id']],limit=1))==1


def test_explicit_library_batch_visits_all_selected_groups(monkeypatch):
    ids=list(range(1,1682));visited=[]
    monkeypatch.setattr(catalogue,'get_book',lambda bid:dict(id=bid))
    def run_one(bid,online):
        assert not online
        visited.append(bid)
        # Selecting a sibling later in the batch must not analyse its group twice.
        members=[dict(id=bid)]+([dict(id=ids[-1])] if bid==ids[0] else [])
        return dict(events=[dict(stage='inventory',status='done',data=dict(members=members))])
    monkeypatch.setattr(P,'run_one',run_one)
    assert len(P.run(ids,limit=len(ids),online=False))==1680
    assert visited==ids[:-1]
    with pytest.raises(ValueError,match='limit must be 1..200'):complete_run(limit=201)
    with pytest.raises(ValueError,match='limit must be 1..200'):complete_run([1],limit=201)


def test_atomic_reservation_never_overwrites_queue_or_processed_book(book):
    catalogue.enqueue_ingest_plan(book['id'],[book['id']],dict(x=1))
    with pytest.raises(catalogue.CatalogueError,match='already has'):catalogue.enqueue_ingest_plan(book['id'],[book['id']],dict(x=2))
    assert catalogue.get_plan(book['id'])['plan']==dict(x=1)
    catalogue.delete_plan(book['id']);catalogue.set_processed(book['id'],dict(status='done',finished='now'))
    with pytest.raises(catalogue.CatalogueError,match='already processed'):catalogue.enqueue_ingest_plan(book['id'],[book['id']],{})


def test_policy_rejects_shapes_and_accepts_bounded_gate():
    p=policy.current()
    for bad in (None,[],dict(p,providers=[]),dict(p,providers='audible'),dict(p,allowed_kinds='book'),dict(p,auto_process='yes')):
        with pytest.raises(ValueError):policy.validate(bad)
    p['gates']['work.score']={'min':.9,'max':1};assert policy.save(p)['revision']==1
    metrics={name:(gate.get('min',gate.get('max',0))) for name,gate in p['gates'].items()}
    assert policy.evaluate(metrics,'book',p)['eligible']
    metrics['work.score']=float('nan');assert not policy.evaluate(metrics,'book',p)['eligible']


def test_detailed_work_and_sidecar_provenance_are_retained(book):
    inv,_=I.discover(book);inv['sidecars']=[dict(raw=dict(title='Dune',authors=[dict(name='Frank Herbert')],narrators=[dict(name='Reader')],runtime_length_min=60,asin='B000000001',format_type='unabridged'))]
    identity,_=I.recover(book,inv)
    work=candidate('hardcover',id='1',title='Dune',authors=['Frank Herbert'],original_year=1965)
    detail=dict(id='1',title='Dune',authors=['Frank Herbert'],release_date='1965-01-01',reviews=[dict(text='retained')])
    data=dict(providers=dict(hardcover=dict(candidates=[work],details=detail),audible=dict(candidates=[recording()])))
    result,m=M.resolve(identity,inv,data)
    assert result['community']['hardcover']['reviews'][0]['text']=='retained'
    assert result['records']['audible_file']['abridged'] is False
    assert result['records']['hardcover']['original_year']==1965


def test_provider_detail_failure_remains_visible(book,monkeypatch,tmp_path):
    inv,_=I.discover(book);identity,_=I.recover(book,inv)
    fake=SimpleNamespace(unavailable=lambda:None,search=lambda *a:[recording('applebooks')],details=lambda c:(_ for _ in ()).throw(providers.MatchError('page down')))
    monkeypatch.setattr(providers,'get',lambda n:fake)
    data,_=M.collect(identity,inv,tmp_path,['applebooks'])
    assert data['providers']['applebooks']['details_error']=='page down'


def test_actual_alignment_path_marks_unknown_copies_without_different_performer_claim(book,monkeypatch,tmp_path):
    inv,_=I.discover(book);first=inv['representations'][0];inv['representations'].append(dict(first,id='other',files=['other.mp3']))
    inv['files'].append(dict(inv['files'][0],path='other.mp3'))
    def extract(path,target):
        rng=random.Random(1 if 'other' not in path else 2)
        return array('f',[-30+rng.random()*20 for _ in range(1000)]),dict(sha256=inv['files'][0]['sha256'],decode_messages='decoder warning' if 'other' in path else '')
    monkeypatch.setattr(audio.A,'extract',extract)
    aligned,m=audio.alignment(inv,tmp_path)
    assert m['alignment.unresolved_representations']==1
    assert aligned['representations'][1]['matched_fraction']==0
    monkeypatch.setattr(audio.A,'extract',lambda *a:(array('f',[1]*100),dict(sha256='different',decode_messages='')))
    with pytest.raises(ValueError,match='changed'):audio.alignment(inv,tmp_path/'next')


def test_provider_archive_tampering_invalidates_frozen_plan(pipeline_fakes,tmp_path):
    r=complete_run([pipeline_fakes['id']])[0];inv=P.stage_data(r,'inventory');selection=P.stage_data(r,'selection');meta=P.stage_data(r,'metadata');identity=P.stage_data(r,'identity')
    raw=tmp_path/'source-response.json';raw.write_text('{"a":1}')
    data,_=P._plan(pipeline_fakes,inv,selection,meta,identity,r['id'],[str(raw)])
    assert P.Q._unchanged(pipeline_fakes,data['plan'])
    raw.write_text('{"b":2}')
    assert not P.Q._unchanged(pipeline_fakes,data['plan'])
    store.event(r['id'],'planning','done',data=data)
    with pytest.raises(ValueError,match='archived evidence'):P.enqueue(r['id'])


def test_selected_copy_plan_validates_all_alternatives(pipeline_fakes,tmp_path):
    r=complete_run([pipeline_fakes['id']])[0];inv=P.stage_data(r,'inventory');selection=P.stage_data(r,'selection')
    alternative=tmp_path/'alternative.mp3';alternative.write_bytes(b'original alternate recording')
    inv['root']=str(tmp_path)
    inv['files'].append(dict(path=str(alternative),size=alternative.stat().st_size,sha256=I.digest(alternative)))
    data,_=P._plan(pipeline_fakes,inv,selection,P.stage_data(r,'metadata'),P.stage_data(r,'identity'),r['id'])
    plan=data['plan']
    assert len(plan['ingest']['manifest'])==1 and len(plan['ingest']['inventory_manifest'])==2
    assert P.Q._unchanged(pipeline_fakes,plan)
    alternative.write_bytes(b'changed alternative')
    assert not P.Q._unchanged(pipeline_fakes,plan)


def test_identical_input_reuses_complete_quality_measurements(pipeline_fakes,monkeypatch):
    first=complete_run([pipeline_fakes['id']])[0];monkeypatch.setattr(audio,'quality',lambda *a:pytest.fail('must reuse complete quality evidence'))
    second=complete_run([pipeline_fakes['id']])[0]
    assert second['state']=='eligible'
    assert P.stage_data(second,'quality')['reused_from']==first['id']
    assert P.metrics(second)['quality.cache_hit']==1


def test_short_story_label_does_not_enter_book_recipe(book):
    inv,_=I.discover(book);inv['files'][0]['probe']['tags']['album']="Summer Farmer: A Story from White Man's Problems"
    assert I.recover(book,inv)[0]['kind']=='story'


def test_partial_in_mixed_folder_does_not_pull_in_unrelated_books(book,monkeypatch):
    folder=Path(book['path']).parent;part=folder/'Dune - 001.m4b';part.write_bytes(b'part');(folder/'Other book.mp3').write_bytes(b'other')
    b=catalogue.add_book(title='Dune part',path=str(part));original=I.probe.probe_file
    def probe(path):
        f=original(path);f['tags']['track']='1/10';return f
    monkeypatch.setattr(I.probe,'probe_file',probe)
    inv,_=I.discover(b)
    assert inv['root']==str(part) and len(inv['files'])==1


@pytest.mark.parametrize('unrelated_sibling',[True,False])
def test_file_track_total_cannot_recursively_inventory_a_library(book,monkeypatch,unrelated_sibling):
    folder=Path(book['path']).parent;selected=Path(book['path'])
    nested=folder/'Other Book';nested.mkdir();(nested/'01.mp3').write_bytes(b'unrelated audio');(nested/'other.metadata.json').write_text('invalid JSON must never be read')
    if unrelated_sibling:(folder/'Unrelated Title.m4b').write_bytes(b'unrelated sibling')
    original=I.probe.probe_file;seen=[]
    def probe(path):
        assert Path(path)==selected
        seen.append(path);info=original(path);info['tags']['track']='1/20';return info
    monkeypatch.setattr(I.probe,'probe_file',probe)
    monkeypatch.setattr(details,'_ffprobe',lambda path:dict(format=dict(tags={})))
    inv,metrics=I.discover(book)
    assert {f['path'] for f in inv['files']}=={str(selected)}
    assert not inv['errors'] and not inv['sidecars'] and metrics['inventory.files']==1
    assert set(seen)=={str(selected)}


@pytest.mark.parametrize('tag',['track','disc'])
def test_corrupt_uint32_sequence_totals_do_not_allocate_a_range(book,monkeypatch,tag):
    inv,_=I.discover(book);inv['files'][0]['probe']['tags']={tag:'1/4294967295'}
    def bounded_range(*args):
        values=range(*args)
        assert len(values)<=10000, 'untrusted sequence totals must not expand into a numeric range'
        return values
    monkeypatch.setattr(I,'range',bounded_range,raising=False)
    representations=I.represent(inv['files'])
    assert len(representations)==1 and representations[0]['missing_tracks']==4294967294


def test_missing_sequence_count_ignores_duplicates_zero_and_out_of_range_numbers(book):
    inv,_=I.discover(book);sample=inv['files'][0];files=[]
    for i,n in enumerate((0,1,1,3,5)):
        f=deepcopy(sample);f['path']=str(Path(book['path']).parent/f'part{i}.mp3');f['probe']['tags']=dict(album='Dune',track=f'{n}/4');files.append(f)
    representation=I.represent(files)[0]
    assert representation['missing_tracks']==2 and representation['duplicate_tracks']==1


def test_selected_byte_split_file_keeps_its_explicit_sibling_family(book,monkeypatch):
    root=Path(book['path']).parent/'Example';root.mkdir();paths=[]
    for n in range(1,5):
        p=root/f'Example {n}.mp3';p.write_bytes(bytes([n])*(6000 if n==4 else 8192));paths.append(p)
    original=I.probe.probe_file
    def probe(path):
        info=original(path);info.update(codec='mp3',tags={},duration=999);return info
    monkeypatch.setattr(I.probe,'probe_file',probe)
    monkeypatch.setattr(I.probe,'decoded_duration',lambda path:dict(duration=10,decode_messages=''))
    chosen=catalogue.add_book(title='Example',path=str(paths[0]))
    inv,_=I.discover(chosen)
    assert [f['path'] for f in inv['files']]==[str(p) for p in paths]
    assert inv['representations'][0]['duration']==40 and not inv['errors']


def test_whole_file_and_part_sequence_are_one_group(book):
    folder=Path(book['path']).parent/'Hitchens-Hitch 22';folder.mkdir();whole=folder/'Christopher Hitchens-Hitch-22.m4b';part=folder/'Hitch-22-Part05.mp3';whole.write_bytes(b'whole');part.write_bytes(b'part')
    b=catalogue.add_book(title='Hitch22 part',path=str(part));inv,_=I.discover(b)
    assert inv['root']==str(folder) and len(inv['files'])==2 and len(inv['representations'])==2


def test_complete_copy_discovers_related_format_and_parts_from_either_entry(book):
    folder=Path(book['path']).parent/'Hitchens-Hitch 22';folder.mkdir();whole=folder/'Christopher Hitchens-Hitch-22.m4b';part=folder/'Hitch-22-Part05.mp3';whole.write_bytes(b'whole');part.write_bytes(b'part')
    b=catalogue.add_book(title='Hitch22',path=str(whole));inv,_=I.discover(b)
    assert inv['root']==str(folder) and len(inv['representations'])==2


def test_quiet_passages_report_levels_and_missing_values(monkeypatch):
    responses = iter(['RMS level dB: -52.3', 'RMS level dB: -inf', 'RMS level dB: -46.1'])
    monkeypatch.setattr(audio.probe, 'run', lambda *a, **kw: SimpleNamespace(returncode=0, stderr=next(responses)))
    timeline = P.P.timeline([dict(path='file', duration=100)])
    result = audio.quiet_passages(timeline, [(9, 11), (49, 51), (89, 91)], 100)
    assert result['median_dbfs'] == -49.2
    assert [s['status'] for s in result['samples']] == ['measured', 'digital_silence', 'measured']
    assert [s['start_seconds'] for s in result['samples']] == [9.75, 49.75, 89.75]
    assert audio.quiet_passages(timeline, [], 100)['median_dbfs'] is None
    monkeypatch.setattr(audio.probe, 'run', lambda *a, **kw: SimpleNamespace(returncode=1, stderr='failed'))
    failed = audio.quiet_passages(timeline, [(0, 2)], 100)
    assert failed['median_dbfs'] is None and failed['samples'][0]['status'] == 'unavailable'
    json.dumps(result, allow_nan=False)


def test_book_and_performance_publishers_dates_and_checks_are_independent(book):
    inv,im=I.discover(book);identity,identity_metrics=I.recover(book,inv)
    audio_record=dict(recording(publisher='Audio Press'),release_date='2024-01-01')
    print_record=candidate('openlibrary',title='Dune',authors=['Frank Herbert'],original_year=1965,publisher='Book Press')
    data=dict(providers=dict(audible=dict(candidates=[audio_record]),openlibrary=dict(candidates=[print_record])))
    meta,mm=M.resolve(identity,inv,data)
    assert meta['work']['resolved']['publisher']=='Book Press'
    assert meta['work']['resolved']['year']==1965
    assert meta['resolved']['publisher']=='Audio Press' and meta['resolved']['year']==1965
    assert meta['performance']['matched']
    assert next(r for r in meta['performance']['rows'] if r['field']=='Release date')['value']=='2024-01-01'
    assert all(not any(k in record for k in ('narrators','release_date','release_year','asin')) for record in meta['work']['records'].values())
    audio_record.update(narrators=['Other Reader'],runtime_minutes=120)
    meta,mm=M.resolve(identity,inv,data)
    assert not meta['performance']['matched'] and meta['recording'] is None
    assert meta['recording_candidate']['narrators']==['Other Reader']
    assert meta['resolved']['narrators']==['Reader'] and not meta['resolved']['publisher']
    assert next(r for r in meta['performance']['rows'] if r['field']=='Publisher')['value']=='unknown'
    assert meta['work']['resolved']['publisher']=='Book Press'
    rid=store.create(book['id'],{})
    for stage,values,measure in [('inventory',inv,im),('identity',identity,identity_metrics),('providers',data,{}),('metadata',meta,mm)]:store.event(rid,stage,'done',data=values,metrics=measure)
    store.finish(rid,'metadata_review');review=P.review_evaluation(store.get(rid),'metadata')
    assert review['groups']['work']['eligible'] and not review['groups']['performance']['eligible']
    assert review['groups']['source']['eligible'] and review['eligible']
    value=policy.current();value['auto_approve_metadata']=True;policy.save(value)
    (Path(P.settings.data_dir())/'ingest').mkdir(exist_ok=True)
    assert P.approve_passing_metadata()==[rid]


def test_old_metadata_is_projected_without_rewriting_evidence_or_applying_a_bad_recording(book):
    inv,im=I.discover(book);identity,identity_metrics=I.recover(book,inv)
    data=dict(providers=dict(audible=dict(candidates=[dict(recording(),narrators=['Other'],runtime_minutes=120)]),openlibrary=dict(candidates=[candidate('openlibrary',title='Dune',authors=['Frank Herbert'],original_year=1965)])))
    old,mm=M._recording_evidence(identity,inv,data);rid=store.create(book['id'],{})
    for stage,values,measure in [('inventory',inv,im),('identity',identity,identity_metrics),('providers',data,{}),('metadata',old,mm)]:store.event(rid,stage,'done',data=values,metrics=measure)
    count=len(store.get(rid)['events']);meta=P.stage_data(store.get(rid),'metadata')
    assert meta['version']==M.VERSION and meta['resolved']['narrators']==['Reader']
    assert P.metrics(store.get(rid))['recording.matched']==0
    assert P.review_evaluation(store.get(rid),'metadata')['groups']['work']['eligible']
    assert len(store.get(rid)['events'])==count and 'version' not in store.get(rid)['events'][-1]['data']


def test_legacy_threshold_names_migrate_to_work_and_recording_scopes():
    p=policy.current();g=p['gates'];g['recording.score']=g.pop('work.score');g['metadata.critical_conflicts']=g.pop('recording.conflicts');g.pop('work.conflicts');g.pop('recording.matched')
    p=policy.validate(p)
    assert p['gates']['work.score']=={'min':.9} and p['gates']['recording.conflicts']=={'max':0}
    assert p['gates']['recording.matched']=={'min':1} and 'recording.score' not in p['gates']


def test_new_book_checks_invalidate_a_legacy_automatic_approval_with_a_locked_year_conflict(book):
    inv,im=I.discover(book);identity,identity_metrics=I.recover(book,inv)
    identity['book'].update(year=1970,locked=['year'])
    data=dict(providers=dict(audible=dict(candidates=[recording()]),openlibrary=dict(candidates=[candidate('openlibrary',title='Dune',authors=['Frank Herbert'],original_year=1965)])))
    old,mm=M._recording_evidence(identity,inv,data)
    assert mm['metadata.critical_conflicts']==0
    rid=store.create(book['id'],{})
    for stage,values,measure in [('inventory',inv,im),('identity',identity,identity_metrics),('providers',data,{}),('metadata',old,mm)]:store.event(rid,stage,'done',data=values,metrics=measure)
    store.event(rid,'metadata_approval','done',data=dict(automatic=True,signature=P.review_signature(store.get(rid),'metadata'),policy_revision=policy.current()['revision']))
    assert not P.review_evaluation(store.get(rid),'metadata')['groups']['work']['eligible']
    assert not P.approval_valid(store.get(rid),'metadata')


def test_proposal_sections_use_frozen_metadata_and_keep_publication_dates_separate(book):
    inv,_=I.discover(book);identity,_=I.recover(book,inv)
    data=dict(providers=dict(audible=dict(candidates=[dict(recording(publisher='Audio Press'),release_date='2024-01-01')]),openlibrary=dict(candidates=[candidate('openlibrary',title='Dune',authors=['Frank Herbert'],original_year=1965,publisher='Book Press')])))
    meta,_=M.resolve(identity,inv,data)
    plan=dict(title='Dune',author='Frank Herbert',narrator='Reader',metadata=sources.rows(book,meta['resolved']),metadata_evidence=meta)
    work,audio=M.plan_sections(plan)
    assert next(r for r in work if r['field']=='Publisher')['value']=='Book Press'
    assert next(r for r in audio if r['field']=='Publisher')['value']=='Audio Press'
    frozen=deepcopy(plan);frozen['metadata_evidence'].pop('work');frozen['metadata_evidence'].pop('performance')
    work,audio=M.plan_sections(frozen)
    assert next(r for r in work if r['field']=='Year')['value']=='1965'
    assert next(r for r in audio if r['field']=='Release date')['value']=='2024-01-01'
    frozen['metadata']=[dict(field='Title',value='My chosen title',source='set by hand',note='')]
    assert next(r for r in M.plan_sections(frozen)[0] if r['field']=='Title')['value']=='My chosen title'
    assert M.plan_sections(dict(metadata=[]))==([],[])


@pytest.mark.parametrize('year_source',['Open Library (first published)','Audible (audiobook release)','folder name'])
def test_legacy_flat_proposals_keep_unknown_scope_fields_explicit(year_source):
    plan=dict(title='Saved output title',metadata=[dict(field='Title',value='A book',source='Local catalogue',note=''),dict(field='Author',value='An Author',source='Local catalogue',note=''),dict(field='Publisher',value='Audio Press',source='Audible',note=''),dict(field='Year',value='2009',source=year_source,note='')])
    work,audio=M.plan_sections(plan)
    assert next(r for r in work if r['field']=='Publisher')['value']=='unknown'
    assert next(r for r in audio if r['field']=='Publisher')['value']=='Audio Press'
    assert next(r for r in work if r['field']=='Year')['value']==('2009' if 'first published' in year_source else 'unknown')
    assert next(r for r in audio if r['field']=='Release date')['value']==('2009' if 'audiobook release' in year_source else 'unknown')
    assert next(r for r in audio if r['field']=='Title')['note']=='Saved output title: Saved output title'


def test_legacy_plan_missing_audio_fields_stays_explicitly_unknown():
    work,audio=M.plan_sections(dict(metadata=[dict(field='Year',value='2009',source='set by hand',note='')]))
    assert next(r for r in work if r['field']=='Year')['value']=='2009'
    assert next(r for r in audio if r['field']=='Narrator')['value']=='unknown'
    assert next(r for r in audio if r['field']=='Publisher')['value']=='unknown'


def test_very_old_proposals_show_saved_identity_and_unknown_recording_fields():
    work,audio=M.plan_sections(dict(title='Saved title',author='Saved Author'))
    assert next(r for r in work if r['field']=='Title')['value']=='Saved title'
    assert next(r for r in work if r['field']=='Author')['source']=='Saved output plan'
    assert next(r for r in audio if r['field']=='Narrator')['value']=='unknown'
    work,_=M.plan_sections(dict(title='Saved title'))
    assert next(r for r in work if r['field']=='Author')['value']=='unknown'


def test_full_cast_credit_uses_independent_recording_evidence_instead_of_a_person_name(book,monkeypatch,tmp_path):
    inv,im=I.discover(book)
    inv['files'][0]['probe']['tags'].update(album='Joint Book',artist='Frank Herbert, Writer Two',composer='full cast',language='')
    identity,identity_metrics=I.recover(dict(book,locked=[]),inv);details=[]
    base=candidate('audible',id='B000000001',asin='B000000001',title='Joint Book',authors=['Frank Herbert','Writer Two'],narrators=['Ada Calhoun','Adam Horovitz','Bette Midler'],runtime_minutes=60,language='english',abridged=False,release_date='2018-10-30')
    class Fake:
        def __init__(self,name):self.name=name
        def unavailable(self):return None
        def by_asin(self,asin):return dict(base,source=self.name)
        def search(self,title,author):
            if self.name=='openlibrary':return [candidate('openlibrary',title='Joint Book',authors=['Frank Herbert','Writer Two'],original_year=2018)]
            if self.name=='applebooks':return [candidate('applebooks',id='apple',title='Joint Book',authors=['Frank Herbert & Writer Two'],release_date='2018-10-30T07:00:00Z')]
            return [base]
        def details(self,c):
            details.append(c['id']);return dict(base,source='applebooks',asin='',authors=['Frank Herbert & Writer Two'],narrators=['Ada Calhoun','Adam Horovitz','B. Midler','Reader Four','Reader Five'])
    monkeypatch.setattr(providers,'get',lambda name:Fake(name))
    collected,_=M.collect(identity,inv,tmp_path/'responses',['audible','audnexus','openlibrary','applebooks'])
    meta,mm=M.resolve(identity,inv,collected)
    assert details==['apple'] and meta['performance']['matched']
    assert meta['performance']['local']['narrators']==['full cast']
    assert mm['recording.narrator_agrees']==1 and mm['recording.language_agrees']==1 and mm['recording.conflicts']==0
    assert meta['records']['applebooks']['narrators'][2]=='B. Midler'
    assert collected['providers']['applebooks']['details']['authors']==['Frank Herbert & Writer Two']
    assert meta['resolved']['authors']==['Frank Herbert','Writer Two']
    rid=store.create(book['id'],{})
    for stage,data,measure in [('inventory',inv,im),('identity',identity,identity_metrics),('providers',collected,{}),('metadata',meta,mm)]:store.event(rid,stage,'done',data=data,metrics=measure)
    store.finish(rid,'metadata_review');p=policy.current();p['auto_approve_metadata']=True;policy.save(p)
    (Path(P.settings.data_dir())/'ingest').mkdir(exist_ok=True)
    assert P.review_evaluation(store.get(rid),'metadata')['eligible'],P.review_evaluation(store.get(rid),'metadata')
    assert P.approve_passing_metadata()==[rid],store.get(rid)['events'][-1]['error']
    assert P.stage_data(store.get(rid),'metadata_approval')['automatic'] and P.stage_data(store.get(rid),'quality_approval') is None
    # Audible and Audnexus alone never constitute independent corroboration.
    collected['providers'].pop('applebooks');meta,mm=M.resolve(identity,inv,collected)
    assert mm['recording.narrator_agrees'] is None and mm['recording.language_agrees'] is None


@pytest.mark.parametrize('change',[
    dict(narrators=['Ada Calhoun','Wrong Reader']),
    dict(narrators=['Ada Calhoun']),
    dict(runtime_minutes=90),
    dict(runtime_minutes=None),
    dict(release_date='2020-01-01'),
    dict(release_date=''),
    dict(language='french'),
    dict(language=''),
    dict(authors=['Someone Else']),
])
def test_partial_cast_cannot_establish_another_or_unverified_edition(change):
    book=dict(title='Joint Book',author='Writer')
    base=candidate('audible',title='Joint Book',authors=['Writer'],narrators=['Ada Calhoun','Bette Midler'],runtime_minutes=60,release_date='2018-10-30',language='english')
    other=dict(base,source='applebooks',narrators=['Ada Calhoun','B. Midler','Reader Three'],**change) if 'narrators' not in change else dict(base,source='applebooks',**change)
    assert not M.cast_agrees(base,other,book)


def test_cast_descriptions_abbreviations_and_named_people_remain_distinct():
    assert M.cast_credit('Full Cast')==([],True)
    assert M.cast_credit('Reader One, full cast')==(['Reader One'],True)
    assert M.cast_credit(['Full Castaneda'])==(['Full Castaneda'],False)
    assert M.local_cast_agreement('full cast',['Only Reader'])==0
    assert M.local_cast_agreement('full cast',['Reader One','Reader Two']) is None
    assert M.local_cast_agreement('Reader One, full cast',['Reader One','Reader Two'])==1
    assert M.local_cast_agreement('Wrong Reader, full cast',['Reader One','Reader Two'])==0
    assert M.local_cast_agreement('Reader One',[]) is None
    assert M.narrator_names(['B. Midler'],M.names(['Bette Midler']))==M.names(['Bette Midler'])
    assert M.narrator_names(['Bette Midler'],M.names(['B. Midler']))==M.names(['B. Midler'])
    assert M.narrator_names(['Brian Midler'],M.names(['Bette Midler']))==M.names(['Brian Midler'])
    assert M.narrator_names(['B. Midler'],M.names(['Bette Midler','Barbara Midler']))==M.names(['B. Midler'])
    assert M.narrator_names(['B. Anne Midler'],M.names(['Bette Midler']))==M.names(['B. Anne Midler'])
    base=candidate('audible',title='Book',authors=['Writer'],asin='B000000001',narrators=['Reader One','Reader Two'],runtime_minutes=60,language='english')
    other=dict(base,narrators=['Reader One','Reader Two','Reader Three'])
    assert M.cast_agrees(base,other,dict(title='Book',author='Writer'))


@pytest.mark.parametrize('author,compatible',[('Chuck Klosterman',True),('Chuck Palahniuk',False)])
def test_title_only_search_recovers_candidates_without_weakening_author_checks(book,monkeypatch,tmp_path,author,compatible):
    inv,_=I.discover(book);identity,_=I.recover(book,inv);identity['book']['author']='Charles Klosterman';calls=[]
    found=dict(recording(),authors=[author],author=author)
    class Provider:
        def unavailable(self):return ''
        def search(self,title,author):
            calls.append((title,author));return [] if author else [found]
    monkeypatch.setattr(providers,'get',lambda name:Provider())
    data,metrics=M.collect(identity,inv,tmp_path/'evidence',['audible'])
    assert calls==[('Dune','Charles Klosterman'),('Dune','')]
    assert data['providers']['audible']['candidates']==[found]
    assert M.compatible_work(identity['book'],found)==compatible
    assert metrics['metadata.provider_attempts']==1


def test_saved_v2_review_recomputes_earliest_year_without_rewriting_raw_events(book):
    inv,im=I.discover(book);identity,identity_metrics=I.recover(book,inv)
    data=dict(providers=dict(audible=dict(candidates=[recording()]),openlibrary=dict(candidates=[candidate('openlibrary',title='Dune',authors=['Frank Herbert'],original_year=2025)]),hardcover=dict(candidates=[candidate('hardcover',title='Dune',authors=['Frank Herbert'],original_year=1985)])))
    old,mm=M.resolve(identity,inv,data);old['version']=2;old['work']['resolved']['year']=2025;old['resolved']['year']=2025
    rid=store.create(book['id'],{})
    for stage,values,measure in [('inventory',inv,im),('identity',identity,identity_metrics),('providers',data,{}),('metadata',old,mm)]:store.event(rid,stage,'done',data=values,metrics=measure)
    run=store.get(rid);meta=P.stage_data(run,'metadata')
    assert meta['work']['resolved']['year']==1985 and meta['work']['resolved']['year_source']=='Hardcover (first published)'
    assert next(r for r in meta['work']['rows'] if r['field']=='Year')['value']=='1985'
    assert store.get(rid)['events'][-1]['data']['work']['resolved']['year']==2025


def test_title_context_allows_similar_author_given_names_but_not_narrators():
    book=dict(title='A Very Specific Book Title',author='Chester Klosterman')
    c=candidate('hardcover',title=book['title'],authors=['Chuck Klosterman'])
    assert M.compatible_work(book,c)
    assert not M.compatible_work(book,dict(c,title='A Very Different Book Title'))
    assert M.compatible_work(book,dict(c,authors=['Chuck Klosterman','Another Writer']))
    assert not M.compatible_work(book,dict(c,authors=['Chuck Palahniuk']))
    assert M.narrator_names(['Chester Klosterman'],M.names(['Chuck Klosterman']))!=M.names(['Chuck Klosterman'])
    # Additional credited names do not invalidate the shared author match.
    assert M.compatible_work(book,dict(c,authors=['Chuck Klosterman','Charles Klosterman']))


def test_book_approval_precedes_recording_search_and_freezes_the_book_identity(pipeline_fakes,monkeypatch):
    collect=M.collect;calls=[]
    def tracked(identity,inv,folder,names):
        calls.append((deepcopy(identity),list(names)))
        return collect(identity,inv,folder,names)
    monkeypatch.setattr(M,'collect',tracked)
    run=P.run([pipeline_fakes['id']])[0];rid=run['id'];book_meta=deepcopy(P.raw_stage(run,'metadata'))
    assert len(calls)==1 and calls[0][1]==['hardcover','openlibrary']
    assert P.stage_data(run,'recording_providers') is None and P.stage_data(run,'alignment') is None
    with pytest.raises(ValueError,match='approve metadata'):P.approve_review(rid,'audiobook')
    P.approve_review(rid,'metadata');run=P.continue_review(rid,propose=False)
    assert run['state']=='audiobook_review' and len(calls)==2
    assert calls[1][1]==['audible','audnexus','audiobookdb','applebooks']
    assert calls[1][0]['book']['title']==book_meta['work']['resolved']['title']
    assert calls[1][0]['book']['author']==', '.join(book_meta['work']['resolved']['authors'])
    assert calls[1][0]['book']['recording_title']==P.stage_data(run,'identity')['book']['title']
    assert P.stage_data(run,'metadata')['work']==book_meta['work']
    assert P.raw_stage(run,'metadata')==book_meta
    assert not P.approval_valid(run,'audiobook') and P.stage_data(run,'alignment') is None
    P.approve_review(rid,'audiobook');run=P.continue_review(rid,propose=False)
    assert run['state']=='quality_review' and P.approval_valid(run,'audiobook')


def test_recording_failure_cannot_undo_book_identification_or_auto_approve_quality(pipeline_fakes,monkeypatch):
    value=policy.current();value.update(auto_approve_metadata=True,auto_approve_audiobook=True);policy.save(value)
    collect=M.collect
    def wrong_recording(identity,inv,folder,names):
        data,metrics=collect(identity,inv,folder,names)
        if 'audible' in names:data['providers']['audible']['candidates'][0]['narrators']=['Another Reader']
        return data,metrics
    monkeypatch.setattr(M,'collect',wrong_recording)
    run=P.run([pipeline_fakes['id']])[0]
    assert run['state']=='audiobook_review' and P.approval_valid(run,'metadata')
    assert P.review_evaluation(run,'metadata')['eligible'] and not P.review_evaluation(run,'audiobook')['eligible']
    assert P.stage_data(run,'alignment') is None and P.stage_data(run,'quality_approval') is None
    assert P.stage_data(run,'metadata')['work']==P.raw_stage(run,'metadata')['work']


def test_changing_book_consent_invalidates_recording_and_quality_consent(pipeline_fakes):
    run=complete_run([pipeline_fakes['id']])[0]
    assert all(P.approval_valid(run,p) for p in P.REVIEW_STAGES)
    P._record_approval(run,'metadata')
    changed=store.get(run['id'])
    assert P.approval_valid(changed,'metadata')
    assert not P.approval_valid(changed,'audiobook') and not P.approval_valid(changed,'quality')


def test_legacy_combined_manual_metadata_consent_preserves_both_identifications(pipeline_fakes):
    run=complete_run([pipeline_fakes['id']])[0]
    run['events']=[e for e in run['events'] if e['stage'] not in ('recording_providers','recording','audiobook_approval')]
    consent=P.raw_stage(run,'metadata_approval');consent.pop('scope')
    quality=P.raw_stage(run,'quality_approval');quality['signature']=P.review_signature(run,'quality')
    assert all(P.approval_valid(run,p) for p in P.REVIEW_STAGES)
    assert P.review_phase(run)=='processing'
    # Invalid book consent must also invalidate the inherited recording consent.
    consent['signature']='invalid'
    assert not P.approval_valid(run,'audiobook') and not P.approval_valid(run,'quality')


def test_shared_author_counts_as_work_agreement_and_preserves_additional_credits(book):
    inv,im=I.discover(book);identity,identity_metrics=I.recover(book,inv)
    primary=candidate('hardcover',title='Dune',authors=['Frank Herbert'],original_year=1965)
    translated=candidate('openlibrary',title='Dune',authors=['Frank Herbert','Additional Credited Person'],original_year=1965)
    data=dict(providers=dict(hardcover=dict(candidates=[primary]),openlibrary=dict(candidates=[translated])))
    result,metrics=M.resolve(identity,inv,data)
    assert M.compatible_work(identity['book'],translated)
    assert M.compatible_work(dict(identity['book'],author='Frank Herbert, Another Writer'),primary)
    assert not M.compatible_work(identity['book'],dict(translated,authors=['Additional Credited Person']))
    assert not M.compatible_work(identity['book'],dict(translated,title='An unrelated book'))
    assert metrics['metadata.work_families']==2 and metrics['work.conflicts']==0
    assert result['work']['resolved']['authors']==['Frank Herbert']
    assert result['work']['records']['openlibrary']['additional_contributors']==['Additional Credited Person']
    assert result['records']['openlibrary']['authors']==['Frank Herbert']
    assert data['providers']['openlibrary']['candidates'][0]['authors']==['Frank Herbert','Additional Credited Person']


@pytest.mark.parametrize('author,title,passes',[
    ('Frank A. Herbert','Dune',True),
    ('Franc A. Herbert','Dune',False),
    ('Frank Alan Herbert','Dune',False),
    ('Frank A. Herbert','Dune: Another Work',True),
    ('Frank A. Herbert','Another Work',False),
])
def test_middle_initial_work_agreement_keeps_approval_gates(pipeline_fakes,monkeypatch,author,title,passes):
    original=M.collect
    def collect(*args):
        data,metrics=original(*args)
        data['providers']['openlibrary']['candidates'][0].update(authors=[author],title=title)
        return data,metrics
    monkeypatch.setattr(M,'collect',collect)
    value=policy.current();value['auto_approve_metadata']=True;policy.save(value)
    run=P.run([pipeline_fakes['id']])[0]
    assert P.review_evaluation(run,'metadata')['eligible']==passes
    assert run['state']==('audiobook_review' if passes else 'metadata_review')
    assert P.approval_valid(run,'metadata')==passes
    assert not P.approval_valid(run,'audiobook') and not P.approval_valid(run,'quality')
    assert P.stage_data(run,'alignment') is None and catalogue.get_plan(pipeline_fakes['id']) is None
    meta=P.stage_data(run,'metadata')
    assert meta['work']['families']==(2 if passes else 1)
    if passes:assert meta['work']['records']['openlibrary']['authors']==[author]


def test_saved_middle_initial_review_is_reprojected_before_automatic_approval(pipeline_fakes,monkeypatch):
    original=M.collect
    def collect(*args):
        data,metrics=original(*args)
        data['providers']['openlibrary']['candidates'][0]['authors']=['Frank A. Herbert']
        return data,metrics
    monkeypatch.setattr(M,'collect',collect)
    value=policy.current();value['auto_approve_metadata']=True;policy.save(value)
    with monkeypatch.context() as old:
        old.setattr(M,'VERSION',10)
        old.setattr(contributors,'same_author',lambda a,b,strong_title=False:contributors.same_person(a,b))
        run=P.run([pipeline_fakes['id']])[0]
        assert run['state']=='metadata_review' and not P.review_evaluation(run,'metadata')['eligible']
    assert P.raw_stage(store.get(run['id']),'metadata')['version']==10
    assert P.approve_passing_metadata()==[run['id']]
    approved=store.get(run['id'])
    assert P.stage_data(approved,'metadata')['version']==M.VERSION
    assert P.approval_valid(approved,'metadata')
    assert not P.approval_valid(approved,'audiobook') and not P.approval_valid(approved,'quality')


@pytest.mark.parametrize('local,credit,other,other_title,accepted',[
    ('Frank Herbert','Frank A. Herbert','Frank B. Herbert','Dune',False),
    ('Frank A. Herbert','Frank Herbert','Frank B. Herbert','Dune',False),
    ('Frank A. Herbert','Frank A. Herbert','Frank B. Herbert','Dune',True),
    ('Frank Herbert','Frank A. Herbert','Frank B. Herbert','Another Work',True),
    ('Frank Herbert','Frank A. Herbert','Frank A. Herbert','Dune',True),
])
def test_omitted_initial_cannot_bridge_competing_authors_of_same_title(local,credit,other,other_title,accepted):
    book=dict(title='Dune',author=local)
    found=candidate('openlibrary',title='Dune',authors=[credit])
    alternative=candidate('hardcover',title=other_title,authors=[other])
    assert M.compatible_work(book,found,[found,alternative])==accepted


def test_competing_middle_initials_do_not_supply_two_independent_work_sources(book):
    inv,_=I.discover(book);identity,_=I.recover(book,inv)
    data=dict(providers=dict(
        hardcover=dict(candidates=[candidate('hardcover',title='Dune',authors=['Frank Herbert'])]),
        openlibrary=dict(candidates=[candidate('openlibrary',title='Dune',authors=[name]) for name in ('Frank A. Herbert','Frank B. Herbert')]),
    ))
    result,metrics=M.resolve(identity,inv,data)
    assert metrics['metadata.work_families']==1
    assert set(result['work']['records'])=={'catalogue','hardcover'}


@pytest.mark.parametrize('same_work',[True,False])
def test_attached_edition_subtitles_approve_only_original_work(pipeline_fakes,monkeypatch,same_work):
    original=M.collect
    def collect(*args):
        data,metrics=original(*args)
        english=data['providers']['openlibrary']['candidates'][0]
        english.update(subtitle='Desert World',subtitle_edition='/books/OL1M')
        translated=dict(english,subtitle='Mundo del desierto',subtitle_edition='/books/OL2M',id='/works/OL1W' if same_work else '/works/OL2W')
        data['providers']['openlibrary']['candidates'].append(translated)
        return data,metrics
    monkeypatch.setattr(M,'collect',collect)
    value=policy.current();value['auto_approve_metadata']=True;policy.save(value)
    run=P.run([pipeline_fakes['id']])[0]
    assert P.review_evaluation(run,'metadata')['eligible']==same_work
    assert run['state']==('audiobook_review' if same_work else 'metadata_review')
    assert P.approval_valid(run,'metadata')==same_work
    assert not P.approval_valid(run,'audiobook') and not P.approval_valid(run,'quality')
    assert P.stage_data(run,'alignment') is None and catalogue.get_plan(pipeline_fakes['id']) is None
    if same_work:
        meta=P.stage_data(run,'metadata')
        assert meta['work']['families']==2
        assert meta['work']['records']['openlibrary']['title_match']=='main title; linked editions of original work'
        assert meta['work']['resolved']['year']==1965


def test_current_projection_preserves_recording_evidence_when_upgrading_book_rules(pipeline_fakes):
    run=finish_reviews(P.run([pipeline_fakes['id']])[0])
    raw=P.raw_stage(run,'recording');raw['version']=3
    projected=P.stage_data(run,'metadata')
    assert projected['version']==M.VERSION and projected['performance']['matched']
    assert P.stage_data(run,'recording')==projected
    assert projected['recording']['asin']=='B000000001'
    assert projected['work']==P.raw_stage(run,'metadata')['work']


@pytest.mark.parametrize('title',[
    'American Gods 10th Anniversary Edition',
    'American Gods (Anniversary Edition)',
    'American Gods: The Anniversary Edition',
    'American Gods [10th-anniversary edition]',
    'American Gods: The Tenth Anniversary Edition',
])
def test_anniversary_book_search_uses_work_title_and_retains_recording_edition(book,monkeypatch,tmp_path,title):
    inv,_=I.discover(book);identity,_=I.recover(book,inv)
    identity['book'].update(title=title,author='Neil Gaiman');calls=[]
    class Provider:
        def __init__(self,name):self.name=name
        def unavailable(self):return ''
        def search(self,title,author):
            calls.append((self.name,title,author))
            return [candidate(self.name,id=self.name,title='American Gods',authors=['Neil Gaiman'],original_year=2001)] if title=='American Gods' else []
        def details(self,identifier):return None
    monkeypatch.setattr(providers,'get',lambda name:Provider(name))
    collected,_=M.collect(identity,inv,tmp_path,['hardcover','openlibrary'])
    result,metrics=M.resolve(identity,inv,collected)
    assert calls==[('hardcover','American Gods','Neil Gaiman'),('openlibrary','American Gods','Neil Gaiman')]
    assert metrics['metadata.work_families']==2 and result['work']['resolved']['year']==2001
    assert identity['book']['title']==title
    assert M.approved_identity(identity,result)['book']['recording_title']==title
    assert not M.compatible_work(identity['book'],candidate('openlibrary',title='Anansi Boys',authors=['Neil Gaiman']))


@pytest.mark.parametrize('title',[
    'The Anniversary','The Anniversary Man',
    'Stories Volume 2','A Guide: Revised and Expanded Edition',
])
def test_anniversary_normalisation_preserves_real_titles_and_other_edition_evidence(title):
    assert M.work_title(title)==title


def test_edition_labels_inside_a_full_title_preserve_the_work_subtitle():
    assert M.work_title('A Guide (20th Anniversary Edition): A New World')=='A Guide: A New World'
    assert M.work_title('A Guide: 20th Anniversary Edition, 2nd Edition')=='A Guide'
    assert M.work_title('A Guide: Second Edition: A New World')=='A Guide: A New World'


def revision_work_evidence():
    return dict(archives=[],providers=dict(
        hardcover=dict(candidates=[candidate('hardcover',id='99',title='Dune: Old World',subtitle='Desert World',authors=['Frank Herbert'],original_year=1965)]),
        openlibrary=dict(candidates=[candidate('openlibrary',id='/works/OL1W',title='Dune',authors=['Frank Herbert'])],edition_work_key='/works/OL1W',publisher_editions=[
            dict(key='/books/OL1M',work_key='/works/OL1W',title='Dune',subtitle='Old World',year=1965,publish_date='1965',publishers=['Original Press']),
            dict(key='/books/OL2M',work_key='/works/OL1W',title='Dune',subtitle='Desert World, 20th Anniversary Edition',year=2019,publish_date='2019',publishers=['Revision Press']),
        ]),
    ))


def test_revised_subtitle_links_to_original_work_and_retains_recording_identity(book):
    inv,_=I.discover(book);identity,_=I.recover(book,inv)
    identity['book']['title']='Dune: 20th Anniversary Edition, 2nd Edition: Desert World'
    data=revision_work_evidence();before=deepcopy(data)
    meta,metrics=M.resolve(identity,inv,data)
    assert metrics['metadata.work_families']==2 and metrics['work.conflicts']==0 and metrics['work.score']>=.9
    assert meta['work']['resolved']['year']==1965 and meta['work']['resolved']['publisher']=='Original Press'
    assert meta['work']['records']['openlibrary']['publisher_edition']=='/books/OL1M'
    assert meta['work']['records']['hardcover']['title_match']=='linked editions of the same original work'
    approved=M.approved_identity(identity,meta)
    assert approved['book']['recording_title']==identity['book']['title']
    assert not meta['performance']['matched'] and data==before


@pytest.mark.parametrize('field,value',[
    ('work_key','/works/OL2W'),('key','unverified'),('title','Another Work'),
    ('title','Dune Volume 2'),('subtitle','Unrelated Subtitle'),
])
def test_revised_subtitle_bridge_requires_valid_attached_title_evidence(book,field,value):
    inv,_=I.discover(book);identity,_=I.recover(book,inv)
    identity['book']['title']='Dune: Second Edition: Desert World'
    data=revision_work_evidence();data['providers']['openlibrary']['publisher_editions'][1][field]=value
    _,metrics=M.resolve(identity,inv,data)
    assert metrics['metadata.work_families']<2


def test_work_edition_title_evidence_deduplicates_existing_variants_and_preserves_archive():
    data=revision_work_evidence();entry=data['providers']['openlibrary']
    entry['candidates'].append(dict(entry['candidates'][0],subtitle='Old World',subtitle_edition='/books/OL1M'))
    before=deepcopy(data);expanded=M.work_edition_evidence(data)
    assert len(expanded['providers']['openlibrary']['candidates'])==3
    assert expanded['providers']['openlibrary']['candidates'][-1]['subtitle_edition']=='/books/OL2M'
    assert data==before


def test_linked_revision_titles_do_not_resolve_conflicting_original_years(book):
    inv,_=I.discover(book);identity,_=I.recover(book,inv)
    identity['book']['title']='Dune: Second Edition: Desert World'
    data=revision_work_evidence();data['providers']['hardcover']['candidates'][0]['original_year']=1966
    meta,metrics=M.resolve(identity,inv,data)
    assert metrics['metadata.work_families']==2 and metrics['work.conflicts']==1
    assert meta['work']['resolved']['year']==1965
    assert meta['work']['resolved']['year_note']=='Hardcover says 1966'


def test_revision_work_automation_stops_at_audiobook_identification(pipeline_fakes,monkeypatch):
    original=I.recover
    def recover(*args):
        identity,metrics=original(*args);identity['book']['title']='Dune: 20th Anniversary Edition, 2nd Edition: Desert World'
        return identity,metrics
    monkeypatch.setattr(I,'recover',recover)
    monkeypatch.setattr(M,'collect',lambda *args:(revision_work_evidence(),{}))
    value=policy.current();value['auto_approve_metadata']=True;policy.save(value)
    run=P.run([pipeline_fakes['id']])[0]
    assert run['state']=='audiobook_review' and P.review_evaluation(run,'metadata')['eligible']
    assert P.approval_valid(run,'metadata')
    assert not P.approval_valid(run,'audiobook') and not P.approval_valid(run,'quality')
    assert P.stage_data(run,'alignment') is None and catalogue.get_plan(pipeline_fakes['id']) is None


@pytest.mark.parametrize('credit',['','Narrated by: Frank Herbert'])
@pytest.mark.parametrize('corroborated',[True,False])
def test_provisional_work_lookup_requires_independent_confirmation_before_approval(book,monkeypatch,credit,corroborated):
    original=I.recover;calls=[]
    def recover(*args):
        identity,metrics=original(*args)
        identity['book'].update(title='Dune Desert World',author=credit,locked=[])
        metrics['identity.author_known']=int(bool(credit))
        return identity,metrics
    class Provider:
        def __init__(self,name):self.name=name
        def unavailable(self):return None
        def by_asin(self,asin):return None
        def search(self,title,author):
            calls.append((self.name,title,author))
            if self.name=='hardcover':return [candidate(self.name,id='99',title='Dune: Desert World',authors=['Frank Herbert'],original_year=1965)]
            if self.name=='openlibrary':
                assert title=='Dune: Desert World' and author in ('Frank Herbert','')
                return [candidate(self.name,id='/works/OL1W',title='Dune',authors=['Frank Herbert'],original_year=1965)] if corroborated else []
            return []
        def details(self,identifier):return None
    monkeypatch.setattr(I,'recover',recover);monkeypatch.setattr(providers,'get',lambda name:Provider(name))
    value=policy.current();value['auto_approve_metadata']=True;policy.save(value)
    run=P.run([book['id']])[0]
    assert run['state']==('audiobook_review' if corroborated else 'metadata_review')
    assert P.approval_valid(run,'metadata')==corroborated
    assert P.review_evaluation(run,'metadata')['eligible']==corroborated
    assert not P.approval_valid(run,'audiobook') and not P.approval_valid(run,'quality')
    assert P.stage_data(run,'alignment') is None and catalogue.get_plan(book['id']) is None
    assert P.stage_data(run,'identity')['book']['author']==credit
    assert ('openlibrary','Dune: Desert World','Frank Herbert') in calls
    if corroborated:assert P.stage_data(run,'metadata')['work']['resolved']['authors']==['Frank Herbert']


@pytest.mark.parametrize('case,accepted', [('asin',True),('unknown_author',True),('measured',True),('wrong_author',False),('wrong_volume',False),('wrong_narrator',False),('wrong_runtime',False),('ambiguous',False)])
def test_audiobook_discovery_supplies_work_search_without_approving_recording(book,monkeypatch,tmp_path,case,accepted):
    inv,_=I.discover(book);identity,_=I.recover(book,inv)
    identity['book'].update(title='Dune: 20th Anniversary Edition',author='Frank Herbert, Reader - introductions',narrator='Reader',asin='B000000001' if case=='asin' else '')
    if case=='unknown_author':identity['book'].update(author='',asin='B000000001')
    identity['_work_discovery_providers']=['audible'];calls=[]
    audio_candidate=recording()
    if case=='wrong_author':audio_candidate['authors']=['Other Writer']
    if case=='wrong_volume':audio_candidate['title']='Dune Volume 2'
    if case=='wrong_narrator':audio_candidate['narrators']=['Other Reader']
    if case=='wrong_runtime':audio_candidate['runtime_minutes']=30
    class Provider:
        def __init__(self,name):self.name=name
        def unavailable(self):return None
        def by_asin(self,asin):
            calls.append((self.name,'asin',asin))
            return audio_candidate if self.name=='audible' else None
        def search(self,title,author):
            calls.append((self.name,title,author))
            if self.name=='audible':return [audio_candidate,dict(audio_candidate,asin='B000000002')] if case=='ambiguous' else [audio_candidate]
            return [candidate(self.name,id='99',title='Dune',authors=['Frank Herbert'],original_year=1965)] if accepted else []
        def details(self,identifier):return None
    monkeypatch.setattr(providers,'get',lambda name:Provider(name))
    data,_=M.collect(identity,inv,tmp_path,['hardcover','openlibrary'])
    meta,metrics=M.resolve(identity,inv,data)
    assert bool(data['providers']['audible']['candidates'])==accepted
    if accepted:
        assert ('hardcover','Dune','Frank Herbert') in calls
        assert meta['work']['resolved']['authors']==['Frank Herbert'] and meta['work']['resolved']['year']==1965
        assert metrics['metadata.work_families']>=2
        audio_only=dict(providers={name:entry for name,entry in data['providers'].items() if name not in ('hardcover','openlibrary')})
        _,audio_metrics=M.resolve(identity,inv,audio_only)
        assert audio_metrics['metadata.original_work_link']==0 and audio_metrics['metadata.work_families']<=1
    assert identity['book']['title']=='Dune: 20th Anniversary Edition'


def test_work_stage_enables_configured_audiobook_discovery_and_retains_separate_consent(pipeline_fakes,monkeypatch):
    collect=M.collect;seen=[]
    def tracked(identity,*args):
        seen.append(identity)
        return collect(identity,*args)
    monkeypatch.setattr(M,'collect',tracked)
    run=P.run([pipeline_fakes['id']])[0]
    assert seen[0]['_work_discovery_providers']==['audible','audnexus','audiobookdb','applebooks']
    assert '_work_discovery_providers' not in P.stage_data(run,'identity')
    assert P.stage_data(run,'recording_providers') is None
    assert not P.approval_valid(run,'audiobook') and not P.approval_valid(run,'quality')


@pytest.mark.parametrize('title,author,candidates',[
    ('(14)','',[candidate('hardcover',title='14',authors=['Wrong Writer'])]),
    ('History','',[candidate('hardcover',title='History',authors=['First Writer']),candidate('hardcover',title='History',authors=['Second Writer'])]),
    ('Dune','Frank Herbert',[candidate('hardcover',title='Dune',authors=['Other Writer'])]),
    ('Dune','',[candidate('hardcover',title='Another Work',authors=['Frank Herbert'])]),
    ('Stories Volume 1','',[candidate('hardcover',title='Stories Volume 2',authors=['Frank Herbert'])]),
])
def test_lookup_hints_reject_fragment_numbers_ambiguous_people_and_conflicts(title,author,candidates):
    assert M.work_lookup_hint(dict(title=title,author=author),dict(providers=dict(hardcover=dict(candidates=candidates)))) is None


def test_numeric_fragment_cannot_gain_an_invented_author_from_catalogue_coincidence(book):
    inv,_=I.discover(book);identity,_=I.recover(book,inv);identity['book'].update(title='(14)',author='')
    data=dict(providers={name:dict(candidates=[candidate(name,title='14',authors=['Unrelated Writer'])]) for name in ('hardcover','openlibrary')})
    meta,metrics=M.resolve(identity,inv,data)
    assert M.inferred_work_identity(identity,data)==(identity,False)
    assert metrics['identity.author_known']==0 and metrics['metadata.work_families']==0
    assert meta['work']['resolved']['authors']==[]


@pytest.mark.parametrize('case,passes', [('distinctive',True),('one_source',False),('collision',False),('locked',False),('generic',False),('missing_subtitle',False),('different_volume',False),('coauthor_conflict',False),('competing_initials',False)])
def test_distinctive_full_title_can_correct_an_unlocked_artist_credit(book,case,passes):
    inv,im=I.discover(book);identity,identity_metrics=I.recover(book,inv)
    title='Sherlock Holmes: The Definitive Collection' if case!='generic' else 'History'
    identity['book'].update(title=title,author='Stephen Fry',locked=['author'] if case=='locked' else [])
    original=deepcopy(identity)
    entries={name:dict(candidates=[candidate(name,id=name,title=title,authors=['Arthur Conan Doyle'],original_year=2017)]) for name in ('hardcover','openlibrary')}
    if case=='one_source':entries.pop('openlibrary')
    if case=='collision':entries['hardcover']['candidates'].append(candidate('hardcover',title=title,authors=['Another Writer']))
    if case=='missing_subtitle':
        for entry in entries.values():entry['candidates'][0]['title']='Sherlock Holmes'
    if case=='different_volume':
        for entry in entries.values():entry['candidates'][0]['title']=title+' Part Two'
    if case=='coauthor_conflict':entries['openlibrary']['candidates'][0]['authors'].append('Another Writer')
    if case=='competing_initials':
        entries['hardcover']['candidates'][0]['authors']=['Charles Murray']
        entries['openlibrary']['candidates'][0]['authors']=['Charles A Murray']
        entries['openlibrary']['candidates'].append(candidate('openlibrary',title=title,authors=['Charles B Murray']))
    data=dict(providers=entries);meta,metrics=M.resolve(identity,inv,data)
    checks=[c for c in policy.evaluate({**im,**identity_metrics,**metrics},identity['kind'],policy.current())['checks'] if c['metric'].startswith(('inventory.','identity.','metadata.original_work_link','metadata.work_families','work.'))]
    assert all(c['passed'] for c in checks)==passes
    assert identity==original
    if passes:
        assert meta['work']['resolved']['authors']==['Arthur Conan Doyle']
        assert meta['work']['records']['catalogue']['unverified_authors']==['Stephen Fry']
        assert meta['performance']['matched'] is False


def test_distinctive_title_lookup_uses_provider_author_before_final_consensus(book,monkeypatch,tmp_path):
    inv,_=I.discover(book);identity,_=I.recover(book,inv)
    title='Sherlock Holmes: The Definitive Collection';identity['book'].update(title=title,author='Stephen Fry',locked=[]);calls=[]
    class Provider:
        def __init__(self,name):self.name=name
        def unavailable(self):return None
        def by_asin(self,asin):return None
        def search(self,query,author):
            calls.append((self.name,query,author))
            if self.name=='hardcover' or author=='Arthur Conan Doyle':return [candidate(self.name,id=self.name,title=title,authors=['Arthur Conan Doyle'],original_year=2017)]
            return []
        def details(self,ident):return None
    monkeypatch.setattr(providers,'get',lambda name:Provider(name))
    data,_=M.collect(identity,inv,tmp_path,['hardcover','openlibrary'])
    assert ('openlibrary',title,'Arthur Conan Doyle') in calls
    assert M.resolve(identity,inv,data)[0]['work']['resolved']['authors']==['Arthur Conan Doyle']
    one_source=dict(providers=dict(hardcover=data['providers']['hardcover']))
    assert M.inferred_work_identity(identity,one_source)==(identity,False)


@pytest.mark.parametrize('case,passes', [('english',True),('unknown_language',False),('foreign_language',False),('wrong_link',False),('manual_title',False),('chapter_titles',False)])
def test_english_edition_and_consistent_full_title_tags_resolve_translation_ambiguity(book,case,passes):
    inv,_=I.discover(book);identity,_=I.recover(book,inv)
    inv['files'][0]['probe']['tags']['title']='Dune: Desert World (Unabridged)'
    identity['book'].update(title='Dune',locked=['title'] if case=='manual_title' else [])
    if case=='chapter_titles':
        inv['files'].append(deepcopy(inv['files'][0]));inv['files'][-1]['probe']['tags']['title']='Chapter Two'
    english=candidate('openlibrary',id='/works/OL1W',title='Dune',authors=['Frank Herbert'],original_year=1965)
    english.update(subtitle='Desert World',subtitle_edition='/books/OL1M')
    translated=candidate('openlibrary',id='/works/OL2W',title='Dune',subtitle='Mundo del desierto',authors=['Frank Herbert','Translator Person'])
    edition=dict(key='/books/OL1M',work_key='/works/OL9W' if case=='wrong_link' else '/works/OL1W',title='Dune',subtitle='Desert World',year=1965,publish_date='1965',publishers=['Publisher'],languages=[] if case=='unknown_language' else ['/languages/spa'] if case=='foreign_language' else ['/languages/eng'])
    data=dict(providers=dict(openlibrary=dict(candidates=[english,translated],edition_work_key='/works/OL1W',publisher_editions=[edition]),hardcover=dict(candidates=[candidate('hardcover',title='Dune: Desert World',authors=['Frank Herbert'],original_year=1965)])))
    before=deepcopy(data);meta,metrics=M.resolve(identity,inv,data)
    assert (metrics['metadata.work_families']==2)==passes
    assert data==before and identity['book']['title']=='Dune'
    if passes:
        assert meta['work']['resolved']['authors']==['Frank Herbert'] and meta['work']['resolved']['year']==1965
        assert metrics['work.conflicts']==0 and not meta['performance']['matched']


def test_english_work_entry_is_preferred_over_popular_translation_but_not_another_volume(book):
    inv,_=I.discover(book);identity,_=I.recover(book,inv)
    english=candidate('openlibrary',id='/works/OL1W',title='Dune',authors=['Frank Herbert'],language='eng',original_year=1965,url='english')
    french=dict(english,id='/works/OL2W',language='fr',url='french',edition_count=100000,ratings_count=100000)
    unknown=dict(english,id='/works/OL3W',language='',url='unknown',edition_count=100000)
    other_volume=dict(english,id='/works/OL4W',title='Dune Volume 2',url='volume two')
    meta,metrics=M.resolve(identity,inv,dict(providers=dict(openlibrary=dict(candidates=[french,unknown,other_volume,english]))))
    assert meta['work']['records']['openlibrary']['url']=='english'
    assert metrics['metadata.work_families']==1


def test_slash_artist_credits_need_agreeing_work_sources_and_preserve_reader_alternative(book):
    inv,_=I.discover(book);identity,_=I.recover(book,inv);identity['book'].update(author='Frank Herbert / Alice Reader',locked=[])
    data=dict(providers={name:dict(candidates=[candidate(name,title='Dune',authors=['Frank Herbert'],original_year=1965)]) for name in ('hardcover','openlibrary')})
    meta,metrics=M.resolve(identity,inv,data)
    assert metrics['metadata.work_families']==2 and metrics['work.conflicts']==0
    assert meta['work']['resolved']['authors']==['Frank Herbert']
    assert meta['work']['records']['catalogue']['authors']==['Frank Herbert','Alice Reader']


@pytest.fixture
def mixed_source(book,monkeypatch):
    root=Path(book['path']).parent/'Mixed';root.mkdir()
    for title in ('Alpha Book','Beta Book'):
        folder=root/title;folder.mkdir()
        for n in (1,2):(folder/f'{n:02d}.mp3').write_bytes(b'audio')
    original=I.probe.probe_file
    def probe(path):
        info=original(path);info['tags'].update(album=Path(path).parent.name,artist='Test Writer',track=f'{int(Path(path).stem)}/2')
        return info
    monkeypatch.setattr(I.probe,'probe_file',probe)
    return catalogue.add_book(title='Mixed collection',path=str(root))


def test_collection_ingest_creates_separate_durable_groups_without_changing_sources(mixed_source):
    root=Path(mixed_source['path']);before={str(p):p.read_bytes() for p in root.rglob('*.mp3')}
    results=P.run([mixed_source['id']],online=False,limit=1)
    assert [r['state'] for r in results]==['split','metadata_review','metadata_review']
    parent,alpha,beta=results
    assert P.review_phase(parent)=='processing'
    assert len(P.stage_data(parent,'split')['children'])==2
    groups=[P.stage_data(r,'inventory') for r in (alpha,beta)]
    assert all(len(inv['files'])==2 and len(inv['members'])==1 for inv in groups)
    assert not {f['path'] for f in groups[0]['files']} & {f['path'] for f in groups[1]['files']}
    for run,inv in zip((alpha,beta),groups):
        assert I.snapshot_unchanged(inv)
        assert P.stage_data(run,'identity')['book']['title'] in ('Alpha Book','Beta Book')
        assert P.stage_data(run,'identity')['book']['author']=='Test Writer'
        refreshed,_=I.discover(catalogue.get_book(run['book_id']))
        assert [f['path'] for f in refreshed['files']]==[f['path'] for f in inv['files']]
    assert {str(p):p.read_bytes() for p in root.rglob('*.mp3')}==before
    assert all(r['id']!=parent['id'] for r in P.dashboard(phase='metadata')['runs'])
    (root/'new.mp3').write_bytes(b'new source')
    assert not I.snapshot_unchanged(groups[0])


def test_collection_preserves_existing_proposals_and_rejects_approval_of_mixed_sources(mixed_source):
    root=Path(mixed_source['path']);existing=catalogue.add_book(title='Alpha Book',path=str(root/'Alpha Book'))
    catalogue.set_plan(existing['id'],'proposed',dict(title='Saved proposal'))
    inv,metrics=I.discover(mixed_source);rid=store.create(mixed_source['id'],{})
    store.event(rid,'inventory','done',metrics,inv)
    with pytest.raises(ValueError,match='multiple books'):P._assert_review_inputs(store.get(rid))
    results=P.run([mixed_source['id']],online=False)
    assert len(results)==3 and any(r['state']=='split_existing' for r in results)
    assert catalogue.get_plan(existing['id'])['plan']==dict(title='Saved proposal')
    from app.ingest import operations
    assert any(r['stage']=='Split into 2 source groups' for r in operations.view([],[],True)['rows'])


def test_split_collection_does_not_consume_the_limit_for_other_selected_sources(mixed_source,book):
    results=P.run([mixed_source['id'],book['id']],limit=2,online=False)
    assert len(results)==4 and results[-1]['book_id']==book['id']


def test_book_author_can_be_recovered_from_two_agreeing_book_catalogues(book):
    inv,_=I.discover(book);identity,_=I.recover(book,inv);identity['book']['author']=''
    source=lambda name,authors:candidate(name,title='Dune',authors=authors,original_year=1965)
    data=dict(providers=dict(hardcover=dict(candidates=[source('hardcover',['Frank Herbert'])]),openlibrary=dict(candidates=[source('openlibrary',['Frank Herbert','A Translator'])])))
    result,metrics=M.resolve(identity,inv,data)
    assert metrics['identity.author_known']==1 and result['work']['resolved']['authors']==['Frank Herbert']
    assert result['work']['records']['catalogue']['authors']==[]
    assert result['work']['records']['openlibrary']['additional_contributors']==['A Translator']
    assert identity['book']['author']==''
    data['providers']['openlibrary']['candidates'].append(source('openlibrary',['Another Writer']))
    data['providers']['hardcover']['candidates'].append(source('hardcover',['Another Writer']))
    result,metrics=M.resolve(identity,inv,data)
    assert metrics['identity.author_known']==0 and result['work']['resolved']['authors']==[]


def test_scoped_reingest_picks_up_new_chapters_but_rejects_changed_book_boundaries(mixed_source,monkeypatch):
    results=P.run([mixed_source['id']],online=False);child=results[1];inv=P.stage_data(child,'inventory');primary=catalogue.get_book(child['book_id'])
    (Path(inv['root'])/'03.mp3').write_bytes(b'new chapter')
    refreshed,_=I.discover(primary)
    assert len(refreshed['files'])==3
    original=I.probe.probe_file
    def changed(path):
        info=original(path)
        if Path(path).name=='02.mp3' and Path(path).parent==Path(inv['root']):info['tags']['album']='A different book'
        return info
    monkeypatch.setattr(I.probe,'probe_file',changed)
    with pytest.raises(ValueError,match='grouping changed'):I.discover(primary)


def test_existing_book_proposal_survives_a_separately_grouped_bonus_track(mixed_source,monkeypatch):
    root=Path(mixed_source['path']);alpha=root/'Alpha Book'
    for n in range(3,11):(alpha/f'{n:02d}.mp3').write_bytes(b'chapter')
    (alpha/'Interview.mp3').write_bytes(b'interview')
    existing=catalogue.add_book(title='Alpha Book',path=str(alpha));catalogue.set_plan(existing['id'],'proposed',dict(title='Preserved'))
    original=I.probe.probe_file
    def probe(path):
        if Path(path).stem=='Interview':
            info=original(str(alpha/'01.mp3'));info['path']=path;info['tags'].update(album='Author Interview',track='1/1');return info
        return original(path)
    monkeypatch.setattr(I.probe,'probe_file',probe)
    results=P.run([mixed_source['id']],online=False)
    child=next(r for r in results if r['book_id']==existing['id'])
    assert child['state']=='split_existing' and len(P.stage_data(child,'inventory')['files'])==10
    reread,_=I.discover(catalogue.get_book(existing['id']))
    assert len(reread['files'])==10 and I.snapshot_unchanged(reread)
    assert catalogue.get_plan(existing['id'])['plan']==dict(title='Preserved')


def test_collection_split_quarantines_unreadable_sources(mixed_source,monkeypatch):
    original=I.probe.probe_file
    def fail(path):
        if 'Beta Book' in path:raise ValueError('unreadable')
        return original(path)
    monkeypatch.setattr(I.probe,'probe_file',fail)
    # One whole book is unreadable; the surviving book cannot make the whole
    # collection appear complete or bypass the source integrity gate.
    result=P.run([mixed_source['id']],online=False)[0]
    assert not P.review_evaluation(result,'metadata')['eligible']
    assert P.stage_data(result,'alignment') is None


def test_pending_mixed_collection_can_be_regrouped_without_reusing_old_consent(mixed_source):
    inv,metrics=I.discover(mixed_source);old=store.create(mixed_source['id'],dict(online=False))
    inv.pop('work_groups');store.event(old,'inventory','done',metrics,inv);store.finish(old,'metadata_review')
    original=store.get(old)
    ids=P.regroup_collection(old)
    assert len(ids)==3 and store.get(ids[0])['state']=='split'
    assert all(P.stage_data(store.get(rid),'metadata_approval') is None for rid in ids[1:])
    assert store.get(old)==original and P.regroup_collection(old)==[]


def test_local_additional_credits_and_ordinal_spelling_do_not_create_identity_conflicts(book):
    inv,_=I.discover(book);first=inv['files'][0]
    first['probe']['tags'].update(album='American Gods 10th Anniversary Edition',artist='Neil Gaiman')
    second=deepcopy(first);second['probe']['tags'].update(album='American Gods Tenth Anniversary Edition',artist='Neil Gaiman, Another Credited Person')
    inv['files'].append(second)
    identity,metrics=I.recover(book,inv)
    assert metrics['identity.conflicts']==0
    second['probe']['tags']['artist']='Completely Different Writer'
    assert I.recover(book,inv)[1]['identity.conflicts']==1


def test_recording_refinement_retains_edition_similarity_after_book_title_is_canonical(book):
    inv,_=I.discover(book);identity,_=I.recover(book,inv)
    identity['book'].update(title='A Novel 10th Anniversary Edition',narrator='',locked=[])
    printed=dict(providers={name:dict(candidates=[candidate(name,title='A Novel',subtitle='10th Anniversary Edition',authors=['Frank Herbert'],original_year=2001)]) for name in ('hardcover','openlibrary')})
    approved,_=M.resolve(identity,inv,printed)
    assert approved['work']['resolved']['title']=='A Novel'
    audio=candidate('audible',title='A Novel: The Tenth Anniversary Edition (A Full Cast Production)',authors=['Frank Herbert'],narrators=['Reader One','Reader Two'],runtime_minutes=60,language='english',abridged=False,release_date='2012-04-12')
    combined=dict(providers=dict(printed['providers'],audible=dict(candidates=[audio]),applebooks=dict(candidates=[dict(audio,source='applebooks',title='A Novel')])) )
    result,metrics=M.refine_recording(identity,inv,combined,approved)
    assert result['work']==approved['work']
    assert metrics['recording.score']>=.9 and metrics['recording.matched']==1
    assert metrics['recording.narrator_agrees']==1 and metrics['recording.language_agrees']==1


def test_grouped_file_alias_uses_the_primary_work_scope(mixed_source):
    root=Path(mixed_source['path']);alias=catalogue.add_book(title='A chapter',path=str(root/'Alpha Book'/'02.mp3'))
    folder=catalogue.add_book(title='Alpha Book',path=str(root/'Alpha Book'))
    # The older chapter entry remains the primary anchor; the folder aliases it.
    results=P.run([mixed_source['id']],online=False)
    alpha=next(r for r in results if r['book_id']==alias['id'])
    reference=catalogue.get_extras(folder['id'])['source_group_ref']['data']
    assert reference['primary_id']==alias['id']
    inv,_=I.discover(catalogue.get_book(folder['id']))
    assert {f['path'] for f in inv['files']}=={f['path'] for f in P.stage_data(alpha,'inventory')['files']}
    assert len(inv['files'])==2


def test_mixed_source_inventory_cannot_execute_an_old_aggregate_plan(mixed_source):
    inv,_=I.discover(mixed_source)
    plan=dict(ingest=dict(root=inv['root'],inventory_manifest=inv['files']))
    assert not P.Q._unchanged(mixed_source,plan)


def test_old_metadata_projection_is_cached_isolated_and_invalidated_by_new_evidence(pipeline_fakes,monkeypatch):
    run=P.run([pipeline_fakes['id']])[0];rid=run['id'];old=deepcopy(P.stage_data(run,'metadata'));old['version']=1
    store.event(rid,'metadata','done',data=old)
    original=M.resolve;calls=[]
    def resolve(*args,**kwargs):
        calls.append(True)
        return original(*args,**kwargs)
    monkeypatch.setattr(M,'resolve',resolve)
    first=P.stage_data(store.get(rid),'metadata');expected=deepcopy(first)
    first['resolved']['title']='Changed by consumer'
    assert P.stage_data(store.get(rid),'metadata')==expected and len(calls)==1
    store.event(rid,'providers','done',data=P.raw_stage(run,'providers'))
    assert P.stage_data(store.get(rid),'metadata')==expected and len(calls)==2


def test_interrupted_collection_split_reuses_saved_children(mixed_source,monkeypatch):
    from app.ingest import collections
    inv,metrics=I.discover(mixed_source);value=policy.current()
    rid=store.create(mixed_source['id'],dict(policy=value,online=False,book_signature=I.signature(mixed_source)))
    store.event(rid,'inventory','done',metrics,inv)
    original=store.event
    def interrupted(run_id,stage,*args,**kwargs):
        if run_id==rid and stage=='split':raise RuntimeError('interrupted after creating children')
        return original(run_id,stage,*args,**kwargs)
    monkeypatch.setattr(store,'event',interrupted)
    with pytest.raises(RuntimeError,match='interrupted'):collections.split(rid,inv,value,False)
    children={r['id'] for r in store.all_runs() if r['options'].get('collection_run')==rid}
    assert len(children)==2
    monkeypatch.setattr(store,'event',original)
    results=P.run(resume=rid)
    assert results[0]['state']=='split' and {r['id'] for r in results[1:]}==children
    assert all(r['state']=='metadata_review' for r in results[1:])


def test_unique_recording_combines_storefront_listings_and_applies_known_edition_fields(book):
    inv,_=I.discover(book);identity,_=I.recover(book,inv)
    identity['book']['narrator']='';identity['language']='';identity['abridged']=None
    data=dict(providers={
        'applebooks':dict(candidates=[dict(recording('applebooks'),id='apple',runtime_minutes=60,abridged=None)]),
        'audible':dict(candidates=[dict(recording(),id='audible',runtime_minutes=59.8)]),
        'audnexus':dict(candidates=[dict(recording('audnexus'),id='audible',runtime_minutes=59.8)]),
    })
    meta,measure=M.resolve(identity,inv,data)
    assert len(meta['performance']['choices'])==1 and meta['performance']['matched']
    assert meta['recording']['abridged'] is False and measure['recording.ambiguous']==0
    assert measure['recording.abridgement_known']==1 and measure['recording.runtime_error']<.02


def test_competing_recordings_require_an_explicit_choice_that_keeps_book_consent(pipeline_fakes):
    run=P.run([pipeline_fakes['id']])[0];rid=run['id'];identity=deepcopy(P.stage_data(run,'identity'))
    identity['book']['narrator']='';identity['language']='';identity['abridged']=None
    store.event(rid,'identity','done',data=identity)
    P.approve_review(rid,'metadata');run=store.get(rid)
    providers=dict(providers={'audible':dict(candidates=[dict(recording(),id='one')]),'audnexus':dict(candidates=[dict(recording('audnexus'),id='two',asin='B000000002',narrators=['Other Reader'])])})
    meta,measure=M.refine_recording(identity,P.stage_data(run,'inventory'),providers,P.stage_data(run,'metadata'))
    store.event(rid,'recording_providers','done',data=providers);store.event(rid,'recording','done',measure,meta);store.finish(rid,'audiobook_review')
    assert len(meta['performance']['choices'])==2 and not meta['performance']['matched'] and measure['recording.ambiguous']==1
    with pytest.raises(ValueError,match='choose a recording'):P.approve_review(rid,'audiobook')
    with pytest.raises(ValueError,match='compatible saved candidate'):P.choose_recording(rid,'invented')
    key=next(c['key'] for c in meta['performance']['choices'] if c['candidate']['narrators']==['Other Reader'])
    assert P.choose_recording(rid,key)['changed']
    chosen=store.get(rid);actual=P.stage_data(chosen,'metadata')
    assert P.approval_valid(chosen,'metadata') and actual['performance']['matched']
    assert actual['recording']['narrators']==['Other Reader'] and actual['performance']['selected_choice']==key
    assert P.metrics(chosen)['recording.ambiguous']==0


def test_distinct_unknown_performances_are_not_collapsed_by_runtime_alone(book):
    inv,_=I.discover(book);identity,_=I.recover(book,inv)
    identity['book']['narrator']='';identity['language']='';identity['abridged']=None
    base=dict(recording(),id='',asin='',narrators=[],language='',abridged=None)
    providers=dict(providers={'audible':dict(candidates=[base]),'applebooks':dict(candidates=[dict(base,source='applebooks')])})
    meta,measure=M.resolve(identity,inv,providers)
    assert len(meta['performance']['choices'])==2 and measure['recording.ambiguous']==1 and not meta['performance']['matched']
    assert M.recording_key(dict(base,narrators=['One Reader']))!=M.recording_key(dict(base,narrators=['Two Reader']))
    providers['providers']['audible']['candidates'][0]=dict(base,asin='B000000001')
    providers['providers']['applebooks']['candidates'][0]=dict(base,source='audnexus',asin='B000000001')
    meta,measure=M.resolve(identity,inv,providers)
    assert len(meta['performance']['choices'])==1


@pytest.mark.parametrize('checks_pass',[True,False])
def test_radio_serial_can_pass_book_identification_without_allowing_radio_processing(pipeline_fakes,checks_pass):
    run=P.run([pipeline_fakes['id']])[0];rid=run['id'];identity=deepcopy(P.stage_data(run,'identity'));identity['kind']='radio'
    store.event(rid,'identity','done',data=identity)
    if not checks_pass:
        meta=deepcopy(P.stage_data(run,'metadata'));meta['analysis_metrics']['work.score']=0
        store.event(rid,'metadata','done',data=meta)
    value=policy.current();value['auto_approve_metadata']=True;policy.save(value)
    evaluation=P.review_evaluation(store.get(rid),'metadata')
    assert evaluation['kind_allowed'] and evaluation['eligible']==checks_pass
    assert not P.review_evaluation(store.get(rid),'audiobook')['kind_allowed']
    assert P.approve_passing_metadata()==([rid] if checks_pass else [])


def test_single_whole_book_does_not_inventory_unrelated_formats_in_a_mixed_folder(book,monkeypatch):
    other=Path(book['path']).parent/'Other Book.mp3';other.write_bytes(b'other')
    original=I.probe.probe_file;read=[]
    def probe(path):
        read.append(path)
        return original(path)
    monkeypatch.setattr(I.probe,'probe_file',probe)
    inv,_=I.discover(book)
    assert [f['path'] for f in inv['files']]==[book['path']] and str(other) not in read


def test_book_only_refresh_reuses_inventory_and_queues_the_next_stage(pipeline_fakes):
    previous=P.run([pipeline_fakes['id']])[0]
    value=policy.current();value['auto_approve_metadata']=True;policy.save(value)
    fresh=P.refresh_metadata(previous['id'],book_only=True)
    assert fresh['state']=='queued' and P.approval_valid(fresh,'metadata')
    assert P.stage_data(fresh,'recording_providers') is None and P.stage_data(fresh,'inventory')['reused_from']==previous['id']


def test_original_publisher_must_belong_to_the_selected_publication_year(book):
    inv,_=I.discover(book);identity,_=I.recover(book,inv)
    data=dict(providers={'openlibrary':dict(candidates=[candidate('openlibrary',id='/works/OL1W',title='Dune',authors=['Frank Herbert'],original_year=1965)],edition_work_key='/works/OL1W',publisher_editions=[dict(key='/books/OL1M',year=1965,publish_date='1965',publishers=['Original Press']),dict(key='/books/OL2M',year=2017,publish_date='2017',publishers=['Reprint Press'])])})
    meta,_=M.resolve(identity,inv,data)
    assert meta['work']['resolved']['year']==1965 and meta['work']['resolved']['publisher']=='Original Press'
    assert meta['work']['records']['openlibrary']['publisher_edition']=='/books/OL1M'
    data['providers']['openlibrary']['publisher_editions'].pop(0)
    meta,_=M.resolve(identity,inv,data)
    assert meta['work']['resolved']['year']==1965 and meta['work']['resolved']['publisher']==''


def test_author_inference_precedes_original_publisher_lookup(book,tmp_path,monkeypatch):
    inv,_=I.discover(book);identity,_=I.recover(book,inv);identity['book']['author']='';calls=[]
    class Provider:
        def __init__(self,name):self.name=name
        def unavailable(self):return None
        def search(self,title,author):return [candidate(self.name,id='/works/OL1W' if self.name=='openlibrary' else '1',title='Dune',authors=['Frank Herbert'],original_year=1965)]
        def publication_editions(self,c,year):
            calls.append((c['author'],year))
            return [dict(key='/books/OL1M',year=1965,publish_date='1965',publishers=['Original Press'])]
    monkeypatch.setattr(providers,'get',lambda name:Provider(name))
    collected,_=M.collect(identity,inv,tmp_path/'sources',['hardcover','openlibrary'])
    meta,_=M.resolve(identity,inv,collected)
    assert calls==[('Frank Herbert',1965)] and meta['work']['resolved']['publisher']=='Original Press'


def test_subtitle_conflicts_across_sources_cannot_auto_confirm_a_main_title(book):
    inv,_=I.discover(book);identity,_=I.recover(book,inv)
    identity['book'].update(title='History',author='Writer')
    def c(source,subtitle=''):return candidate(source,title='History',subtitle=subtitle,authors=['Writer'])
    data=dict(providers={'hardcover':dict(candidates=[c('hardcover','Ancient World')]),'openlibrary':dict(candidates=[c('openlibrary','Modern World'),c('openlibrary')])})
    meta,_=M.resolve(identity,inv,data)
    assert set(meta['work']['records'])=={'catalogue'}
    identity['book']['title']='History: Ancient World'
    meta,_=M.resolve(identity,inv,data)
    assert set(meta['work']['records'])=={'catalogue','hardcover'}
    # An omitted subtitle is usable when the available full titles agree.
    data['providers']['openlibrary']['candidates']=[c('openlibrary')]
    meta,_=M.resolve(identity,inv,data)
    assert set(meta['work']['records'])=={'catalogue','hardcover','openlibrary'}


def test_verified_layout_corrections_are_hash_bound_and_survive_split_and_rediscovery(book,monkeypatch):
    root=Path(book['path']).parent/'Anthologies';root.mkdir();repairs={};original=I.probe.probe_file
    for volume in (5,6):
        folder=root/f'Disc for {volume}';folder.mkdir()
        for n in (1,2,3):
            path=folder/f'{n:02} Story {n}.mp3';path.write_bytes(b'original audio')
            repairs[str(path)]=dict(sha256=I.digest(path),album=f'Stories Volume {volume}',disc=1,reason='Publisher contents verify the source disc',source_url='https://publisher.test/contents')
    def probe(path):
        info=original(path);info['tags'].update(album='Bad tag '+Path(path).parent.name,track=Path(path).stem[:2]+'/3');return info
    monkeypatch.setattr(I.probe,'probe_file',probe)
    parent=catalogue.add_book(title='Anthologies',path=str(root));catalogue.set_extra(parent['id'],'source_layout',dict(files=repairs))
    results=P.run([parent['id']],online=False,limit=1)
    assert [r['state'] for r in results]==['split','metadata_review','metadata_review']
    for child in results[1:]:
        saved=P.stage_data(child,'inventory');restored,_=I.discover(catalogue.get_book(child['book_id']))
        assert len(restored['files'])==3 and restored['work_hint']['title']==saved['work_hint']['title']
        assert len(I.layout_overrides(restored))==3 and len(restored['representations'])==1
        assert all(f['probe']['tags']['album'].startswith('Bad tag') for f in restored['files'])
        assert catalogue.get_extras(child['book_id'])['source_group']['data']['layout_overrides']==repairs
    changed=Path(next(iter(repairs)));changed.write_bytes(b'changed source audio')
    fresh,_=I.discover(parent)
    assert str(changed) not in I.layout_overrides(fresh) and len(I.layout_overrides(fresh))==5
    assert len(fresh['work_groups'])==3


@pytest.mark.parametrize('recording_passes',[True,False])
def test_existing_runs_use_current_automation_for_each_later_identification_stage(pipeline_fakes,recording_passes):
    run=P.run([pipeline_fakes['id']])[0];original=run['options']['policy']
    updated=policy.current();updated.update(auto_approve_metadata=True,auto_approve_audiobook=True)
    if not recording_passes:updated['gates']['recording.matched']={'min':2}
    policy.save(updated)
    assert P.approve_passing_metadata()==[run['id']]
    run=P.continue_review(run['id'])
    assert run['state']==('quality_review' if recording_passes else 'audiobook_review')
    assert run['options']['policy']==original
    approval=P.stage_data(run,'audiobook_approval')
    assert bool(approval)==recording_passes
    if approval:assert approval['automatic'] and approval['policy_revision']==policy.current()['revision']
    assert not P.approval_valid(run,'quality') and catalogue.get_plan(pipeline_fakes['id']) is None


def test_failed_ingest_processing_can_be_reproposed_with_its_valid_stage_approvals(pipeline_fakes):
    book=pipeline_fakes;run=complete_run([book['id']])[0]
    catalogue.set_plan(book['id'],'failed',error='Temporary encoder failure',keep_plan=True);store.finish(run['id'],'processing_failed')
    changed=P.Q.repropose([book['id']])
    result=P.Q.propose(ids=changed,limit=1)
    assert result['planned']==1 and result['failed']==0
    assert catalogue.get_plan(book['id'])['state']=='proposed'
    assert all(P.approval_valid(store.get(run['id']),phase) for phase in P.REVIEW_STAGES)
