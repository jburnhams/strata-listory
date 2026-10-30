"""Automatic backups of the catalogue database.

Files: `<backup_dir>/catalogue-YYYYmmdd-HHMMSS[-reason].db`, made with SQLite's online backup API (safe while the app is running), verified with an integrity check, built and verified on the local disk (SQLite is unreliable over SMB), then copied to the backup folder under a temporary name and renamed, so a half-written backup never looks real. The backup folder (setting `backup_dir`, default `<data_dir>/backups`) may be a network share.
Policy: one per day when the database is opened (db.py), before a schema migration, before a restore, and on demand (`./app backup`). Pruning keeps the newest backup of each of the last 7 days and each of the last 4 weeks, and never deletes a manual or `pre-*` backup newer than 30 days (so a restore's own safety copy cannot prune the backup being restored)."""
import os, re, shutil, sqlite3
from datetime import datetime, timedelta, timezone

from app import fs, settings

KEEP_DAYS, KEEP_WEEKS, KEEP_SPECIAL_DAYS, INTERVAL = 7, 4, 30, timedelta(hours=24)
NAME = re.compile(r'^catalogue-(\d{8}-\d{6})(?:-([a-z-]+))?\.db$')


def folder() -> str:
    return settings.backup_dir()


def _db() -> str:
    return os.path.join(settings.data_dir(), 'catalogue.db')


def list_backups(where: str | None = None) -> list[dict]:
    """Backups newest first: path, taken (UTC datetime), reason ('' for routine ones). Looks in the configured folder and the local fallback folder (see `backup`), or only in `where`."""
    out = []
    for d in dict.fromkeys([where] if where else [folder(), os.path.join(settings.data_dir(), 'backups')]):
        try: names = os.listdir(d) if os.path.isdir(d) else []
        except OSError: names = []                              # an unreachable share lists nothing
        for n in names:
            if m := NAME.match(n): out.append(dict(path=os.path.join(d, n), taken=datetime.strptime(m[1], '%Y%m%d-%H%M%S').replace(tzinfo=timezone.utc), reason=m[2] or ''))
    return sorted(out, key=lambda b: b['taken'], reverse=True)


def check(p: str) -> bool:
    """True when the file is a sound SQLite database."""
    try:
        c = sqlite3.connect(f'file:{p}?mode=ro', uri=True)
        try: return c.execute('PRAGMA integrity_check').fetchone()[0] == 'ok'
        finally: c.close()
    except sqlite3.DatabaseError: return False


def _scratch(name: str) -> str:
    """A path on the local disk (next to the database) for building and checking a file."""
    return os.path.join(settings.data_dir(), name)


def _usable_folder() -> str:
    """The backup folder, or the local default one while the configured folder (a share, say) cannot be written, so a backup is never lost to an unreachable share."""
    try:
        if not fs.reachable(folder()): raise OSError('backup folder not reachable')
        os.makedirs(folder(), exist_ok=True)
        probe = os.path.join(folder(), '.write-test'); open(probe, 'w').close(); os.remove(probe)
        return folder()
    except OSError:
        local = os.path.join(settings.data_dir(), 'backups'); os.makedirs(local, exist_ok=True); return local


def backup(reason: str = '', conn: sqlite3.Connection | None = None) -> str | None:
    """Copy the live database to a new verified backup file and prune old ones; returns its path, or None when there is no database yet. While the configured backup folder is unreachable the backup goes to `<data_dir>/backups` instead."""
    if conn is None and not os.path.exists(_db()): return None
    dest = _usable_folder()
    stamp = datetime.now(timezone.utc).strftime('%Y%m%d-%H%M%S')
    final = os.path.join(dest, f'catalogue-{stamp}' + (f'-{reason}' if reason else '') + '.db'); work = _scratch('backup-building.tmp')
    src = conn or sqlite3.connect(_db()); dst = sqlite3.connect(work)
    try: src.backup(dst)
    finally:
        dst.close()
        if conn is None: src.close()
    try:
        if not check(work): raise RuntimeError('the new backup failed its integrity check; the database may be damaged')
        shutil.copyfile(work, final + '.tmp'); os.replace(final + '.tmp', final)
    finally:
        for f in (work, work + '-journal', final + '.tmp'):
            if os.path.exists(f): os.remove(f)
    prune(dest); return final


def maybe_backup() -> str | None:
    """Take a routine backup unless one is under a day old."""
    last = next(iter(list_backups()), None)
    if last and datetime.now(timezone.utc) - last['taken'] < INTERVAL: return None
    return backup()


def prune(where: str | None = None, now: datetime | None = None):
    """Apply the retention policy to the backups in one folder (default: the configured one)."""
    where = where or folder(); now = now or datetime.now(timezone.utc); keep = set(); days = {}; weeks = {}
    for b in list_backups(where):                                   # newest first, so setdefault keeps the newest of each day / week
        age = now - b['taken']
        if b['reason'] and age < timedelta(days=KEEP_SPECIAL_DAYS): keep.add(b['path'])
        if age < timedelta(days=KEEP_DAYS): days.setdefault(b['taken'].date(), b['path'])
        if age < timedelta(weeks=KEEP_WEEKS): weeks.setdefault(b['taken'].isocalendar()[:2], b['path'])
    keep |= set(days.values()) | set(weeks.values())
    for b in list_backups(where):
        if b['path'] not in keep: os.remove(b['path'])


def restore(p: str) -> str | None:
    """Replace the live database with the backup at `p` (checked first); the current one is kept as a `pre-restore` backup. Returns that safety copy's path."""
    tmp = _db() + '.restoring'
    try:
        if not os.path.isfile(p): raise ValueError(f'{p} is not a sound database backup')
        shutil.copyfile(p, tmp)                                    # a local copy: checked here (not over SMB), and the safety backup below prunes, so it must not be able to remove `p`
        if not check(tmp): raise ValueError(f'{p} is not a sound database backup')
        safety = backup('pre-restore') if os.path.exists(_db()) and check(_db()) else None
        for ext in ('-wal', '-shm'):
            if os.path.exists(_db() + ext): os.remove(_db() + ext)
        os.replace(tmp, _db()); return safety
    finally:
        if os.path.exists(tmp): os.remove(tmp)
