"""Local Audible index: build from the archive (`data/audible/audible.sqlite`, written by scripts/fetch_audible_people.py) and query it for candidate recordings.

The archive keeps every response losslessly; this module derives a small query index (`data/audible/index.sqlite`, built as `.building` and renamed atomically) using the shared keys in textkeys, so the key written at build time is the key looked up at query time.

Strictness: many audiobooks are older tape/CD recordings that Audible never had, so "not on Audible" is a normal outcome and a title alone never produces a candidate. Without an ASIN, a candidate needs an exact author-person match as well as a title key match. Each candidate carries its evidence and a `grade`:
  asin    the ASIN was asked for and exists
  strong  title + author match and at least one independent recording agreement (narrator overlap or runtime within 2%), with none disagreeing
  weak    title + author match with nothing else to check, or a recording feature that disagrees
Audible results are one source family (with Audnexus and the Audible sidecar) for corroboration purposes; they are never independent confirmation of each other."""
import json, os, sqlite3, zlib
from datetime import datetime, timezone
from pathlib import Path

from app import settings
from app.identify import textkeys as tk

RUNTIME_TOLERANCE = 0.02


def folder() -> Path: return Path(settings.data_dir()) / 'audible'
def archive_path() -> Path: return folder() / 'audible.sqlite'
def index_path() -> Path: return folder() / 'index.sqlite'


SCHEMA = '''
CREATE TABLE book (asin TEXT NOT NULL, region TEXT NOT NULL, title TEXT, subtitle TEXT, title_key TEXT, main_key TEXT, language TEXT, abridged INTEGER, runtime_min INTEGER, release TEXT, publisher TEXT, isbn TEXT, series TEXT, series_asin TEXT, series_pos TEXT, state TEXT, PRIMARY KEY (asin, region));
CREATE TABLE credit (asin TEXT NOT NULL, region TEXT NOT NULL, role TEXT NOT NULL, person_key TEXT NOT NULL, person_asin TEXT, name TEXT, PRIMARY KEY (asin, region, role, person_key)) WITHOUT ROWID;
CREATE INDEX book_title ON book(title_key); CREATE INDEX book_main ON book(main_key); CREATE INDEX book_isbn ON book(isbn) WHERE isbn IS NOT NULL;
CREATE INDEX credit_person ON credit(person_key, role); CREATE INDEX credit_person_asin ON credit(person_asin) WHERE person_asin IS NOT NULL;
CREATE VIRTUAL TABLE book_fts USING fts5(title, subtitle, series, content='', tokenize='unicode61 remove_diacritics 2');
CREATE TABLE meta (k TEXT PRIMARY KEY, v TEXT);
'''


def raw(value) -> dict:
    return json.loads(zlib.decompress(value) if isinstance(value, (bytes, bytearray)) else value)


def build(archive: str | Path | None = None, output: str | Path | None = None, region: str = 'us') -> dict:
    """Rebuild the index from the archive and publish it atomically; an error leaves any existing index untouched."""
    archive, output = Path(archive or archive_path()), Path(output or index_path()); tmp = output.with_name(output.name + '.building')
    tmp.unlink(missing_ok=True)
    src = sqlite3.connect(f'file:{archive}?mode=ro', uri=True, timeout=60); out = sqlite3.connect(tmp)
    out.executescript('PRAGMA journal_mode=OFF; PRAGMA synchronous=OFF;' + SCHEMA)
    n = 0
    for asin, reg, blob in src.execute('SELECT asin, region, raw FROM product WHERE region=?', (region,)):
        p = raw(blob); title, sub = p.get('title') or '', p.get('subtitle') or ''; series = (p.get('series') or [{}])[0]
        abridged = {'abridged': 1, 'unabridged': 0}.get((p.get('format_type') or '').lower())
        out.execute('INSERT OR REPLACE INTO book VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)', (asin, reg, title, sub, tk.title_key(title), tk.title_main(title), p.get('language'), abridged, p.get('runtime_length_min'), p.get('release_date') or p.get('issue_date'), p.get('publisher_name'), p.get('isbn') or None, series.get('title'), series.get('asin'), series.get('sequence'), p.get('product_state')))
        out.execute('INSERT INTO book_fts(rowid, title, subtitle, series) VALUES ((SELECT rowid FROM book WHERE asin=? AND region=?), ?, ?, ?)', (asin, reg, title, sub, series.get('title') or ''))
        for role, key in (('Author', 'authors'), ('Narrator', 'narrators')):
            for x in p.get(key) or []:
                k = tk.person_key(x.get('name'))
                if k: out.execute('INSERT OR IGNORE INTO credit VALUES (?,?,?,?,?,?)', (asin, reg, role, k, x.get('asin'), x['name']))
        n += 1
    stamp = datetime.now(timezone.utc).isoformat(timespec='seconds')
    out.executemany('INSERT INTO meta VALUES (?,?)', [('built', stamp), ('books', str(n)), ('region', region), ('archive', str(archive))]); out.commit()
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
    """Candidate recordings, best first. `asin` is a direct lookup. Otherwise both title and author are required: no author, no candidates. The recording features (narrators, runtime) grade a candidate and never create one."""
    if c is None: return []
    if asin:
        rows = c.execute('SELECT * FROM book WHERE asin=?', (asin.strip().upper(),)).fetchall()
        if rows: return [_describe(c, r, dict(asin=True), 'asin') for r in rows[:limit]]
    variants, people = tk.title_variants(title), tk.person_variants(author) if author else []
    if not variants or not people: return []
    ph = lambda xs: ','.join('?' * len(xs))
    rows = c.execute(f'SELECT DISTINCT b.* FROM book b JOIN credit a ON a.asin=b.asin AND a.region=b.region AND a.role=\'Author\' WHERE a.person_key IN ({ph(people)}) AND (b.title_key IN ({ph(variants)}) OR b.main_key IN ({ph(variants)}))', (*people, *variants, *variants)).fetchall()
    want = {tk.person_key(n) for n in narrators if n}; out = []
    for r in rows:
        ev = dict(title='full' if r['title_key'] in variants[:2] else 'main', author=True)
        have = {x['person_key'] for x in c.execute("SELECT person_key FROM credit WHERE asin=? AND region=? AND role='Narrator'", (r['asin'], r['region']))}
        if want and have: ev['narrators'] = 'match' if want & have else 'conflict'
        if runtime_min and r['runtime_min']:
            delta = abs(r['runtime_min'] - runtime_min) / max(runtime_min, 1); ev['runtime_delta'] = round(delta, 4); ev['runtime'] = 'match' if delta <= RUNTIME_TOLERANCE else 'conflict'
        agree = [k for k in ('narrators', 'runtime') if ev.get(k) in ('match',)]; bad = [k for k in ('narrators', 'runtime') if ev.get(k) == 'conflict']
        grade = 'strong' if agree and not bad and ev['title'] == 'full' else 'weak'
        out.append(_describe(c, r, ev, grade))
    out.sort(key=lambda d: (d['grade'] != 'strong', d['evidence'].get('runtime_delta', 1), d['evidence']['title'] != 'full', d['asin'])); return out[:limit]
