"""Current work across both workers, including books a batch has not reached yet."""
from datetime import datetime, timezone
from app import catalogue
from app.ingest import pipeline as P, store


def view(ingest_tasks,process_tasks,completed=False):
    rows=[];pending=set();runs={};books={b['id']:b for b in catalogue.list_books()};plans={r['book_id'] for r in catalogue.list_plans()}
    for n,task in enumerate(ingest_tasks):
        options=task['options'];ids=options.get('ids') or []
        if not ids and options.get('run_id'):ids=[store.get(options['run_id'])['book_id']]
        for bid in ids:
            if bid not in books or bid in pending:continue
            run=store.latest(bid)
            if task['state']=='running' and run and (run['state']=='running' or run['created']>=task['started']):continue
            pending.add(bid)
            rows.append(dict(id=f'queued-{n}-{bid}',book_id=bid,run_id=None,title=books[bid]['title'],stage='Ingest analysis',state='queued',phase=None,error=''))
        if not ids:rows.append(dict(id=f'ingest-task-{n}',book_id=None,run_id=None,title='Metadata approvals' if task['kind']=='auto_metadata' else 'Discover ingest candidates',stage='Ingest',state=task['state'],phase=None,error=''))
    for run in store.all_runs():
        runs[run['id']]=run
        inv=P.stage_data(run,'inventory') or {};bid=inv.get('primary_id',run['book_id'])
        if store.latest_id(bid)!=run['id'] or pending.intersection(m['id'] for m in inv.get('members',[dict(id=bid)])):continue
        if bid in plans and run['state'] in ('processed','processing','processing_failed','eligible','review','split_existing'):continue
        phase=P.review_phase(run)
        state='running' if run['state']=='running' else 'failed' if run['state'] in ('failed','processing_failed') else 'queued' if run['state']=='queued' else 'completed' if run['state'] in ('processed','split','split_existing') else 'waiting'
        if state=='completed' and not completed:continue
        last=run['events'][-1] if run['events'] else {}
        error=next((e['error'] for e in reversed(run['events']) if e['status']=='failed'),'') if state=='failed' else ''
        title=(P.stage_data(run,'metadata') or {}).get('resolved',{}).get('title') or books.get(bid,{}).get('title') or f'Book {bid}'
        rows.append(dict(id=run['id'],run_id=run['id'],book_id=bid,title=title,stage=(f"Split into {len((P.stage_data(run,'split') or {}).get('children',[]))} source groups" if run['state']=='split' else 'Existing proposal/output retained') if run['state'] in ('split','split_existing') else {'providers':'Book catalogue search','metadata':'Book identification','recording_providers':'Recording catalogue search','recording':'Audiobook identification'}.get(last.get('stage'),last.get('stage','inventory')) if state in ('running','failed') or run['state'] in ('split','split_existing') else ('Book identification approval' if phase=='metadata' else 'Audiobook identification approval' if phase=='audiobook' else f'{phase.capitalize()} approval'),state=state,phase=phase,error=error))
    for n,task in enumerate(process_tasks):
        rows.append(dict(id=f'process-task-{n}',book_id=None,run_id=None,title='Build processing proposals' if task['kind']=='propose' else 'Process approved books',stage='Processing',state=task['state'],phase=None,error=''))
    labels={'inventory':'Source inventory','identity':'Recover local identity','providers':'Book catalogue search','metadata':'Book identification','recording_providers':'Recording catalogue search','recording':'Audiobook identification','alignment':'Compare audio versions','quality':'Measure audio quality','selection':'Choose source audio','planning':'Build processing proposal','validation':'Validate source files'}
    for row in rows:
        run=runs.get(row.get('run_id'))
        if not run:continue
        events=run['events'];latest=events[-1] if events else {};stage=latest.get('stage','inventory')
        start=next((e for e in reversed(events) if e['stage']==stage and e['status']=='running'),latest)
        row['current_step']=(latest.get('data') or {}).get('message') if latest.get('status')=='progress' else labels.get(stage,stage.replace('_',' '))
        row['step_elapsed']=max(0,(datetime.now(timezone.utc)-datetime.fromisoformat(start['created'])).total_seconds()) if row['state']=='running' and start.get('created') else latest.get('elapsed',0)
        row['logs']=[dict(created=e['created'],message=e['data'].get('message') or f"{labels.get(e['stage'],e['stage'].replace('_',' '))}: {e['status']}"+(f" — {e['error']}" if e['error'] else ''),status=e['status']) for e in events[-12:]]
    order={'running':0,'queued':1,'failed':2,'waiting':3,'completed':4}
    rows.sort(key=lambda r:order[r['state']])
    return dict(rows=rows,busy=bool(ingest_tasks or process_tasks))


def library(ingest_tasks,process_tasks):
    """Current workflow membership for Library, including every linked group member."""
    from app import db
    connection=db.connect()
    try:
        latest={}
        for row in connection.execute('SELECT m.book_id,m.run_id FROM ingest_members m JOIN ingest_runs r ON r.id=m.run_id ORDER BY r.created DESC'):
            latest.setdefault(row['book_id'],row['run_id'])
    finally:connection.close()
    groups={};runs={rid:store.get(rid) for rid in set(latest.values())};stages={}
    for bid,rid in latest.items():groups.setdefault(rid,[]).append(bid)
    def add(bid,stage):
        for member in groups.get(latest.get(bid),[bid]):stages.setdefault(member,set()).add(stage)
    for rid,run in runs.items():
        state=run['state']
        stage='finished' if state=='processed' else 'processing' if state in ('running','queued','failed','processing','processing_failed','split','split_existing') else P.review_phase(run)
        for bid in groups[rid]:stages.setdefault(bid,set()).add(stage)
    for row in catalogue.list_plans():
        add(row['book_id'],'proposed' if row['state'] in ('proposed','rejected') else 'finished' if row['state']=='done' else 'processing')
    for row in catalogue.list_processed():add(row['book_id'],'finished')
    for task in ingest_tasks:
        options=task['options'];ids=options.get('ids') or []
        if not ids and options.get('run_id'):ids=[store.get(options['run_id'])['book_id']]
        for bid in ids:
            run=runs.get(latest.get(bid))
            if task['state']=='running' and run and (run['state']=='running' or run['created']>=task['started']):continue
            add(bid,'processing')
    for task in process_tasks:
        for bid in task['options'].get('ids') or []:add(bid,'processing')
    return dict(books={str(bid):sorted(values) for bid,values in stages.items()})
