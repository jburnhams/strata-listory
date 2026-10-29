# Design brief

## 1. Goal

Catalogue and process my audiobook collection locally: first a catalogue of books (who wrote and narrated them, series, length, where the files are, what I have listened to), then processing of the audio files (scan a folder, read tags and chapters, convert/merge). The stack and CI setup are those proven in `strata-360` and `strata-stills`.

## 2. Architecture

- **Python CLI and HTTP server (`src/app/`)**:
  - `cli.py`: command-line interface (`doctor`, `add`, `list`, `show`, `edit`, `remove`, `set`, `serve`), invoked via `./app` (POSIX) or `app.cmd` (Windows) without needing to activate `.venv` manually.
  - `server/app.py`: FastAPI application serving JSON endpoints under `/api/*` (`/api/books` is the catalogue) and the built React single-page app at `/` (falling back to `server/landing.html` when `web/` has not been built yet).
  - `settings.py`: persistent user configuration (`data_dir`, `library_dir`) in `~/.app/settings.json` (`APP_HOME` override) with atomic file writes.
  - `oslib.py`: cross-platform primitives (file locks, process liveness, process tree termination, priority, CLI command builder) isolating POSIX vs Windows differences in one place.
  - `catalogue.py`: the audiobook catalogue: book validation and a file-locked JSON store (`<data_dir>/catalogue.json`) with add, list/search, get, update and remove.
- **React frontend (`web/`)**:
  - React 19, TypeScript, Vite 7, Tailwind CSS v4 (`@tailwindcss/vite`).
  - Hash-based routing (`web/src/route.ts`) and a typed `fetch` client (`web/src/api.ts`) with `withBase()` so the same build works at `/` or behind a reverse proxy at `/<subpath>/`.
  - `npm run build` outputs directly to `src/app/server/static/`.

## 3. Principles

- **Hermetic tests by default**: every test run gets an isolated home and data directory outside `tmp_path`, stripped environment overrides, and a socket guard that blocks outbound network calls.
- **Two-tier test suites**: fast unit tests (`tests/unit/`, `web/tests/unit/`) and end-to-end integration tests (`tests/integration/`, `web/tests/integration/`), with clear file ownership so parallel agent branches never conflict.
- **Ratcheting coverage floors**: Python unit coverage (`fail_under` in `pyproject.toml`) and web combined coverage (`web/coverage-floor.json`) only ratchet up.
