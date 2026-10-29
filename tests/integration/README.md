# Integration tests

Shared commands, layout, hermetic setup and file-ownership rules are in [`tests/README.md`](../README.md); read that first. This file is owned by integration-test work.

These run the program's boundaries end to end: the command line (in-process and as a subprocess) and the HTTP API over real on-disk files.

## Fixtures (`conftest.py`)

| Fixture | What it gives you |
| --- | --- |
| `cli` | `cli('add', 'Dune', '--json')` -> `Result(stdout, stderr, code, out)`, **in process** (fast). Raises with the output on a non-zero exit unless `check=False`. |
| `cli_subprocess` | The same through a real `python -m app` process: entry-point smoke test, and anything that must not share this process's state. |
| `client` | A FastAPI `TestClient` over the real on-disk state. |

## Coverage

Not gated. `pytest tests/integration -n auto --cov --cov-fail-under=0 --cov-report=term-missing`.

| Area | Tests |
| --- | --- |
| `cli.py` | `test_cli.py`: doctor, catalogue commands, settings, real subprocess |
| `server/app.py` | `test_server_api.py`: health, books shared with the CLI, settings updates, validation errors |
