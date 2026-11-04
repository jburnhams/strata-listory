"""Durable shared queue. HTTP submits small records; only bounded workers execute them."""
from datetime import datetime, timezone
import hashlib
import json
import os
import threading
import uuid
from app import db, oslib


def now():return datetime.now(timezone.utc).isoformat()


class JobQueue:
    def __init__(self,handler,workers=None):
        self.handler=handler;self.workers=max(1,min(8,int(workers or os.environ.get('APP_WORKERS','1'))))
        self.wake=threading.Event();self.stopping=threading.Event();self.threads=[]

    def submit(self,domain,kind,**options):
        payload=json.dumps(options,sort_keys=True,allow_nan=False);key=hashlib.sha256(json.dumps([domain,kind,payload]).encode()).hexdigest();c=db.connect()
        try:
            with c:
                ident=uuid.uuid4().hex
                ids=set(options.get('ids') or [])
                if options.get('book_id'):ids.add(options['book_id'])
                if options.get('run_id'):
                    ids.update(r[0] for r in c.execute('SELECT book_id FROM ingest_members WHERE run_id=?',(options['run_id'],)))
                resources=json.dumps(sorted(f'book:{bid}' for bid in ids))
                c.execute("INSERT OR IGNORE INTO jobs(id,domain,kind,options,dedupe,state,created,resources) VALUES (?,?,?,?,?,'queued',?,?)",(ident,domain,kind,payload,key,now(),resources))
                row=c.execute("SELECT id FROM jobs WHERE dedupe=? AND state IN ('queued','running')",(key,)).fetchone()
                ident=row[0]
        finally:c.close()
        self.wake.set()
        return ident

    def get(self,ident):
        c=db.connect()
        try:row=c.execute('SELECT id,domain,kind,state,created,started,finished,error,result FROM jobs WHERE id=?',(ident,)).fetchone()
        finally:c.close()
        if row is None:raise ValueError('no such job')
        out=dict(row);out['result']=json.loads(out['result']) if out['result'] else None
        return out

    def tasks(self,domain=None):
        c=db.connect()
        try:
            return [dict(id=r['id'],kind=r['kind'],options=json.loads(r['options']),state=r['state'],started=r['started'] or '') for r in c.execute("SELECT id,kind,options,state,started FROM jobs WHERE state IN ('queued','running')"+(' AND domain=?' if domain else '')+" ORDER BY CASE state WHEN 'running' THEN 0 ELSE 1 END,created,id",(domain,) if domain else ())]
        finally:c.close()

    def status(self,domain):
        tasks=self.tasks(domain);c=db.connect()
        try:row=c.execute("SELECT kind,state,result,error FROM jobs WHERE domain=? AND state IN ('done','failed') ORDER BY finished DESC LIMIT 1",(domain,)).fetchone()
        finally:c.close()
        return dict(busy=next((t['kind'] for t in tasks if t['state']=='running'),None),queued=sum(t['state']=='queued' for t in tasks),last={row['kind']:json.loads(row['result']) if row['state']=='done' else dict(error=row['error'])} if row else {})

    def run_one(self):
        c=db.connect()
        try:
            with c:
                c.execute('BEGIN IMMEDIATE')
                # The database also enforces the shared cap if several callers try to claim concurrently.
                if c.execute("SELECT count(*) FROM jobs WHERE state='running'").fetchone()[0]>=self.workers:return False
                row=c.execute("SELECT j.id,j.domain,j.kind,j.options FROM jobs j WHERE j.state='queued' AND NOT EXISTS (SELECT 1 FROM jobs active,json_each(active.resources) a,json_each(j.resources) b WHERE active.state='running' AND a.value=b.value) ORDER BY j.created,j.id LIMIT 1").fetchone()
                if row is None:return False
                c.execute("UPDATE jobs SET state='running',started=?,owner_pid=? WHERE id=?",(now(),os.getpid(),row['id']))
        finally:c.close()
        result=None;error='';serialized=None
        try:
            result=self.handler(row['domain'],row['kind'],json.loads(row['options']))
            serialized=json.dumps(result,allow_nan=False)
        except Exception as exc:error=str(exc) or type(exc).__name__
        c=db.connect()
        try:
            with c:c.execute('UPDATE jobs SET state=?,finished=?,result=?,error=? WHERE id=?',('failed' if error else 'done',now(),serialized if not error else None,error,row['id']))
        finally:c.close()
        return True

    def start(self):
        if self.threads:return
        c=db.connect()
        try:
            with c:
                for row in c.execute("SELECT id,owner_pid,domain,kind,options,started FROM jobs WHERE state='running'").fetchall():
                    if not oslib.pid_alive(row['owner_pid']):
                        c.execute("UPDATE jobs SET state='queued',started=NULL,owner_pid=NULL WHERE id=?",(row['id'],))
                        if row['domain']=='ingest':
                            options=json.loads(row['options']);options['_recovered']=True
                            if row['kind']=='run' and options.get('ids'):
                                latest=c.execute('SELECT r.id FROM ingest_runs r JOIN ingest_members m ON m.run_id=r.id WHERE m.book_id=? AND r.created>=? ORDER BY r.created DESC LIMIT 1',(options['ids'][0],row['started'] or '')).fetchone()
                                if latest:options['resume']=latest[0]
                            c.execute('UPDATE jobs SET options=? WHERE id=?',(json.dumps(options),row['id']))
                        if row['domain']=='process' and row['kind']=='run':c.execute("UPDATE process_plan SET state='approved' WHERE state='running'")
        finally:c.close()
        for n in range(self.workers):
            thread=threading.Thread(target=self._loop,name=f'job-worker-{n}',daemon=True);self.threads.append(thread);thread.start()

    def _loop(self):
        while not self.stopping.is_set():
            self.wake.clear()
            if not self.run_one():self.wake.wait(1)

    def stop(self):
        self.stopping.set();self.wake.set()
        for thread in self.threads:thread.join(timeout=1)
