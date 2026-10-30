# Progress log

## 2026-10-30: the processor

- Output naming set by the user: `Author/[year] title/00n - author - title.mp3` (book title in every file name, chapter titles in the tags and playlist; no series folder). Added `year` and `year_source` to books (migration 6) with the priority described in the README, and a year tag (`date`). The Processing page now lists the metadata that will be written with each value's source and flags disagreements with the Audible metadata file; the first real example caught Brave New World's author as "CBS Radio Workshop, Aldous Huxley" (an AudiobookDB match) while the Audible file says "Aldous Huxley".

- Chapter sizes set by the user: split only above 60 minutes (parts of about 45), combine only when one is under 10 minutes and the pair stays within 30. Added one exception, flagged to the user: credits-length pieces (under 2 minutes) join a neighbour up to an hour. Real books: a 150-file title went to 26 chapters (12 to 51 minutes), the others stayed at 16 to 30.

- Limiter budget measured from the book's peaks instead of a fixed 3 dB: peak levels per short window are collected in the same pass as loudness; the lift is allowed until the 99th-percentile peak would lose more than 2 dB (loudest peak: 7 dB). On three real books this moved the end level from -20.9/-21.1/-20.6 to -18.7/-18.2/-18.0 LUFS; the one book re-encoded as a test kept its loudness range (6.6 to 6.1 LU) and came out at -1.2 dBTP.

- Added the manual approval queue (`process/queue.py`, Processing page, `process_plan` table): proposals carry the exact plan, a person approves or rejects, only approved plans run; a plan is refused if the inbox files changed since it was proposed. The server runs proposals and approved plans on one background worker thread.
- The first full copy to `~/audio-local` finished (428 GB, 29,086 files). rsync exit 23 came from two files whose names exceed ext4's 255 bytes (curly quotes in long titles); `audio-sync` now lists such files and still fails on any other error. The app's library was switched to the copy with `use-library` (5,128 paths moved, DB backed up first).

- Decided with the user (later the same day): process only confirmed books with a title and author; loudness has a tolerance band (3 dB quiet / 2 dB loud around -18 LUFS) outside which a book is normalised even if it would otherwise be copied; quieter is better than clipping or compensating, so the earlier idea of ReplayGain tags for what gain could not do was dropped, and the limiter allowance was cut from 6 dB to 3 dB.

- Built `./app process` after agreeing the design with the user: mono, LAME VBR tiers by content, -18 LUFS per book, copy-as-is for mono mp3 up to 96 kbps (lossless cut and `mp3gain`), chapters from embedded/Audible/file/pause sources.
- Timings on beelink: LAME V5 about 550x realtime per core; `ebur128` about 1000x (the `loudnorm` filter is 16x slower, so it is not used for measuring); a whole book takes 1.5 to 2 minutes. Measuring the spectrum of whole chapters was the slow part, so it samples three 90 s stretches per chapter.
- Calibration (30 speech and 30 music-heavy excerpts): silence fraction separates them (speech median 19%, music 0%); energy above 10 kHz does not (speech median -25 dB with p90 -21, music median -23, p90 -19). First guess thresholds on the HF ratio put every speech chapter in the top tier, which is why tiers are now chosen on pauses first.
- Bugs caught by testing on real books: a short credits track gave the merged chapter the wrong name; the spectrum pause fraction was computed against a zero duration when a segment ran to the end of its file; after encoding, true peaks overshot the limiter by about 0.8 dB, hence LIMITER_MARGIN.

## 2026-10-30: local copy of the audio share

- beelink has ~850 GB free and the share is 428 GB, so `audio-sync` mirrors it to `~/audio-local` (rsync, one way, nightly) and the app is switched to that copy with `./app use-library`. First full copy ran in the background (about 30 MB/s observed at the start). The copy is excluded from restic (it can be rebuilt from the share, which has its own backup story).
- Safeguards in the script after thinking about "sync" deleting things: never writes to the share; `--delete-delay` plus `--max-delete=300`; aborts if the share lists empty or below half of its previous top-level count; one run at a time (flock); failures raise the usual ntfy alert.

## 2026-10-30: resilience to the share being down

- Added `fs.py`: reachability probe with timeout, short memory and no pile-up, used before touching the audio share or a share-based backup folder; scans abort on a vanished share instead of storing half a library; the book page, covers, mirror and backups degrade as described in the README. The Linux mount table is read from `/proc/self/mountinfo` (`autofs` counts as a network mount because touching an unmounted automount mounts it, which can take a while); other systems skip the probe and rely on error handling.
- Not changed: the data folder must stay on a local disk (SQLite). The systemd service has no dependency on the mounts.

## 2026-10-30: Hardcover

- Hardcover's GraphQL API (token in `~/.config/strata-listory/hardcover.key`, not tracked) works as a search provider (4 of 4 test titles right) and gives ratings, counts, genres, moods, content warnings, tags, series, audiobook editions and public reviews. Its documentation pages returned 403 to my fetch tool, so the exact rate limit and terms were not read; the client keeps to about one request a second.
- First live run: 11 of 12 confirmed books enriched. Hardcover has several records for some classics, so a rating can come from a small duplicate record (Adrian Mole #1: 5.0 from 2 ratings); the book page shows the counts so this is visible.
- Bug found while testing: the backup folder pointed at the SMB share and the `server` PC was down, so opening the database failed with "Host is down". Backups now fall back to `<data_dir>/backups` when the configured folder cannot be written, and a failing automatic backup only prints a warning.

## 2026-10-30: mirror and provider covers

- Covers can now come from any provider (suggestion cover, then each provider by ASIN, then AudiobookDB by id) and are saved with the book's final data in a mirror of the audio tree under `<data_dir>/library/` (the share is read-only). Covers cached earlier under `covers/` move into the mirror on first use; only the "no cover found" markers stay in `covers/`.
- Rate limit and cache lifetime are properties of each provider class, not global constants.

## 2026-10-30: modular providers

- Audnexus (the project AudiobookDB succeeds) found 53 of 53 real ASINs from the `Audible/` folders (47 uk, 6 only in the us catalogue); AudiobookDB found 1 of 12. Only ~150 catalogue items (117 books) have an ASIN in the name, so title search matters more: Audible's own catalogue search returned the right book in the top 2 for 8 of 10 titles.
- Matching is now provider based (`providers/`). Sample of 60 scanned books (`match-test`, 2026-10-30, after the scanner fix below): Audnexus 5% (it is ASIN only), Audible 30% good / 33% any, AudiobookDB 30% / 50%, all three 37% / 55%. Misses are mostly unclean titles, not provider gaps.
- Scanner fix found through that test: chapter files whose names differ only in numbers (`AB_022`, `ch0`, `Part06`) were each becoming a "book". They now fold into one book, and a rescan removes untouched `new` fragments (1,104 on the live library; books 2,280 -> 1,681).

## 2026-10-30: book page and covers

- Found that 118 of the `Audible/` books have a `<title> [ASIN].metadata.json` next to the audio (Audible's own product data: narrators, series, publisher, release date, runtime, ratings, categories, summary, chapters, cover URL). The book page reads it as the richest local source; no API terms involved.
- Covers come from the user's own files (folder image, embedded art, then the cover URL in that metadata file), then, by the user's decision, from the book's AudiobookDB match as a last resort (its terms say the API licence does not cover cover art, i.e. users must sort out rights themselves; judged fine for a private internal catalogue). ffmpeg writes embedded MP3 art as PNG, so the cache accepts jpg/png/webp.
- Mistake worth recording: while testing the backup folder I ran `./app remove 1` on the live database, meaning to remove a test book I had just added, and deleted scanned item #1 instead (an unreviewed, unmatched loose mp3). Restored it from the backup taken just before; nothing else was affected. Use the id the add command prints.

## 2026-10-29: scanning and matching

- Surveyed `~/audio` (read-only SMB mount): 428 GB, 28,000 mp3 + 355 m4b + 94 m4a in 1,183 folders. Layouts vary: author folders with book subfolders, `Title (Unabridged)` folders of chapter files, CD1/CD2 and "(Disc N)" splits, loose files, an `Audible/` folder named `Author Year Title [ASIN]`, and lots of radio (Desert Island Discs, comedy, BBC dramas).
- Scanner on that library: ~5,700 candidates, ~2,300 books, ~3,400 radio episodes. First version split chapter files into thousands of "books" (10,900 candidates); fixed by treating numbered/chapter-named files, disc folders and `Title 02-10` siblings as one book.
- AudiobookDB (beta, ~218K books, successor to Audnexus): ASIN lookup found only 1 of 12 of the `Audible/` ASINs, but title search found most of the same books. On 40 Audible folders title search matched 65% with a naive scorer (some misses were parsing artefacts). Later in the day the service returned 503 for a while, so matching retries 502/503/504 and reports failures plainly.
- Matching never changes a book directly: it records a suggestion with a score and the user confirms or ignores it. Radio is never looked up (saves budget, avoids noise).

## 2026-10-29: start of the audiobook app

- Turned the template into Strata Listory. Replaced `greet.py` with `catalogue.py` and the greeting UI with a library page; the `greeting` setting became `library_dir`. Kept the template structure, tooling, test layout and coverage gates unchanged.
- Catalogue moved to SQLite after the first draft used JSON, because past SQLite corruption makes backups a requirement: corruption check on first open, daily verified online backups with pruning, `./app backup|backups|restore`. Backups share the data disk, so the restic job covers disk loss.
- Assumptions (single user, no file access yet, manual entry) are listed in the README; no feature list was given.

## 2026-10-29: template extraction

- Extracted the shared Python + React full-stack structure from `strata-360` and `strata-stills` into `python-app-template`.
- Included cross-platform `oslib.py`, atomic JSON `settings.py`, FastAPI server with `landing.html` fallback and static asset mounting from `src/app/server/static/`, React 19 + Vite 7 + Tailwind v4 web app with `withBase()` reverse-proxy path resolution, hermetic pytest and Vitest suites with MSW v3, ratcheting coverage floors, and multi-OS GitHub Actions workflows (`unit.yml` and `integration.yml`).
