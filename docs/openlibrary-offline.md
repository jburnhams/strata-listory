# Offline Open Library

The Open Library provider checks a persistent local SQLite index first and falls back to its online API when no sufficiently close local match is available. Local and online Open Library results remain one source family. The importer never changes audiobook files or the main catalogue database.

Download the works, authors and ratings dumps from <https://openlibrary.org/developers/dumps> into `data/openlibrary/dumps/`, named `works.txt.gz`, `authors.txt.gz` and `ratings.txt.gz`. Then run:

```sh
./app openlibrary-import
./app openlibrary-status
```

`--directory PATH` imports files from another directory. `--wait` lets the importer start while downloads finish, using only completed `.gz` files. Downloads should write to `.part` files and rename them after completion.

Import happens once per snapshot. It streams compressed input without keeping expanded text files. Author names and aliases are indexed separately, work records retain author relationships, and individual ratings are aggregated by work into average and count. Exact normalised title indexes and SQLite FTS5 support title/author lookup. Monthly dumps document publication dates inconsistently; missing original years remain unknown and can be supplied by other independent providers. Work creation dates are never used as publication years. Works do not provide edition publishers reliably.

The published database is `data/openlibrary/index.sqlite`. A replacement is built as `index.sqlite.building`, verified, and atomically published after every input and index is complete. Existing searches continue using the previous index during an update. Failed imports leave the previous database intact. The status JSON records import progress, source URLs, compressed and uncompressed sizes, row counts and build time. Saved ingest archives include the local snapshot identity and returned candidates so later review retains the evidence used.

Store this database on local SSD storage. Keep the source `.gz` files for rebuilding; budget additional space for the published index and a replacement during updates. No process loads an entire dump into memory, and normal queries never import or decompress dumps.

Publisher enrichment uses small cached edition API requests for the matched work, bounded to three pages. No editions dump is downloaded. Publishers are applied only from editions in the selected earliest publication year; later reprints cannot supply that publisher. Empty duplicate work records can be resolved through author/year/title-checked edition searches. Audio editions are excluded from book-publisher evidence.

The initial index stores work main titles. Search accepts a missing subtitle only when available subtitle evidence does not conflict or introduce ambiguity. Coauthor lookup searches each name independently, and joined title words such as ChiRunning are recognised. Numbered author-named collections can be discovered from subtitle-only tags; small cached work/attached-edition records supply their subtitles and edition references. Different volume numbers remain distinct books. These title enrichments reuse the existing index.
