"""Saved quick reviews, exact candidate acceptance and durable revocation."""
import json
from pathlib import Path
import pytest
from app import catalogue, details, read_models
from app.ingest import inventory as I, metadata as M, match_review as R, pipeline as P, policy, store
from app.providers.base import candidate


@pytest.fixture
def review(tmp_path,monkeypatch):
    path=tmp_path/'Dune.m4b';path.write_bytes(b'original audio')
    catalogue.add_scanned([dict(title='Dune',author='Frank Herbert',path=str(path),narrator='',series_position=None,asin='',kind='book',file_count=1)])
    book=catalogue.list_books()[0]
    monkeypatch.setattr(I.probe,'probe_file',lambda path:dict(path=path,duration=3600,codec='aac',channels=1,sample_rate=24000,bit_rate=64,chapters=[],tags=dict(album='Dune',artist='Frank Herbert',composer='Reader',language='english',track='1/1')))
    monkeypatch.setattr(details,'_ffprobe',lambda p:dict(format=dict(tags={})))
    def collect(*args):
        return dict(providers={name:dict(status='ok',candidates=[candidate(name,id='1' if name=='hardcover' else '/works/OL1W',title='Dune',authors=['Frank Herbert'],original_year=1965,url='https://'+name+'.test/dune')]) for name in ('hardcover','openlibrary')},archives=[]),{}
    monkeypatch.setattr(M,'collect',collect)
    run=P.run([book['id']])[0]
    assert run['state']=='metadata_review', [(e['stage'],e['error']) for e in run['events'] if e['status']=='failed']
    P.publish_run(run['id'])
    return run


def suggest(run,tmp_path,**changes):
    c=P.raw_stage(run,'providers')['providers']['hardcover']['candidates'][0]
    row=dict(run_id=run['id'],candidate=c,above_threshold=True,model_score=.97,features=dict(title_edit=1,author_edit=1),candidate_key='chosen')|changes
    path=tmp_path/'pending.jsonl';path.write_text(json.dumps(row)+'\n')
    assert R.import_shortlist(path)==dict(imported=1)
    return R.detail(run['id'])


def test_shortlist_page_reads_saved_cards_without_raw_evidence_or_source_io(review,tmp_path,monkeypatch):
    value=suggest(review,tmp_path);assert value['shortlisted'] and not value['approved']
    def forbidden(*args,**kwargs):raise AssertionError('GET must only use saved views')
    monkeypatch.setattr(store,'get',forbidden);monkeypatch.setattr(M,'resolve',forbidden);monkeypatch.setattr(I,'snapshot_unchanged',forbidden)
    page=R.page('shortlist',q='Frank');assert page['total']==1 and page['rows'][0]['id']==review['id']
    assert 'evidence' not in page['rows'][0]
    assert R.detail(review['id'])['evidence']['providers']['hardcover']['candidates'][0]['title']=='Dune'
    assert R.page('shortlist',q='%')['total']==0 and R.page('shortlist',offset=25)['rows']==[]
    assert R.page('automatic')['total']==0
    with pytest.raises(ValueError,match='not indexed'):R.detail('missing')


def test_accept_records_the_displayed_candidate_and_preserves_source_identity(review,tmp_path):
    value=suggest(review,tmp_path);original=P.raw_stage(review,'identity')
    assert R.accept(review['id'],value['basis'])==dict(changed=True,phase='metadata')
    current=store.get(review['id']);meta=P.raw_stage(current,'metadata')
    assert meta['manual_selection']['candidate']['id']=='1'
    assert meta['work']['resolved']['title']=='Dune' and meta['work']['resolved']['authors']==['Frank Herbert']
    assert P.raw_stage(current,'identity')==original and P.approval_valid(current,'metadata')
    assert not P.approval_valid(current,'audiobook') and not P.approval_valid(current,'quality')
    assert not R.detail(review['id'])['shortlisted']
    with pytest.raises(ValueError,match='changed'):R.accept(review['id'],value['basis'])


@pytest.mark.parametrize('action',['accept','reject'])
def test_reimported_candidate_invalidates_open_shortlist_decisions(review,tmp_path,action):
    collected=P.raw_stage(review,'providers')
    other=candidate('hardcover',id='2',title='Dune Messiah',authors=['Frank Herbert'],original_year=1969)
    collected['providers']['hardcover']['candidates'].append(other)
    store.event(review['id'],'providers','done',data=collected)
    shown=suggest(store.get(review['id']),tmp_path)
    replacement=suggest(store.get(review['id']),tmp_path,candidate=other,candidate_key='replacement')
    assert shown['basis']!=replacement['basis']
    before=store.get(review['id'])['events']
    with pytest.raises(ValueError,match='changed'):getattr(R,action)(review['id'],shown['basis'])
    assert store.get(review['id'])['events']==before
    assert R.detail(review['id'])['suggestion']['candidate']['id']=='2'
    getattr(R,action)(review['id'],replacement['basis'])
    current=store.get(review['id'])
    if action=='accept':assert P.raw_stage(current,'metadata')['manual_selection']['candidate']['id']=='2'
    else:assert P.raw_stage(current,'match_review_decision')['candidate']['id']=='2'


def test_revocation_invalidates_all_consent_rejects_pending_plan_and_blocks_reautomation(review):
    p=policy.current();p['auto_approve_metadata']=True;policy.save(p)
    P.approve_review(review['id'],'metadata',automatic=True);store.finish(review['id'],'audiobook_review');P.publish_run(review['id'])
    value=R.page('automatic')['rows'][0]
    catalogue.set_plan(review['book_id'],'approved',dict(title='Dune'))
    assert R.revoke(review['id'],value['basis'])['changed']
    current=store.get(review['id'])
    assert current['state']=='metadata_review' and P.book_review_held(current)
    assert not any(P.approval_valid(current,p) for p in P.REVIEW_STAGES)
    assert catalogue.get_plan(review['book_id'])['state']=='rejected'
    assert not P.review_evaluation(current,'metadata')['eligible']
    assert P.header_phase(current,policy.current())=='metadata'
    assert P.approve_passing_metadata()==[] and R.page('automatic')['total']==0
    with pytest.raises(ValueError,match='automatic'):P.approve_review(review['id'],'metadata',automatic=True)
    with pytest.raises(ValueError,match='no current approval'):R.revoke(review['id'],R.basis(current))


def test_revocation_holds_refresh_and_explicit_manual_approval_releases_hold(review):
    P.approve_review(review['id'],'metadata');store.finish(review['id'],'audiobook_review');P.publish_run(review['id'])
    R.revoke(review['id'],R.basis(store.get(review['id'])))
    p=policy.current();p['auto_approve_metadata']=True;policy.save(p)
    fresh=P.refresh_metadata(review['id'],book_only=True)
    assert fresh['state']=='metadata_review' and P.book_review_held(fresh)
    P.approve_review(fresh['id'],'metadata');assert P.approval_valid(store.get(fresh['id']),'metadata')


def test_mutations_refuse_changed_superseded_running_and_locked_reviews(review,tmp_path):
    value=suggest(review,tmp_path)
    with pytest.raises(ValueError,match='changed'):R.accept(review['id'],'outdated')
    store.finish(review['id'],'running')
    with pytest.raises(ValueError,match='active job'):R.accept(review['id'],value['basis'])
    store.finish(review['id'],'metadata_review')
    catalogue.set_plan(review['book_id'],'proposed',dict(title='Dune'))
    with pytest.raises(ValueError,match='Replan'):R.accept(review['id'],value['basis'])
    catalogue.delete_plan(review['book_id'])
    identity=P.raw_stage(review,'identity');identity['book']['locked']=['title'];identity['book']['title']='Other Book'
    store.event(review['id'],'identity','done',data=identity)
    read_models.save('match_suggestion',review['id'],dict(value['suggestion'],basis=R.basis(store.get(review['id']))))
    with pytest.raises(ValueError,match='catalogue metadata changed|locked'):R.accept(review['id'],R.basis(store.get(review['id'])))
    newer=store.create(review['book_id'],{});store.link(newer,[review['book_id']])
    with pytest.raises(ValueError,match='newer run'):R.revoke(review['id'],R.basis(store.get(review['id'])))
    assert R.page('shortlist')['total']==0


def test_stale_or_unapproved_suggestions_cannot_be_accepted(review):
    with pytest.raises(ValueError,match='stale'):R.accept(review['id'],R.basis(review))
    with pytest.raises(ValueError,match='no current approval'):R.revoke(review['id'],R.basis(review))


def test_revoke_processed_identity_preserves_outputs_and_audit_history(review):
    P.approve_review(review['id'],'metadata');store.finish(review['id'],'processed');P.publish_run(review['id'])
    before=len(store.get(review['id'])['events'])
    R.revoke(review['id'],R.basis(store.get(review['id'])))
    current=store.get(review['id']);assert current['state']=='processed' and len(current['events'])==before+2
    assert P.raw_stage(current,'metadata_approval') and not P.approval_valid(current,'metadata')


def test_accept_uses_chosen_candidate_when_current_resolver_proposes_another_work(review,tmp_path):
    collected=P.raw_stage(review,'providers');other=candidate('hardcover',id='2',title='Dune Messiah',authors=['Frank Herbert'],original_year=1969)
    collected['providers']['hardcover']['candidates'].append(other)
    store.event(review['id'],'providers','done',data=collected)
    current=store.get(review['id']);value=suggest(current,tmp_path,candidate=other)
    assert value['match']['title']=='Dune' and value['suggestion']['candidate']['title']=='Dune Messiah'
    R.accept(review['id'],value['basis'])
    current=store.get(review['id']);meta=P.raw_stage(current,'metadata')
    assert meta['work']['resolved']['title']=='Dune Messiah' and meta['work']['resolved']['year']==1969
    assert meta['manual_selection']['candidate']['id']=='2' and P.approval_valid(current,'metadata')
    assert meta['resolved']['merged']['fields']['title']['value']=='Dune Messiah'
    assert P.raw_stage(current,'identity')['book']['title']=='Dune'


def test_firm_confirmation_hides_automatic_match_and_preserves_dependent_consent(review):
    p=policy.current();p['auto_approve_metadata']=True;policy.save(p)
    P.approve_review(review['id'],'metadata',automatic=True);store.finish(review['id'],'quality_review')
    # Retained downstream consents bind the existing approval event, not the UI decision.
    for phase in ('audiobook','quality'):
        current=store.get(review['id']);store.event(review['id'],phase+'_approval','done',data=dict(signature=P.review_signature(current,phase),automatic=False,scope=phase))
    current=store.get(review['id']);approval=P.raw_stage(current,'metadata_approval');signatures={phase:P.review_signature(current,phase) for phase in P.REVIEW_STAGES}
    assert all(P.approval_valid(current,phase) for phase in P.REVIEW_STAGES)
    P.publish_run(review['id']);value=R.page('automatic')['rows'][0]
    assert R.confirm(review['id'],value['basis'])==dict(changed=True,confirmed=True)
    confirmed=store.get(review['id'])
    assert P.book_confirmed(confirmed) and R.page('automatic')['total']==0
    assert P.raw_stage(confirmed,'metadata_approval')==approval
    assert {phase:P.review_signature(confirmed,phase) for phase in P.REVIEW_STAGES}==signatures
    assert all(P.approval_valid(confirmed,phase) for phase in P.REVIEW_STAGES)
    P.publish_run(review['id']);assert R.page('automatic')['total']==0
    # A firm human decision remains book consent when automatic thresholds change.
    p=policy.current();p['gates']['work.score']['min']=1.1;policy.save(p)
    assert P.approval_valid(store.get(review['id']),'metadata')
    headers=store.workflow_snapshot()['runs'];assert P.book_confirmed(headers[review['id']])
    assert P.header_phase(headers[review['id']],policy.current())!='metadata'
    assert P.book_confirmed(next(r for r in store.review_headers() if r['id']==review['id']))
    assert R.confirm(review['id'],R.basis(store.get(review['id'])))==dict(changed=False,confirmed=True)
    with pytest.raises(ValueError,match='changed'):R.confirm(review['id'],value['basis'])


def test_shortlist_rejection_persists_across_reindex_and_reimport(review,tmp_path):
    value=suggest(review,tmp_path)
    assert R.reject(review['id'],value['basis'])==dict(changed=True,rejected=True)
    current=store.get(review['id']);decision=P.raw_stage(current,'match_review_decision')
    assert decision['action']=='reject' and decision['candidate_key']=='chosen'
    assert P.book_review_held(current) and not P.approval_valid(current,'metadata')
    assert R.page('shortlist')['total']==0
    P.publish_run(review['id']);assert R.page('shortlist')['total']==0
    assert R.import_shortlist(tmp_path/'pending.jsonl')==dict(imported=0)
    with pytest.raises(ValueError,match='stale'):R.reject(review['id'],R.basis(current))


def test_reject_automatic_withdraws_consent_and_human_confirmation_is_versioned(review):
    p=policy.current();p['auto_approve_metadata']=True;policy.save(p)
    P.approve_review(review['id'],'metadata',automatic=True);store.finish(review['id'],'audiobook_review')
    current=store.get(review['id']);R.confirm(review['id'],R.basis(current))
    current=store.get(review['id']);assert P.book_confirmed(current)
    R.reject(review['id'],R.basis(current))
    current=store.get(review['id']);assert not P.book_confirmed(current) and not P.approval_valid(current,'metadata')
    with pytest.raises(ValueError,match='displayed approval'):R.confirm(review['id'],R.basis(current))
    P.approve_review(review['id'],'metadata')
    assert not P.book_confirmed(store.get(review['id']))


def test_pages_limit_ten_and_cursor_survives_reviewed_rows_disappearing(review):
    from copy import deepcopy
    from app import db
    template=R.detail(review['id']);ids=[]
    for number in range(12):
        book=catalogue.add_book(title=f'Paged {number}',author='Writer');rid=store.create(book['id'],{})
        c=db.connect()
        try:
            with c:c.execute("UPDATE ingest_runs SET created='2026-01-01T00:00:00+00:00' WHERE id=?",(rid,))
        finally:c.close()
        value=deepcopy(template);value.update(id=rid,book_id=book['id'],automatic=True)
        read_models.save('match_review',rid,value);ids.append(rid)
    ids.sort()
    first=R.page('automatic');assert first['limit']==10 and len(first['rows'])==10 and first['total']==12
    assert [r['id'] for r in first['rows']]==ids[:10]
    cursor=first['rows'][-1]['id']
    for rid in ids[:10]:
        value=R.detail(rid);value['automatic']=False;read_models.save('match_review',rid,value)
    second=R.page('automatic',offset=10,after=cursor)
    assert second['total']==2 and [r['id'] for r in second['rows']]==ids[10:]
    assert [r['id'] for r in R.page('automatic')['rows']]==ids[10:]
    with pytest.raises(ValueError,match='page changed'):R.page('automatic',after='missing')


def test_restore_rejected_automatic_match_renews_only_book_consent(review):
    p=policy.current();p['auto_approve_metadata']=True;policy.save(p)
    P.approve_review(review['id'],'metadata',automatic=True);store.finish(review['id'],'quality_review')
    current=store.get(review['id'])
    store.event(review['id'],'audiobook_approval','done',data=dict(signature=P.review_signature(current,'audiobook'),automatic=False))
    P.publish_run(review['id']);shown=R.detail(review['id'])
    R.confirm(review['id'],shown['basis'])
    R.reject(review['id'],R.detail(review['id'])['basis'])
    rejected=R.detail(review['id'])
    assert R.restore(review['id'],rejected['basis'])==dict(changed=True,confirmed=True)
    current=store.get(review['id'])
    assert P.book_confirmed(current) and P.approval_valid(current,'metadata')
    assert not P.approval_valid(current,'audiobook')
    assert not P.book_review_held(current) and current['state']=='audiobook_review'
    with pytest.raises(ValueError,match='changed'):R.restore(review['id'],rejected['basis'])
    with pytest.raises(ValueError,match='no longer rejected'):R.restore(review['id'],R.detail(review['id'])['basis'])


def test_restore_shortlist_rejection_accepts_only_its_original_candidate(review,tmp_path):
    shown=suggest(review,tmp_path)
    R.reject(review['id'],shown['basis'])
    result=R.restore(review['id'],R.detail(review['id'])['basis'])
    assert result['changed'] and result['resume']
    current=store.get(review['id'])
    assert P.approval_valid(current,'metadata') and not P.book_review_held(current)
    assert P.raw_stage(current,'metadata')['manual_selection']['candidate']['id']=='1'


def test_restore_refuses_changed_candidate_or_source_bytes(review,tmp_path):
    shown=suggest(review,tmp_path);R.reject(review['id'],shown['basis'])
    rejected=R.detail(review['id']);suggestion=read_models.get('match_suggestion',review['id'])
    read_models.save('match_suggestion',review['id'],dict(suggestion,candidate_key='replacement'))
    with pytest.raises(ValueError,match='suggestion changed'):R.restore(review['id'],rejected['basis'])
    read_models.save('match_suggestion',review['id'],suggestion)
    Path(P.raw_stage(review,'inventory')['files'][0]['path']).write_bytes(b'changed audio!')
    with pytest.raises(ValueError,match='source files changed'):R.restore(review['id'],rejected['basis'])
    assert P.book_review_held(store.get(review['id']))


def test_shortlist_acceptance_hashes_sources_once_without_skipping_integrity_checks(review,tmp_path,monkeypatch):
    shown=suggest(review,tmp_path);validate=P._assert_review_inputs;calls=[]
    def checked(run):
        calls.append(run['id']);return validate(run)
    monkeypatch.setattr(P,'_assert_review_inputs',checked)
    assert R.accept(review['id'],shown['basis'])['changed']
    assert calls==[review['id']]


def test_deferred_shortlist_acceptance_stays_reversible_until_next(review,tmp_path):
    from app.jobs import JobQueue
    from app.server import tasks
    shown=suggest(review,tmp_path);jobs=JobQueue(None)
    accepted=tasks.execute(jobs,'ingest','match_review',dict(run_id=review['id'],action='accept',expected=shown['basis'],defer_continuation=True))
    assert accepted['changed'] and accepted['basis']==R.detail(review['id'])['basis']
    assert store.get(review['id'])['state']=='audiobook_review' and not jobs.tasks()
    rejected=tasks.execute(jobs,'ingest','match_review',dict(run_id=review['id'],action='reject',expected=accepted['basis'],defer_continuation=True))
    assert P.book_review_held(store.get(review['id']))
    restored=tasks.execute(jobs,'ingest','match_review',dict(run_id=review['id'],action='restore',expected=rejected['basis'],defer_continuation=True))
    assert restored['changed'] and P.book_confirmed(store.get(review['id']))
    assert store.get(review['id'])['state']=='audiobook_review' and not jobs.tasks()
