"""Real child isolation: an overrunning worker is killed and the next job proceeds."""
import sys
from app import oslib
from app.jobs import JobQueue


def test_timeout_kills_a_real_child_and_releases_the_worker(tmp_path,monkeypatch):
    queue=JobQueue(None,workers=1,timeout_seconds=.3);processes=[];start=oslib.start_job
    def sleeper(*args):
        process=oslib.job_process([sys.executable,'-c','import time; time.sleep(60)'])
        processes.append(process);return process
    monkeypatch.setattr(oslib,'start_job',sleeper)
    stalled=queue.submit('system','scan',folder='stalled')
    assert queue.run_one(isolated=True)
    assert queue.get(stalled)['state']=='failed' and 'time limit' in queue.get(stalled)['error']
    assert processes[0].poll() is not None and not oslib.pid_alive(processes[0].pid)
    assert queue.submit('system','scan',folder='stalled')==stalled
    monkeypatch.setattr(oslib,'start_job',start)
    queue.timeout_seconds=15
    following=queue.submit('system','scan',folder=str(tmp_path))
    assert queue.run_one(isolated=True)
    assert queue.get(following)['state']=='done' and queue.get(following)['result']['found']==0
    assert not queue.pending()
