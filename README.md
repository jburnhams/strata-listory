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
./app set library_dir ~/audio                 # where the audiobooks live
./app scan                                    # find books (read-only), add them as `new`; --dry-run just counts
./app match                                   # look new books up on audiobookdb.org and record suggestions
./app list --review suggested                 # check them; ./app confirm ID applies a suggestion, ./app ignore ID drops one
./app serve                                   # web UI + API on http://127.0.0.1:8360/
```

For hot-reload frontend development, run `./app serve` in one terminal and `cd web && npm run dev` in another (Vite proxies `/api` to `127.0.0.1:8360`).

## Assumptions in this first step

There was no feature list, so these are guesses to be corrected:

- Single user, local-first, no accounts; data lives in `data/catalogue.json` (or the folder set with `./app set data_dir`), a SQLite database (WAL mode). Keep the data folder on a local disk, not an SMB share, because SQLite locking is unreliable over SMB.
- A book has: title (required), author, narrator, series and position, duration, path to the audio, and a listening status (`unlistened`, `listening`, `finished`). Titles are not unique, so an audiobook can be catalogued twice.
- Books can still be entered by hand. Scanning reads folder and file names only (no tags or durations yet) and never writes to, moves or deletes audio files.
- `library_dir` ("audiobook folder") is the folder `scan` walks. Tested against a ~430 GB library of ~28,000 files: about 5,700 candidates, roughly 2,300 books and the rest radio/podcast episodes (kind `radio`, never looked up).
- **Scan heuristics** (`src/app/scan.py`): a single `.m4b`/`.m4a` is a book; a folder of numbered or chapter-named mp3s is one book; folders split as CD1/CD2, "(Disc 3)" or "Title 02-10" are one book; a folder of unrelated tracks gives one candidate per file. Titles, authors, ASINs (`[B004EVP6BY]`), series numbers and "read by" are parsed from names like `Author 2008 Title [ASIN]`, `Author - Title (Unabridged)` and `Title by Author`. Expect some mistakes, which is why everything starts as `new` and goes through review.
- **Matching** (`src/app/matcher.py`): [AudiobookDB](https://audiobookdb.org) (a free community catalogue, beta) is the first source. An ASIN in the folder name is looked up first, then a title search scored on title and author similarity; the best candidate scoring 0.6 or more is recorded as a *suggestion* (0.85 or more is "likely right"). A suggestion changes nothing until confirmed. Needs a free API key in `~/.config/strata-listory/audiobookdb.key` or `APP_ADB_KEY`. Their terms are respected: personal use, responses cached 7 days, attribution shown in the UI, no cover art or descriptions used. The service is a beta and sometimes returns 503; calls are retried and the failure reported.
- **Book page** (`#/book/ID`, `/api/books/ID/details`): all the metadata available for a book: catalogue fields; the Audible `*.metadata.json` file that sits next to purchased books (narrators, series, publisher, release date, runtime, rating, categories, summary, chapters; about 118 of the library's books have one); the audio files with sizes; embedded tags, codec, bitrate and exact duration from `ffprobe` (cached until the files change; needs ffmpeg installed); and the AudiobookDB suggestion. Opening the page fills in `duration_seconds` when it was empty.
- **Covers** (`/api/books/ID/cover`): taken from your own files, in this order: a picture in the book's folder (cover/folder/front, else any picture; for a loose file only one with the same name), art embedded in the audio, then the cover URL in the book's own Audible metadata file, and last the cover of its AudiobookDB match (once confirmed, or a likely match). The downloaded ones are cached in `<data_dir>/covers/`. AudiobookDB's terms do not license its images (§5.1: users must obtain those rights themselves), so they are used only as a private, internal catalogue.
- **Review states:** `new` (scanned), `suggested` (a match awaits review), `confirmed` (accepted, or entered by hand), `ignored`.
- **Backups:** the database is checked for corruption when a process first opens it (a corrupt one is refused with advice to restore), and a verified copy is taken automatically at most once a day, before a schema migration and before a restore, into `<backup_dir>` (default `<data_dir>/backups/`; `./app set backup_dir /path` moves it, e.g. to a network share: backups are built and checked on the local disk, then copied there). Kept: the newest of each of the last 7 days and 4 weeks, plus manual and safety copies for 30 days. `./app backup` backs up now, `./app backups` lists them, `./app restore [file]` restores (default newest; the current database is kept as a `pre-restore` copy). On the default they share the database's disk, so they protect against corruption and mistakes, not disk loss; point `backup_dir` at another disk or share for that.

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
