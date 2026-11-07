"""Stage 1: candidate works for a local title/author from the local identification index. Recall only: nothing here decides a match.

Sources of candidates, all via `textkeys`: ISBN → work; any title key variant → work (with author filter when an author is known, else the best-attested works); and the credited authors' own works that are fuzzily close to the title (typos, subtitle differences)."""
import difflib
import json
from pathlib import Path
import sqlite3

from app.identify import olindex, textkeys as tk

MAX_TITLE_ONLY = 40
MAX_AUTHOR_WORKS = 400
FUZZY_FLOOR = .6


def path(): return olindex.folder() / 'identify.sqlite'


def connect(db=None):
    db = Path(db) if db else path()
    if not db.is_file(): raise FileNotFoundError(f'identification index not built: {db}')
    connection = sqlite3.connect(db.resolve().as_uri() + '?mode=ro', uri=True); connection.row_factory = sqlite3.Row
    return connection


def _in(connection, sql, values, extra=()):
    """Run `sql` containing one `{}` placeholder for an IN list, in safe batches."""
    values = list(dict.fromkeys(values)); rows = []
    for i in range(0, len(values), 400):
        batch = values[i:i + 400]
        rows.extend(connection.execute(sql.format(','.join('?' * len(batch))), [*batch, *extra]).fetchall())
    return rows


def canonical(connection, keys):
    """Follow redirects so duplicate/merged works collapse onto one key."""
    keys = list(dict.fromkeys(keys)); mapping = {k: k for k in keys}
    for row in _in(connection, 'SELECT old, new FROM redirects WHERE old IN ({})', keys): mapping[row['old']] = row['new']
    return mapping


def author_ids(connection, names):
    """Author keys per credited name, by every name variant (aliases included)."""
    found = {}
    for name in tk.people(names):
        ids = {r['author'] for r in _in(connection, 'SELECT author FROM author_names WHERE norm IN ({})', tk.person_variants(name))}
        found[name] = sorted(ids)
    return found


def find(connection, title, author='', isbns=(), limit=60):
    """Candidate works: {work_key: {'via': set(...), 'isbn': bool}}; each later enriched by `describe`."""
    hits = {}
    def add(work, via): hits.setdefault(work, set()).add(via)
    keys = [k for isbn in isbns for k in tk.isbn_keys(isbn)]
    if keys:
        for row in _in(connection, 'SELECT work FROM work_isbns WHERE isbn IN ({})', keys): add(row['work'], 'isbn')
    people = author_ids(connection, author); every = sorted({a for ids in people.values() for a in ids})
    variants = tk.title_variants(title)
    titled = _in(connection, 'SELECT DISTINCT work FROM work_titles WHERE norm IN ({})', variants) if variants else []
    titled = [r['work'] for r in titled]
    if every:
        by_author = {r['work'] for r in _in(connection, 'SELECT work FROM work_authors WHERE author IN ({})', every)}
        mapping = canonical(connection, titled)
        for work in titled:
            if work in by_author or mapping[work] in by_author: add(mapping[work], 'title+author')
    if not every and tk.people(author) and titled:
        # Surname-only (or unknown-spelling) credit: let the title narrow the works, then compare surnames of their authors.
        mapping = canonical(connection, titled); wanted = {tk.surname(p) for p in tk.people(author)}
        for work in list(dict.fromkeys(mapping.values()))[:300]:
            names = [r['name'] for r in connection.execute('SELECT a.name FROM work_authors w JOIN authors a ON a.key=w.author WHERE w.work=?', (work,))]
            if wanted & {tk.surname(n) for n in names}: add(work, 'title+surname')
    if not every or not any(v & {'title+author', 'title+surname'} for v in hits.values()):
        # No credited author, or none of them wrote a work with this title: keep the best-attested title-only hits as candidates so the model can see (and refuse) them.
        mapping = canonical(connection, titled)
        ranked = sorted(set(mapping.values()), key=lambda w: -(connection.execute('SELECT editions FROM works WHERE key=?', (w,)).fetchone() or [0])[0])
        for work in ranked[:MAX_TITLE_ONLY]: add(work, 'title')
    if every:
        mine = [r['key'] for r in connection.execute(f'SELECT w.key FROM work_authors a JOIN works w ON w.key=a.work WHERE a.author IN ({",".join("?" * len(every))}) ORDER BY w.editions DESC LIMIT {MAX_AUTHOR_WORKS}', every)]
        want = tk.title_key(title)
        if want:
            scored = []
            for work in mine:
                best = max((difflib.SequenceMatcher(None, want, t).ratio() for t in _titles(connection, work, 60)), default=0)
                if best >= FUZZY_FLOOR: scored.append((best, work))
            for _, work in sorted(scored, reverse=True)[:15]: add(work, 'author-fuzzy')
    return dict(list(hits.items())[:limit * 3]) if len(hits) > limit * 3 else hits


def _titles(connection, work, limit=200):
    return [r['norm'] for r in connection.execute('SELECT norm FROM work_titles WHERE work=? ORDER BY n DESC LIMIT ?', (work, limit))]


def describe(connection, work):
    """Everything the features need about one work cluster."""
    row = connection.execute('SELECT * FROM works WHERE key=?', (work,)).fetchone()
    if row is None: return None
    authors = [dict(key=r['key'], name=r['name']) for r in connection.execute('SELECT a.key, a.name FROM work_authors w JOIN authors a ON a.key=w.author WHERE w.work=?', (work,))]
    titles = {(r['norm'], r['kind']): r['n'] for r in connection.execute('SELECT norm, kind, n FROM work_titles WHERE work=? ORDER BY n DESC LIMIT 400', (work,))}
    series = [r['norm'] for r in connection.execute('SELECT norm FROM work_series WHERE work=? ORDER BY n DESC LIMIT 5', (work,))]
    isbns = [r['isbn'] for r in connection.execute('SELECT isbn FROM work_isbns WHERE work=? LIMIT 50', (work,))]
    return dict(key=work, title=row['title'], subtitle=row['subtitle'], year=row['year'] or row['first_edition_year'], work_year=row['year'], editions=row['editions'], audio_editions=row['audio_editions'], ratings=row['ratings'],
                rating_average=row['rating_total'] / row['ratings'] if row['ratings'] else None, authors=authors, titles=titles, series=series, isbns=isbns, subjects=json.loads(row['subjects'] or '[]'))
