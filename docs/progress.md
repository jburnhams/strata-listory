# Progress log

## 2026-10-29: start of the audiobook app

- Turned the template into Strata Listory. Replaced `greet.py` with `catalogue.py` and the greeting UI with a library page; the `greeting` setting became `library_dir`. Kept the template structure, tooling, test layout and coverage gates unchanged.
- Assumptions (single user, JSON store, manual entry, no file access yet) are listed in the README; no feature list was given.

## 2026-10-29: template extraction

- Extracted the shared Python + React full-stack structure from `strata-360` and `strata-stills` into `python-app-template`.
- Included cross-platform `oslib.py`, atomic JSON `settings.py`, FastAPI server with `landing.html` fallback and static asset mounting from `src/app/server/static/`, React 19 + Vite 7 + Tailwind v4 web app with `withBase()` reverse-proxy path resolution, hermetic pytest and Vitest suites with MSW v3, ratcheting coverage floors, and multi-OS GitHub Actions workflows (`unit.yml` and `integration.yml`).
