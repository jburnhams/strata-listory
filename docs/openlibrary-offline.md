# Offline Open Library

The Open Library provider checks a persistent local SQLite index first and falls back to its online API when no sufficiently close local match is available. Local and online Open Library results remain one source family. The importer never changes audiobook files or the main catalogue database.

Download the works, authors, ratings and editions dumps from <https://openlibrary.org/developers/dumps> into `data/openlibrary/dumps/`, named `works.txt.gz`, `authors.txt.gz`, `ratings.txt.gz` and `editions.txt.gz`. Then run:

```sh
./app openlibrary-import --editions
./app openlibrary-status
```

`--directory PATH` imports files from another directory. `--wait` lets the importer start while downloads finish, using only completed `.gz` files. Downloads should write to `.part` files and rename them after completion.

Import happens once per snapshot. It streams compressed input without keeping expanded text files. Author names and aliases are indexed separately, work records retain author relationships, and individual ratings are aggregated by work into average and count. Exact normalised title indexes and SQLite FTS5 support title/author lookup. Monthly dumps document publication dates inconsistently; missing original years remain unknown and can be supplied by other independent providers. Work creation dates are never used as publication years. Works do not provide edition publishers reliably.

The published database is `data/openlibrary/index.sqlite`. A replacement is built as `index.sqlite.building`, verified, and atomically published after every input and index is complete. Existing searches continue using the previous index during an update. Failed imports leave the previous database intact. The status JSON records import progress, source URLs, compressed and uncompressed sizes, row counts and build time. Saved ingest archives include the local snapshot identity and returned candidates so later review retains the evidence used.

Store this database on local SSD storage. Keep the source `.gz` files for rebuilding; budget additional space for the published index and a replacement during updates. No process loads an entire dump into memory, and normal queries never import or decompress dumps.

The optional `--editions` import streams edition records into indexed edition/work and ISBN tables. It retains publisher, publication date, language, format and edition identifiers separately from the work record; edition dates never overwrite the work index’s first-publication year. Publisher enrichment first uses bounded local edition lookups when available and otherwise uses small cached edition API requests, bounded to three pages. Publishers are applied only from editions in the selected earliest publication year; later reprints cannot supply that publisher. Empty duplicate work records can be resolved through author/year/title-checked edition searches. Audio editions are excluded from book-publisher evidence.

The initial index stores work main titles. Search accepts a missing subtitle only when available subtitle evidence does not conflict or introduce ambiguity. Coauthor lookup searches each name independently, and joined title words such as ChiRunning are recognised. Numbered author-named collections can be discovered from subtitle-only tags; small cached work/attached-edition records supply their subtitles and edition references. Different volume numbers remain distinct books. These title enrichments reuse the existing index.

## omarchy rebuild (2026-11-04)

The Beelink migration omitted the dumps and index. Downloads for all four files in the official `2026-10-31` snapshot were started on omarchy (about 17.5 GB compressed, including about 12.6 GB editions). `scripts/fetch_openlibrary.py 2026-10-31` resumes `.part` transfers, verifies the Archive.org snapshot’s sizes and SHA-1 checksums, then publishes complete `.gz` files and provenance JSON. No expanded text copies are written.

The temporary user units `listory-openlibrary-download` and `listory-openlibrary-import` run the transfer and `./app openlibrary-import --editions --wait` independently of the app, with low CPU/IO priority and memory limits. Inspect `journalctl --user -u listory-openlibrary-download -u listory-openlibrary-import` and `./app openlibrary-status`. Import status stays pending until all required inputs and indexes are complete; only the atomic published index is used for queries. The app continues using the online provider while no index is ready. Re-run the download script and import command after a reboot/interruption; the transient units are not boot services.

## Edition language and index size

Edition imports keep records explicitly tagged English (`/languages/eng`), including multilingual editions containing English, and records without a recognised language code. Explicitly non-English editions and their ISBN/work links are omitted before insertion. Authors, works, ratings and original work years remain intact. Snapshot metadata records `edition_languages: english_or_unknown`.

For an older all-language index, run `./app openlibrary-compact`. It waits for any active import, copies the published database locally, removes explicitly non-English editions and their links, runs SQLite VACUUM to reclaim disk space, verifies the copy, then atomically replaces the index. Existing lookups continue using the old published file until replacement. An error leaves that file intact. Repeating the command on an already filtered index is a no-op. Budget temporary disk space for the copy and VACUUM; compressed source dumps remain available for rebuilding.

On omarchy, `listory-openlibrary-compact.service` is scheduled after the initial import via the shared import lock. Check its journal alongside the import service and `./app openlibrary-status`; `filtering_editions` and `compacting_editions` mean a published index is already available while the smaller replacement is prepared.

## ISBN-led matching

Matching now gathers checksum-valid ISBNs from local identity/tag evidence, compatible provider candidates and indexed editions of up to three compatible suggested works. ISBN-10 and its equivalent 978 ISBN-13 are queried together; 979 identifiers remain ISBN-13. Up to twelve distinct ISBN queries per review use the local edition/ISBN index and exact linked-work lookups, retaining bounded duplicate records, edition titles, subtitles, dates, publisher/ISBN facts and snapshot provenance. Following a work then exposes its other editions; their formats need not share an ISBN.

Conflicting title/author assertions are retained for the normal resolver and review checks. Multiple Open Library edition records remain one provider family and cannot create independent corroboration. Edition dates never overwrite a work's first-publication date. Matching archives the new ISBN evidence, and the incremental training cache records only affected source revisions.

The reverse edition/work index `editions_edition(edition,work)` is included in new imports. It was also added to omarchy's existing index without rereading the dumps; live lookup plans now use that covering index. Read-only ISBN discovery for House of Cards (William D. Cohan, `0141039590`) and The Push (Tommy Caldwell, `0399562702`) completed in 0.185 and 0.115 seconds respectively, including initial work/edition lookup.

## Full-record detail store (`detail.sqlite`)

`identify.sqlite` answers "which work is this" in about a millisecond but keeps only normalised title variants. `data/openlibrary/detail.sqlite` keeps the **whole dump record** of every author, work and edition as one zstd blob per OL key (one trained dictionary per kind, dictionary id stored on every row), so a matched work can later give its edition subtitles, original-case titles, publishers and a link to the full record for output tags. `app.identify.oldetail.connect().get('/works/OL..W')` returns the parsed record (about 0.05 ms for works and authors, 0.4 ms for editions). It is only read for matched works; matching never touches it.

Build (`scripts/build_ol_detail.py`, Python 3.14 for `compression.zstd`): streams the dumps once with parallel workers into `detail.sqlite.building`, verifies, then renames atomically. First build on omarchy (snapshot 2026-10-31): 30 minutes, 22.9 GB (15.5M authors at 75 B, 41.7M works at 126 B, 39.6M English-or-unknown editions at 249 B; the 17.3M explicitly non-English editions are skipped as in the identify index). The key text is stored twice (table and unique index); making the tables `WITHOUT ROWID` should save several GB. Readers on Python < 3.14 use the `zstandard` package (same frame format).

Update story: Open Library arrives as a full dump, so each new snapshot rebuilds `identify.sqlite` and `detail.sqlite` into `.building` files and swaps them in; Audible is incremental and keeps its own archive as the detail store (ASIN-keyed, about 0.4 ms), with its small query index rebuilt from it.

`scripts/openlibrary_refresh.py` (weekly, `listory-openlibrary-refresh.timer` on omarchy) does that: it compares archive.org's latest snapshot with `dumps/snapshot.json`, and when newer downloads it to `dumps-<date>/`, runs `scripts/build_ol_identify.py --dumps` and `scripts/build_ol_detail.py --dumps` (each atomic), then swaps the dump directory and deletes the old one. `--check` only reports; it refuses to start with under 120 GB free.
