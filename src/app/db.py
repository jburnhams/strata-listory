"""The SQLite database `<data_dir>/catalogue.db`: connections, schema, and a corruption check plus a daily automatic backup the first time a process opens it.

Keep the data folder on a local disk: SQLite file locking is unreliable on SMB shares. Backups go to `<data_dir>/backups/` (see backup.py); restoring is `./app restore`."""

import os
import sqlite3
import sys

from app import backup, settings

SCHEMA = (
    """CREATE TABLE books (
        id INTEGER PRIMARY KEY AUTOINCREMENT, title TEXT NOT NULL, author TEXT NOT NULL DEFAULT '', narrator TEXT NOT NULL DEFAULT '', series TEXT NOT NULL DEFAULT '',
        series_position REAL, duration_seconds INTEGER, path TEXT NOT NULL DEFAULT '', status TEXT NOT NULL DEFAULT 'unlistened', added TEXT NOT NULL)""",
    """ALTER TABLE books ADD COLUMN kind TEXT NOT NULL DEFAULT 'book';
       ALTER TABLE books ADD COLUMN review TEXT NOT NULL DEFAULT 'confirmed';
       ALTER TABLE books ADD COLUMN asin TEXT NOT NULL DEFAULT '';
       ALTER TABLE books ADD COLUMN file_count INTEGER;
       ALTER TABLE books ADD COLUMN suggestion TEXT;
       ALTER TABLE books ADD COLUMN match_score REAL;
       CREATE UNIQUE INDEX books_path ON books(path) WHERE path <> '';
       CREATE TABLE match_cache (key TEXT PRIMARY KEY, fetched TEXT NOT NULL, body TEXT NOT NULL)""",
    """CREATE TABLE book_extra (book_id INTEGER NOT NULL, source TEXT NOT NULL, fetched TEXT NOT NULL, data TEXT NOT NULL, PRIMARY KEY (book_id, source))""",
    """CREATE TABLE processed (book_id INTEGER PRIMARY KEY, status TEXT NOT NULL, finished TEXT NOT NULL, summary TEXT NOT NULL)""",
    """CREATE TABLE process_plan (book_id INTEGER PRIMARY KEY, state TEXT NOT NULL, plan TEXT, error TEXT NOT NULL DEFAULT '', created TEXT NOT NULL, updated TEXT NOT NULL)""",
    """ALTER TABLE books ADD COLUMN year INTEGER;
       ALTER TABLE books ADD COLUMN year_source TEXT NOT NULL DEFAULT ''""",
    "ALTER TABLE books ADD COLUMN locked TEXT NOT NULL DEFAULT ''",
    """CREATE TABLE ingest_runs(id TEXT PRIMARY KEY, book_id INTEGER NOT NULL, created TEXT NOT NULL, state TEXT NOT NULL, options TEXT NOT NULL, version INTEGER NOT NULL);
       CREATE TABLE ingest_events(id INTEGER PRIMARY KEY AUTOINCREMENT,run_id TEXT NOT NULL,stage TEXT NOT NULL,status TEXT NOT NULL,created TEXT NOT NULL,elapsed REAL NOT NULL,metrics TEXT NOT NULL,data TEXT NOT NULL,error TEXT NOT NULL);
       CREATE INDEX ingest_events_run ON ingest_events(run_id,id);
       CREATE TABLE ingest_members(run_id TEXT NOT NULL,book_id INTEGER NOT NULL,PRIMARY KEY(run_id,book_id));
       CREATE INDEX ingest_members_book ON ingest_members(book_id);
       CREATE TABLE ingest_policy(id INTEGER PRIMARY KEY AUTOINCREMENT,created TEXT NOT NULL,data TEXT NOT NULL)""",
    """CREATE TABLE jobs(id TEXT PRIMARY KEY,domain TEXT NOT NULL,kind TEXT NOT NULL,options TEXT NOT NULL,dedupe TEXT NOT NULL,state TEXT NOT NULL,created TEXT NOT NULL,started TEXT,finished TEXT,result TEXT,owner_pid INTEGER,error TEXT NOT NULL DEFAULT '');
       CREATE UNIQUE INDEX jobs_pending ON jobs(dedupe) WHERE state IN ('queued','running');
       CREATE INDEX jobs_state ON jobs(state,created);
       CREATE TABLE read_models(kind TEXT NOT NULL,owner TEXT NOT NULL,data TEXT NOT NULL,updated TEXT NOT NULL,PRIMARY KEY(kind,owner))""",
    "ALTER TABLE jobs ADD COLUMN resources TEXT NOT NULL DEFAULT '[]'",
    """ALTER TABLE jobs ADD COLUMN timeout_seconds REAL NOT NULL DEFAULT 1800;
       CREATE TABLE job_work(kind TEXT NOT NULL,owner TEXT NOT NULL,job_id TEXT NOT NULL,PRIMARY KEY(kind,owner));
       CREATE INDEX job_work_job ON job_work(job_id)""",
    """CREATE INDEX jobs_history_dedupe ON jobs(dedupe,created);
       CREATE INDEX jobs_failures ON jobs(finished) WHERE state='failed'""",
    "ALTER TABLE jobs ADD COLUMN progress TEXT",  # JSON {done, total, current} the running handler reports for the Processing page
    lambda c: _queue_table(c),  # strata_python.tasks.queue's table replaces `jobs` (kept as history)
)  # schema version n is SCHEMA[n-1]; append new migrations, never edit old ones (PRAGMA user_version tracks the applied count)


QUEUE = """CREATE TABLE queue (
    id INTEGER NOT NULL, kind VARCHAR(100) NOT NULL, domain VARCHAR(500), options TEXT NOT NULL, dedupe VARCHAR(64),
    resources TEXT NOT NULL, state VARCHAR(20) NOT NULL, priority INTEGER NOT NULL, attempts INTEGER NOT NULL,
    run_after FLOAT, timeout_s FLOAT, created_at FLOAT NOT NULL, started_at FLOAT, finished_at FLOAT, heartbeat_at FLOAT,
    owner VARCHAR(200), owner_pid INTEGER, claim_token VARCHAR(32), cancel_requested BOOLEAN NOT NULL, error TEXT,
    result TEXT, progress TEXT, PRIMARY KEY (id));
    CREATE INDEX ix_queue_dedupe ON queue (dedupe);
    CREATE INDEX ix_queue_claim ON queue (state, priority, id);
    CREATE INDEX ix_queue_domain ON queue (domain)"""  # strata_python.tasks.queue.jobs_table(metadata, "queue") as SQLite writes it, frozen here: a migration never follows the library
OLD_SHUTDOWN = ("job interrupted by server shutdown", "job worker exited without a completed result (exit -15)")


def _progress(raw):
    """The old {current, done, total} as the library's Progress JSON."""
    import json

    if not raw:
        return None
    p = json.loads(raw)
    return json.dumps(dict(done=p.get("done") or 0, total=p.get("total"), text=p.get("current") or ""))


def _queue_table(c):
    """Create the queue and carry over what a person still has to see: queued jobs (they run), failed jobs nobody retried or cleared (they stay on the Processing page) and jobs that were running when the old server stopped (failed, as the old server's own start would have; a stop it asked for is replayed). `job_work` rows that point at a carried job follow it to its new id."""
    import json
    from datetime import datetime

    from strata_python.tasks.queue import dedupe_key

    c.executescript(QUEUE)

    def seconds(text):
        try:
            return datetime.fromisoformat(text).timestamp() if text else None
        except ValueError:
            return None

    cursor = c.execute(
        "SELECT * FROM jobs j WHERE j.state IN ('queued','running') OR (j.state='failed' AND NOT EXISTS (SELECT 1 FROM job_work r WHERE r.kind IN ('retry','dismissed') AND r.owner=j.id) AND NOT EXISTS (SELECT 1 FROM jobs n WHERE n.dedupe=j.dedupe AND n.created>j.created)) ORDER BY j.created,j.id"
    )
    names = [d[0] for d in cursor.description]
    for values in cursor.fetchall():
        r = dict(zip(names, values, strict=True))
        options = json.loads(r["options"])
        state, error, finished = r["state"], r["error"] or None, seconds(r["finished"])
        if state == "running":
            state, error, finished = (
                "failed",
                "Interrupted: job interrupted: previous worker stopped",
                seconds(r["started"]),
            )
        elif error in OLD_SHUTDOWN:
            error = "Interrupted: job interrupted by server shutdown"
        kind = f"{r['domain']}.{r['kind']}"
        c.execute(
            "INSERT INTO queue(kind,domain,options,dedupe,resources,state,priority,attempts,timeout_s,created_at,started_at,finished_at,owner,cancel_requested,error,progress) VALUES (?,?,?,?,?,?,0,0,?,?,?,?,?,0,?,?)",
            (
                kind,
                r["domain"],
                r["options"],
                dedupe_key(kind, r["domain"], options),
                r["resources"],
                state,
                r["timeout_seconds"],
                seconds(r["created"]) or 0.0,
                seconds(r["started"]),
                finished,
                "legacy:" + r["id"],
                error,
                _progress(r["progress"]),
            ),
        )
    c.execute(
        "UPDATE job_work SET job_id=(SELECT CAST(q.id AS TEXT) FROM queue q WHERE q.owner='legacy:'||job_work.job_id) WHERE job_id IN (SELECT substr(owner,8) FROM queue WHERE owner LIKE 'legacy:%')"
    )
    c.execute("UPDATE queue SET owner=NULL WHERE owner LIKE 'legacy:%'")


class CorruptDatabase(RuntimeError):
    """Raised when the database fails its integrity check; the message says how to restore."""


_checked = set()


def path() -> str:
    return os.path.join(settings.data_dir(), "catalogue.db")


def _first_open(p: str):
    """Once per process and database: verify it is sound, then take the daily backup if one is due."""
    if not os.path.exists(p):
        return
    try:
        c = sqlite3.connect(p)
        try:
            ok = c.execute("PRAGMA quick_check").fetchone()[0] == "ok"
        finally:
            c.close()
    except sqlite3.DatabaseError:
        ok = False
    if not ok:
        raise CorruptDatabase(
            f"{p} is corrupt: restore the newest good backup with `./app restore` (`./app backups` lists them)"
        )
    try:
        backup.maybe_backup()
    except (OSError, RuntimeError, sqlite3.Error) as e:
        print(
            f"warning: automatic backup skipped ({e})", file=sys.stderr
        )  # a failing backup must never stop the app opening its database


def connect() -> sqlite3.Connection:
    """A connection with rows as sqlite3.Row, WAL journaling and the schema migrated to the latest version."""
    p = path()
    if p not in _checked:
        _first_open(p)
        _checked.add(p)
    c = sqlite3.connect(p, timeout=30)
    c.row_factory = sqlite3.Row
    c.execute("PRAGMA journal_mode=WAL")
    c.execute("PRAGMA foreign_keys=ON")
    v = c.execute("PRAGMA user_version").fetchone()[0]
    if v < len(SCHEMA):
        if v:
            backup.backup("pre-migration", conn=c)  # an existing database is about to change shape
        for i in range(v, len(SCHEMA)):
            step = SCHEMA[i]
            step(c) if callable(step) else c.executescript(step)
        c.execute(f"PRAGMA user_version={len(SCHEMA)}")
        c.commit()
    return c
