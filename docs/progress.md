# Progress log

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
