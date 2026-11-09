"""Local Audible index: build from the archive (`data/audible/audible.sqlite`, written by scripts/fetch_audible_people.py) and query it for candidate recordings.

The archive keeps every response losslessly; this module derives a small query index (`data/audible/index.sqlite`, built as `.building` and renamed atomically) using the shared keys in textkeys, so the key written at build time is the key looked up at query time.

Candidate generation is deliberately permissive (a title alone still finds the possible recordings) and the model decides how reliable a match is from each candidate's `evidence`; many audiobooks are older tape/CD recordings that Audible never had, so "no candidates", or only candidates whose author or recording conflicts, is a normal outcome and the model must be able to say "not on Audible". `grade` is a coarse hint for simple gates:
  asin    the ASIN was asked for and exists
  strong  full-title and author match plus an agreeing recording feature (narrator overlap or runtime within 2%), nothing conflicting
  weak    everything else
Audible results are one source family (with Audnexus and the Audible sidecar) for corroboration purposes; they are never independent confirmation of each other."""
import json, os, sqlite3, zlib  # noqa: F401  (json/zlib: `raw` reads archived responses)
from datetime import datetime, timezone
from pathlib import Path

from app import settings
from app.identify import textkeys as tk

RUNTIME_TOLERANCE = 0.02


def folder() -> Path: return Path(settings.data_dir()) / 'audible'
def archive_path() -> Path: return folder() / 'audible.sqlite'
def index_path() -> Path: return folder() / 'index.sqlite'


SCHEMA = '''
CREATE TABLE book (asin TEXT NOT NULL, region TEXT NOT NULL, title TEXT, subtitle TEXT, title_key TEXT, main_key TEXT, language TEXT, abridged INTEGER, runtime_min INTEGER, release TEXT, publisher TEXT, isbn TEXT, series TEXT, series_asin TEXT, series_pos TEXT, state TEXT, format TEXT, ratings INTEGER, PRIMARY KEY (asin, region));
CREATE TABLE credit (asin TEXT NOT NULL, region TEXT NOT NULL, role TEXT NOT NULL, person_key TEXT NOT NULL, person_asin TEXT, name TEXT, PRIMARY KEY (asin, region, role, person_key)) WITHOUT ROWID;
CREATE TABLE meta (k TEXT PRIMARY KEY, v TEXT);
'''
INDEXES = '''
CREATE INDEX book_title ON book(title_key); CREATE INDEX book_main ON book(main_key); CREATE INDEX book_isbn ON book(isbn) WHERE isbn IS NOT NULL; CREATE INDEX book_asin ON book(asin);
CREATE INDEX credit_person ON credit(person_key, role); CREATE INDEX credit_person_asin ON credit(person_asin) WHERE person_asin IS NOT NULL;
CREATE VIRTUAL TABLE book_fts USING fts5(title, subtitle, series, content='book', content_rowid='rowid', tokenize='unicode61 remove_diacritics 2');
INSERT INTO book_fts(book_fts) VALUES ('rebuild');
'''
LANGUAGES = ('english', '')     # the library is English; other-language editions only add homonyms


def raw(value) -> dict:
    return json.loads(zlib.decompress(value) if isinstance(value, (bytes, bytearray)) else value)


def build(archive: str | Path | None = None, output: str | Path | None = None, regions: tuple[str, ...] = ('uk', 'us')) -> dict:
    """Rebuild the index from the archive's columns (no JSON parsing) and publish it atomically; an error leaves any existing index untouched. Both stores by default (the library is mostly UK-store; one ASIN can be in both, `find` returns it once). Safe while the fetchers write: the archive is opened read-only."""
    archive, output = Path(archive or archive_path()), Path(output or index_path()); tmp = output.with_name(output.name + '.building')
    tmp.unlink(missing_ok=True)
    src = sqlite3.connect(f'file:{archive}?mode=ro', uri=True, timeout=60); out = sqlite3.connect(tmp)
    out.executescript('PRAGMA journal_mode=OFF; PRAGMA synchronous=OFF;' + SCHEMA)
    marks = ','.join('?' * len(regions)); langs = ','.join('?' * len(LANGUAGES)); n = 0
    rows = src.execute(f"SELECT asin, region, title, subtitle, series, series_asin, series_position, runtime_min, language, format_type, release_date, publisher, rating_count, isbn FROM product WHERE region IN ({marks}) AND coalesce(language, '') IN ({langs})", (*regions, *LANGUAGES))
    while batch := rows.fetchmany(5000):
        out.executemany('INSERT OR REPLACE INTO book VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)', [(a, reg, t or '', sub or '', tk.title_key(t), tk.title_main(t), lang, {'abridged': 1, 'unabridged': 0}.get((fmt or '').lower()), rt, rel, pub, isbn or None, ser, ser_asin, pos, None, fmt, rc) for a, reg, t, sub, ser, ser_asin, pos, rt, lang, fmt, rel, pub, rc, isbn in batch]); n += len(batch)
    credits = src.execute(f'SELECT asin, region, role, name, person_asin FROM credit WHERE region IN ({marks})', regions)
    while batch := credits.fetchmany(20000):
        out.executemany('INSERT OR IGNORE INTO credit VALUES (?,?,?,?,?,?)', [(a, reg, role, k, pa or None, name) for a, reg, role, name, pa in batch if (k := tk.person_key(name))])
    out.execute('DELETE FROM credit WHERE NOT EXISTS (SELECT 1 FROM book b WHERE b.asin=credit.asin AND b.region=credit.region)')
    out.executescript(INDEXES)
    stamp = datetime.now(timezone.utc).isoformat(timespec='seconds')
    out.executemany('INSERT INTO meta VALUES (?,?)', [('built', stamp), ('books', str(n)), ('region', ','.join(regions)), ('archive', str(archive))]); out.commit()
    ok = out.execute('PRAGMA integrity_check').fetchone()[0] == 'ok'; out.close(); src.close()
    if not ok: tmp.unlink(missing_ok=True); raise RuntimeError('index failed its integrity check')
    os.replace(tmp, output); return dict(books=n, built=stamp, path=str(output))


def connect(path: str | Path | None = None) -> sqlite3.Connection | None:
    p = Path(path or index_path())
    if not p.exists(): return None
    c = sqlite3.connect(f'file:{p}?mode=ro', uri=True, timeout=30); c.row_factory = sqlite3.Row; return c


def snapshot(c) -> str: return c.execute("SELECT v FROM meta WHERE k='built'").fetchone()[0]


def _describe(c, row, evidence: dict, grade: str) -> dict:
    cr = c.execute('SELECT role, name FROM credit WHERE asin=? AND region=? ORDER BY role', (row['asin'], row['region'])).fetchall()
    return dict(asin=row['asin'], region=row['region'], title=row['title'], subtitle=row['subtitle'], authors=[r['name'] for r in cr if r['role'] == 'Author'], narrators=[r['name'] for r in cr if r['role'] == 'Narrator'],
                series=row['series'], series_position=row['series_pos'], runtime_minutes=row['runtime_min'], language=row['language'], abridged=None if row['abridged'] is None else bool(row['abridged']), release_date=row['release'], publisher=row['publisher'], isbn=row['isbn'], state=row['state'],
                grade=grade, evidence=evidence, source='audible_index', snapshot=snapshot(c))


def find(c, title: str = '', author: str = '', narrators=(), runtime_min: float | None = None, asin: str = '', limit: int = 20) -> list[dict]:
    """Candidate recordings, best first, for the model to judge. `asin` is a direct lookup. Otherwise candidates come from the title alone (exact keys, then a fuzzy full-text fallback when no key matches), so a book with no author still gets its possible matches; whatever else is known (author, narrators, runtime) is compared and recorded in `evidence`, never used to drop a candidate. Evidence values: title `full` / `main` / `fuzzy`; author `match` / `conflict` / `unknown`; narrators and runtime `match` / `conflict` (absent when either side is unknown). `grade` is only a hint for simple gates: `strong` needs a full-title and author match plus an agreeing recording feature and no conflict, `asin` is a direct hit, everything else is `weak`."""
    if c is None: return []
    if asin:
        rows = c.execute('SELECT * FROM book WHERE asin=?', (asin.strip().upper(),)).fetchall()
        if rows: return [_describe(c, r, dict(asin=True), 'asin') for r in rows[:limit]]
    variants = tk.title_variants(title)
    if not variants: return []
    ph = lambda xs: ','.join('?' * len(xs))
    rows = c.execute(f'SELECT * FROM book WHERE title_key IN ({ph(variants)}) OR main_key IN ({ph(variants)}) LIMIT 500', (*variants, *variants)).fetchall()
    if not rows:      # no key matches: fall back to words of the title (all must appear), still only candidates
        words = [w for w in tk.title_main(title).split() if len(w) > 1][:8]
        if words: rows = c.execute('SELECT b.* FROM book_fts f JOIN book b ON b.rowid=f.rowid WHERE book_fts MATCH ? LIMIT 200', (' '.join(f'"{w}"' for w in words),)).fetchall()
    people, want, out = set(tk.person_variants(author)) if author else set(), {tk.person_key(n) for n in narrators if n}, []
    for r in rows:
        ev = dict(title='full' if r['title_key'] in variants[:2] else 'main' if r['title_key'] in variants or r['main_key'] in variants else 'fuzzy')
        credits = c.execute('SELECT role, person_key FROM credit WHERE asin=? AND region=?', (r['asin'], r['region'])).fetchall()
        authors, narr = {x['person_key'] for x in credits if x['role'] == 'Author'}, {x['person_key'] for x in credits if x['role'] == 'Narrator'}
        ev['author'] = 'unknown' if not people or not authors else 'match' if people & authors else 'conflict'
        if want and narr: ev['narrators'] = 'match' if want & narr else 'conflict'
        if runtime_min and r['runtime_min']:
            delta = abs(r['runtime_min'] - runtime_min) / max(runtime_min, 1); ev['runtime_delta'] = round(delta, 4); ev['runtime'] = 'match' if delta <= RUNTIME_TOLERANCE else 'conflict'
        agree = [k for k in ('narrators', 'runtime') if ev.get(k) == 'match']; bad = [k for k in ('author', 'narrators', 'runtime') if ev.get(k) == 'conflict']
        out.append(_describe(c, r, ev, 'strong' if ev['title'] == 'full' and ev['author'] == 'match' and agree and not bad else 'weak'))
    rank = lambda d: (d['grade'] != 'strong', {'match': 0, 'unknown': 1, 'conflict': 2}[d['evidence']['author']], {'full': 0, 'main': 1, 'fuzzy': 2}[d['evidence']['title']], d['evidence'].get('runtime_delta', 1), d['asin'])
    out.sort(key=rank); seen: set[str] = set()
    out = [d for d in out if not (d['asin'] in seen or seen.add(d['asin']))]      # one ASIN listed by both stores is one recording
    return out[:limit]


def content_info(asins, *, archive: str | Path | None = None) -> dict[str, dict]:
    """What Audible calls each product beyond the index's columns: `content_type` ("Radio/TV Program" for BBC dramatisations and broadcasts, "Audiobook" for readings), subject `keywords` ("dramatizations") and publisher, read from the archive by ASIN (a primary-key lookup, fast even on the 10 GB file). {} when the archive is missing or locked: the kind classifier treats Audible's view as optional evidence."""
    asins = sorted({a for a in asins if a}); archive = Path(archive or archive_path())
    if not asins or not archive.exists(): return {}
    out: dict[str, dict] = {}
    try:
        db = sqlite3.connect(f'file:{archive}?mode=ro', uri=True, timeout=5)
        try:
            for asin, region, value in db.execute(f"SELECT asin, region, raw FROM product WHERE asin IN ({','.join('?' * len(asins))})", asins):
                try: p = raw(value); p = p.get('product', p)
                except (ValueError, zlib.error, TypeError): continue
                cur = dict(content_type=p.get('content_type') or '', keywords=list(p.get('thesaurus_subject_keywords') or []), publisher=p.get('publisher_name') or '', region=region)
                if asin not in out or region == 'uk': out[asin] = cur
        finally: db.close()
    except sqlite3.Error: return {}
    return out
