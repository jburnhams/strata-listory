"""The queue with real job processes: a job runs in a process of its own (strata_python.tasks.child), a stalled one is killed at its deadline and the next job proceeds, a stopping server fails its job with the known message and the next start replays it. The job processes load a `sitecustomize` from a temporary folder that wraps the handlers, so the real server code runs except for the one job a test wants to stall."""

import json
import os
import time
from pathlib import Path

import pytest

from app import oslib
from app.jobs import JobQueue

SITE = """
import json, os, time
from pathlib import Path
from app.server import tasks

real = tasks.execute
log = Path(os.environ["JOB_LOG"])


def execute(jobs, domain, kind, options):
    if options.get("folder") == "stalled":
        log.write_text(json.dumps(dict(pid=os.getpid(), nice=os.getpriority(os.PRIO_PROCESS, 0) if hasattr(os, "getpriority") else None)))
        time.sleep(60)
    return real(jobs, domain, kind, options)


tasks.execute = execute
"""


@pytest.fixture
def stalling(tmp_path, monkeypatch):
    site = tmp_path / "site"
    site.mkdir()
    (site / "sitecustomize.py").write_text(SITE)
    monkeypatch.setenv("JOB_LOG", str(tmp_path / "stalled.json"))
    monkeypatch.syspath_prepend(str(site))  # the library hands this process's sys.path to its job processes
    return tmp_path / "stalled.json"


def wait_until(predicate, what="the expected state"):
    deadline = time.monotonic() + 60
    while not predicate():
        assert time.monotonic() < deadline, f"never reached {what}"
        time.sleep(0.05)


def test_a_job_runs_in_a_job_process_and_its_outcome_is_recorded(tmp_path):
    queue = JobQueue(None, workers=1)
    queue.start()
    try:
        scan = queue.submit("system", "scan", folder=str(tmp_path))
        missing = queue.submit("system", "scan", folder=str(tmp_path / "nowhere"))
        wait_until(lambda: not queue.pending(), "an empty queue")
        assert queue.get(scan)["state"] == "done" and queue.get(scan)["result"]["found"] == 0
        failed = queue.get(missing)
        assert failed["state"] == "failed" and "ValueError" not in failed["error"] and failed["error"]
        assert [f["id"] for f in queue.failures()] == [missing]
    finally:
        queue.stop()


@pytest.mark.skipif(oslib.WIN, reason="POSIX priorities and process states")
def test_timeout_kills_a_real_job_process_at_idle_priority_and_the_next_job_proceeds(tmp_path, stalling):
    before = os.getpriority(os.PRIO_PROCESS, 0)
    queue = JobQueue(None, workers=1, timeout_seconds=60)
    queue.start()
    try:
        # A deadline is fixed at submission and counts the job process's start-up (seconds of imports on a slow runner):
        # run one job first so the process is warm, then give the stalled one a short deadline.
        warm = queue.submit("system", "scan", folder=str(tmp_path))
        wait_until(lambda: queue.get(warm)["state"] == "done", "the warm-up job")
        queue.timeout_seconds = 1
        stalled = queue.submit("system", "scan", folder="stalled")
        wait_until(stalling.exists, "the stalled job running")
        seen = json.loads(stalling.read_text())
        assert seen["nice"] > before  # background scheduling, inherited by the tools a job starts
        wait_until(lambda: queue.get(stalled)["state"] == "failed", "the deadline")
        assert queue.get(stalled)["error"] == "took longer than 1 s"
        wait_until(lambda: not oslib.pid_alive(seen["pid"]), "the job process gone")
        assert queue.submit("system", "scan", folder="stalled") == stalled  # still shown, not run again by itself
        queue.timeout_seconds = 60  # the killed process is replaced by a new one that must import the app first
        following = queue.submit("system", "scan", folder=str(tmp_path))
        wait_until(lambda: queue.get(following)["state"] == "done", "the next job")
    finally:
        queue.stop()


@pytest.mark.skipif(oslib.WIN, reason="POSIX process states")
def test_a_stopping_server_fails_its_job_with_the_known_message_and_the_next_start_replays_it(tmp_path, stalling):
    queue = JobQueue(None, workers=1)
    queue.start()
    stalled = queue.submit("system", "scan", folder="stalled")
    wait_until(stalling.exists, "the stalled job running")
    pid = json.loads(stalling.read_text())["pid"]
    queue.stop()
    assert not oslib.pid_alive(pid)  # the job and the tools it started die with the server
    assert (
        queue.get(stalled)["state"] == "failed" and queue.get(stalled)["error"] == "job interrupted by server shutdown"
    )
    stalling.unlink()
    again = JobQueue(None, workers=1)
    again.start()
    try:
        wait_until(stalling.exists, "the replayed job running")
        assert [t["options"] for t in again.tasks("system")] == [dict(folder="stalled")]
        assert again.failures() == []
    finally:
        again.stop()
    assert Path(stalling).exists()
