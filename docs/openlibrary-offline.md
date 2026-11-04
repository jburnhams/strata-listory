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
