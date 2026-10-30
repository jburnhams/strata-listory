# Unit tests

Shared commands, layout, hermetic setup and file-ownership rules are in [`tests/README.md`](../README.md); read that first. This file is owned by unit-test work.

Fast and isolated: synthetic data and fakes; no external services, no network. `pytest tests/unit --cov` is what CI gates on (`fail_under` in `pyproject.toml`).

## Where things are tested

| Module | Tests | Coverage | Status | Notes |
| --- | --- | --- | --- | --- |
| `settings.py` | `test_settings.py` | 100% | done | home/data_dir/library_dir defaults, overrides, save/load, corrupt JSON |
| `catalogue.py` | `test_catalogue.py` | 93% | done | validation, defaults, ids, search/sort, corrupt database refused and restored, hypothesis title strip |
| `details.py` | `test_details.py` | 96% | done | Audible sidecar parsing, file list, cached probe, cover order and negative cache, loose-file covers, download rules, real ffmpeg probe/embedded art (skipped without ffmpeg) |
| `scan.py` | `test_scan.py` | 97% | done | name parsing (table), one-work decisions, library shapes in a tmp tree, never writes |
| `matcher.py` | `test_matcher.py` | 98% | done | scoring, ASIN-then-search, early stop, merge and bonus, alternatives, failures, pending/radio/tried, evaluate, confirm/ignore, rescan fragments, v1 migration |
| `providers/` | `test_providers.py` | 93% | done | registry and selection, each provider against fake HTTP, key lookup, 429/5xx retries, cache expiry |
| `db.py`, `backup.py` | `test_backup.py` | 97% | done | daily backup, restore with safety copy, migration backup, pruning, failed verification |
| `oslib.py` | `test_oslib.py` | 100% | done | file locks, process liveness, process tree termination, priority, venv/cli helpers |
| `cli.py` | `test_cli.py` | 100% | done | doctor, catalogue commands, set, serve delegation |
| `server/app.py` | `test_server.py` | 100% | done | landing/static index, health, settings, books endpoints |
