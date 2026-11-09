"""Local index of radio programme titles (`data/radio/index.sqlite`): an offline lookup that tells the kind classifier "this title is a broadcast programme", the same way the Audible and Open Library indexes tell it about books. Never a hard dependency: without the file every lookup answers "unknown" and the classifier uses its other signals.

Sources (both free, no key, polite throttle through the shared `Http` client; raw pages are cached under `data/radio/raw/` so a refresh resumes and a rebuild needs no network):
* **BBC Programmes A-Z** (`bbc.co.uk/programmes/a-z/by/<letter>/all.json`): every top-level brand/series with the service that owns it (`bbc_radio_four`, `bbc_one`...). Only radio-service entries are kept, as `bbc_radio`.
* **Wikidata** (SPARQL): English-labelled instances of radio programme or radio drama, as `wikidata_radio` / `wikidata_drama`.
Not usable: BBC Genome (Radio Times listings 1923-2009) sits behind a human-verification wall and has no bulk download or API, so it is not fetched.

A title in this index is *evidence*, not proof: book titles and programme titles collide ("Middlemarch", "Just William"), so the classifier gives it a moderate weight and never lets it outvote a matched unabridged recording."""
from __future__ import annotations

import json, math, os, sqlite3
from pathlib import Path

from app import settings
from app.identify import textkeys as tk

BBC_LETTERS = 'abcdefghijklmnopqrstuvwxyz@'
BBC_URL = 'https://www.bbc.co.uk/programmes/a-z/by/{letter}/all.json?page={page}'
WIKIDATA_URL = 'https://query.wikidata.org/sparql'
WIKIDATA_CLASSES = {'Q1555508': 'wikidata_radio', 'Q2635894': 'wikidata_drama'}        # radio programme, radio drama (direct instances)
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
        return [dict(source=s, ref=r, title=t, service=svc, first_broadcast=fb) for s, r, t, svc, fb in db.execute(f'SELECT source, ref, title, service, first_broadcast FROM programme WHERE title_key IN ({",".join("?" * len(keys))}) ORDER BY source, ref', sorted(keys))]
    except sqlite3.Error: return []
    finally: db.close()
