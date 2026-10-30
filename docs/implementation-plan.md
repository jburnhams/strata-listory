# Implementation plan

- [x] **M0 scaffold**: repository layout, `.venv` setup scripts (`scripts/setup_env.sh`, `scripts/setup_env.ps1`), `./app` and `app.cmd` launchers, `oslib.py`, `settings.py`, `doctor` CLI command.
- [x] **M1 hello-world backend & CLI**: `greet.py` domain module, `hello` and `messages` CLI commands, FastAPI server (`server/app.py`) with `landing.html` fallback.
- [x] **M2 React web app**: Vite + React 19 + Tailwind CSS v4 frontend (`web/`), hash router (`route.ts`), reverse-proxy-safe API client (`api.ts`), greeting and settings pages.
- [x] **M3 test suites & CI**: hermetic pytest unit and integration suites, Vitest unit (node) and integration (jsdom + msw) suites, coverage gates, and GitHub Actions workflows (`unit.yml`, `integration.yml`).
- [x] **M4 catalogue**: `catalogue.py` book model and SQLite store, `add`/`list`/`show`/`edit`/`remove` CLI, `/api/books` endpoints, web library page (add, search, status filter, remove), `library_dir` setting. Replaced the template's hello-world greeting module.
- [x] **M5 folder scan (names only)**: `scan.py` finds books and radio under `library_dir` and parses names; `./app scan`, `/api/scan`, library page button.
- [ ] **M5b file metadata**: read embedded tags, durations and chapters (ffprobe or mutagen) to improve titles and fill `duration_seconds`.
- [ ] **M6 processing**: operations on the audio (merge to `.m4b`, chapter files, normalise names); decide whether to shell out to ffmpeg.
- [x] **M7 storage**: SQLite (`db.py`) with automatic verified backups and restore (`backup.py`).
- [x] **M8 matching, first source**: AudiobookDB title/ASIN lookup with scored suggestions and a review UI (confirm / ignore).
- [x] **M8b modular providers**: Audnexus, Audible catalogue search and AudiobookDB behind one interface; ordered or combined queries; `match-test` to compare.
- [ ] **M8c improve the hit rate**: sample on 2026-10-30 gave ~37% good / ~55% any with all three providers; the rest are mostly names the scanner could not clean (chapter files, radio-like folders). Next: better title cleaning and author detection, tag-based titles, Open Library for non-Audible titles.
- [x] **M9 community data**: Hardcover as a provider and as the source of ratings, review counts, genres/moods/warnings and top reviews for confirmed books (`enrich.py`, book page, mirror).
