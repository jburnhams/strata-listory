"""Saved quick-review cards and explicit human decisions on original-work matches."""
import argparse
from collections import defaultdict
import copy
import hashlib
import json
from pathlib import Path
from app import catalogue, db, read_models, sources
from app.ingest import metadata, store


CANDIDATE_FIELDS=('source','id','url','title','subtitle','subtitle_edition','authors','narrators','original_year','publisher','release_date','runtime_minutes','language','abridged','asin','isbns','series','series_position','genres','description','rating','ratings_count','pages')


def basis(run):
    return hashlib.sha256(json.dumps([(e['stage'],e['id']) for e in run['events'] if e['status']=='done' and e['stage'] in ('inventory','identity','providers','metadata','metadata_approval','metadata_revocation','match_review_decision','match_shortlist')]).encode()).hexdigest()


def chosen_fields(candidate):return {k:candidate.get(k) for k in CANDIDATE_FIELDS}


def publish(run):
    """Worker projection only; request handlers read small persisted cards."""
    from app.ingest import pipeline as P
    identity=P.raw_stage(run,'identity') or {};inv=P.raw_stage(run,'inventory') or {}
    with P.saved_evidence_only():
        meta=P.stage_data(run,'metadata') or {}
        evaluation=P.review_evaluation(run,'metadata') if all(P.raw_stage(run,s) is not None for s in P.REVIEW_STAGES['metadata']) else dict(checks=[],complete=False)
    book=identity.get('book',{});work=meta.get('work') or {};resolved=work.get('resolved') or meta.get('resolved') or {}
    approval=P.raw_stage(run,'metadata_approval');held=P.book_review_held(run);suggestion=read_models.get('match_suggestion',run['id'])
    approved=bool(approval and not held and approval.get('signature')==P.review_signature(run,'metadata'))
    active_suggestion=bool(suggestion and suggestion['basis']==basis(run) and not approved and run['state'] in ('metadata_review','review','eligible'))
    plan=catalogue.get_plan(run['book_id']);blocked='Wait for the active job to finish.' if run['state'] in ('running','processing') or plan and plan['state']=='running' else ''
    providers=(P.raw_stage(run,'providers') or {}).get('providers',{})
    tags=defaultdict(set)
    for entry in identity.get('evidence',[]):
        for key,value in entry.get('tags',{}).items():
            if key in ('title','album','artist','author','album_artist','narrator','narratedby','date','genre','language','asin') and value:tags[key].add(str(value))
    approved_at=next((e['created'] for e in reversed(run['events']) if e['stage']=='metadata_approval' and e['status']=='done'),None)
    value=dict(id=run['id'],book_id=run['book_id'],created=run['created'],state=run['state'],basis=basis(run),automatic=bool(approved and approval.get('automatic') and not P.book_confirmed(run)),confirmed=P.book_confirmed(run),review_decision=P.raw_stage(run,'match_review_decision'),approved=approved,held=held,shortlisted=active_suggestion,
        blocked=blocked,approval=approval,approved_at=approved_at,local=dict(title=book.get('title',''),author=book.get('author',''),narrator=book.get('narrator',''),path=book.get('path',''),asin=book.get('asin',''),kind=identity.get('kind','unknown'),locked=book.get('locked',[])),
        match=dict(title=resolved.get('title',''),authors=resolved.get('authors',[]),year=resolved.get('year'),publisher=resolved.get('publisher',''),rows=work.get('rows',[]),sources=[name for name in work.get('records',meta.get('records',{})) if name!='catalogue']),
        recording=meta.get('performance') or {},suggestion=suggestion if active_suggestion else None,checks=evaluation['checks'],complete=evaluation['complete'],
        source=dict(folder=inv.get('root',book.get('path','')),count=len(inv.get('files',[])),files=[Path(f['path']).name for f in inv.get('files',[])],hours=max((r.get('duration',0) for r in inv.get('representations',[])),default=0)/3600),
        evidence=dict(records=work.get('records',meta.get('records',{})),providers={name:dict(status=entry.get('status'),reason=entry.get('reason'),candidates=[chosen_fields(c) for c in entry.get('candidates',[])]) for name,entry in providers.items()},tags={k:sorted(v) for k,v in tags.items()}),cover=bool(read_models.get('cover',run['book_id'])))
    read_models.save('match_review',run['id'],value)
    return value


def page(mode,offset=0,q='',after=None):
    c=db.connect()
    try:
        predicate="json_extract(v.data,'$.automatic')=1" if mode=='automatic' else "json_extract(v.data,'$.shortlisted')=1"
        where=f"""v.kind='match_review' AND {predicate}
            AND NOT EXISTS (SELECT 1 FROM ingest_members mine JOIN ingest_members later ON later.book_id=mine.book_id
                JOIN ingest_runs newer ON newer.id=later.run_id WHERE mine.run_id=r.id AND newer.created>r.created)"""
        params=[]
        if q.strip():
            where+=" AND lower(json_extract(v.data,'$.local.title')||' '||json_extract(v.data,'$.local.author')||' '||COALESCE(json_extract(v.data,'$.match.title'),'')||' '||COALESCE(json_extract(v.data,'$.suggestion.candidate.title'),'')) LIKE ? ESCAPE '\\'"
            params.append('%'+q.strip().lower().replace('\\','\\\\').replace('%','\\%').replace('_','\\_')+'%')
        total=c.execute('SELECT count(*) FROM read_models v JOIN ingest_runs r ON r.id=v.owner WHERE '+where,params).fetchone()[0]
        row_params=list(params)
        if after:
            cursor=c.execute('SELECT id,created FROM ingest_runs WHERE id=?',(after,)).fetchone()
            if cursor is None:raise ValueError('Review page changed. Refresh before continuing.')
            where+=' AND (r.created<? OR (r.created=? AND r.id>?))'
            row_params.extend((cursor['created'],cursor['created'],cursor['id']))
        rows=[]
        for record in c.execute('SELECT v.data FROM read_models v JOIN ingest_runs r ON r.id=v.owner WHERE '+where+' ORDER BY r.created DESC,r.id LIMIT 10 OFFSET ?',row_params+[0 if after else offset]):
            row=json.loads(record[0]);row.pop('evidence',None);row['source']['files']=row['source']['files'][:3]
            # Recording evidence is available on expansion, without a large list payload.
            row['recording']={k:row['recording'].get(k) for k in ('matched','local','rows')}
            rows.append(row)
        return dict(rows=rows,total=total,offset=offset,limit=10)
    finally:c.close()


def detail(run_id):
    value=read_models.get('match_review',run_id)
    if value is None:raise ValueError('review details are not indexed yet')
    return value


def import_shortlist(path):
    """Explicit local artifact import; GET requests never scan model files."""
    from app.ingest import pipeline as P
    rows=[json.loads(line) for line in Path(path).read_text().splitlines() if line.strip()];best={}
    for row in rows:
        if row.get('above_threshold') and row.get('candidate'):
            if row['run_id'] not in best or row['model_score']>best[row['run_id']]['model_score']:best[row['run_id']]=row
    imported=0
    for rid,row in best.items():
        run=store.get(rid)
        if run['state'] not in ('metadata_review','review','eligible') or P.raw_stage(run,'metadata_approval'):continue
        if store.latest_id(run['book_id'])!=rid:continue
        if (P.raw_stage(run,'identity') or {}).get('kind')!='book':continue
        collected=metadata.work_edition_evidence(P.raw_stage(run,'providers') or dict(providers={}))
        c=row['candidate']
        decision=P.raw_stage(run,'match_review_decision') or {}
        if decision.get('action')=='reject' and decision.get('candidate_key')==row.get('candidate_key'):continue
        if not any(c.get('source')==name and c.get('id')==other.get('id') and c.get('title')==other.get('title') and c.get('authors')==other.get('authors') and c.get('subtitle','')==other.get('subtitle','') for name,entry in collected['providers'].items() for other in entry.get('candidates',[])):continue
        # Version the displayed suggestion as well as the underlying evidence:
        # an open card must never consent to a later import's replacement.
        store.event(rid,'match_shortlist','done',data=dict(candidate=chosen_fields(c),candidate_key=row['candidate_key'],model=Path(path).parent.name,score=row['model_score']))
        run=store.get(rid)
        read_models.save('match_suggestion',rid,dict(candidate=chosen_fields(c),score=row['model_score'],features=row['features'],basis=basis(run),candidate_key=row['candidate_key'],model=Path(path).parent.name))
        P.publish_run(rid);imported+=1
    return dict(imported=imported)


def _current(run_id,expected):
    run=store.get(run_id)
    if basis(run)!=expected:raise ValueError('This review changed. Refresh before deciding.')
    inv=next((e['data'] for e in reversed(run['events']) if e['stage']=='inventory' and e['status']=='done'),{})
    if any(store.latest_id(m['id'])!=run_id for m in inv.get('members',[])) or store.latest_id(run['book_id'])!=run_id:raise ValueError('A newer run exists. Refresh before deciding.')
    plan=catalogue.get_plan(run['book_id'])
    if run['state'] in ('running','processing') or plan and plan['state']=='running':raise ValueError('Wait for the active job to finish.')
    return run


def revoke(run_id,expected):
    from app.ingest import pipeline as P
    from app.process import queue
    run=_current(run_id,expected)
    if not P.raw_stage(run,'metadata_approval') or P.book_review_held(run):raise ValueError('This book has no current approval to withdraw.')
    queue.decide([run['book_id']],False)
    store.event(run_id,'metadata_revocation','done',data=dict(reason='Book match withdrawn during quick review',basis=expected))
    store.event(run_id,'match_review_decision','done',data=dict(action='reject',scope='book',signature=P.review_signature(run,'metadata'),basis=expected))
    if run['state']!='processed':store.finish(run_id,'metadata_review')
    P.publish_run(run_id)
    return dict(changed=True,book_id=run['book_id'])


def confirm(run_id,expected):
    """Confirm book consent without changing dependent approval signatures."""
    from app.ingest import pipeline as P
    run=_current(run_id,expected);approval=P.raw_stage(run,'metadata_approval')
    if not approval or P.book_review_held(run) or approval.get('signature')!=P.review_signature(run,'metadata'):raise ValueError('This book no longer has the displayed approval. Refresh before deciding.')
    if P.book_confirmed(run):return dict(changed=False,confirmed=True)
    approval_id=next(e['id'] for e in reversed(run['events']) if e['stage']=='metadata_approval' and e['status']=='done')
    store.event(run_id,'match_review_decision','done',data=dict(action='approve',scope='book',automatic=False,approval_id=approval_id,signature=P.review_signature(run,'metadata'),basis=expected))
    P.publish_run(run_id)
    return dict(changed=True,confirmed=True)


def reject(run_id,expected):
    from app.ingest import pipeline as P
    run=_current(run_id,expected)
    if P.raw_stage(run,'metadata_approval') and not P.book_review_held(run):return revoke(run_id,expected)
    suggestion=read_models.get('match_suggestion',run_id)
    if not suggestion or suggestion['basis']!=expected:raise ValueError('The model suggestion is stale. Refresh the shortlist.')
    store.event(run_id,'metadata_revocation','done',data=dict(reason='Model match rejected during quick review'))
    store.event(run_id,'match_review_decision','done',data=dict(action='reject',scope='book',candidate_key=suggestion['candidate_key'],candidate=suggestion['candidate'],basis=expected))
    store.finish(run_id,'metadata_review');P.publish_run(run_id)
    return dict(changed=True,rejected=True)


def accept(run_id,expected):
    from app.ingest import pipeline as P
    run=_current(run_id,expected);suggestion=read_models.get('match_suggestion',run_id)
    if not suggestion or suggestion['basis']!=expected:raise ValueError('The model suggestion is stale. Refresh the shortlist.')
    if run['state'] not in ('metadata_review','review','eligible'):raise ValueError('This book is no longer awaiting identification.')
    if catalogue.get_plan(run['book_id']):raise ValueError('Replan the existing processing proposal before accepting another book match.')
    P._assert_review_inputs(run);identity=P.raw_stage(run,'identity');candidate=suggestion['candidate']
    # Providers sometimes repeat the subtitle inside title; preserve the asserted title.
    title=metadata.work_title(candidate['title'] if not candidate.get('subtitle') or candidate['subtitle'] in candidate['title'] else candidate['title']+': '+candidate['subtitle'])
    authors=candidate.get('authors') or []
    if not title or not authors:raise ValueError('The suggested match needs a title and author before acceptance.')
    proposed=dict(identity['book'],title=title,author=', '.join(authors))
    for field in ('title','author'):
        if field in identity['book'].get('locked',[]) and proposed[field]!=identity['book'].get(field):raise ValueError('Edit locked '+field+' before accepting a different match.')
    prior=P.stage_data(run,'metadata');value=copy.deepcopy(prior);records=copy.deepcopy((prior.get('work') or {}).get('records',{}))
    # Other assertions remain visible as evidence; only identity-compatible
    # records support the chosen work. The exact displayed candidate is retained.
    records={name:record for name,record in records.items() if name=='catalogue' or metadata.compatible_work(proposed,record)}
    records[candidate['source']]=sources.from_candidate(candidate)
    resolved=sources.resolve(proposed,records,field_sources={'publisher':('hardcover','openlibrary')})
    resolved.update(title=title,authors=authors)
    value['work']=dict(records=records,resolved=resolved,rows=metadata.scoped_rows(proposed,resolved,'work'),families=len({metadata.family(s) for s in records if s!='catalogue'}),score=metadata.work_candidate_score(identity['book'],candidate))
    value['resolved']=dict(value.get('resolved',{}),**resolved)
    value['manual_selection']=dict(candidate=candidate,model_score=suggestion['score'],candidate_key=suggestion['candidate_key'])
    value['version']=metadata.VERSION
    measure=dict(value.get('analysis_metrics',{}));measure.update({'work.score':value['work']['score'],'metadata.work_families':value['work']['families']});value['analysis_metrics']=measure
    store.event(run_id,'metadata_revocation','done',data=dict(reason='Explicit match selection pending human approval'))
    store.event(run_id,'metadata','done',metrics=measure,data=value)
    result=P.approve_review(run_id,'metadata')
    P.publish_run(run_id)
    return result


def main():
    parser=argparse.ArgumentParser(description=__doc__);parser.add_argument('shortlist',nargs='?');args=parser.parse_args()
    if args.shortlist:print(json.dumps(import_shortlist(args.shortlist)))
    else:
        for run in store.all_runs():publish(run)
        print('Indexed saved match reviews')


if __name__=='__main__':main()
