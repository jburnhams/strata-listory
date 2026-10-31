"""A single background ingest worker; persistent stages allow explicit restart/resume."""
from app.ingest import pipeline
from app.process.queue import Worker as Base


class Worker(Base):
    def _do(self,kind,kw):
        self.busy=kind
        try:
            if kind=='run':result=[r['id'] for r in pipeline.run(**kw)]
            elif kind=='resume':result=[r['id'] for r in pipeline.run(resume=kw['run_id'])]
            else:raise ValueError('unknown ingest task')
            self.last={kind:result}
        except Exception as e:self.last={kind:dict(error=str(e))}
        finally:self.busy=None
    def status(self):return dict(busy=self.busy,queued=self.q.qsize(),last=self.last)
