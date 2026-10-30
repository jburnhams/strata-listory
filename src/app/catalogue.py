"""The audiobook catalogue: one record per book, in the SQLite database `<data_dir>/catalogue.db` (see db.py; backed up automatically, see backup.py).

A book is a dict: id, title, author, narrator, series, series_position, duration_seconds, path (where the audio lives, optional), status (one of STATUSES), added (UTC ISO time); from scanning also asin, kind (KINDS), review (REVIEWS), file_count, and while matching suggestion (see matcher.py) and match_score."""
import json, os
from datetime import datetime, timedelta, timezone

from app import db, mirror

STATUSES = ('unlistened', 'listening', 'finished')
KINDS = ('book', 'radio')                                   # radio: broadcast episodes and the like that scanning found next to the books
REVIEWS = ('new', 'suggested', 'confirmed', 'ignored')      # new: scanned, not matched; suggested: a match awaits review; confirmed: accepted (or entered by hand); ignored: not wanted
TEXT_FIELDS = ('title', 'author', 'narrator', 'series', 'path', 'asin')
CHOICES = dict(status=STATUSES, kind=KINDS, review=REVIEWS)
EDITABLE = TEXT_FIELDS + ('series_position', 'duration_seconds', 'status', 'kind', 'review', 'file_count')


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
        elif k in CHOICES:
            if v not in CHOICES[k]: raise CatalogueError(f'{k} must be one of {", ".join(CHOICES[k])}')
        elif v is not None:
            try: v = float(v) if k == 'series_position' else int(v)
            except (TypeError, ValueError): raise CatalogueError(f'{k} must be a number')
            if v < 0: raise CatalogueError(f'{k} cannot be negative')
        out[k] = v
    if not partial:
        if not out.get('title'): raise CatalogueError('a title is required')
        out = dict(dict(author='', narrator='', series='', path='', asin='', series_position=None, duration_seconds=None, file_count=None, status='unlistened', kind='book', review='confirmed'), **out)
    elif 'title' in out and not out['title']: raise CatalogueError('a title is required')
    return out


COLUMNS = 'id, title, author, narrator, series, series_position, duration_seconds, path, status, added, asin, kind, review, file_count, suggestion, match_score'


def _row(r) -> dict:
    d = dict(r); d['suggestion'] = json.loads(d['suggestion']) if d['suggestion'] else None      # None: matching not tried yet; {}: tried, nothing found
    return d


def _one(c, book_id):
    r = c.execute(f'SELECT {COLUMNS} FROM books WHERE id=?', (book_id,)).fetchone()
    if r is None: raise NotFound(book_id)
    return _row(r)


def _mirrored(book: dict) -> dict:
    """Keep the on-disk mirror (mirror.py) current for books that are part of the catalogue proper (not just scanned); never fails the change."""
    if book['review'] == 'confirmed': mirror.forget_missing_cover(book); mirror.write(book)
    return book


def list_books(query: str = '', status: str | None = None, review: str | None = None, kind: str | None = None) -> list[dict]:
    """Books sorted by author, series (in order) and title; `query` matches title, author, narrator or series (case-insensitive substring), `status` filters on one status."""
    q = (query or '').strip().lower(); where, args = [], []
    for col, val in (('status', status), ('review', review), ('kind', kind)):
        if val: where.append(f'{col}=?'); args.append(val)
    if q: where.append("(instr(lower(title), ?) OR instr(lower(author), ?) OR instr(lower(narrator), ?) OR instr(lower(series), ?))"); args += [q] * 4
    c = db.connect()
    try: return [_row(r) for r in c.execute(f"SELECT {COLUMNS} FROM books {'WHERE ' + ' AND '.join(where) if where else ''} ORDER BY lower(author), lower(series), COALESCE(series_position, 0), lower(title), id", args)]
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
            cur = c.execute('INSERT INTO books (title, author, narrator, series, series_position, duration_seconds, path, status, added, asin, kind, review, file_count) VALUES (:title, :author, :narrator, :series, :series_position, :duration_seconds, :path, :status, :added, :asin, :kind, :review, :file_count)',
                            dict(rec, added=datetime.now(timezone.utc).isoformat()))
        return _mirrored(_one(c, cur.lastrowid))
    finally: c.close()


def update_book(book_id: int, **fields) -> dict:
    """Change the given fields of a book; returns the updated record."""
    changes = clean(fields, partial=True); c = db.connect()
    try:
        with c:
            _one(c, book_id)
            if changes: c.execute(f"UPDATE books SET {', '.join(f'{k}=:{k}' for k in changes)} WHERE id=:id", dict(changes, id=book_id))
        return _mirrored(_one(c, book_id))
    finally: c.close()


def remove_book(book_id: int) -> dict:
    """Delete a book from the catalogue (never touches audio files); returns the removed record."""
    c = db.connect()
    try:
        with c:
            b = _one(c, book_id); c.execute('DELETE FROM books WHERE id=?', (book_id,)); c.execute('DELETE FROM book_extra WHERE book_id=?', (book_id,))
        return b
    finally: c.close()


def add_scanned(candidates) -> dict:
    """Store scan candidates (scan.scan) as `new` books, skipping paths already in the catalogue; returns {added, existing, replaced}. A rescan refreshes the file count of known paths and nothing else, except that untouched `new` entries for single files now inside a larger candidate (the scanner has learned that they are chapters of one book) are removed."""
    added = existing = replaced = 0; c = db.connect()
    try:
        with c:
            for k in candidates:
                if os.path.isdir(k['path']):
                    replaced += c.execute("DELETE FROM books WHERE review='new' AND (suggestion IS NULL OR suggestion='{}') AND path LIKE ? ESCAPE '\\'", (k['path'].replace('\\', '\\\\').replace('%', '\\%').replace('_', '\\_') + os.sep + '%',)).rowcount
                if c.execute('SELECT 1 FROM books WHERE path=?', (k['path'],)).fetchone():
                    c.execute('UPDATE books SET file_count=? WHERE path=?', (k['file_count'], k['path'])); existing += 1; continue
                rec = clean(dict(title=k['title'] or os.path.basename(k['path']), author=k['author'], narrator=k['narrator'], series_position=k['series_position'], path=k['path'], asin=k['asin'], kind=k['kind'], review='new', file_count=k['file_count']))
                c.execute('INSERT INTO books (title, author, narrator, series, series_position, duration_seconds, path, status, added, asin, kind, review, file_count) VALUES (:title, :author, :narrator, :series, :series_position, :duration_seconds, :path, :status, :added, :asin, :kind, :review, :file_count)', dict(rec, added=datetime.now(timezone.utc).isoformat()))
                added += 1
        return dict(added=added, existing=existing, replaced=replaced)
    finally: c.close()


def set_suggestion(book_id: int, suggestion: dict, score: float | None):
    """Record the outcome of matching: a suggestion (review -> `suggested`) or {} when nothing was found (stays `new`, but is not tried again)."""
    c = db.connect()
    try:
        with c:
            _one(c, book_id)
            c.execute('UPDATE books SET suggestion=?, match_score=?, review=CASE WHEN ? THEN \'suggested\' ELSE review END WHERE id=?', (json.dumps(suggestion), score, bool(suggestion), book_id))
        return _one(c, book_id)
    finally: c.close()


def pending_match(limit: int) -> list[dict]:
    """Scanned books (not radio) whose matching has not been tried yet."""
    c = db.connect()
    try: return [_row(r) for r in c.execute(f"SELECT {COLUMNS} FROM books WHERE review='new' AND kind='book' AND suggestion IS NULL ORDER BY id LIMIT ?", (limit,))]
    finally: c.close()


def confirm(book_id: int) -> dict:
    """Accept a book: apply its suggestion's title, author, narrators, series and ASIN (and the runtime when the duration is unknown), and mark it confirmed."""
    b = get_book(book_id); sug = b['suggestion'] or {}
    changes = dict(review='confirmed')
    for k in ('title', 'author', 'series', 'series_position', 'asin'):
        if sug.get(k) not in (None, ''): changes[k] = sug[k]
    if sug.get('narrators'): changes['narrator'] = ', '.join(sug['narrators'])
    if sug.get('runtime_minutes') and not b['duration_seconds']: changes['duration_seconds'] = int(sug['runtime_minutes']) * 60
    return update_book(book_id, **changes)


def ignore(book_id: int) -> dict:
    return update_book(book_id, review='ignored')


def set_extra(book_id: int, source: str, data: dict):
    """Store a provider's extra data about a book (ratings, reviews and the like), replacing the previous copy."""
    c = db.connect()
    try:
        with c:
            _one(c, book_id)
            c.execute('INSERT OR REPLACE INTO book_extra (book_id, source, fetched, data) VALUES (?, ?, ?, ?)', (book_id, source, datetime.now(timezone.utc).isoformat(), json.dumps(data)))
    finally: c.close()


def get_extras(book_id: int) -> dict:
    """{source: {fetched, data}} for a book."""
    c = db.connect()
    try: return {r['source']: dict(fetched=r['fetched'], data=json.loads(r['data'])) for r in c.execute('SELECT source, fetched, data FROM book_extra WHERE book_id=?', (book_id,))}
    finally: c.close()


def pending_extra(source: str, days: float, limit: int) -> list[dict]:
    """Confirmed books with no `source` extra data, or data older than `days`."""
    cutoff = (datetime.now(timezone.utc) - timedelta(days=days)).isoformat(); c = db.connect()
    try: return [_row(r) for r in c.execute(f"SELECT {COLUMNS} FROM books b WHERE review='confirmed' AND kind='book' AND NOT EXISTS (SELECT 1 FROM book_extra e WHERE e.book_id=b.id AND e.source=? AND e.fetched>?) ORDER BY id LIMIT ?", (source, cutoff, limit))]
    finally: c.close()


def rebase_paths(old: str, new: str) -> int:
    """Point every book whose path is under `old` at the same place under `new` (the library moved, for example from the share to a local copy); returns how many."""
    old, new = old.rstrip(os.sep), new.rstrip(os.sep); c = db.connect()
    try:
        with c:
            esc = old.replace('\\', '\\\\').replace('%', '\\%').replace('_', '\\_')
            return c.execute("UPDATE books SET path = ? || substr(path, ?) WHERE path = ? OR path LIKE ? ESCAPE '\\'", (new, len(old) + 1, old, esc + os.sep + '%')).rowcount
    finally: c.close()


def paths_under(root: str) -> list[str]:
    root = root.rstrip(os.sep); c = db.connect()
    try: return [r['path'] for r in c.execute("SELECT path FROM books WHERE path = ? OR path LIKE ? ESCAPE '\\'", (root, root.replace('\\', '\\\\').replace('%', '\\%').replace('_', '\\_') + os.sep + '%'))]
    finally: c.close()
