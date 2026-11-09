"""Real child isolation: an overrunning worker is killed and the next job proceeds."""

import json
import os
import subprocess
import sys

import pytest

from app import oslib
from app.jobs import JobQueue


def test_timeout_kills_a_real_child_and_releases_the_worker(tmp_path, monkeypatch):
    queue = JobQueue(None, workers=1, timeout_seconds=0.3)
    processes = []
    start = oslib.start_job

    def sleeper(*args):
        process = oslib.job_process([sys.executable, "-c", "import time; time.sleep(60)"])
        processes.append(process)
        return process

    monkeypatch.setattr(oslib, "start_job", sleeper)
    stalled = queue.submit("system", "scan", folder="stalled")
    assert queue.run_one(isolated=True)
    assert queue.get(stalled)["state"] == "failed" and "time limit" in queue.get(stalled)["error"]
    assert processes[0].poll() is not None and not oslib.pid_alive(processes[0].pid)
    assert queue.submit("system", "scan", folder="stalled") == stalled
    monkeypatch.setattr(oslib, "start_job", start)
    queue.timeout_seconds = 15
    following = queue.submit("system", "scan", folder=str(tmp_path))
    assert queue.run_one(isolated=True)
    assert queue.get(following)["state"] == "done" and queue.get(following)["result"]["found"] == 0
    assert not queue.pending()


@pytest.mark.skipif(oslib.WIN, reason="POSIX nice values; Windows priority class is covered at the OS boundary")
def test_job_priority_is_inherited_by_tools_without_lowering_the_server():
    before = os.getpriority(os.PRIO_PROCESS, 0)
    inspect = "import os,json,shutil,subprocess,sys; tool=shutil.which('ionice') if sys.platform.startswith('linux') else None; print(json.dumps(dict(nice=os.getpriority(os.PRIO_PROCESS,0),io=subprocess.check_output([tool,'-p',str(os.getpid())],text=True).strip() if tool else None)))"
    code = f'import subprocess,sys; from app import oslib; oslib.lower_job_priority(); subprocess.run([sys.executable,"-c",{inspect!r}],check=True)'
    result = subprocess.run(
        [sys.executable, "-c", code],
        env=dict(os.environ, PYTHONPATH=os.pathsep.join(sys.path), LC_ALL="C"),
        capture_output=True,
        text=True,
        check=True,
        timeout=15,
    )
    priority = json.loads(result.stdout)
    assert priority["nice"] == 19 and os.getpriority(os.PRIO_PROCESS, 0) == before
    if priority["io"] is not None:
        assert priority["io"] == "idle"


@pytest.mark.skipif(oslib.WIN, reason="POSIX abrupt supervisor death and detached process group")
def test_server_crash_stops_the_detached_worker_and_its_tool(tmp_path):
    import threading
    import time

    ready = tmp_path / "ready.json"
    tool = "import os,time; print(os.getpid(),flush=True); time.sleep(60)"
    worker = f"import os,json,subprocess,sys,time; from pathlib import Path; from app import oslib; oslib.watch_supervisor(); p=subprocess.Popen([sys.executable,'-c',{tool!r}],stdin=subprocess.DEVNULL,stdout=subprocess.PIPE,text=True); tool_pid=int(p.stdout.readline()); Path({str(ready) + '.tmp'!r}).write_text(json.dumps([os.getpid(),tool_pid])); os.replace({str(ready) + '.tmp'!r},{str(ready)!r}); p.wait()"
    supervisor = f"import os,sys; from app import oslib; p=oslib.job_process([sys.executable,'-c',{worker!r}],dict(os.environ,APP_JOB_SUPERVISED='1'),supervised=True); sys.stdin.buffer.read(1); p.wait()"
    parent = subprocess.Popen(
        [sys.executable, "-c", supervisor],
        stdin=subprocess.PIPE,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.PIPE,
        env=dict(os.environ, PYTHONPATH=os.pathsep.join(sys.path)),
    )
    ids = []

    def wait_until(predicate):
        deadline = time.monotonic() + 15
        while not predicate():
            assert time.monotonic() < deadline, "child did not reach the expected state"
            threading.Event().wait(0.02)

    def stopped(pid):
        state = subprocess.run(
            ["ps", "-o", "stat=", "-p", str(pid)], capture_output=True, text=True, check=False
        ).stdout.strip()
        return not state or state.startswith("Z")  # A dead adopted child may await reaping by init.

    try:
        wait_until(ready.exists)
        ids = json.loads(ready.read_text())
        assert all(not stopped(pid) for pid in ids)
        parent.kill()
        parent.wait(timeout=5)
        wait_until(lambda: all(stopped(pid) for pid in ids))
    finally:
        if parent.poll() is None:
            parent.kill()
        parent.communicate(timeout=5)
        for pid in ids:
            if not stopped(pid):
                oslib.kill_tree(pid, 9)
