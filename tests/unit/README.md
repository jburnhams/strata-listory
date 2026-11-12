# Unit tests

Shared commands, layout, hermetic setup and file-ownership rules are in [`tests/README.md`](../README.md); read that first. This file is owned by unit-test work.

Fast and isolated: synthetic data and fakes; no external services, no network. `pytest tests/unit --cov` is what CI gates on (`fail_under` in `pyproject.toml`).

## Where things are tested

| Module | Tests | Coverage | Status | Notes |
| --- | --- | --- | --- | --- |
| `settings.py` | `test_settings.py` | 100% | done | home/data_dir/library_dir defaults, overrides, save/load, corrupt JSON |
| `source_files.py` | `test_source_files.py`, `test_ingest_api.py` | 100% | done | Saved inventory names/counts and grouping notes; missing inventory stays unknown without traversing source paths |
| `book_titles.py`, `ingest/metadata.py` | `test_book_titles.py`, `test_ingest.py` | covered | done | Full-title preference, safe missing-subtitle fallback, ambiguity across providers, joined words, author-prefixed collections, linked edition subtitles, publication-edition title aliases, numbered edition labels, archive preservation, conflicting years, invalid links and volume conflicts; provisional lookup hints require independent confirmation, complete-cohort author inference and numeric-fragment protection |
| `contributors.py` | `test_contributors.py` | covered | done | Contribution roles (including plural introductions/forewords/afterwords), complete coauthors, honorifics, bounded name variants, title-bound omission of a single middle initial, slash-separated person credits and explicit reader labels |
| `ingest/grouping.py`, `ingest/collections.py` | `test_grouping.py`, `test_ingest.py` | covered | done | Mixed collections, scoped manifests, disc sequencing, 395-file anthology regression, volume variants, gaps, alternatives, hash-bound corrections, tag conflicts and durable children; N-of-total/counter/byte-split sequences, legacy scope merging, preserved previews, fresh consent and existing plans |
| `ingest/operations.py` | `test_operations.py`, `test_ingest_api.py` | covered | done | Current workflow membership for every linked/queued Library entry, inclusion filters and global review sorting with page-bounded evidence loading |
| `ingest/pipeline.py` | `test_ingest.py`, `test_ingest_api.py` | covered | done | Sequential book/recording/quality consent, approved-identity provider handoff, immutable work fields, dependent signatures, automation, legacy approvals and saved-review projection for omitted-initial matches (competing initials stay blocked) |
| `ingest/previews.py` | `test_previews.py`, `test_ingest_api.py` | covered | done | Stable stratified samples, background deduplication/retries, atomic normalized cache, source freshness and HTTP audio |
| `catalogue.py` | `test_catalogue.py` | 93% | done | validation, defaults, ids, search/sort, corrupt database refused and restored, hypothesis title strip |
| `details.py` | `test_details.py` | 96% | done | Saved HTTP details/covers without source inspection or catalogue writes, saved legacy probes; Audible sidecar parsing, file list, cached probe, cover order and negative cache, loose-file covers, download rules, real ffmpeg probe/embedded art (skipped without ffmpeg) |
| `process/` | `test_process.py` | 96% | done | parsers, chapters/cuts, tiers, gain/copy rules, real-audio planning/execution, cleanup, verification and CLI; decoded timing, mandatory encoding of bad-header sources, verified selected facts and refusal to execute contradictory copy plans; ffmpeg tests skip when ffmpeg is missing |
| `process/queue.py` | `test_queue.py` | 93% | done | summary, propose once, failed plans, decision rules, only approved plans run, changed inbox refused, worker | 
| `providers/openlibrary.py` | `test_providers.py`, `test_enrich.py` | 100% | done | search, fields, cover, not a default matcher; accepted only for a likely match, stored or remembered as not found, asked once and failures tolerated |
| `merge.py` | `test_merge.py` | 98% | done | names/titles, weighted vote with alternatives, people by agreement, layers (who may speak for a field), locks, ranked lists, empty and single source |
| `sources.py` | `test_sources.py` | 98% | done | records from each kind of source, local records, locked values, year resolution (original first, flagged fallbacks), rows with sources and outvoted values, two-step gather with scripted providers |
| `fs.py` | `test_fs.py` | 95% | done | local paths never probed, remembered answers, timeout and no pile-up, mountinfo parsing, and the degraded behaviour of book page, covers, mirror, scan and backups with a down share |
| `enrich.py` | `test_enrich.py` | 97% | done | Hardcover id from suggestion/ASIN/likely search, stored extras and mirror, pending and stale, removal |
| `mirror.py` | `test_mirror.py` | 100% | done | folder layout (files, external, manual, odd names), confirmed books written and kept current, cover replacement and source, failures never break a change |
| `scan.py` | `test_scan.py` | 97% | done | name parsing (table), one-work decisions, library shapes in a tmp tree, never writes |
| `store/kinds.py`, `store/kinds_db.py`, `identify/radioindex.py` | `test_kinds.py`, `store/test_kinds_db.py` (Postgres), `test_radioindex.py`, `test_audibleindex.py` | pure scoring covered | done | Invented anonymous examples modelled on missed patterns (a series credited to a channel, a play with a bracketed channel and year, a collection of radio adaptations); readings by broadcasters stay books; Audible content types, doubtful matches, reruns, dry run and a person's choice never overwritten; fake HTTP for the BBC A-Z and Wikidata fetches |
| `matcher.py` | `test_matcher.py` | 98% | done | scoring, ASIN-then-search, early stop, merge and bonus, alternatives, failures, pending/radio/tried, evaluate, confirm/ignore, rescan fragments, v1 migration |
| `providers/` | `test_providers.py` | 93% | done | registry and selection, each provider against fake HTTP, key lookup, 429/5xx retries, cache expiry |
| `db.py`, `backup.py` | `test_backup.py` | 97% | done | daily backup, restore with safety copy, migration backup, pruning, failed verification |
| `oslib.py` | `test_oslib.py` | 100% | done | file locks, process liveness, process tree termination, priority, venv/cli helpers |
| `cli.py` | `test_cli.py` | 100% | done | doctor, catalogue commands, set, serve delegation |
| `server/app.py` | `test_server.py` | 100% | done | landing/static index, health, settings, books endpoints |

Lookup regression validation (2026-11-03): 668 full-suite tests passed at 95.52% coverage, plus focused saved-probe and bounded-activity regressions. Library/Operations tests forbid full evidence loaders and resolver calls. HTTP reads forbid legacy metadata recomputation and implicit source probing/artwork retrieval.

Distinctive-title regressions in `test_ingest.py` cover correcting unverified artist credits through independent full-title consensus, provisional search hints, observed title collisions, complete coauthors, competing initials and preserved manual author locks. Missing subtitles, generic labels and volume conflicts cannot bypass author checks.

English-edition regressions in `test_ingest.py` / `test_providers.py` cover explicit edition-language hydration, English versus popular foreign/unknown alternatives, consistent full title tags versus chapter labels, manual title locks, invalid links and unknown/multilingual language. Original publication years and separate recording approval remain protected.

Inventory regressions in `test_ingest.py` prohibit loose-file track totals from recursively probing unrelated library files/sidecars. Malformed uint32 track/disc totals retain their missing counts without building a numeric range; duplicate, zero and out-of-range track numbers retain correct completeness counts.

Job hardening tests use fake child processes/clocks to verify hard deadlines, shutdown/crash handling, stopped failures, explicit retries, owned-work failure propagation, remaining batch preservation and interrupted legacy work. HTTP tests reproduce data-folder changes from inside an active handler. Scan tests forbid inline inspection; new inspection jobs remain durable. OS-boundary tests mock process sessions/tree termination; real child termination and following-job execution live in integration tests. The shared isolation fixture also patches the default data directory so removing APP_DATA cannot access the real catalogue.

`test_jobs.py` verifies durable deduplication, transactional shared worker limits across job types, same-book exclusion, error/result persistence, owner-aware recovery and interrupted batch continuation. `test_tasks.py` covers worker action dispatch and legacy asset/read-view indexing. `tests/utils/job_client.py` is a deterministic client that awaits job completion like the browser; raw TestClient regressions independently assert HTTP 202 precedes all processing.

Bulk identification regressions cover recording marketing versus written-work subtitle conflicts, explicit English edition discovery with work/volume bounds, failed-provider preservation, and canonical main-title retries retaining contrary hits. `test_match_audit.py` verifies candidate features, unknown labels despite automatic consent, read-only export, overwrite refusal and zero-error precision uncertainty.

`test_match_review.py` covers saved-only reads, exact displayed candidate acceptance,
source identity preservation, stale/superseded/locked/active refusals, downstream
consent invalidation, rejected pending plans, processed-output preservation and
manual holds surviving automatic reconciliation and metadata refresh. HTTP and
job dispatch contracts are covered in `test_ingest_api.py` and `test_tasks.py`.
