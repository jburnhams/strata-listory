"""Local index of radio programme titles (`data/radio/index.sqlite`): an offline lookup that tells the kind classifier "this title is a broadcast programme", the same way the Audible and Open Library indexes tell it about books. Never a hard dependency: without the file every lookup answers "unknown" and the classifier uses its other signals.

Sources (both free, no key, polite throttle through the shared `Http` client; raw pages are cached under `data/radio/raw/` so a refresh resumes and a rebuild needs no network):
* **BBC Programmes A-Z** (`bbc.co.uk/programmes/a-z/by/<letter>/all.json`): every top-level brand/series with the service that owns it (`bbc_radio_four`, `bbc_one`...). Only radio-service entries are kept, as `bbc_radio`.
* **Wikidata** (SPARQL): English-labelled instances of radio programme or radio drama, as `wikidata_radio` / `wikidata_drama`.
Not usable: BBC Genome (Radio Times listings 1923-2009) sits behind a human-verification wall and has no bulk download or API, so it is not fetched.

A title in this index is *evidence*, not proof: book titles and programme titles collide ("Middlemarch", "Just William"), so the classifier gives it a moderate weight and never lets it outvote a matched unabridged recording."""
from __future__ import annotations

import json, math, os, re, sqlite3
from pathlib import Path

from app import settings
from app.identify import textkeys as tk

BBC_LETTERS = 'abcdefghijklmnopqrstuvwxyz@'
BBC_URL = 'https://www.bbc.co.uk/programmes/a-z/by/{letter}/all.json?page={page}'
WIKIDATA_URL = 'https://query.wikidata.org/sparql'
WIKIDATA_CLASSES = {'Q1555508': 'wikidata_radio', 'Q2635894': 'wikidata_drama'}        # radio programme, radio drama (direct instances)
CHANNEL_NAME = re.compile(r'^(?:the\s+)?(?:bbc\s+)?(?:radio\s*6\s*music|radio\s*(?:[1-7]|one|two|three|four|five|live|scotland|wales|cymru|ulster|nan\s+gaidheal|wm|london|kent)(?:\s+(?:extra|live|xtra))?|world\s+service|6\s*music|asian\s+network|1xtra|r[1-7]x?)$', re.I)      # a programme "titled" Radio 4 is the channel's own listing, not a show
MIN_KEY_LEN = 6      # shorter keys ("it", "dune") collide with book titles far too often to mean anything


def folder() -> Path: return Path(settings.data_dir()) / 'radio'
def index_path() -> Path: return folder() / 'index.sqlite'
def available() -> bool: return index_path().exists()


SCHEMA = '''
CREATE TABLE programme (source TEXT NOT NULL, ref TEXT NOT NULL, title TEXT NOT NULL, title_key TEXT NOT NULL, service TEXT, first_broadcast TEXT, PRIMARY KEY (source, ref));
CREATE INDEX programme_key ON programme(title_key);
CREATE TABLE meta (k TEXT PRIMARY KEY, v TEXT);
'''


def bbc_rows(page: dict) -> list[tuple]:
    """(source, ref, title, service, first broadcast) for the radio entries of one A-Z page."""
    out = []
    for t in (page.get('atoz') or {}).get('tleo_titles') or []:
        p = t.get('programme') or {}; svc = ((p.get('ownership') or {}).get('service')) or {}
        if svc.get('type') == 'radio' and p.get('pid') and (p.get('title') or t.get('title')): out.append(('bbc_radio', p['pid'], p.get('title') or t['title'], svc.get('id') or '', (p.get('first_broadcast_date') or '')[:10]))
    return out


def fetch_bbc(http, raw_dir: Path, *, refresh: bool = False, log=print) -> int:
    """Page through the A-Z of every letter, saving each page under raw_dir (a page already saved is reused unless `refresh`). Returns pages written. `http.request(url)` gives parsed JSON (None for 404)."""
    raw_dir.mkdir(parents=True, exist_ok=True); written = 0
    for letter in BBC_LETTERS:
        page, pages = 1, 1
        while page <= pages:
            f = raw_dir / f'bbc-{"at" if letter == "@" else letter}-{page}.json'
            if f.exists() and not refresh: body = json.loads(f.read_text())
            else:
                body = http.request(BBC_URL.format(letter=letter, page=page))
                if body is None: break
                f.write_text(json.dumps(body)); written += 1
            a = body.get('atoz') or {}; pages = max(1, math.ceil((a.get('total') or 0) / (a.get('limit') or 30))); page += 1
        log(f'bbc {letter}: {pages} pages')
    return written


def fetch_wikidata(http, raw_dir: Path, *, refresh: bool = False, log=print) -> int:
    """English-labelled direct instances of each class, one query per class (fast; walking subclasses with P279* times the query service out). Returns files written."""
    from urllib.parse import urlencode
    raw_dir.mkdir(parents=True, exist_ok=True); written = 0
    for cls in WIKIDATA_CLASSES:
        f = raw_dir / f'wikidata-{cls}.json'
        if f.exists() and not refresh: continue
        q = f'SELECT ?x ?label WHERE {{ ?x wdt:P31 wd:{cls} . ?x rdfs:label ?label . FILTER(LANG(?label) = "en") }}'
        body = http.request(WIKIDATA_URL + '?' + urlencode({'query': q, 'format': 'json'}), headers={'Accept': 'application/sparql-results+json'})
        if body is None: continue
        f.write_text(json.dumps(body)); written += 1; log(f'wikidata {cls}: {len((body.get("results") or {}).get("bindings") or [])}')
    return written


def wikidata_rows(body: dict, cls: str) -> list[tuple]:
    return [(WIKIDATA_CLASSES.get(cls, 'wikidata_radio'), b['x']['value'].rsplit('/', 1)[-1], b['label']['value'], '', '') for b in (body.get('results') or {}).get('bindings') or []]


def build(raw_dir: Path | None = None, output: Path | None = None) -> dict:
    """Rebuild the index from the saved raw pages (atomic: the old index keeps serving until the new file is complete)."""
    raw_dir = Path(raw_dir or folder() / 'raw'); output = Path(output or index_path()); tmp = output.with_name(output.name + '.building')
    output.parent.mkdir(parents=True, exist_ok=True); tmp.unlink(missing_ok=True)
    rows: dict[tuple, tuple] = {}
    for f in sorted(raw_dir.glob('*.json')):
        body = json.loads(f.read_text())
        for r in (bbc_rows(body) if f.name.startswith('bbc-') else wikidata_rows(body, f.stem.split('-', 1)[1]) if f.name.startswith('wikidata-') else []): rows[(r[0], r[1])] = r
    db = sqlite3.connect(tmp); db.executescript(SCHEMA)
    db.executemany('INSERT INTO programme VALUES (?,?,?,?,?,?)', [(s, ref, title, tk.title_key(title), svc, first) for (s, ref, title, svc, first) in rows.values() if tk.title_key(title)])
    counts = dict(db.execute('SELECT source, count(*) FROM programme GROUP BY source').fetchall())
    db.execute('INSERT INTO meta VALUES (?, ?)', ('counts', json.dumps(counts))); db.commit(); db.close()
    os.replace(tmp, output); return counts


def find(titles: list[str], *, path: Path | None = None) -> list[dict]:
    """Programmes whose title is exactly one of `titles` (by the shared title key; keys under MIN_KEY_LEN characters are ignored). [] without an index."""
    p = Path(path or index_path())
    if not p.exists(): return []
    keys = {tk.title_key(t) for t in titles if t}; keys = {k for k in keys if len(k.replace(' ', '')) >= MIN_KEY_LEN}
    if not keys: return []
    db = sqlite3.connect(f'file:{p}?mode=ro', uri=True)
    try:
        return [dict(source=s, ref=r, title=t, service=svc, first_broadcast=fb) for s, r, t, svc, fb in db.execute(f'SELECT source, ref, title, service, first_broadcast FROM programme WHERE title_key IN ({",".join("?" * len(keys))}) ORDER BY source, ref', sorted(keys)) if not CHANNEL_NAME.match(t.strip())]
    except sqlite3.Error: return []
    finally: db.close()


# ---- per-programme detail: fetched only for programmes that a listen's candidates name, cached, refreshable

BBC_DETAIL_URL = 'https://www.bbc.co.uk/programmes/{pid}.json'


def detail_path() -> Path: return folder() / 'detail.sqlite'


def _detail_db(path: Path | None = None, create: bool = False) -> sqlite3.Connection | None:
    p = Path(path or detail_path())
    if not p.exists() and not create: return None
    p.parent.mkdir(parents=True, exist_ok=True); db = sqlite3.connect(p)
    try: db.execute('CREATE TABLE IF NOT EXISTS detail (ref TEXT PRIMARY KEY, body TEXT NOT NULL, fetched TEXT NOT NULL)')
    except sqlite3.DatabaseError: db.close(); return None          # not a database: treated as no cache
    return db


def summarise_bbc(body: dict) -> dict:
    """The fields a match card and the comparison use, from `/programmes/<pid>.json`: type (brand, series, episode), service, first broadcast, episode count, genres and formats, short synopsis."""
    p = (body or {}).get('programme') or {}
    cats = p.get('categories') or []
    return dict(type=p.get('type') or '', service=((p.get('ownership') or {}).get('service') or {}).get('id') or '', channel=((p.get('ownership') or {}).get('service') or {}).get('title') or '', first_broadcast=(p.get('first_broadcast_date') or '')[:10],
                episodes=p.get('aggregated_episode_count'), genres=[c['title'] for c in cats if c.get('type') == 'genre' and c.get('title')], formats=[c['title'] for c in cats if c.get('type') == 'format' and c.get('title')], synopsis=(p.get('short_synopsis') or '')[:240])


def fetch_details(http, refs: list[str], *, refresh: bool = False, path: Path | None = None, log=print) -> int:
    """Fetch and cache `/programmes/<pid>.json` for BBC pids not cached yet (all with `refresh`). Returns how many were stored. A pid the BBC no longer knows is skipped."""
    from datetime import datetime, timezone
    db = _detail_db(path, create=True); stored = 0
    if db is None: return 0
    try:
        have = {r for (r,) in db.execute('SELECT ref FROM detail')}
        for ref in dict.fromkeys(refs):
            if ref in have and not refresh: continue
            body = http.request(BBC_DETAIL_URL.format(pid=ref))
            if body is None: continue
            db.execute('INSERT OR REPLACE INTO detail VALUES (?,?,?)', (ref, json.dumps(summarise_bbc(body)), datetime.now(timezone.utc).isoformat())); db.commit(); stored += 1
            log(f'detail {ref}')
    finally: db.close()
    return stored


def details(refs: list[str], *, path: Path | None = None) -> dict[str, dict]:
    """Cached detail for these refs (those not fetched are absent). {} without a cache."""
    db = _detail_db(path)
    if db is None or not refs: return {}
    try: return {r: json.loads(b) for r, b in db.execute(f'SELECT ref, body FROM detail WHERE ref IN ({",".join("?" * len(refs))})', list(refs))}
    except (sqlite3.Error, ValueError): return {}
    finally: db.close()
