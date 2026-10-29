"""Automatic backups of the catalogue database.

Files: `<data_dir>/backups/catalogue-YYYYmmdd-HHMMSS[-reason].db`, made with SQLite's online backup API (safe while the app is running), verified with an integrity check, and written under a temporary name first so a half-written backup never looks real.
Policy: one per day when the database is opened (db.py), before a schema migration, before a restore, and on demand (`./app backup`). Pruning keeps the newest backup of each of the last 7 days and each of the last 4 weeks, and never deletes a `pre-*` backup newer than 30 days."""
import os, re, shutil, sqlite3
from datetime import datetime, timedelta, timezone

from app import settings

KEEP_DAYS, KEEP_WEEKS, KEEP_PRE_DAYS, INTERVAL = 7, 4, 30, timedelta(hours=24)
NAME = re.compile(r'^catalogue-(\d{8}-\d{6})(?:-([a-z-]+))?\.db$')


def folder() -> str:
    return os.path.join(settings.data_dir(), 'backups')


def _db() -> str:
    return os.path.join(settings.data_dir(), 'catalogue.db')


def list_backups() -> list[dict]:
    """Backups newest first: path, taken (UTC datetime), reason ('' for routine ones)."""
    out = []
    for n in os.listdir(folder()) if os.path.isdir(folder()) else []:
        m = NAME.match(n)
        if m: out.append(dict(path=os.path.join(folder(), n), taken=datetime.strptime(m[1], '%Y%m%d-%H%M%S').replace(tzinfo=timezone.utc), reason=m[2] or ''))
    return sorted(out, key=lambda b: b['taken'], reverse=True)


def check(p: str) -> bool:
    """True when the file is a sound SQLite database."""
    try:
        c = sqlite3.connect(f'file:{p}?mode=ro', uri=True)
        try: return c.execute('PRAGMA integrity_check').fetchone()[0] == 'ok'
        finally: c.close()
    except sqlite3.DatabaseError: return False


def backup(reason: str = '', conn: sqlite3.Connection | None = None) -> str | None:
    """Copy the live database to a new verified backup file and prune old ones; returns its path, or None when there is no database yet."""
    if conn is None and not os.path.exists(_db()): return None
    os.makedirs(folder(), exist_ok=True)
    stamp = datetime.now(timezone.utc).strftime('%Y%m%d-%H%M%S')
    final = os.path.join(folder(), f'catalogue-{stamp}' + (f'-{reason}' if reason else '') + '.db'); tmp = final + '.tmp'
    src = conn or sqlite3.connect(_db()); dst = sqlite3.connect(tmp)
    try: src.backup(dst)
    finally:
        dst.close()
        if conn is None: src.close()
    if not check(tmp): os.remove(tmp); raise RuntimeError('the new backup failed its integrity check; the database may be damaged')
    os.replace(tmp, final); prune(); return final


def maybe_backup() -> str | None:
    """Take a routine backup unless one is under a day old."""
    last = next(iter(list_backups()), None)
    if last and datetime.now(timezone.utc) - last['taken'] < INTERVAL: return None
    return backup()


def prune(now: datetime | None = None):
    now = now or datetime.now(timezone.utc); keep = set(); days = {}; weeks = {}
    for b in list_backups():                                   # newest first, so setdefault keeps the newest of each day / week
        age = now - b['taken']
        if b['reason'].startswith('pre') and age < timedelta(days=KEEP_PRE_DAYS): keep.add(b['path'])
        if age < timedelta(days=KEEP_DAYS): days.setdefault(b['taken'].date(), b['path'])
        if age < timedelta(weeks=KEEP_WEEKS): weeks.setdefault(b['taken'].isocalendar()[:2], b['path'])
    keep |= set(days.values()) | set(weeks.values())
    for b in list_backups():
        if b['path'] not in keep: os.remove(b['path'])


def restore(p: str) -> str | None:
    """Replace the live database with the backup at `p` (checked first); the current one is kept as a `pre-restore` backup. Returns that safety copy's path."""
    if not os.path.isfile(p) or not check(p): raise ValueError(f'{p} is not a sound database backup')
    safety = backup('pre-restore') if os.path.exists(_db()) and check(_db()) else None
    for ext in ('-wal', '-shm'):
        if os.path.exists(_db() + ext): os.remove(_db() + ext)
    tmp = _db() + '.restoring'; shutil.copyfile(p, tmp); os.replace(tmp, _db()); return safety
