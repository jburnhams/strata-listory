"""The SQLite database `<data_dir>/catalogue.db`: connections, schema, and a corruption check plus a daily automatic backup the first time a process opens it.

Keep the data folder on a local disk: SQLite file locking is unreliable on SMB shares. Backups go to `<data_dir>/backups/` (see backup.py); restoring is `./app restore`."""
import os, sqlite3

from app import backup, settings

SCHEMA = (
    '''CREATE TABLE books (
        id INTEGER PRIMARY KEY AUTOINCREMENT, title TEXT NOT NULL, author TEXT NOT NULL DEFAULT '', narrator TEXT NOT NULL DEFAULT '', series TEXT NOT NULL DEFAULT '',
        series_position REAL, duration_seconds INTEGER, path TEXT NOT NULL DEFAULT '', status TEXT NOT NULL DEFAULT 'unlistened', added TEXT NOT NULL)''',
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
    backup.maybe_backup()


def connect() -> sqlite3.Connection:
    """A connection with rows as sqlite3.Row, WAL journaling and the schema migrated to the latest version."""
    p = path()
    if p not in _checked: _first_open(p); _checked.add(p)
    c = sqlite3.connect(p, timeout=30); c.row_factory = sqlite3.Row
    c.execute('PRAGMA journal_mode=WAL'); c.execute('PRAGMA foreign_keys=ON')
    v = c.execute('PRAGMA user_version').fetchone()[0]
    if v < len(SCHEMA):
        if v: backup.backup('pre-migration', conn=c)         # an existing database is about to change shape
        for i in range(v, len(SCHEMA)): c.execute(SCHEMA[i])
        c.execute(f'PRAGMA user_version={len(SCHEMA)}'); c.commit()
    return c
