"""A single background ingest worker; persistent stages allow explicit restart/resume."""
from app.ingest import pipeline
from datetime import datetime, timezone
from app.process.queue import Worker as Base


class Worker(Base):
    def _do(self,kind,kw):
        self.busy=kind;self.active=dict(kind=kind,options=kw,state='running',started=datetime.now(timezone.utc).isoformat())
        remainder=None
        try:
            if kind=='run':
                ids=kw.get('ids') or [];limit=kw.get('limit',5)
                if not self.inline and len(ids)>1 and limit>1:
                    remainder=dict(kw,ids=ids[1:],limit=limit-1)
                    runs=pipeline.run(**dict(kw,ids=ids[:1],limit=1))
                    members={member['id'] for run in runs for member in (pipeline.stage_data(run,'inventory') or {}).get('members',[])}
                    remainder['ids']=[bid for bid in remainder['ids'] if bid not in members]
                    result=[run['id'] for run in runs]
                else:result=[r['id'] for r in pipeline.run(**kw)]
            elif kind=='resume':result=[pipeline.continue_review(kw['run_id'])['id']]
            elif kind=='continue':result=[pipeline.continue_review(kw['run_id'])['id']]
            elif kind=='restore':
                current=pipeline.store.get(kw['run_id'])
                result=[pipeline.continue_review(kw['run_id'])['id']] if current['state'] in ('queued','running') else [current['id']]
            elif kind=='regroup':result=pipeline.regroup_collection(kw['run_id'])
            elif kind=='auto_metadata':
                result=pipeline.approve_passing_metadata()
                for rid in result:self.submit('continue',run_id=rid)
            else:raise ValueError('unknown ingest task')
            self.last={kind:result}
        except Exception as e:self.last={kind:dict(error=str(e))}
        finally:
            self.busy=None;self.active=None
            if remainder and remainder['ids']:
                remainder['limit']=min(remainder['limit'],len(remainder['ids']))
                self.submit('run',**remainder)
    def status(self):return dict(busy=self.busy,queued=self.q.qsize(),last=self.last)
