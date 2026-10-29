# Unit tests

Shared commands, layout, hermetic setup and file-ownership rules are in [`tests/README.md`](../README.md); read that first. This file is owned by unit-test work.

Fast and isolated: synthetic data and fakes; no external services, no network. `pytest tests/unit --cov` is what CI gates on (`fail_under` in `pyproject.toml`).

## Where things are tested

| Module | Tests | Coverage | Status | Notes |
| --- | --- | --- | --- | --- |
| `settings.py` | `test_settings.py` | 100% | done | home/data_dir/library_dir defaults, overrides, save/load, corrupt JSON |
| `catalogue.py` | `test_catalogue.py` | 93% | done | validation, defaults, ids, search/sort, corrupt store, hypothesis title strip |
| `oslib.py` | `test_oslib.py` | 100% | done | file locks, process liveness, process tree termination, priority, venv/cli helpers |
| `cli.py` | `test_cli.py` | 100% | done | doctor, catalogue commands, set, serve delegation |
| `server/app.py` | `test_server.py` | 100% | done | landing/static index, health, settings, books endpoints |
