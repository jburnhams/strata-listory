# Offline Open Library

The Open Library provider checks two local, read-only stores first and falls back to the online API when no sufficiently close local match is found. Local and online results stay one source family. Nothing here changes audiobook files or the main catalogue database. History of the earlier single `index.sqlite` design (FTS5 over work titles, removed 2026-11-09) is in `progress.md`.

| Store | Built by | Holds | Used for |
|---|---|---|---|
| `data/openlibrary/identify.sqlite` (~36 GB) | `scripts/build_ol_identify.py` (`identify/olindex.py`, ~2 h) | every title ever given to a work (work, edition, subtitle, full title, other titles) with counts, author names and aliases, ISBNs, ASINs, series, redirects, edition and rating aggregates | finding the work (`identify/candidates.py`, `providers/openlibrary_local.search`, `work_candidates`, ISBN → work) |
| `data/openlibrary/detail.sqlite` (~25 GB) | `scripts/build_ol_detail.py` (`identify/oldetail.py`, ~40 min) | the whole dump record of every author, work and English-or-unlabelled edition as a zstd blob keyed by OL id, plus the edition → work link | covers, edition facts (publishers, dates, formats, ISBNs, subtitles), later output tags |
| `data/openlibrary/dumps/` (~17 GB) | `scripts/fetch_openlibrary.py` | the gzipped monthly dumps (authors, works, editions, ratings, redirects, deletes), kept for rebuilds | |

Both stores are built from one dump directory, as `.building` files, verified, and renamed over the published file; readers keep using the old file until then. `./app openlibrary-status` shows both. Keep them on local SSD.

## Full-record detail store (`detail.sqlite`)

`identify.sqlite` answers "which work is this" in about a millisecond but keeps only normalised title variants. `data/openlibrary/detail.sqlite` keeps the **whole dump record** of every author, work and edition as one zstd blob per OL key (one trained dictionary per kind, dictionary id stored on every row), so a matched work can later give its edition subtitles, original-case titles, publishers and a link to the full record for output tags. `app.identify.oldetail.connect().get('/works/OL..W')` returns the parsed record (about 0.05 ms for works and authors, 0.4 ms for editions). It is only read for matched works; matching never touches it.

Build (`scripts/build_ol_detail.py`, Python 3.14 for `compression.zstd`): streams the dumps once with parallel workers into `detail.sqlite.building`, verifies, then renames atomically. First build on omarchy (snapshot 2026-10-31): 30 minutes, 22.9 GB (15.5M authors at 75 B, 41.7M works at 126 B, 39.6M English-or-unknown editions at 249 B; the 17.3M explicitly non-English editions are skipped as in the identify index). The key text is stored twice (table and unique index); making the tables `WITHOUT ROWID` should save several GB. Readers on Python < 3.14 use the `zstandard` package (same frame format).

Update story: Open Library arrives as a full dump, so each new snapshot rebuilds `identify.sqlite` and `detail.sqlite` into `.building` files and swaps them in; Audible is incremental and keeps its own archive as the detail store (ASIN-keyed, about 0.4 ms), with its small query index rebuilt from it.

`scripts/openlibrary_refresh.py` (weekly, `listory-openlibrary-refresh.timer` on omarchy) does that: it compares archive.org's latest snapshot with `dumps/snapshot.json`, and when newer downloads it to `dumps-<date>/`, runs `scripts/build_ol_identify.py --dumps` and `scripts/build_ol_detail.py --dumps` (each atomic), then swaps the dump directory and deletes the old one. `--check` only reports; it refuses to start with under 120 GB free.
