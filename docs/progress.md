# Progress log

## 2026-10-31: books-only ingest-to-output pilot

- Deferred non-books at the user's request. Processed three reviewed book matches with direct Audible product checks and explicit manual review events, retaining unknown corroboration and unchanged global automation settings.
- Private Equity: 12 chapters, -18.1 LUFS/-1.6 dBTP; Is This Anything?: 9 chapters, -18.9/-1.1; Outliers: 16 chapters, -19.2/-1.6. Durations, original source hashes and portable evidence verified. Previous Seinfeld pilot output preserved separately.
- Fixed selected-source handoff validation to include preserved alternatives and fixed the stereo-to-mono gain decision to use measured mono loudness. Existing encoding, loudness tolerance and peak thresholds retained. 296 unit tests passed, 95.52% coverage; targeted real-audio integration, lint and diff checks passed.
- Full outcomes and review limitations: [book-output-pilot-2026-10-31.md](book-output-pilot-2026-10-31.md).

## 2026-10-31: optional audio analysis dependencies installed

- Installed signed Arch Chromaprint 1.6.2 user-locally and NumPy 2.5.4/SciPy 1.18.1 in the project virtual environment. Added optional requirements and `./app doctor --audio` checks, with unit coverage.
- Verified real local fingerprints and waveform alignment against matching Hitch-22 excerpts and an unrelated recording. Dependency consistency passed. These smoke tests do not change ingest thresholds or its current alignment implementation. Setup and evidence: [audio-tools.md](audio-tools.md).

## 2026-10-31: integrated staged ingest and metric policies

- Built the nine-stage ingest pipeline with append-only stage attempts, timings, raw response archives, immutable source manifests and policy snapshots. Added CLI/API and Ingest UI for background jobs, saved metric evaluation, resumable failures and manual proposal. SQLite migration 8 uses existing pre-migration backups.
- Integrated work/recording evidence, selected candidate details/reviews, numeric Audible IDs, Apple public page details, optional keyed Google Books, per-file alignment, quality and source selection. Verified partials remain alternatives; differing/unresolved performances require review. Existing processor consumes only selected files and reused measurements. Final metadata carries selected values/provenance, rejected alternatives and copies of raw source evidence.
- Added atomic group proposals/plan claims, full-hash/sidecar/catalogue/evidence revalidation, auto-job isolation, downstream processing metrics and content-hash quality reuse. Automation defaults off. Metrics/thresholds are predicates, not confidence percentages; unknowns fail closed.
- Live run `19e1dc09b3d04882a86fb1420567bf45` selected Hitch-22 MP3s: 99.9849% coverage, zero unmatched windows, -1.1 dBTP. All stages completed; 21/25 initial gates passed and four reasons kept it in review. No live processing. Per-part matching was corrected during validation so encoder timing differences do not suppress full-source coverage. Earlier run retained.
- Validation: 293 backend unit tests, 11 integration tests and 42 web tests passed; backend coverage 95.48%, web coverage gate/typecheck/build and lint passed.
- Usage, stages, thresholds, preservation model, calibration limits and validation: [ingest-pipeline.md](ingest-pipeline.md).


## 2026-10-31: Hitch-22 audio alignment and quality pilot

- Added an offline experimental comparison module (`python -m app.audio_compare`): local 100 ms energy fingerprints, whole-window correlation, conservative anchored refinement, unresolved/ambiguous states, source hashes, per-file timing maps, coverage and sampled quality diagnostics. No external audio service or new dependency.
- All 15 MP3 parts align to the whole M4B; 2108/2108 windows matched, covering 99.9849% of the reference by correlated intervals. Small boundary differences remain below the feature resolution; neither source has embedded chapter markers. This identifies the same performance, not bit-identical audio or semantic chapters.
- Additional whole-book quality scans and matched spectral samples support preferring the MP3 set: mono 80 kbps/22.05 kHz vs AAC 16 kbps/12 kHz, more high-frequency energy, -1.1 vs +0.8 dBTP. Both decode successfully. All 16 originals rehashed unchanged; no processing approved.
- Full report and provenance: [hitch22-audio-comparison-2026-10-31.md](hitch22-audio-comparison-2026-10-31.md). Unit/adversarial and real FFmpeg tests cover excerpts, reordered parts, gain/encoding changes, edits, repeated content and negative controls.


## 2026-10-31: approved seven-source comparison completed

- User explicitly approved all listed services; ran fresh Audible UK/US, Audnexus, Hardcover, AudiobookDB, Open Library, Apple Books and five Google Books attempts against the original sample. Saved 179 response/page snapshots without credentials or audio. Google returned 429 throughout and contributed no corroboration.
- Investigator outcome: 18/20 identities externally supported; 14 book-related entries represent 13 original works, plus stand-up, one short story and two Audible episodes. The two radio episodes remain externally unresolved. Four entries have embedded IDs corroborated by fresh Audible; another 12 have recording candidates, not automatic approvals.
- Apple product pages supplied narrator/language/duration missing from search results. Collected 45 selected Hardcover review excerpts and 41 Audible product review excerpts. Rejected Thai Outliers and Spanish Hitch-22 false positives; preserved regional/reissue conflicts. Full Sherlock collection measures 3484.5 minutes vs Audible 3484.
- Findings, source assessment and per-entry judgements: [sample20-multisource-review-2026-10-31.md](sample20-multisource-review-2026-10-31.md). Raw and structured evidence under `data/sample20-investigation-20261031T100258/`. No product-code, catalogue or queue changes.


## 2026-10-31: investigator review of all 20 samples

- Fresh Open Library on original catalogue fields: 2 unique links. Recovering titles/authors from tags and parent groups: 9 unique links, 4 duplicate-work ambiguities, plus a convincing Skunk Works candidate rejected by literal author comparison. Investigator assessment: 13 strong book-work identifications, 1 probable, 6 other audio items; 6 of the book entries are fragments, including two from the same book.
- Found duplicated complete M4B + MP3 parts for Hitch-22, explicit unabridged sidecar values ignored by the assessor, and a suspicious publication year for Private Equity. Full per-entry judgements, evidence paths and additional-source assessment: [sample20-review-2026-10-31.md](sample20-review-2026-10-31.md).
- Expanded provider investigation was blocked by automatic approval review pending explicit user approval of the destinations/account-backed API calls. Fresh external findings here are Open Library only; no catalogue or queue changes.


## 2026-10-31: fresh Open Library evidence

- `evidence-report --fresh-openlibrary` searches the candidate title and authors, fetches a uniquely matching work and its ratings, and includes the work description, subjects, original publication year and attributed rating/count. Conflicting recording identities, ambiguous works and contradictory work details are not linked. Work metadata cannot establish recording identity.
- Every search, details or ratings response (including failed requests) is archived before use in a unique, exclusively created JSON file under `data/evidence/openlibrary/`, with book ID, query, URL and retrieval timestamp. Raw unknown fields are retained. Refreshes never overwrite earlier responses or catalogue metadata; default reports remain offline. API reference: https://openlibrary.org/dev/docs/api/books and https://openlibrary.org/dev/docs/api/search.


## 2026-10-31: read-only identity evidence baseline

- Added `evidence-report [IDS] --limit N --stride N`: SQLite read-only snapshot, no normal DB connection/migrations/backups, provider requests, cache writes or queue changes. Every audio file is probed without the book page's 60-file cap. The JSON retains original catalogue/suggestion/extras, all root Audible sidecars, full ffprobe JSON, filename parsing, timestamps and file signatures. Selection retains field provenance, alternatives and manual locks.
- Separate work and recording checks: full title/author agreement, book-catalogue linkage, narrator/identifier, explicit abridgement, measured duration (2% or 120 seconds), album tags and conflicting source fields. Audible/Audnexus/sidecar count as one family. Assessment is deliberately conservative and not a probability. Even candidates cannot automatically process.
- Live stored-evidence sample: 20 books, every 25th eligible item by ID; all 20 require review. All lack a qualifying independent source family, linked work metadata and explicit abridgement; 18 lack recording identity/runtime evidence. One catalogue/filename title conflicts with recording metadata. Artifact: `data/evidence-report-2026-10-31.json` (local, gitignored). No conclusion about fresh provider hit rate follows from this stored-only sample.
- Next: fresh multi-source evidence collection with append-only history, edition-aware candidate association and explicit abridgement/recording fields before calibrating automatic approvals. Existing expiring provider caches are not an archive. File signatures are size/mtime, not content hashes; file completeness beyond what is present cannot yet be proven.


## 2026-10-30: the processor

- Metadata is now merged across sources instead of falling back down a list (user's direction: audiobooks have narrators and producers, the original book has its own metadata, match as many sources as possible and choose the best of each). Probes first: ISBN as the link from audiobook to book rarely works (Audnexus ISBN for 11 of 30 Audible books, Open Library found 0 of those 11, Hardcover recognised 0 of 30 ASINs), so the link is title + author with the audiobook sources agreeing first. New `merge.py` (weighted agreement per field, layers, locks, alternatives kept) and `sources.py` (normalised records, two-step gather, resolve, rows). Added a `locked` column: fields set by hand win; confirming a match locks nothing. Real result: Brave New World now resolves to Aldous Huxley / Michael York / Blackstone Audio / 1932, with "CBS Radio Workshop" shown as outvoted; Right to Kill gains its narrator (Dick Hill) and ASIN from the Audible catalogue search although the folder had none.

- The folder year is now the original publication year (user's decision). Open Library was chosen over Goodreads: Goodreads' terms exclude robots and data extraction (and it has had no API since 2020), so nothing was scraped and the supplied login was not used (stored at `~/.config/strata-listory/goodreads.cred`, mode 600, offered for deletion). On 12 test titles Open Library gave the exact first-published year for 9; misses were a missing work, an off edition-year (The Secret Barrister 2020) and a bad record (Oliver Twist 1822). Hardcover's release date is a weaker second source (it said 1986 for Born a Crime, which is 2016), hence the disagreement note. On the confirmed books: Brave New World 1932 (via Hardcover; Open Library did not match because the catalogue has the author as "CBS Radio Workshop, Aldous Huxley"), Banking on It 2020, Right to Kill 2016, Secret Diary of Adrian Mole 1982, Adrian Mole and the Weapons of Mass Destruction 2004.

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
