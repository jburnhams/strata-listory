# Implementation plan

- [x] **M0 scaffold**: repository layout, `.venv` setup scripts (`scripts/setup_env.sh`, `scripts/setup_env.ps1`), `./app` and `app.cmd` launchers, `oslib.py`, `settings.py`, `doctor` CLI command.
- [x] **M1 hello-world backend & CLI**: `greet.py` domain module, `hello` and `messages` CLI commands, FastAPI server (`server/app.py`) with `landing.html` fallback.
- [x] **M2 React web app**: Vite + React 19 + Tailwind CSS v4 frontend (`web/`), hash router (`route.ts`), reverse-proxy-safe API client (`api.ts`), greeting and settings pages.
- [x] **M3 test suites & CI**: hermetic pytest unit and integration suites, Vitest unit (node) and integration (jsdom + msw) suites, coverage gates, and GitHub Actions workflows (`unit.yml`, `integration.yml`).
- [x] **M4 catalogue**: `catalogue.py` book model and SQLite store, `add`/`list`/`show`/`edit`/`remove` CLI, `/api/books` endpoints, web library page (add, search, status filter, remove), `library_dir` setting. Replaced the template's hello-world greeting module.
- [ ] **M5 folder scan**: walk `library_dir` for audiobook folders / `.m4b` / `.mp3` files, read tags (title, author, narrator, duration, chapters) and propose catalogue entries; link a book to its files.
- [ ] **M6 processing**: operations on the audio (merge to `.m4b`, chapter files, normalise names); decide whether to shell out to ffmpeg.
- [x] **M7 storage**: SQLite (`db.py`) with automatic verified backups and restore (`backup.py`).
- [ ] **M8 matching**: local Open Library dump in SQLite FTS to match scanned books; optional Audnexus enrichment (narrator, series, runtime); review UI for low-confidence matches.
