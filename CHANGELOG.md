# Changelog

Newest first, grouped by day. One entry per change, in the same commit or PR as the change.

## 2026-11-25

**Summaries go to strata-models through strata-python's client (branch `claude/models-bulk`)**
strata-models now takes a normal or low priority job only on its bulk endpoint (`POST /api/jobs` is for one high priority job), so this app's hand-made `POST /api/jobs` with priority normal would be refused once the service restarts.
- `src/app/enrichment/run.py`: `call_strata_models_summarise` calls `strata_python.clients.models.run`, which routes a normal job through the bulk endpoint; its errors are RuntimeErrors and TimeoutError, which `summarise_run` already swallows. The unused `base_url` argument is gone.
- `tests/unit/test_enrichment.py`: the urlopen monkeypatch is replaced by two tests against `fake_models` (a normal job, an empty answer, a failed job, the service down).
- `pyproject.toml`, `uv.lock`: strata-python 0.2.22 or later.

**The job queue is strata-python's `tasks.queue` (branch `claude/project-thread-ewarvp`)**

Jonathan chose "jobs and ingest pipeline" for the follow-up to the 0.2.17 upgrade. This is the jobs half; the ingest pipeline is left, with the reason below.

- `src/app/jobs.py` is rewritten on `strata_python.tasks.queue` (`Queue`, `Worker`): the claim loop, supervisor, deadlines, heartbeats and lost-worker recovery are the library's; kept here are the app's rules (see the file's opening comment). A job is `domain.kind`; `submit` still returns the failed job until a person retries or clears it; `retries=0`, so a failure waits for a person; the failure cascade onto ingest runs and process plans is `on_fail`, in the same transaction; a stopping server fails its job with a known message that the next start replays (bounded as before). `JobQueue`'s methods and the HTTP routes under `/api/jobs` are unchanged; ids are integers in the table and text in the API.
- `src/app/db.py`: schema 14 creates `queue` (the library's table, frozen as SQL here) and carries over queued jobs, failed jobs nobody retried or cleared, and jobs that were running (as failed); `job_work` rows follow their job to its new id. `jobs` stays as history. **New:** a migration may be a function.
- **Removed:** `src/app/job_worker.py` (a job process is `strata_python.tasks.child`'s, kept between jobs), `oslib.start_job`, `kill_job`, `lower_job_priority`, `lower_priority`, `watch_supervisor`; the review lane and the `training_features` priority (nothing has submitted those kinds since the old matching was retired).
- A failure now reads as its message everywhere (`jobs.plain` strips the library's `TypeName: ` prefix); a timeout reads "took longer than N s".
- Tests: `tests/unit/test_jobs.py` rewritten for the new queue (the fake-supervisor tests are gone with the supervisor); `tests/unit/test_backup.py` has the migration test; `tests/integration/test_jobs.py` runs real job processes (a scan, a stalled job killed at its deadline at idle priority, a stop and its replay); `tests/unit/test_oslib.py` keeps the two tests that are still the app's own.
- **Left out on purpose, and why:** `tasks.web.add_jobs` (its `retry` queues the same row again, which would skip the cleanup of a failed job's runs and plans that `JobQueue.retry` does by making a new job; its `cancel` would need the same cascade) and `workflow.Pipeline` for `src/app/ingest/` (5.5k lines of run, event and approval-signature code that the listens flow replaces; moving it would carry the old design over, not retire it). The listens steps (`pipeline_steps.py`) are the candidate: their state lives in Postgres, where `workflow.store.DbStore` fits, and that needs a migration (Jonathan's explicit words).

## 2026-11-24

**strata-python 0.2.17 and strata-react 1.3.10 (branch `claude/project-thread-ewarvp`)**

- `pyproject.toml`, `uv.lock`: `strata-python>=0.2.17,<0.3` (was 0.2.3). `web/package.json`: `strata-react ^1.3.10` (was 1.0.39).
- `src/app/settings.py` is now a `strata_python.settings.Settings` declaration (validated saves, one atomic file write under a lock, `audible_region` as a fixed set of choices); the old function names stay. `./app set`, `./app doctor` and `./app serve` are strata-python's `add_set`, `add_doctor` and `add_serve`; `doctor` always lists the audio modules and tools (optional rows) instead of needing `--audio`.
- `src/app/server/app.py`: `web.base_app` (health with the source commit), `web.add_error_handlers` (the `guard()` wrapper is gone: `CatalogueError` 400, `NotFound` 404, `CorruptDatabase` 503), `web.add_system` (`GET /api/system`: versions and the disk space of the data, backup and final folders), `web.add_web` (relative `<base href>`, so a deep link reloads behind a reverse proxy) and `web.serve`. `PUT /api/settings` keeps the rule that the data folder cannot change under running jobs.
- `src/app/covers/net.py` is a thin adapter over `strata_python.http.Http` (per-host gap, Retry-After, cool-down, retries); `ffprobe` calls in `details.py`, `store/filegroups.py` and `process/probe.py` go through `media.probe`; sha256 and temp-file-then-rename writes use `strata_python.files`.
- Web: strata-react now refuses `title` on `Button` (a `Tooltip` in `CoversPage`); the Node 25 `localStorage` shim in the test setup is gone (`installLayoutShims` does it).
- Left as they were, on purpose: `providers/base.py` keeps its own client (its retry rules, `MatchError` and per-attempt `emit` are tested, and differ from `Http`'s); the web `req` helper keeps the 202 job wait that `fetchJson` has no hook for; `strata-api-types` needs `openapi-typescript`, a new dependency that needs a decision first.

## 2026-11-14

**Tests and OS helpers from strata-python (branch `strata-python-adopt`, on top of the PyPI move)**

- `tests/conftest.py` shrinks to what is the app's own: the plugin now provides the isolated home and data folder (`strata_env_prefix`, `strata_tuning_vars`, `strata_env` in pyproject.toml), the no-network guard, the integration marker and `fake_urlopen`, `fake_popen`, `fake_run`, `no_sleep`. `tests/utils/fakes.py` is deleted (`from strata_python.testing import http_error`).
- `src/app/oslib.py` re-exports `strata_python.oslib` (file lock, pid liveness, kill tree, priority, supervised children with the lifeline on stdin) and keeps only the app's own `cli_command` and `start_job`; a job's worker output now reaches the journal instead of being discarded. Tests of the library's own functions are removed; the real behaviour stays in `tests/integration/test_jobs.py`.
**`listens-match` links a match, not a candidate (branch `manual-match-role`)**
The 28-disc Just William set and its 1613-minute twin, both matched by hand to one work, were not merged as copies: `same_work_groups` reads `match` parts and `apply.assign_work` had linked a `candidate`.

- `src/app/linkage/apply.py` `assign_work`: links role `match` (accepted by the user), as the model's own acceptances are. Test: two hand-matched listens are one `same_work_groups` entry.

**strata-python from PyPI (0.2.3), and the database layer moves from omarchy-db to `strata_python.db` (branch `strata-python-pypi`)**
Jonathan asked to update to the latest release and make the most of it. First step: the dependency and the database layer; the rest follows in small PRs.

- `pyproject.toml`: `strata-python>=0.2.3,<0.3` from PyPI (no git source); dev tools via `strata-python[dev]`, the catalogue via `strata-python[db]`; `omarchy-db` is gone. `[tool.omarchy-db]` is `[tool.strata-db]`. `upgrade-strata-python.yml` is removed (dependabot moves a PyPI pin).
- Imports: `from strata_python.db import connect`; `Base` is written out as the changelog asks (the naming convention is unchanged: no migration drift, `strata-db check` passes).
- **Environment (service and CLI):** `strata_python.db` reads `STRATA_DB_LIVE=1` and `STRATA_DB_DATABASE=omarchy` (the old `OMARCHY_DB_LIVE` no longer counts). The strata-listory service drop-in `strata-db-live.conf` sets both; for the CLI prefix `STRATA_DB_LIVE=1 STRATA_DB_DATABASE=omarchy`.
- `.github/workflows/store.yml` runs `strata-db check` and `tests/unit/store` on a PostgreSQL 18 service instead of the shared workflow, which broke when its fixtures moved ("the strata_python.db fixtures are off").
- A flaky test fixed on the way: the Files search test matched a number that appears in pytest's temp folder names.

**Identification: a CD set kept as one listen per disc, as long as a listen already accepted elsewhere (branch `numbered-runs-twin`)**
Jonathan found Just William kept as 28 per-CD listens (a second library copy) beside the 1613-minute Just William Collection listen.

- `src/app/linkage/containers.py` `find_sequences`, `twin_of`: within a folder, numbered folders of one name (`Title 01`..`Title 28`, no gap, at least three) whose runtimes sum to an accepted listen's runtime within 2% (title words in common, every piece under 60% of it) are joined as ordered parts, status review, no match decided. Run `listens-identify-prepare` and `listens-identify --merge-copies` afterwards: the whole-length listen is then a copy of its twin. Part of `--merge-containers`.
- `./app listens-match <listen> <Open Library work key>` (`apply.assign_work`): gives a listen a work you know it is when the model never offered it (here an Open Library box set with no recording length); accepted by you, other candidates unlinked, and it becomes a label.

**Identification: a CD set kept as one listen per disc, as long as a listen already accepted elsewhere (branch `numbered-runs-twin`)**
Jonathan found Just William kept as 28 per-CD listens (a second library copy) beside the 1613-minute Just William Collection listen.

- `src/app/linkage/containers.py` `find_sequences`, `twin_of`: within a folder, numbered folders of one name (`Title 01`..`Title 28`, no gap, at least three) whose runtimes sum to an accepted listen's runtime within 2% (title words in common, every piece under 60% of it) are joined as ordered parts, status review, no match decided. Run `listens-identify-prepare` and `listens-identify --merge-copies` afterwards: the whole-length listen is then a copy of its twin. Part of `--merge-containers`.

**Covers: a listen of parts is compared by all its parts (branch `covers-parts-length`)**
The joined Sherlock collection showed 4 h (its first book) against the 58 h recording.

- Same fix for the Listens page (list and detail: `src/app/store/api.py`) and the kind classifier's length (`src/app/store/kinds_db.py`), which read only the first or largest part. The cover ranking and the Matches page already summed parts.
- `src/app/covers/api.py` `_compare`: parts (`relation` = part) count as one runtime and file total (the path shown stays the first part's, linked to its own file group).

**Identification: folders of books that are one collection (branch `container-merge`)**
Jonathan asked for anthologies kept as numbered books to match as the whole collection, using approved sub-matches too, and for a general twin check.

- Covers page: each card shows the listen's folder path, linking to its file group on the Files page (`source.path`, `source.group_id` from `src/app/covers/api.py`).
- `src/app/linkage/containers.py`, `./app listens-identify --merge-containers [--dry-run]`: a folder's pieces are matched as one pseudo-listen (folder name, shared title and author, summed runtime); joined as ordered parts when the work is accepted, the sum is a recording within 2% and every piece is under 80% of it. Close sums (to 12%) are listed as proposals. Sub-book matches, including approved ones, are superseded by the collection match. Also lists listens far longer than their work (`overlong`).

## 2026-11-13

**Covers: searched when a match is accepted, and a way back to the matches (branch `claude/covers-sweep-unaccept`)**
Jonathan asked for a cover search as soon as a match is accepted (not only approved) and a button on the Covers page to take a match back to the Matches page.

- `src/app/covers/run.py`: `unsearched`, `sweep`, `sweep_forever`; `src/app/server/app.py` runs the sweep in a thread (`APP_COVER_SWEEP_SECONDS`, default 60, 0 off; tests set 0).
- `src/app/linkage/apply.py` `reopen`, `POST /api/covers/{id}/unaccept`; the list leaves out listens with no accepted match. Covers page: "Back to matches" on each card, gone at once, the card returns with a message if refused.


**Covers: cut plain bands off pictures (branch `claude/covers-crop-padding`)**
Jonathan pointed out that pictures like image 6190 count as square but are a book cover centred on a plain square canvas.

- `src/app/covers/imaging.py`: `padding` (pure, on a 256x256 RGB copy) and `trim_padding` (ffmpeg crop of the original, same format); `src/app/covers/run.py` `store_image` stores the cropped picture. Pictures already stored are unchanged: `./app covers --force --queue` searches again.


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
