# Progress log

## 2026-10-29: start of the audiobook app

- Turned the template into Strata Listory. Replaced `greet.py` with `catalogue.py` and the greeting UI with a library page; the `greeting` setting became `library_dir`. Kept the template structure, tooling, test layout and coverage gates unchanged.
- Catalogue moved to SQLite after the first draft used JSON, because past SQLite corruption makes backups a requirement: corruption check on first open, daily verified online backups with pruning, `./app backup|backups|restore`. Backups share the data disk, so the restic job covers disk loss.
- Assumptions (single user, no file access yet, manual entry) are listed in the README; no feature list was given.

## 2026-10-29: template extraction

- Extracted the shared Python + React full-stack structure from `strata-360` and `strata-stills` into `python-app-template`.
- Included cross-platform `oslib.py`, atomic JSON `settings.py`, FastAPI server with `landing.html` fallback and static asset mounting from `src/app/server/static/`, React 19 + Vite 7 + Tailwind v4 web app with `withBase()` reverse-proxy path resolution, hermetic pytest and Vitest suites with MSW v3, ratcheting coverage floors, and multi-OS GitHub Actions workflows (`unit.yml` and `integration.yml`).
