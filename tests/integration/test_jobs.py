"""Real child isolation: an overrunning worker is killed and the next job proceeds."""
import sys,json,os,subprocess
import pytest
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


@pytest.mark.skipif(oslib.WIN,reason='POSIX nice values; Windows priority class is covered at the OS boundary')
def test_job_priority_is_inherited_by_tools_without_lowering_the_server():
    before=os.getpriority(os.PRIO_PROCESS,0)
    inspect="import os,json,shutil,subprocess,sys; tool=shutil.which('ionice') if sys.platform.startswith('linux') else None; print(json.dumps(dict(nice=os.getpriority(os.PRIO_PROCESS,0),io=subprocess.check_output([tool,'-p',str(os.getpid())],text=True).strip() if tool else None)))"
    code=f'import subprocess,sys; from app import oslib; oslib.lower_job_priority(); subprocess.run([sys.executable,"-c",{inspect!r}],check=True)'
    result=subprocess.run([sys.executable,'-c',code],env=dict(os.environ,PYTHONPATH=os.pathsep.join(sys.path),LC_ALL='C'),capture_output=True,text=True,check=True,timeout=15)
    priority=json.loads(result.stdout)
    assert priority['nice']==19 and os.getpriority(os.PRIO_PROCESS,0)==before
    if priority['io'] is not None:assert priority['io']=='idle'
