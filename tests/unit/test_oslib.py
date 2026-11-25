"""The app's own OS helper (app.oslib): how the CLI starts. File locks, process liveness and termination, priorities and the job processes are strata-python's and tested there; the real behaviour (a crashed server stops its job process and the tools it started, jobs run at idle priority) is in tests/integration/test_jobs.py."""

import os
import subprocess

from app import oslib


def test_building_the_command_again_and_again_does_not_grow_the_python_environment(monkeypatch):
    monkeypatch.setenv("PYTHONPATH", "extra")
    monkeypatch.setenv("PYTHONWARNINGS", "default")
    for _ in range(800):
        oslib.cli_command()
    assert os.environ["PYTHONPATH"] == os.path.join(oslib.ROOT_DIR, "src") + os.pathsep + "extra"


def test_cli_command_runs_the_cli():
    out = subprocess.run([*oslib.cli_command(), "--help"], capture_output=True, text=True, timeout=60)
    assert out.returncode == 0 and "usage" in out.stdout, out.stderr[-300:]
