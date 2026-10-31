"""One durable run per group; every stage has elapsed time, metrics and evidence."""
from datetime import datetime, timezone
import json
import math
from pathlib import Path
import time

from app import catalogue, oslib, settings, sources
from app.ingest import audio, inventory, metadata, policy, store
from app.process import plan as P, queue as Q

STAGES=('inventory','identity','providers','metadata','alignment','quality','selection','planning','eligibility')


def stage_data(run,stage):
    rows=[e for e in run['events'] if e['stage']==stage and e['status']=='done']
    return rows[-1]['data'] if rows else None


def metrics(run):
    out={}
    for e in run['events']:
        if e['status']=='done':out.update(e['metrics'])
    return out


def evaluate(run_id,value=None,save=True):
    run=store.get(run_id);identity=stage_data(run,'identity') or {};result=policy.evaluate(metrics(run),identity.get('kind','unknown'),value)
    if identity.get('book',{}).get('review')=='ignored':result.update(eligible=False,auto_approve=False,auto_process=False,review_blocked=True)
    if run['state']=='failed' or any(stage_data(run,s) is None for s in STAGES[:-1]):result.update(eligible=False,auto_approve=False,auto_process=False,incomplete=True)
    if save:store.event(run_id,'evaluation','done',data=result)
    return result


def _plan(book,inv,selected,meta,identity,run_id,archives=()):
    if not inventory.unchanged(selected['manifest']):raise ValueError('input changed since inventory')
    # Do not give an episode/radio source a print-work processing identity by accident.
    if identity['kind']!='book':return dict(plan=None,reason='non-book content requires a separate processing recipe'),{'planning.warning_count':None}
    plan=P.make_plan(book,settings.final_dir(),None,None,meta['records'],input_paths=selected['paths'],measurements=selected['measurements'])
    plan['ingest']=dict(run_id=run_id,group=inv['group'],primary_id=inv['primary_id'],members=[b['id'] for b in inv['members']],root=inv['root'],sidecars=inv['sidecars'],member_signatures={str(b['id']):inventory.signature(b) for b in inv['members']},manifest=selected['manifest'],book_signature=inventory.signature(catalogue.get_book(inv['primary_id'])))
    plan['metadata_evidence']={k:meta[k] for k in ('records','resolved','recording','community','candidates','rejected')}
    plan['ingest']['inventory_manifest']=inv['files']
    plan['ingest']['evidence_manifest']=[dict(path=str(p),size=p.stat().st_size,sha256=inventory.digest(p)) for path in archives for p in [Path(path)]]
    plan['source_paths']=selected['paths']
    return dict(plan=plan),{'planning.warning_count':len(plan['warnings'])}


def quality_with_cache(inv,aligned,run_id):
    key=lambda x:sorted((f['path'],f['sha256']) for f in x['files'])
    for old in store.runs(200):
        if old['id']==run_id:continue
        prior=stage_data(old,'inventory');data=stage_data(old,'quality')
        if prior and data and data.get('version',1)==1 and key(prior)==key(inv) and prior['representations']==inv['representations'] and all(not r['decode_messages'] for r in aligned['representations']):
            completed=[e for e in old['events'] if e['stage']=='quality' and e['status']=='done']
            if completed and completed[-1]['metrics'].get('quality.complete')==1:
                return dict(data,reused_from=old['id']),dict(completed[-1]['metrics'],**{'quality.cache_hit':1})
    data,metrics=audio.quality(inv,aligned)
    return data,dict(metrics,**{'quality.cache_hit':0})


def run_one(book_id,run_id=None,online=True):
    book=catalogue.get_book(book_id);p=policy.current()
    if run_id:
        run=store.get(run_id)
        if run['book_id']!=book_id:raise ValueError('run/book mismatch')
        if run['state'] not in ('failed','running'):raise ValueError('only interrupted or failed runs can resume')
        p=run['options']['policy'];online=run['options']['online']
        inv=stage_data(run,'inventory')
        if inv and (not inventory.snapshot_unchanged(inv) or any(inventory.signature(m)!=inventory.signature(catalogue.get_book(m['id'])) for m in inv['members']) or run['options']['book_signature']!=inventory.signature(book)):raise ValueError('inputs or metadata changed; start a new run')
        store.finish(run_id,'running')
    else:run_id=store.create(book_id,dict(policy=p,online=online,book_signature=inventory.signature(book)))
    folder=Path(settings.data_dir())/'ingest'/run_id;folder.mkdir(parents=True,exist_ok=True);data={}
    for stage in STAGES:
        prior=stage_data(store.get(run_id),stage)
        if prior is not None:
            data[stage]=prior
            if stage=='inventory':book=catalogue.get_book(prior['primary_id'])
            continue
        started=time.monotonic();store.event(run_id,stage,'running')
        try:
            if stage=='inventory':
                value,measure=inventory.discover(book);store.link(run_id,[b['id'] for b in value['members']]);book=catalogue.get_book(value['primary_id'])
            elif stage=='identity':value,measure=inventory.recover(book,data['inventory'])
            elif stage=='providers':
                value,measure=metadata.collect(data['identity'],data['inventory'],folder/'sources',p['providers']) if online else (dict(providers={},archives=[],offline=True),{'metadata.provider_attempts':0,'metadata.provider_errors':0,'metadata.raw_responses':0})
            elif stage=='metadata':value,measure=metadata.resolve(data['identity'],data['inventory'],data['providers'])
            elif stage=='alignment':value,measure=audio.alignment(data['inventory'],folder/('fingerprints-'+str(time.time_ns())))
            elif stage=='quality':value,measure=quality_with_cache(data['inventory'],data['alignment'],run_id)
            elif stage=='selection':value,measure=audio.select(data['inventory'],data['alignment'],data['quality'])
            elif stage=='planning':value,measure=_plan(book,data['inventory'],data['selection'],data['metadata'],data['identity'],run_id,data['providers']['archives'])
            else:
                value=evaluate(run_id,p,save=False);measure={'eligibility.passed':sum(c['passed'] for c in value['checks']),'eligibility.failed':sum(not c['passed'] for c in value['checks'])}
                if not inventory.unchanged(data['selection']['manifest']):raise ValueError('inputs changed before eligibility decision')
            data[stage]=value;store.event(run_id,stage,'done',measure,value,elapsed=time.monotonic()-started)
        except Exception as e:
            store.event(run_id,stage,'failed',error=str(e),elapsed=time.monotonic()-started);store.finish(run_id,'failed');return store.get(run_id)
    eligible=data['eligibility']['eligible'];store.finish(run_id,'eligible' if eligible else 'review')
    if data['eligibility']['auto_approve']:
        try:
            enqueue(run_id,automatic=True)
            if data['eligibility']['auto_process']:Q.run_approved(ids=[data['inventory']['primary_id']])
        except Exception as e:
            store.event(run_id,'queue','failed',error=str(e));store.finish(run_id,'failed')
    return store.get(run_id)


def run(ids=None,limit=5,online=True,resume=None):
    if not 1<=limit<=200:raise ValueError('limit must be 1..200')
    lock=Path(settings.data_dir())/'ingest.lock'
    with lock.open('a+b') as f,oslib.file_lock(f):
        if resume:
            previous=store.get(resume);return [run_one(previous['book_id'],resume)]
        books=[catalogue.get_book(i) for i in ids] if ids else [b for b in catalogue.list_books(kind='book') if b['review']!='ignored' and store.latest(b['id']) is None]
        seen=set();results=[]
        for b in books:
            if len(results)>=limit:break
            if b['id'] in seen:continue
            result=run_one(b['id'],online=online);results.append(result);inv=stage_data(result,'inventory')
            if inv:seen.update(m['id'] for m in inv['members'])
        return results


def enqueue(run_id,automatic=False):
    run=store.get(run_id);inv=stage_data(run,'inventory');planned=stage_data(run,'planning');selection=stage_data(run,'selection')
    if run['state'] not in ('review','eligible') or not inv or not planned or not planned.get('plan'):raise ValueError('ingest has no completed processing plan')
    if not inventory.snapshot_unchanged(inv):raise ValueError('source files or metadata changed; ingest again')
    plan=planned['plan'];bid=inv['primary_id']
    if not inventory.unchanged(plan['ingest'].get('evidence_manifest',[])):raise ValueError('archived evidence changed; ingest again')
    for member in inv['members']:
        if inventory.signature(member)!=inventory.signature(catalogue.get_book(member['id'])):raise ValueError('group catalogue metadata changed; ingest again')
    if plan['ingest']['book_signature']!=inventory.signature(catalogue.get_book(bid)):raise ValueError('catalogue metadata changed; ingest again')
    if automatic:
        current=policy.current()
        if not evaluate(run_id,current)['auto_approve']:raise ValueError('thresholds do not permit automatic approval')
    for member in inv['members']:
        prior=catalogue.get_plan(member['id'])
        if prior:raise ValueError('group already has a processing plan; explicitly replan before replacing it')
        if (catalogue.get_processed(member['id']) or {}).get('status')=='done':raise ValueError('group already processed')
    checks=evaluate(run_id,save=False)
    plan=dict(plan,warnings=list(plan['warnings'])+[f"ingest: {c['metric']} {c['reason']}" for c in checks['checks'] if not c['passed']])
    if automatic:plan['ingest']['auto_policy_revision']=policy.current()['revision']
    catalogue.enqueue_ingest_plan(bid,[m['id'] for m in inv['members']],plan)
    if automatic:Q.decide([bid],True)
    store.event(run_id,'queue','done',data=dict(book_id=bid,approved=automatic,policy_revision=policy.current()['revision']))
    return dict(book_id=bid,approved=automatic)


def dashboard():
    rows=store.runs();output=[]
    for r in rows:
        inv=stage_data(r,'inventory') or {};identity=stage_data(r,'identity') or {};ev=stage_data(r,'eligibility');sel=stage_data(r,'selection');latest=r['events'][-1] if r['events'] else {}
        output.append(dict(id=r['id'],book_id=r['book_id'],title=identity.get('book',{}).get('title',''),state=r['state'],created=r['created'],stage=latest.get('stage'),metrics=metrics(r),stages=[dict(stage=s,**dict({k:e[k] for k in ('status','elapsed','error','metrics')},elapsed=(datetime.now(timezone.utc)-datetime.fromisoformat(e['created'])).total_seconds() if e['status']=='running' else e['elapsed'])) for s in STAGES+('queue','processing') for e in [next((e for e in reversed(r['events']) if e['stage']==s),None)] if e],selection=dict(files=[Path(p).name for p in sel['paths']],reason=sel['reason'],ranking=sel['ranking']) if sel else None,eligibility=ev,current_evaluation=policy.evaluate(metrics(r),identity.get('kind','unknown')),members=[b['id'] for b in inv.get('members',[])]))
    return dict(runs=output,policy=policy.current(),stages=list(STAGES))
