"""The SQLite database `<data_dir>/catalogue.db`: connections, schema, and a corruption check plus a daily automatic backup the first time a process opens it.

Keep the data folder on a local disk: SQLite file locking is unreliable on SMB shares. Backups go to `<data_dir>/backups/` (see backup.py); restoring is `./app restore`."""
import os, sqlite3, sys

from app import backup, settings

SCHEMA = (
    '''CREATE TABLE books (
        id INTEGER PRIMARY KEY AUTOINCREMENT, title TEXT NOT NULL, author TEXT NOT NULL DEFAULT '', narrator TEXT NOT NULL DEFAULT '', series TEXT NOT NULL DEFAULT '',
        series_position REAL, duration_seconds INTEGER, path TEXT NOT NULL DEFAULT '', status TEXT NOT NULL DEFAULT 'unlistened', added TEXT NOT NULL)''',
    '''ALTER TABLE books ADD COLUMN kind TEXT NOT NULL DEFAULT 'book';
       ALTER TABLE books ADD COLUMN review TEXT NOT NULL DEFAULT 'confirmed';
       ALTER TABLE books ADD COLUMN asin TEXT NOT NULL DEFAULT '';
       ALTER TABLE books ADD COLUMN file_count INTEGER;
       ALTER TABLE books ADD COLUMN suggestion TEXT;
       ALTER TABLE books ADD COLUMN match_score REAL;
       CREATE UNIQUE INDEX books_path ON books(path) WHERE path <> '';
       CREATE TABLE match_cache (key TEXT PRIMARY KEY, fetched TEXT NOT NULL, body TEXT NOT NULL)''',
    '''CREATE TABLE book_extra (book_id INTEGER NOT NULL, source TEXT NOT NULL, fetched TEXT NOT NULL, data TEXT NOT NULL, PRIMARY KEY (book_id, source))''',
    '''CREATE TABLE processed (book_id INTEGER PRIMARY KEY, status TEXT NOT NULL, finished TEXT NOT NULL, summary TEXT NOT NULL)''',
    '''CREATE TABLE process_plan (book_id INTEGER PRIMARY KEY, state TEXT NOT NULL, plan TEXT, error TEXT NOT NULL DEFAULT '', created TEXT NOT NULL, updated TEXT NOT NULL)''',
    """ALTER TABLE books ADD COLUMN year INTEGER;
       ALTER TABLE books ADD COLUMN year_source TEXT NOT NULL DEFAULT ''""",
    "ALTER TABLE books ADD COLUMN locked TEXT NOT NULL DEFAULT ''",
    """CREATE TABLE ingest_runs(id TEXT PRIMARY KEY, book_id INTEGER NOT NULL, created TEXT NOT NULL, state TEXT NOT NULL, options TEXT NOT NULL, version INTEGER NOT NULL);
       CREATE TABLE ingest_events(id INTEGER PRIMARY KEY AUTOINCREMENT,run_id TEXT NOT NULL,stage TEXT NOT NULL,status TEXT NOT NULL,created TEXT NOT NULL,elapsed REAL NOT NULL,metrics TEXT NOT NULL,data TEXT NOT NULL,error TEXT NOT NULL);
       CREATE INDEX ingest_events_run ON ingest_events(run_id,id);
       CREATE TABLE ingest_members(run_id TEXT NOT NULL,book_id INTEGER NOT NULL,PRIMARY KEY(run_id,book_id));
       CREATE INDEX ingest_members_book ON ingest_members(book_id);
       CREATE TABLE ingest_policy(id INTEGER PRIMARY KEY AUTOINCREMENT,created TEXT NOT NULL,data TEXT NOT NULL)""",
)       # schema version n is SCHEMA[n-1]; append new migrations, never edit old ones (PRAGMA user_version tracks the applied count)


class CorruptDatabase(RuntimeError):
    """Raised when the database fails its integrity check; the message says how to restore."""


_checked = set()


def path() -> str:
    return os.path.join(settings.data_dir(), 'catalogue.db')


def _first_open(p: str):
    """Once per process and database: verify it is sound, then take the daily backup if one is due."""
    if not os.path.exists(p): return
    try:
        c = sqlite3.connect(p)
        try: ok = c.execute('PRAGMA quick_check').fetchone()[0] == 'ok'
        finally: c.close()
    except sqlite3.DatabaseError: ok = False
    if not ok: raise CorruptDatabase(f'{p} is corrupt: restore the newest good backup with `./app restore` (`./app backups` lists them)')
    try: backup.maybe_backup()
    except (OSError, RuntimeError, sqlite3.Error) as e: print(f'warning: automatic backup skipped ({e})', file=sys.stderr)      # a failing backup must never stop the app opening its database


def connect() -> sqlite3.Connection:
    """A connection with rows as sqlite3.Row, WAL journaling and the schema migrated to the latest version."""
    p = path()
    if p not in _checked: _first_open(p); _checked.add(p)
    c = sqlite3.connect(p, timeout=30); c.row_factory = sqlite3.Row
    c.execute('PRAGMA journal_mode=WAL'); c.execute('PRAGMA foreign_keys=ON')
    v = c.execute('PRAGMA user_version').fetchone()[0]
    if v < len(SCHEMA):
        if v: backup.backup('pre-migration', conn=c)         # an existing database is about to change shape
        for i in range(v, len(SCHEMA)): c.executescript(SCHEMA[i])
        c.execute(f'PRAGMA user_version={len(SCHEMA)}'); c.commit()
    return c
