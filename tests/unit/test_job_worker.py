"""Isolated worker entry point delegates exactly one already-claimed job."""
import pytest
from app import db,job_worker
from app.server import tasks


def test_worker_executes_one_claimed_job_in_the_supervisors_database(monkeypatch):
    calls=[];monkeypatch.setattr(job_worker.oslib,'watch_supervisor',lambda:calls.append('watch supervisor'));monkeypatch.setattr(db,'_checked',set())
    monkeypatch.setattr(job_worker.oslib,'lower_job_priority',lambda:calls.append('low priority'))
    class Queue:
        def __init__(self,handler):self.handler=handler
        def execute_claimed(self,ident):
            calls.append(ident);self.handler('system','scan',dict(folder='/source'))
    monkeypatch.setattr(job_worker,'JobQueue',Queue)
    monkeypatch.setattr(tasks,'execute',lambda *args:calls.append(args[1:]))
    assert job_worker.main(['saved'])==0
    assert calls==['watch supervisor','low priority','saved',('system','scan',dict(folder='/source'))]
    assert db.path() in db._checked


@pytest.mark.parametrize('args',[[],['one','two']])
def test_worker_requires_exactly_one_identifier(args):
    with pytest.raises(ValueError,match='one job ID'):job_worker.main(args)
