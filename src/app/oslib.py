"""OS differences. The shared parts (file locks, process liveness and termination, priority, supervised child processes) are `strata_python.oslib`; this module re-exports what the app calls and adds the one thing that is the app's own: how to start its CLI. (Job processes, their priority and their cleanup are `strata_python.tasks.child`'s.)

Other modules should call these instead of importing fcntl or calling os.kill(pid, 0) (on Windows that would terminate the process)."""

import os
import sys

from strata_python.oslib import WIN, file_lock, kill_tree, pid_alive, venv_python  # noqa: F401

ROOT_DIR = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))


def cli_command():
    """Command prefix that starts the app CLI with the interpreter running this process (works on POSIX and Windows, with or without a .venv)."""
    # Per-job process launches must not grow this environment variable on every book.
    paths = [os.path.join(ROOT_DIR, "src")] + [p for p in os.environ.get("PYTHONPATH", "").split(os.pathsep) if p]
    os.environ["PYTHONPATH"] = os.pathsep.join(dict.fromkeys(paths))
    os.environ.setdefault("PYTHONWARNINGS", "ignore")
    return [sys.executable, "-m", "app"]
