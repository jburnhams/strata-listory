"""Append-only stage events and policy revisions in the catalogue database."""
from datetime import datetime, timezone
import json
import uuid
from app import db


def now(): return datetime.now(timezone.utc).isoformat()


def create(book_id, options):
    ident = uuid.uuid4().hex; c = db.connect()
    try:
        with c:
            c.execute('INSERT INTO ingest_runs VALUES (?,?,?,?,?,?)', (ident, book_id, now(), 'running', json.dumps(options), 1))
            # Bind the selected entry before inventory so an older run cannot
            # remain current while a new run is discovering its group.
            c.execute('INSERT INTO ingest_members VALUES (?,?)',(ident,book_id))
    finally: c.close()
    return ident


def event(run_id, stage, status, metrics=None, data=None, error='', elapsed=0):
    c = db.connect()
    try:
        with c: c.execute('INSERT INTO ingest_events(run_id,stage,status,created,elapsed,metrics,data,error) VALUES (?,?,?,?,?,?,?,?)', (run_id, stage, status, now(), elapsed, json.dumps(metrics or {}, allow_nan=False), json.dumps(data or {}, allow_nan=False), error))
    finally: c.close()


def finish(run_id, state):
    c = db.connect()
    try:
        with c: c.execute('UPDATE ingest_runs SET state=? WHERE id=?', (state, run_id))
    finally: c.close()


def get(run_id):
    c = db.connect()
    try:
        r = c.execute('SELECT * FROM ingest_runs WHERE id=?', (run_id,)).fetchone()
        if r is None: raise ValueError('no such ingest run')
        out = dict(r); out['options'] = json.loads(out['options']); out['events'] = []
        for e in c.execute('SELECT * FROM ingest_events WHERE run_id=? ORDER BY id', (run_id,)):
            e = dict(e); e['metrics'] = json.loads(e['metrics']); e['data'] = json.loads(e['data']); out['events'].append(e)
        return out
    finally: c.close()


def runs(limit=50,offset=0):
    return [get(i) for i in run_ids(limit,offset)]


def run_ids(limit=50,offset=0):
    c=db.connect()
    try:return [r[0] for r in c.execute('SELECT id FROM ingest_runs ORDER BY created DESC LIMIT ? OFFSET ?',(max(1,min(limit,200)),max(0,offset)))]
    finally:c.close()


def run_count():
    c=db.connect()
    try:return c.execute('SELECT COUNT(*) FROM ingest_runs').fetchone()[0]
    finally:c.close()


def all_runs():
    for offset in range(0,run_count(),200):
        for rid in run_ids(200,offset):yield get(rid)


def link(run_id, members):
    c = db.connect()
    try:
        with c:
            for bid in members: c.execute('INSERT OR IGNORE INTO ingest_members VALUES (?,?)', (run_id, bid))
    finally: c.close()


def latest_id(book_id):
    c = db.connect()
    try: r = c.execute('SELECT r.id FROM ingest_runs r JOIN ingest_members m ON m.run_id=r.id WHERE m.book_id=? ORDER BY r.created DESC LIMIT 1', (book_id,)).fetchone()
    finally: c.close()
    return r[0] if r else None


def latest(book_id):
    ident=latest_id(book_id)
    return get(ident) if ident else None


def policy_save(value):
    c = db.connect()
    try:
        with c: cursor = c.execute('INSERT INTO ingest_policy(created,data) VALUES (?,?)', (now(), json.dumps(value, allow_nan=False))); return cursor.lastrowid
    finally: c.close()


def policy_latest():
    c = db.connect()
    try: r = c.execute('SELECT id,data FROM ingest_policy ORDER BY id DESC LIMIT 1').fetchone(); return dict(revision=r[0], **json.loads(r[1])) if r else None
    finally: c.close()


def review_headers():
    """Small current-run records for queue pagination, without loading source evidence."""
    c=db.connect()
    try:
        rows=c.execute("""SELECT r.id,r.book_id,r.created,r.state,b.title FROM ingest_runs r JOIN books b ON b.id=r.book_id
            WHERE r.state IN ('metadata_review','audiobook_review','quality_review','review','eligible')
            AND NOT EXISTS (SELECT 1 FROM ingest_members mine JOIN ingest_members later ON later.book_id=mine.book_id
                JOIN ingest_runs newer ON newer.id=later.run_id WHERE mine.run_id=r.id AND newer.created>r.created)
            ORDER BY r.created DESC""").fetchall()
        runs={r['id']:dict(r,events=[]) for r in rows};ids=list(runs)
        stages=('inventory','identity','providers','metadata','recording_providers','recording','alignment','quality','selection','metadata_approval','audiobook_approval','quality_approval','quality_rating')
        for offset in range(0,len(ids),200):
            batch=ids[offset:offset+200]
            # Extract only ranking fields in SQLite: provider payloads and audio inventories stay off the queue path.
            summary="""CASE WHEN stage='identity' THEN json_object('title',json_extract(data,'$.book.title'),'kind',json_extract(data,'$.kind'))
                WHEN stage IN ('metadata','recording') THEN json_object('title',COALESCE(json_extract(data,'$.work.resolved.title'),json_extract(data,'$.resolved.title')),
                    'authors',COALESCE(json_extract(data,'$.work.resolved.authors'),json_extract(data,'$.resolved.authors')),
                    'year',COALESCE(json_extract(data,'$.work.resolved.year'),json_extract(data,'$.resolved.year')),
                    'publisher',COALESCE(json_extract(data,'$.work.resolved.publisher'),json_extract(data,'$.resolved.publisher')),
                    'analysis_metrics',json_extract(data,'$.analysis_metrics')) ELSE '{}' END"""
            for event in c.execute(f"SELECT id,run_id,stage,status,metrics,{summary} AS summary,CASE WHEN stage LIKE '%_approval' THEN data ELSE '{{}}' END AS data FROM ingest_events WHERE status='done' AND run_id IN ({','.join('?' for _ in batch)}) AND stage IN ({','.join('?' for _ in stages)}) ORDER BY id",batch+list(stages)):
                value=dict(event)
                for field in ('data','metrics','summary'):value[field]=json.loads(value[field])
                runs[value['run_id']]['events'].append(value)
        return list(runs.values())
    finally:c.close()


def workflow_snapshot(activity=False):
    """Current membership and small saved stage headers; never deserialize audio/provider evidence."""
    c=db.connect()
    try:
        latest={}
        for row in c.execute('SELECT m.book_id,m.run_id FROM ingest_members m JOIN ingest_runs r ON r.id=m.run_id ORDER BY r.created DESC'):
            latest.setdefault(row['book_id'],row['run_id'])
        runs={};ids=list(dict.fromkeys(latest.values()))
        for offset in range(0,len(ids),200):
            batch=ids[offset:offset+200];slots=','.join('?' for _ in batch)
            for row in c.execute(f'SELECT id,book_id,created,state FROM ingest_runs WHERE id IN ({slots})',batch):
                runs[row['id']]=dict(row,events=[])
            # Signatures use event IDs. Only consent records need their small JSON body.
            for row in c.execute(f"SELECT id,run_id,stage,status,CASE WHEN stage LIKE '%_approval' THEN data ELSE '{{}}' END AS data FROM ingest_events WHERE run_id IN ({slots}) AND status='done' AND id=(SELECT max(newer.id) FROM ingest_events newer WHERE newer.run_id=ingest_events.run_id AND newer.stage=ingest_events.stage AND newer.status='done') ORDER BY id",batch):
                event=dict(row);event['data']=json.loads(event['data']);runs[event['run_id']]['events'].append(event)
            if activity:
                for rid in batch:
                    run=runs[rid]
                    for stage,field,expr in (('inventory','primary_id',"json_extract(data,'$.primary_id')"),('metadata','title',"COALESCE(json_extract(data,'$.work.resolved.title'),json_extract(data,'$.resolved.title'))"),('split','children',"json_array_length(data,'$.children')")):
                        row=c.execute(f"SELECT {expr} FROM ingest_events WHERE run_id=? AND stage=? AND status='done' ORDER BY id DESC LIMIT 1",(rid,stage)).fetchone()
                        run[field]=row[0] if row else None
                    events=[dict(r) for r in c.execute("SELECT id,stage,status,created,elapsed,error,CASE WHEN status='progress' THEN json_extract(data,'$.message') END AS message FROM ingest_events WHERE run_id=? ORDER BY id DESC LIMIT 12",(rid,))][::-1]
                    for event in events:event['data']=dict(message=event.pop('message'))
                    run['activity']=events
                    if events:
                        start=c.execute("SELECT created FROM ingest_events WHERE run_id=? AND stage=? AND status='running' ORDER BY id DESC LIMIT 1",(rid,events[-1]['stage'])).fetchone()
                        run['step_started']=start[0] if start else events[-1]['created']
                    failure=c.execute("SELECT error FROM ingest_events WHERE run_id=? AND status='failed' ORDER BY id DESC LIMIT 1",(rid,)).fetchone()
                    run['error']=failure[0] if failure else ''
        plans=[dict(r) for r in c.execute('SELECT book_id,state FROM process_plan')]
        processed=[dict(r) for r in c.execute('SELECT book_id FROM processed')]
        return dict(latest=latest,runs=runs,plans=plans,processed=processed)
    finally:c.close()


def run_book_id(run_id):
    c=db.connect()
    try:
        row=c.execute('SELECT book_id FROM ingest_runs WHERE id=?',(run_id,)).fetchone()
        if row is None:raise ValueError('no such ingest run')
        return row[0]
    finally:c.close()


def source_summary(book_id):
    """Extract only source names/count from the latest saved inventory."""
    rid=latest_id(book_id)
    if not rid:return None
    c=db.connect()
    try:
        row=c.execute("""SELECT json_extract(data,'$.root') AS root,json_array_length(data,'$.files') AS count,
            json_extract(data,'$.files[0].path') AS first,json_extract(data,'$.files[1].path') AS second,
            json_extract(data,'$.files[2].path') AS third,json_extract(data,'$.work_hint.grouping_notes') AS notes
            FROM ingest_events WHERE run_id=? AND stage='inventory' AND status='done' ORDER BY id DESC LIMIT 1""",(rid,)).fetchone()
        return dict(root=row['root'],count=row['count'],paths=[row[k] for k in ('first','second','third') if row[k]],grouping=json.loads(row['notes'] or '[]')) if row else None
    finally:c.close()


def projection_get(key):
    c=db.connect()
    try:
        row=c.execute('SELECT body FROM match_cache WHERE key=?',(key,)).fetchone()
        return json.loads(row[0]) if row else None
    finally:c.close()


def projection_save(key,value):
    c=db.connect()
    try:
        with c:c.execute('INSERT OR REPLACE INTO match_cache(key,fetched,body) VALUES (?,?,?)',(key,now(),json.dumps(value,allow_nan=False)))
    finally:c.close()


def run_summary(run_id):
    """Explicit evidence view uses event headers; provider/audio payloads remain archived."""
    c=db.connect()
    try:
        row=c.execute('SELECT id,book_id,created,state FROM ingest_runs WHERE id=?',(run_id,)).fetchone()
        if row is None:raise ValueError('no such ingest run')
        events=[dict(e) for e in c.execute('SELECT id,stage,status,created,elapsed,error,metrics FROM ingest_events WHERE run_id=? ORDER BY id DESC LIMIT 100',(run_id,))][::-1]
        for event in events:event['metrics']=json.loads(event['metrics'])
        return dict(row,events=events)
    finally:c.close()


def page_headers(limit=50,offset=0):
    c=db.connect()
    try:return [dict(r,events=[]) for r in c.execute('SELECT r.id,r.book_id,r.created,r.state,b.title FROM ingest_runs r JOIN books b ON b.id=r.book_id ORDER BY r.created DESC LIMIT ? OFFSET ?',(max(1,min(limit,200)),max(0,offset)))]
    finally:c.close()


def plan_exists(book_id):
    c=db.connect()
    try:return c.execute('SELECT 1 FROM process_plan WHERE book_id=?',(book_id,)).fetchone() is not None
    finally:c.close()
