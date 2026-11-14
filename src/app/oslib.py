"""OS differences. The shared parts (file locks, process liveness and termination, priority, supervised child processes) are `strata_python.oslib`; this module re-exports what the app calls and adds the two things that are the app's own: how to start its CLI and the job worker.

Other modules should call these instead of importing fcntl or calling os.kill(pid, 0) (on Windows that would terminate the process)."""

import os
import subprocess
import sys

from strata_python import oslib as _os
from strata_python.oslib import WIN, file_lock, kill_tree, pid_alive, venv_python, watch_supervisor  # noqa: F401

ROOT_DIR = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))

kill_job = _os.kill_group  # a job and every tool it started, then reaped
terminate_job_group = _os.terminate_own_group


def lower_priority(nice=19):
    """Run this process at lower CPU priority (nice value on POSIX, below-normal/idle class on Windows). Children inherit it."""
    _os.lower_priority(nice)


def lower_job_priority():
    """Background scheduling for a job: idle CPU priority and idle disk I/O. Tools inherit the job's priorities."""
    _os.lower_priority(19, io=True)


def start_job(ident, data_dir) -> subprocess.Popen:
    """The isolated worker process of one durable job: its own process group with a lifeline on stdin (`kill_job` stops it and its tools; it stops itself if the server dies)."""
    return _os.start_supervised(cli_command()[:-1] + ["app.job_worker", ident], env=dict(os.environ, APP_DATA=data_dir))


def cli_command():
    """Command prefix that starts the app CLI with the interpreter running this process (works on POSIX and Windows, with or without a .venv)."""
    # Per-job process launches must not grow this environment variable on every book.
    paths = [os.path.join(ROOT_DIR, "src")] + [p for p in os.environ.get("PYTHONPATH", "").split(os.pathsep) if p]
    os.environ["PYTHONPATH"] = os.pathsep.join(dict.fromkeys(paths))
    os.environ.setdefault("PYTHONWARNINGS", "ignore")
    return [sys.executable, "-m", "app"]
