# Changelog

Newest first, grouped by day. One entry per change, in the same commit or PR as the change.

## 2026-11-13

**Covers: files against match beside the best fit, instant save, refit every 25 (branch `claude/covers-compare-fast-save`)**
Jonathan asked to see the source's and the match's metadata next to the best fit (same narrator, length...), for saving to be instant with the work in the background, and for the ranking to be refitted every 25 choices like the matches.

- `src/app/covers/api.py`: each card has `source` (what the files say: chosen fields, tags, folder, first copy's length) and `match` (the accepted work and the recording it was matched to: author, narrator, length, year). The list no longer asks the database once per searched listen (it took 6-8 s with 800 searched). Choosing refits the ranking only every `AUTO_RELEARN` (25) choices, counted like `linkage.api.AUTO_RETRAIN`.
- `web/src/components/CoversPage.tsx`: a table of the two sides with same/differs marks (words of the shorter name inside the longer one; length within 3%) beside the best fit and in the expanded choices. A saved card leaves the "To choose" list at once and the write follows in the background; a refused save brings the card back with a message. No refetch after a save.


**Cover searches run on the job queue (branch `claude/covers-queue-jobs`)**
Jonathan asked for cover searches to be jobs in the processing queue, to then queue a search for every approved and accepted listen.

- `src/app/covers/run.py`: `enqueue` (one `covers`/`search` job per listen `pending` names) and `search_job` (what the job does, one listen per job). `src/app/server/tasks.py` runs them; `src/app/jobs.py` locks `listen:<id>` for a job with a `listen_id`.
- `src/app/covers/api.py`: `POST /api/covers/run` queues instead of starting a thread (the in-process `STATE` and lock are gone); the list's `job` is now `running`, `queued`, `current`, `error` from the queue. The Covers page shows the queue; `./app covers --queue` queues from a shell.

## 2026-11-09

**Move the Python side to uv, ruff, pyright and the shared strata-python gate (branch `claude/project-thread-iw7199`)**
Jonathan asked for every Python project to use the same tooling as [python-app-template](https://github.com/jburnhams/python-app-template): uv, ruff with `ruff format`, pyright and ty, one `scripts/check` gate, and dependencies kept current. Behaviour of the program is unchanged.

- `pyproject.toml`, `uv.lock`: **new** `[project]`, a `dev` group (test tooling), a `db` group (omarchy-db and psycopg: the Postgres layer, installed by default for development and with `--group db` on a server), `audio` and `model` groups (the old optional requirements files), git sources for `strata-python` and `omarchy-db`, tool configuration (ruff `E,F,I,B,UP,SIM,RUF,C4,PT,S,TID` with `pytest.importorskip` banned, pyright strict ratchet starting at `__init__.py`, ty). `requirements*.txt`: **removed**.
- ruff: line length 120 (was 400) and `ruff format` over `src`, `tests` and `scripts` (a mechanical rewrite of 180 files). Rules the existing code breaks are ignored with a reason in `pyproject.toml`; the ones to look at first are B023, F841, RUF071 (`os.path.commonprefix`) and B005 (multi-character `strip`), which can hide bugs, and S608 (SQL assembled from fragments: audit before adding any new f-string query).
- `src/app/providers/openlibrary.py`: catches `sqlite3.Error` through its own `import sqlite3` instead of through `openlibrary_local.sqlite3` (ruff's unused-import fix removed that module's import; found by comparing every removed import with attribute uses elsewhere).
- `tests/unit/store/*.py`: `pytest.importorskip("omarchy_db")` removed; with the `db` group installed the store tests import it. They need a PostgreSQL, so the default `pytest` run now ignores `tests/unit/store` (`addopts`), and `.github/workflows/store.yml` runs them with `-o addopts=` as before.
- Coverage floor 95 -> 93 with no test removed: `ruff format` splits the one-line `if x: return y` of the old dense style into two lines, and coverage counts lines, so the same unexecuted branches count twice (95%+ -> 93.3% on the same 1056 tests). Ratchet it up from here.
- `.github/workflows/unit.yml`, `integration.yml`, `store.yml`: uv, current action versions, `strata-check --fast` once on Linux 3.13. `upgrade-strata-python.yml`: **new**, weekly PR that moves the library pin after the gate. `.github/dependabot.yml`: **new**, daily, minor and patch grouped, no ignores (Actions, npm, uv).
- `scripts/check`: **new**. `scripts/setup_env.sh`, `setup_env.ps1`: build the environment with uv.
- `README.md`, `AGENTS.md`, `tests/README.md`, `docs/`, messages in `cli.py`: commands for uv.
- Still skipped on a machine without the tools: two audio tests that need `ffmpeg` and `mp3gain` (CI installs both); making them fail instead of skip is a follow-up. Not adopted here: strata-python's `oslib`, settings and server skeleton (this app's `oslib` also runs the job supervisor, so it is not a drop-in), and its pytest plugin (`tests/conftest.py` already isolates home, data folder and tuning variables).
