"""Staged ingest invariants: source retention, fail-closed gates and exact manifests."""
from copy import deepcopy
from array import array
import json
import math
from pathlib import Path
import random
from types import SimpleNamespace

import pytest

from app import catalogue, db, details, providers, sources
from app.ingest import audio, inventory as I, metadata as M, pipeline as P, policy, store
from app.ingest.observe import capture
from app.ingest.worker import Worker
from app.providers.base import candidate


def recording(source='audible', **kw):
    return candidate(source,title='Dune',authors=['Frank Herbert'],narrators=['Reader'],runtime_minutes=60,language='english',abridged=False,asin='B000000001',release_date='2000-01-01',**kw)


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
    def plan(book,final,*args,**kw):
        assert kw['input_paths']==[book['path']]
        return dict(book_id=book['id'],title='Dune',author='Frank Herbert',out_dir=final+'/Dune',warnings=[],in_files=1,in_bytes=15,chapters=[],duration=3600)
    monkeypatch.setattr(P.P,'make_plan',plan)
    return book


def test_complete_run_persists_every_stage_and_replays_policy(pipeline_fakes):
    runs=P.run([pipeline_fakes['id']]);r=runs[0]
    assert r['state']=='eligible'
    assert [e['stage'] for e in r['events'] if e['status']=='done']==list(P.STAGES)
    assert all(e['elapsed']>=0 for e in r['events'])
    assert P.metrics(r)['metadata.original_work_link']==1
    assert P.evaluate(r['id'])['eligible']
    changed=deepcopy(policy.current());changed['gates']['recording.score']['min']=1.01
    assert not P.evaluate(r['id'],changed)['eligible']
    assert len([e for e in store.get(r['id'])['events'] if e['stage']=='evaluation'])==2
    assert P.dashboard()['runs'][0]['members']==[pipeline_fakes['id']]
    assert P.run()==[]
    assert catalogue.get_plan(pipeline_fakes['id']) is None


def test_enqueue_freezes_inputs_and_catches_same_size_edit(pipeline_fakes):
    r=P.run([pipeline_fakes['id']])[0];P.enqueue(r['id']);plan=catalogue.get_plan(pipeline_fakes['id'])['plan']
    assert P.Q._unchanged(pipeline_fakes,plan)
    with pytest.raises(ValueError,match='already has'):P.enqueue(r['id'])
    p=Path(pipeline_fakes['path']);old=p.read_bytes();p.write_bytes(b'x'*len(old))
    assert not P.Q._unchanged(pipeline_fakes,plan)
    with pytest.raises(ValueError,match='changed'):P.enqueue(r['id'])


def test_manual_metadata_edit_invalidates_plan(pipeline_fakes):
    r=P.run([pipeline_fakes['id']])[0];plan=P.stage_data(r,'planning')['plan'];catalogue.update_book(pipeline_fakes['id'],title='Different')
    assert not P.Q._unchanged(pipeline_fakes,plan)
    with pytest.raises(ValueError,match='metadata changed'):P.enqueue(r['id'])


def test_stage_failure_resumes_without_repeating_completed_stages(pipeline_fakes,monkeypatch):
    original=audio.quality
    monkeypatch.setattr(audio,'quality',lambda *a:(_ for _ in ()).throw(ValueError('quality failed')))
    r=P.run([pipeline_fakes['id']])[0];assert r['state']=='failed'
    assert not P.evaluate(r['id'])['eligible']
    monkeypatch.setattr(audio,'quality',original)
    monkeypatch.setattr(M,'collect',lambda *a:pytest.fail('must reuse completed provider stage'))
    resumed=P.run(resume=r['id'])[0]
    assert resumed['state']=='eligible'
    assert len([e for e in resumed['events'] if e['stage']=='quality' and e['status']=='failed'])==1
    with pytest.raises(ValueError,match='only interrupted'):P.run(resume=r['id'])


def test_changed_input_cannot_resume(pipeline_fakes,monkeypatch):
    monkeypatch.setattr(audio,'quality',lambda *a:(_ for _ in ()).throw(ValueError('fail')))
    r=P.run([pipeline_fakes['id']])[0];Path(pipeline_fakes['path']).write_bytes(b'new')
    with pytest.raises(ValueError,match='changed'):P.run(resume=r['id'])


def test_automation_explicit_policy_and_only_selected_job_runs(pipeline_fakes,monkeypatch):
    p=policy.current();p.update(auto_approve=True,auto_process=True);policy.save(p);called=[]
    monkeypatch.setattr(P.Q,'run_approved',lambda **kw:called.append(kw))
    r=P.run([pipeline_fakes['id']])[0]
    assert catalogue.get_plan(pipeline_fakes['id'])['state']=='approved'
    assert called==[dict(ids=[pipeline_fakes['id']])]
    plan=catalogue.get_plan(pipeline_fakes['id'])['plan'];policy.save(dict(policy.current(),auto_process=False))
    assert not P.Q._unchanged(pipeline_fakes,plan)


def test_threshold_failure_proposes_for_manual_review_only(pipeline_fakes):
    p=policy.current();p['gates']['recording.score']={'min':1.1};policy.save(p)
    r=P.run([pipeline_fakes['id']])[0];assert r['state']=='review';P.enqueue(r['id'])
    row=catalogue.get_plan(pipeline_fakes['id']);assert row['state']=='proposed'
    assert any('recording.score' in w for w in row['plan']['warnings'])
    with pytest.raises(ValueError,match='thresholds'):P.enqueue(r['id'],automatic=True)


def test_offline_missing_measurements_fail_closed(pipeline_fakes):
    r=P.run([pipeline_fakes['id']],online=False)[0]
    assert r['state']=='review'
    checks=P.evaluate(r['id'])['checks']
    assert next(c for c in checks if c['metric']=='recording.runtime_error')['reason']=='missing measurement'


def test_bad_run_and_policy_arguments(pipeline_fakes):
    with pytest.raises(ValueError):store.get('missing')
    with pytest.raises(ValueError):P.run(limit=0)
    p=policy.current();assert p['revision']==0
    for change in [dict(extra=True),dict(auto_process=True),dict(auto_approve=1),dict(providers=['bad']),dict(allowed_kinds=['bad']),dict(gates={})]:
        with pytest.raises(ValueError):policy.save(dict(p,**change))
    for value in (math.nan,math.inf,True):
        q=deepcopy(p);q['gates']['recording.score']={'min':value}
        with pytest.raises(ValueError):policy.save(q)
    for gate in ({'min':2,'max':1},{'bad':0},{}):
        q=deepcopy(p);q['gates']['recording.score']=gate
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


def test_audio_aligns_copies_and_selects_safe_complete_representation(book,monkeypatch,tmp_path):
    inv,_=I.discover(book);rep=inv['representations'][0];other=dict(rep,id='other',files=['second.mp3']);inv['representations'].append(other)
    inv['files'].append(dict(inv['files'][0],path='second.mp3'))
    values=array('f',(-40+random.Random(i).random()*30 for i in range(1000)))
    def extract(path,target):return values,dict(sha256=inv['files'][0]['sha256'],decode_messages='')
    monkeypatch.setattr(A:=audio.A,'extract',extract)
    aligned,metrics=audio.alignment(inv,tmp_path/'fingerprints');assert metrics['alignment.coverage']==1
    monkeypatch.setattr(audio.probe,'measure_book',lambda *a:dict(I=-20,TP=-1,LRA=5,silences=[]))
    monkeypatch.setattr(audio,'band_sample',lambda *a:-20)
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
    assert cli.main(['ingest-enqueue',rid])==0
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
    r=P.run([pipeline_fakes['id']])[0];assert r['state']=='failed'
    assert r['events'][-1]['stage']=='queue' and 'already processed' in r['events'][-1]['error']


def test_ignored_and_nonbook_content_cannot_auto_process(pipeline_fakes,monkeypatch):
    catalogue.ignore(pipeline_fakes['id']);r=P.run([pipeline_fakes['id']])[0]
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
    r=P.run([pipeline_fakes['id']])[0]
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
    assert len(P.run([pipeline_fakes['id'],pipeline_fakes['id']],limit=2))==1
    assert len(P.run([pipeline_fakes['id'],other['id']],limit=1))==1


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
    p['gates']['recording.score']={'min':.9,'max':1};assert policy.save(p)['revision']==1
    metrics={name:(gate.get('min',gate.get('max',0))) for name,gate in p['gates'].items()}
    assert policy.evaluate(metrics,'book',p)['eligible']
    metrics['recording.score']=float('nan');assert not policy.evaluate(metrics,'book',p)['eligible']


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
    r=P.run([pipeline_fakes['id']])[0];inv=P.stage_data(r,'inventory');selection=P.stage_data(r,'selection');meta=P.stage_data(r,'metadata');identity=P.stage_data(r,'identity')
    raw=tmp_path/'source-response.json';raw.write_text('{"a":1}')
    data,_=P._plan(pipeline_fakes,inv,selection,meta,identity,r['id'],[str(raw)])
    assert P.Q._unchanged(pipeline_fakes,data['plan'])
    raw.write_text('{"b":2}')
    assert not P.Q._unchanged(pipeline_fakes,data['plan'])
    store.event(r['id'],'planning','done',data=data)
    with pytest.raises(ValueError,match='archived evidence'):P.enqueue(r['id'])


def test_selected_copy_plan_validates_all_alternatives(pipeline_fakes,tmp_path):
    r=P.run([pipeline_fakes['id']])[0];inv=P.stage_data(r,'inventory');selection=P.stage_data(r,'selection')
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
    first=P.run([pipeline_fakes['id']])[0];monkeypatch.setattr(audio,'quality',lambda *a:pytest.fail('must reuse complete quality evidence'))
    second=P.run([pipeline_fakes['id']])[0]
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


def test_whole_file_and_part_sequence_are_one_group(book):
    folder=Path(book['path']).parent/'Hitchens-Hitch 22';folder.mkdir();whole=folder/'Christopher Hitchens-Hitch-22.m4b';part=folder/'Hitch-22-Part05.mp3';whole.write_bytes(b'whole');part.write_bytes(b'part')
    b=catalogue.add_book(title='Hitch22 part',path=str(part));inv,_=I.discover(b)
    assert inv['root']==str(folder) and len(inv['files'])==2 and len(inv['representations'])==2


def test_complete_copy_discovers_related_format_and_parts_from_either_entry(book):
    folder=Path(book['path']).parent/'Hitchens-Hitch 22';folder.mkdir();whole=folder/'Christopher Hitchens-Hitch-22.m4b';part=folder/'Hitch-22-Part05.mp3';whole.write_bytes(b'whole');part.write_bytes(b'part')
    b=catalogue.add_book(title='Hitch22',path=str(whole));inv,_=I.discover(b)
    assert inv['root']==str(folder) and len(inv['representations'])==2
