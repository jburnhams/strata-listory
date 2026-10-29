"""The audiobook catalogue: one record per book, kept in `<data_dir>/catalogue.json` (file-locked, written atomically).

A book is a dict: id, title, author, narrator, series, series_position, duration_seconds, path (where the audio lives, optional), status (one of STATUSES), added (UTC ISO time)."""
import json, os
from datetime import datetime, timezone

from app import oslib, settings

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


def _store_path() -> str:
    return os.path.join(settings.data_dir(), 'catalogue.json')


def _read() -> list[dict]:
    try:
        with open(_store_path(), encoding='utf8') as f: data = json.load(f)
    except (OSError, ValueError): return []
    return data if isinstance(data, list) else []


def _write(items: list[dict]):
    tmp = _store_path() + '.tmp'
    with open(tmp, 'w', encoding='utf8') as f: json.dump(items, f, indent=1)
    os.replace(tmp, _store_path())


def _locked():
    os.makedirs(settings.data_dir(), exist_ok=True)
    return open(_store_path() + '.lock', 'a+', encoding='utf8')


def list_books(query: str = '', status: str | None = None) -> list[dict]:
    """Books sorted by author, series (in order) and title; `query` matches title, author, narrator or series (case-insensitive substring), `status` filters on one status."""
    q = (query or '').strip().lower()
    books = [b for b in _read() if (not status or b.get('status') == status) and (not q or any(q in (b.get(k) or '').lower() for k in ('title', 'author', 'narrator', 'series')))]
    return sorted(books, key=lambda b: ((b.get('author') or '').lower(), (b.get('series') or '').lower(), b.get('series_position') or 0, (b.get('title') or '').lower()))


def get_book(book_id: int) -> dict:
    for b in _read():
        if b['id'] == book_id: return b
    raise NotFound(book_id)


def add_book(**fields) -> dict:
    """Validate and store a new book; returns the created record."""
    rec = clean(fields)
    with _locked() as lf, oslib.file_lock(lf):
        items = _read()
        rec = dict(id=max((b['id'] for b in items), default=0) + 1, **rec, added=datetime.now(timezone.utc).isoformat())
        _write(items + [rec])
    return rec


def update_book(book_id: int, **fields) -> dict:
    """Change the given fields of a book; returns the updated record."""
    changes = clean(fields, partial=True)
    with _locked() as lf, oslib.file_lock(lf):
        items = _read()
        for b in items:
            if b['id'] == book_id: b.update(changes); _write(items); return b
    raise NotFound(book_id)


def remove_book(book_id: int) -> dict:
    """Delete a book from the catalogue (never touches audio files); returns the removed record."""
    with _locked() as lf, oslib.file_lock(lf):
        items = _read()
        for b in items:
            if b['id'] == book_id: _write([x for x in items if x is not b]); return b
    raise NotFound(book_id)
