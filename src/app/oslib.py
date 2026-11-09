"""OS differences in one place: file locks, process liveness and termination, priority, and how to start the CLI.

POSIX (macOS, Linux) uses fcntl, os.kill and os.setpriority; Windows uses msvcrt, the Win32 API through ctypes and taskkill. Other modules should call these
instead of importing fcntl or calling os.kill(pid, 0) (on Windows that would terminate the process)."""

import contextlib
import os
import shutil
import subprocess
import sys
import threading
import time

WIN = sys.platform == "win32"
ROOT_DIR = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))


@contextlib.contextmanager
def file_lock(f):
    """Exclusive lock on an open file, held for the duration of the block (other processes block until it is released)."""
    if WIN:
        import msvcrt

        f.seek(0)
        while True:
            try:
                msvcrt.locking(f.fileno(), msvcrt.LK_NBLCK, 1)
                break
            except OSError:
                time.sleep(0.02)
        try:
            yield
        finally:
            f.seek(0)
            with contextlib.suppress(OSError):
                msvcrt.locking(f.fileno(), msvcrt.LK_UNLCK, 1)
    else:
        import fcntl

        fcntl.flock(f, fcntl.LOCK_EX)
        try:
            yield
        finally:
            fcntl.flock(f, fcntl.LOCK_UN)


def pid_alive(pid):
    """Is that process still running?"""
    try:
        pid = int(pid)
    except (TypeError, ValueError):
        return False
    if pid <= 0:
        return False
    if WIN:
        import ctypes

        k = ctypes.windll.kernel32
        h = k.OpenProcess(0x1000, False, pid)  # PROCESS_QUERY_LIMITED_INFORMATION
        if not h:
            return False
        try:
            code = ctypes.c_ulong()
            ok = k.GetExitCodeProcess(h, ctypes.byref(code))
            return bool(ok) and code.value == 259  # STILL_ACTIVE
        finally:
            k.CloseHandle(h)
    try:
        os.kill(pid, 0)
        return True
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    except OSError:
        return False


def kill_tree(pid, sig=15):
    """Terminate a worker and every process it started; errors are ignored."""
    if WIN:
        with contextlib.suppress(OSError):
            subprocess.run(["taskkill", "/T", "/F", "/PID", str(int(pid))], capture_output=True)
        return
    try:
        kids = subprocess.run(["pgrep", "-P", str(pid)], stdout=subprocess.PIPE, text=True).stdout.split()
    except OSError:
        kids = []
    for k in kids:
        kill_tree(int(k), sig)
    with contextlib.suppress(OSError):
        os.kill(int(pid), sig)


def lower_priority(nice=19):
    """Run this process at lower CPU priority (nice value on POSIX, below-normal/idle class on Windows). Children inherit it."""
    if WIN:
        import ctypes

        with contextlib.suppress(Exception):
            ctypes.windll.kernel32.SetPriorityClass(
                ctypes.windll.kernel32.GetCurrentProcess(), 0x40 if nice >= 15 else 0x4000
            )  # IDLE / BELOW_NORMAL
        return
    with contextlib.suppress(OSError, AttributeError):
        os.setpriority(os.PRIO_PROCESS, 0, max(nice, os.getpriority(os.PRIO_PROCESS, 0)))


def lower_job_priority():
    """Background CPU scheduling; Linux idle I/O. Tools inherit the job's priorities."""
    lower_priority(19)
    if sys.platform.startswith("linux") and (ionice := shutil.which("ionice")):
        with contextlib.suppress(OSError, subprocess.TimeoutExpired):
            subprocess.run(
                [ionice, "-c", "3", "-p", str(os.getpid())],
                stdin=subprocess.DEVNULL,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                timeout=5,
            )


def job_process(command, env=None, supervised=False):
    """Isolate descendants so the supervisor can stop all work at the deadline."""
    options = dict(
        stdin=subprocess.PIPE if supervised else subprocess.DEVNULL,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        env=env,
    )
    if not WIN:
        options["start_new_session"] = True
    return subprocess.Popen(command, **options)


def start_job(ident, data_dir):
    command = cli_command()[:-1] + ["app.job_worker", ident]
    return job_process(command, dict(os.environ, APP_DATA=data_dir, APP_JOB_SUPERVISED="1"), supervised=True)


def watch_supervisor():
    """The parent's private pipe closes on crash too; never leave orphan audio tools.

    Only start_job creates this pipe and an isolated session. The read end may
    pass to tools; none inherits the parent's write end (Popen close_fds).
    """
    if os.environ.get("APP_JOB_SUPERVISED") != "1":
        return

    def watch():
        try:
            while os.read(0, 1):
                pass
        finally:
            terminate_job_group()

    threading.Thread(target=watch, name="job-parent-watch", daemon=True).start()


def terminate_job_group():
    """Called inside the isolated worker when its supervisor disappears."""
    if WIN:
        kill_tree(os.getpid())
    else:
        import signal

        # Avoid killing an unrelated session if invoked outside start_job.
        if os.getpgrp() == os.getpid():
            with contextlib.suppress(OSError):
                os.killpg(os.getpid(), signal.SIGKILL)
    os._exit(1)


def kill_job(process):
    """Force-stop a job and its tools, then reap the supervised child."""
    if WIN:
        kill_tree(process.pid)
    else:
        import signal

        with contextlib.suppress(OSError):
            os.killpg(process.pid, signal.SIGKILL)
    with contextlib.suppress(subprocess.TimeoutExpired):
        process.wait(timeout=5)


def venv_python(venv):
    """The interpreter of a virtual environment: bin/python on POSIX, Scripts\\python.exe on Windows."""
    return os.path.join(venv, "Scripts", "python.exe") if WIN else os.path.join(venv, "bin", "python")


def cli_command():
    """Command prefix that starts the app CLI with the interpreter running this process (works on POSIX and Windows, with or without a .venv)."""
    # Per-job process launches must not grow this environment variable on every book.
    paths = [os.path.join(ROOT_DIR, "src")] + [p for p in os.environ.get("PYTHONPATH", "").split(os.pathsep) if p]
    os.environ["PYTHONPATH"] = os.pathsep.join(dict.fromkeys(paths))
    os.environ.setdefault("PYTHONWARNINGS", "ignore")
    return [sys.executable, "-m", "app"]
