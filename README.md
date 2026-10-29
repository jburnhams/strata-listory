# Strata Listory

A local-first app to catalogue and process my audiobooks. This is the first step: a catalogue of books (CLI, HTTP API and web UI) on top of a Python + React stack. Processing (scanning a folder of audio files, reading tags, chapters, conversion) comes next.

The stack comes from the `python-app-template`: a FastAPI backend and CLI (`src/app/`), a React 19 + TypeScript + Tailwind CSS v4 frontend (`web/`) that builds into the Python package, hermetic unit and integration test suites (pytest + hypothesis, Vitest + Testing Library + MSW), ratcheting coverage gates, and multi-OS GitHub Actions CI.

## Quick start

```bash
scripts/setup_env.sh                          # macOS / Linux (or scripts\setup_env.ps1 on Windows)
./app doctor                                  # check the environment
./app add "Dune" --author "Frank Herbert" --series Dune --series-position 1
./app list                                    # the catalogue (search with ./app list herbert --status unlistened)
./app backup                                  # back up the database now (also automatic daily)
./app serve                                   # web UI + API on http://127.0.0.1:8360/
```

For hot-reload frontend development, run `./app serve` in one terminal and `cd web && npm run dev` in another (Vite proxies `/api` to `127.0.0.1:8360`).

## Assumptions in this first step

There was no feature list, so these are guesses to be corrected:

- Single user, local-first, no accounts; data lives in `data/catalogue.json` (or the folder set with `./app set data_dir`), a SQLite database (WAL mode). Keep the data folder on a local disk, not an SMB share, because SQLite locking is unreliable over SMB.
- A book has: title (required), author, narrator, series and position, duration, path to the audio, and a listening status (`unlistened`, `listening`, `finished`). Titles are not unique, so an audiobook can be catalogued twice.
- Books are entered by hand for now. The catalogue never reads, moves or deletes audio files.
- `library_dir` ("audiobook folder") is a setting only; nothing uses it yet. It is reserved for the folder scan.
- **Backups:** the database is checked for corruption when a process first opens it (a corrupt one is refused with advice to restore), and a verified copy is taken automatically at most once a day, before a schema migration and before a restore, into `<data_dir>/backups/`. Kept: the newest of each of the last 7 days and 4 weeks, plus safety copies for 30 days. `./app backup` backs up now, `./app backups` lists them, `./app restore [file]` restores (default newest; the current database is kept as a `pre-restore` copy). These are on the same disk as the database, so they protect against corruption and mistakes, not disk loss; the nightly restic backup of `$HOME` covers that when the data folder is under it.

## Documentation

| Document | What it is |
|---|---|
| [docs/overview.md](docs/overview.md) | The design brief: goals, architecture, layout. |
| [docs/implementation-plan.md](docs/implementation-plan.md) | Milestones and what to build next, in order. |
| [docs/progress.md](docs/progress.md) | The log of what was tried, measured and decided. |
| [AGENTS.md](AGENTS.md) | Repository conventions and commands (for people and coding agents). |

## Tests

```bash
pip install -r requirements-test.txt
pytest tests/unit --cov                 # unit tests + coverage (CI gates on fail_under in pyproject.toml)
pytest tests/integration -n auto        # CLI and HTTP API end-to-end
ruff check src tests
cd web && npm run test:coverage:gate    # web unit + jsdom integration tests against coverage-floor.json
```

See [tests/README.md](tests/README.md) and [web/tests/README.md](web/tests/README.md) for the test layout, hermetic fixtures, and file-ownership conventions.
