"""The audiobook catalogue: one record per book, in the SQLite database `<data_dir>/catalogue.db` (see db.py; backed up automatically, see backup.py).

A book is a dict: id, title, author, narrator, series, series_position, duration_seconds, path (where the audio lives, optional), status (one of STATUSES), added (UTC ISO time)."""
from datetime import datetime, timezone

from app import db

STATUSES = ('unlistened', 'listening', 'finished')
TEXT_FIELDS = ('title', 'author', 'narrator', 'series', 'path')
EDITABLE = TEXT_FIELDS + ('series_position', 'duration_seconds', 'status')


class CatalogueError(ValueError):
    """Raised when a book record is invalid."""


class NotFound(KeyError):
    """Raised when no book has the requested id."""


def clean(fields: dict, partial: bool = False) -> dict:
    """Validate and normalise the editable fields of a book. With `partial` (an edit) only the fields given are returned; otherwise a title is required and the rest get defaults."""
    out = {}
    for k in EDITABLE:
        if k not in fields or (partial and fields[k] is None): continue
        v = fields[k]
        if k in TEXT_FIELDS:
            v = (v or '').strip()
            if len(v) > 300: raise CatalogueError(f'{k} is too long (at most 300 characters)')
        elif k == 'status':
            if v not in STATUSES: raise CatalogueError(f'status must be one of {", ".join(STATUSES)}')
        elif v is not None:
            try: v = float(v) if k == 'series_position' else int(v)
            except (TypeError, ValueError): raise CatalogueError(f'{k} must be a number')
            if v < 0: raise CatalogueError(f'{k} cannot be negative')
        out[k] = v
    if not partial:
        if not out.get('title'): raise CatalogueError('a title is required')
        out = dict(dict(author='', narrator='', series='', path='', series_position=None, duration_seconds=None, status='unlistened'), **out)
    elif 'title' in out and not out['title']: raise CatalogueError('a title is required')
    return out


COLUMNS = 'id, title, author, narrator, series, series_position, duration_seconds, path, status, added'


def _one(c, book_id):
    r = c.execute(f'SELECT {COLUMNS} FROM books WHERE id=?', (book_id,)).fetchone()
    if r is None: raise NotFound(book_id)
    return dict(r)


def list_books(query: str = '', status: str | None = None) -> list[dict]:
    """Books sorted by author, series (in order) and title; `query` matches title, author, narrator or series (case-insensitive substring), `status` filters on one status."""
    q = (query or '').strip().lower(); where, args = [], []
    if status: where.append('status=?'); args.append(status)
    if q: where.append("(instr(lower(title), ?) OR instr(lower(author), ?) OR instr(lower(narrator), ?) OR instr(lower(series), ?))"); args += [q] * 4
    c = db.connect()
    try: return [dict(r) for r in c.execute(f"SELECT {COLUMNS} FROM books {'WHERE ' + ' AND '.join(where) if where else ''} ORDER BY lower(author), lower(series), COALESCE(series_position, 0), lower(title), id", args)]
    finally: c.close()


def get_book(book_id: int) -> dict:
    c = db.connect()
    try: return _one(c, book_id)
    finally: c.close()


def add_book(**fields) -> dict:
    """Validate and store a new book; returns the created record."""
    rec = clean(fields); c = db.connect()
    try:
        with c:
            cur = c.execute('INSERT INTO books (title, author, narrator, series, series_position, duration_seconds, path, status, added) VALUES (:title, :author, :narrator, :series, :series_position, :duration_seconds, :path, :status, :added)',
                            dict(rec, added=datetime.now(timezone.utc).isoformat()))
        return _one(c, cur.lastrowid)
    finally: c.close()


def update_book(book_id: int, **fields) -> dict:
    """Change the given fields of a book; returns the updated record."""
    changes = clean(fields, partial=True); c = db.connect()
    try:
        with c:
            _one(c, book_id)
            if changes: c.execute(f"UPDATE books SET {', '.join(f'{k}=:{k}' for k in changes)} WHERE id=:id", dict(changes, id=book_id))
        return _one(c, book_id)
    finally: c.close()


def remove_book(book_id: int) -> dict:
    """Delete a book from the catalogue (never touches audio files); returns the removed record."""
    c = db.connect()
    try:
        with c:
            b = _one(c, book_id); c.execute('DELETE FROM books WHERE id=?', (book_id,))
        return b
    finally: c.close()
