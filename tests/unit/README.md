# Unit tests

Shared commands, layout, hermetic setup and file-ownership rules are in [`tests/README.md`](../README.md); read that first. This file is owned by unit-test work.

Fast and isolated: synthetic data and fakes; no external services, no network. `pytest tests/unit --cov` is what CI gates on (`fail_under` in `pyproject.toml`).

## Where things are tested

| Module | Tests | Coverage | Status | Notes |
| --- | --- | --- | --- | --- |
| `settings.py` | `test_settings.py` | 100% | done | home/data_dir/library_dir defaults, overrides, save/load, corrupt JSON |
| `source_files.py` | `test_source_files.py`, `test_ingest_api.py` | 100% | done | Inventory reuse, directory/single-file samples, unavailable sources, filename counts and no audio probing |
| `book_titles.py` | `test_book_titles.py`, `test_ingest.py` | covered | done | Full-title preference, safe missing-subtitle fallback, ambiguity across providers, joined words, author-prefixed collections and volume conflicts |
| `contributors.py` | `test_contributors.py` | covered | done | Contribution roles, complete coauthors, honorifics and bounded name variants |
| `ingest/grouping.py`, `ingest/collections.py` | `test_grouping.py`, `test_ingest.py` | covered | done | Mixed/nested/flat collections, explicit scoped manifests, disc sequencing, 395-file/28-disc anthology regression, album/volume spelling variants, missing discs/tracks, alternative recordings, hash-bound publisher corrections, filename/tag conflicts, durable child runs and existing-plan preservation |
| `ingest/operations.py` | `test_operations.py`, `test_ingest_api.py` | covered | done | Current workflow membership for every linked/queued Library entry, inclusion filters and global review sorting with page-bounded evidence loading |
| `ingest/pipeline.py` | `test_ingest.py`, `test_ingest_api.py` | covered | done | Sequential book/recording/quality consent, approved-identity provider handoff, immutable work fields, dependent signatures, automation and legacy approvals |
| `ingest/previews.py` | `test_previews.py`, `test_ingest_api.py` | covered | done | Stable stratified samples, background deduplication/retries, atomic normalized cache, source freshness and HTTP audio |
| `catalogue.py` | `test_catalogue.py` | 93% | done | validation, defaults, ids, search/sort, corrupt database refused and restored, hypothesis title strip |
| `details.py` | `test_details.py` | 96% | done | Audible sidecar parsing, file list, cached probe, cover order and negative cache, loose-file covers, download rules, real ffmpeg probe/embedded art (skipped without ffmpeg) |
| `process/` | `test_process.py` | 96% | done | parsers, chapter sources and cut points, tiers, gain rules, copy rules, real-audio planning and execution (encode, music tiers, lossless copy with mp3gain, stereo cancellation, failure cleanup, verify), CLI plan/run/status; ffmpeg tests skip when ffmpeg is missing |
| `process/queue.py` | `test_queue.py` | 93% | done | summary, propose once, failed plans, decision rules, only approved plans run, changed inbox refused, worker | 
| `providers/openlibrary.py` | `test_providers.py`, `test_enrich.py` | 100% | done | search, fields, cover, not a default matcher; accepted only for a likely match, stored or remembered as not found, asked once and failures tolerated |
| `merge.py` | `test_merge.py` | 98% | done | names/titles, weighted vote with alternatives, people by agreement, layers (who may speak for a field), locks, ranked lists, empty and single source |
| `sources.py` | `test_sources.py` | 98% | done | records from each kind of source, local records, locked values, year resolution (original first, flagged fallbacks), rows with sources and outvoted values, two-step gather with scripted providers |
| `fs.py` | `test_fs.py` | 95% | done | local paths never probed, remembered answers, timeout and no pile-up, mountinfo parsing, and the degraded behaviour of book page, covers, mirror, scan and backups with a down share |
| `enrich.py` | `test_enrich.py` | 97% | done | Hardcover id from suggestion/ASIN/likely search, stored extras and mirror, pending and stale, removal |
| `mirror.py` | `test_mirror.py` | 100% | done | folder layout (files, external, manual, odd names), confirmed books written and kept current, cover replacement and source, failures never break a change |
| `scan.py` | `test_scan.py` | 97% | done | name parsing (table), one-work decisions, library shapes in a tmp tree, never writes |
| `matcher.py` | `test_matcher.py` | 98% | done | scoring, ASIN-then-search, early stop, merge and bonus, alternatives, failures, pending/radio/tried, evaluate, confirm/ignore, rescan fragments, v1 migration |
| `providers/` | `test_providers.py` | 93% | done | registry and selection, each provider against fake HTTP, key lookup, 429/5xx retries, cache expiry |
| `db.py`, `backup.py` | `test_backup.py` | 97% | done | daily backup, restore with safety copy, migration backup, pruning, failed verification |
| `oslib.py` | `test_oslib.py` | 100% | done | file locks, process liveness, process tree termination, priority, venv/cli helpers |
| `cli.py` | `test_cli.py` | 100% | done | doctor, catalogue commands, set, serve delegation |
| `server/app.py` | `test_server.py` | 100% | done | landing/static index, health, settings, books endpoints |
