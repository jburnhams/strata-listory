"""The app's own OS helpers (app.oslib): how the CLI and a job worker start. File locks, process liveness and termination, priorities and supervised children are strata-python's and tested there; the real behaviour (a crashed server stops its worker and the tools, jobs run at idle priority) is in tests/integration/test_jobs.py."""

import os
import subprocess

from app import oslib


def test_a_job_starts_the_worker_in_its_own_supervised_group_with_the_data_folder_pinned(monkeypatch):
    calls = []
    monkeypatch.setattr(oslib, "cli_command", lambda: ["python", "-m", "app"])
    monkeypatch.setattr(oslib._os, "start_supervised", lambda cmd, **kw: calls.append((cmd, kw)) or "child")
    assert oslib.start_job("saved", "/database") == "child"
    cmd, kw = calls[0]
    assert cmd == ["python", "-m", "app.job_worker", "saved"] and kw["env"]["APP_DATA"] == "/database"


def test_launching_eight_hundred_jobs_does_not_grow_the_python_environment(monkeypatch):
    monkeypatch.setenv("PYTHONPATH", "extra")
    monkeypatch.setenv("PYTHONWARNINGS", "default")
    paths = []
    monkeypatch.setattr(
        oslib._os, "start_supervised", lambda cmd, env=None, **kw: paths.append(os.environ["PYTHONPATH"])
    )
    for n in range(800):
        oslib.start_job(str(n), "/database")
    assert set(paths) == {os.path.join(oslib.ROOT_DIR, "src") + os.pathsep + "extra"}


def test_cli_command_runs_the_cli():
    out = subprocess.run([*oslib.cli_command(), "--help"], capture_output=True, text=True, timeout=60)
    assert out.returncode == 0 and "usage" in out.stdout, out.stderr[-300:]


def test_a_job_runs_at_idle_cpu_and_disk_priority(monkeypatch):
    calls = []
    monkeypatch.setattr(oslib._os, "lower_priority", lambda nice, io=False: calls.append((nice, io)))
    monkeypatch.setattr(oslib, "WIN", False)
    monkeypatch.setattr(oslib.os, "setpriority", lambda which, who, value: calls.append(("nice", value)), raising=False)
    monkeypatch.setattr(oslib.os, "getpriority", lambda which, who: 0, raising=False)
    oslib.lower_job_priority()
    assert calls == [("nice", 19), (0, True)]  # an absolute nice of 19, then the library's I/O class


def test_priority_is_absolute_and_never_raised(monkeypatch):
    values = []
    monkeypatch.setattr(oslib, "WIN", False)
    monkeypatch.setattr(oslib.os, "getpriority", lambda which, who: 5, raising=False)
    monkeypatch.setattr(oslib.os, "setpriority", lambda which, who, value: values.append(value), raising=False)
    oslib.lower_priority(19)
    oslib.lower_priority(2)
    assert values == [19, 5]  # from a process already at -10 the library's relative os.nice(19) would give 9


def test_windows_uses_the_library_for_priority(monkeypatch):
    calls = []
    monkeypatch.setattr(oslib, "WIN", True)
    monkeypatch.setattr(oslib._os, "lower_priority", lambda nice, io=False: calls.append((nice, io)))
    oslib.lower_priority(19)
    oslib.lower_job_priority()
    assert calls == [(19, False), (19, True)]
