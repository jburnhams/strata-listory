#!/usr/bin/env python3
"""List Audible's audiobooks for every person in the AudiobookDB people index (scripts/crawl_audiobookdb_people.py) into a local SQLite store keyed by (ASIN, region).

For each person, `author=<name>` (people with the Author role) and `narrator=<name>` (Narrator role) are queried on Audible's unauthenticated catalogue API (`api.audible.<tld>/1.0/catalog/products`, the endpoint Audnexus uses), 50 products per page. Audible matches names as free text, so a product is stored only when one of its credited authors/narrators equals the person's name (case- and accent-insensitive); the others are dropped. `total_results` in the response is unreliable and the API stops at about 1,000 results a query, so paging ends at the first short or repeated page, and a query that hit the cap is marked `capped` for a later narrower pass. Listing is cheap (one call per 50 books); full detail (description, chapters, ratings) is fetched later on demand by ASIN.

    scripts/fetch_audible_people.py [--people data/audiobookdb/people.sqlite] [--db data/audible/audible.sqlite] [--region us] [--rate 1] [--limit N] [--min-credits N] [--name NAME]

Order is by AudiobookDB credit count, highest first. Progress is a row per (person, role, region) in `query`, so it resumes and a later run can refresh queries older than `--refresh-days`. Private, personal use; keep the rate low."""
import argparse, json, os, zlib, sqlite3, sys, time, unicodedata, urllib.error, urllib.parse, urllib.request
from datetime import datetime, timedelta, timezone

TLD = {'us': 'com', 'uk': 'co.uk', 'au': 'com.au', 'ca': 'ca', 'de': 'de', 'fr': 'fr', 'in': 'in', 'it': 'it', 'es': 'es', 'jp': 'co.jp', 'br': 'com.br'}
GROUPS = 'contributors,product_attrs,series,media,rating,product_desc,product_extended_attrs,product_details,category_ladders,relationships,customer_rights,rights,sample'
GV = 2      # version of GROUPS: queries recorded with a lower one are fetched again (v1 lacked ISBN, copyright, summaries, genres, relationships, rights/state)
UA = 'Mozilla/5.0 (strata-listory personal audiobook index)'
HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(HERE, '..', 'src'))


def now() -> str: return datetime.now(timezone.utc).isoformat(timespec='seconds')


def norm(s: str) -> str:
    s = unicodedata.normalize('NFKD', s or '').encode('ascii', 'ignore').decode().lower()
    return ' '.join(''.join(ch if ch.isalnum() else ' ' for ch in s).split())


def open_db(path: str) -> sqlite3.Connection:
    os.makedirs(os.path.dirname(os.path.abspath(path)), exist_ok=True)
    c = sqlite3.connect(path, timeout=60); c.execute('PRAGMA journal_mode=WAL'); c.execute('PRAGMA synchronous=NORMAL')
    c.executescript('''CREATE TABLE IF NOT EXISTS product (asin TEXT NOT NULL, region TEXT NOT NULL, title TEXT, subtitle TEXT, authors TEXT, narrators TEXT, series TEXT, series_asin TEXT, series_position TEXT, runtime_min INTEGER, language TEXT, format_type TEXT, release_date TEXT, publisher TEXT, rating REAL, rating_count INTEGER, raw TEXT, fetched TEXT NOT NULL, PRIMARY KEY (asin, region));
        CREATE TABLE IF NOT EXISTS credit (asin TEXT NOT NULL, region TEXT NOT NULL, role TEXT NOT NULL, name TEXT NOT NULL, person_asin TEXT, PRIMARY KEY (asin, region, role, name));
        CREATE INDEX IF NOT EXISTS credit_name ON credit(name, role);
        CREATE TABLE IF NOT EXISTS query (person_id TEXT NOT NULL, role TEXT NOT NULL, region TEXT NOT NULL, name TEXT NOT NULL, pages INTEGER, returned INTEGER, matched INTEGER, capped INTEGER, fetched TEXT NOT NULL, PRIMARY KEY (person_id, role, region));''')
    if 'gv' not in {r[1] for r in c.execute('PRAGMA table_info(query)')}: c.execute('ALTER TABLE query ADD COLUMN gv INTEGER NOT NULL DEFAULT 1')
    if 'isbn' not in {r[1] for r in c.execute('PRAGMA table_info(product)')}: c.execute('ALTER TABLE product ADD COLUMN isbn TEXT')
    return c


class Audible:
    def __init__(self, region: str, rate: float):
        self.base, self.gap, self.last = f'https://api.audible.{TLD[region]}/1.0/catalog/products', 1 / rate, 0.0

    def page(self, kind: str, name: str, page: int, sort: str = '-ReleaseDate', extra: str = '') -> list[dict]:
        url = f'{self.base}?{kind}={urllib.parse.quote(name)}&num_results=50&page={page}&products_sort_by={sort}&response_groups={GROUPS}&image_sizes=500{extra}'
        for attempt in range(1, 6):
            time.sleep(max(0, self.last + self.gap - time.monotonic())); self.last = time.monotonic()
            try:
                with urllib.request.urlopen(urllib.request.Request(url, headers={'User-Agent': UA}), timeout=40) as r: return json.load(r).get('products') or []
            except urllib.error.HTTPError as e:
                if e.code in (429, 500, 502, 503, 504): time.sleep(min(300, int(e.headers.get('Retry-After') or 0) or 5 * 2 ** attempt)); continue
                if e.code == 400: return []      # a name Audible will not accept as a filter
                raise
            except (urllib.error.URLError, OSError, ValueError): time.sleep(5 * attempt)
        raise SystemExit(f'giving up on {name!r}')


def store(c: sqlite3.Connection, region: str, p: dict) -> None:
    sp = (p.get('series') or [{}])[0]; rt = p.get('rating') or {}; ov = rt.get('overall_distribution') or {}
    c.execute('INSERT OR REPLACE INTO product (asin, region, title, subtitle, authors, narrators, series, series_asin, series_position, runtime_min, language, format_type, release_date, publisher, rating, rating_count, raw, fetched, isbn) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)', (
        p['asin'], region, p.get('title'), p.get('subtitle'), json.dumps([a['name'] for a in p.get('authors') or []]), json.dumps([n['name'] for n in p.get('narrators') or []]),
        sp.get('title'), sp.get('asin'), sp.get('sequence'), p.get('runtime_length_min'), p.get('language'), p.get('format_type'), p.get('release_date') or p.get('issue_date'), p.get('publisher_name'),
        ov.get('display_average_rating') and float(ov['display_average_rating']), ov.get('num_ratings'), zlib.compress(json.dumps(p, separators=(',', ':')).encode(), 6), now(), p.get('isbn')))      # raw is the complete response, zlib-compressed (older v1 rows hold plain text)
    for role, key in (('Author', 'authors'), ('Narrator', 'narrators')):
        for x in p.get(key) or []: c.execute('INSERT OR IGNORE INTO credit VALUES (?,?,?,?,?)', (p['asin'], region, role, x['name'], x.get('asin')))


CAP = 500      # Audible repeats the last page beyond about the tenth, so one sorted listing yields at most ~500 products


def fetch_person(c, api: Audible, region: str, pid: str, name: str, role: str) -> tuple:
    """All of a person's products, matched by exact credited name. A person with more than CAP products is listed again in other sort orders (newest, oldest, A-Z, Z-A), each reaching a different 500, so up to ~2,000 are found; `capped` is 1 when even that left the last sort full."""
    key = 'authors' if role == 'Author' else 'narrators'; want = norm(name); seen, ids, pages, capped = set(), set(), 0, 0
    for sort in ('-ReleaseDate', 'ReleaseDate', 'Title', '-Title'):
        got, pg = set(), -1
        while pg < 20:      # Audible pages count from 0 (page=1 is the second page)
            pg += 1; pages += 1; ps = api.page(role.lower(), name, pg, sort); fresh = [p for p in ps if p['asin'] not in got]; got.update(p['asin'] for p in ps)
            with c:
                for p in fresh:
                    seen.add(p['asin'])
                    if any(norm(x['name']) == want for x in p.get(key) or []): store(c, region, p); ids.add(p['asin'])
            if len(ps) < 50 or not fresh: break
        if len(got) < CAP: break      # this listing was complete, so no other order can add anything
        capped = int(sort == '-Title')
    return pages, len(seen), len(ids), capped


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split('\n')[0])
    ap.add_argument('--people', default=os.path.join(HERE, '..', 'data', 'audiobookdb', 'people.sqlite'))
    ap.add_argument('--db', default=os.path.join(HERE, '..', 'data', 'audible', 'audible.sqlite'))
    ap.add_argument('--region', default='us', choices=sorted(TLD))
    ap.add_argument('--rate', type=float, default=1, help='requests per second (default 1)')
    ap.add_argument('--limit', type=int, default=0, help='stop after this many people-queries (0 = no limit)')
    ap.add_argument('--min-credits', type=int, default=1)
    ap.add_argument('--refresh-days', type=float, default=0, help='re-run queries older than this (0 = never, resume only)')
    ap.add_argument('--name', help='only this person (exact name)')
    ap.add_argument('--roles', default='Author,Narrator', help='comma-separated roles to query, in this order of priority per person (default Author,Narrator); Contributor/Translator/Illustrator people are queried as authors')
    ap.add_argument('--from-credits', action='store_true', help='snowball: query the credited names found in the archive that are neither in the AudiobookDB people index nor already queried (person ids are `c:<name key>`)')
    ap.add_argument('--shard', default='0/1', help='I/N: handle only people whose id hashes to I of N, so N processes can share the work (each keeps its own --rate)')
    ap.add_argument('--follow', action='store_true', help='when the people index is exhausted, keep waiting for the crawl to add more (poll every 10 minutes)')
    a = ap.parse_args(argv)
    c, api = open_db(a.db), Audible(a.region, a.rate)
    si, sn = (int(x) for x in a.shard.split('/'))
    done, want = 0, [r.strip() for r in a.roles.split(',') if r.strip()]
    while True:
        p = sqlite3.connect(f'file:{a.people}?mode=ro', uri=True, timeout=60)
        rows = p.execute('SELECT id, name, roles, COALESCE(credits,0) FROM person WHERE COALESCE(credits,0) >= ? ' + ('AND name = ? ' if a.name else '') + 'ORDER BY credits DESC, id', (a.min_credits, *([a.name] if a.name else []))).fetchall(); p.close()
        if a.from_credits:      # names Audible itself credits that AudiobookDB's people list lacks, most-credited first
            from app.identify import textkeys as tk
            have = {tk.person_key(n) for (n,) in sqlite3.connect(f'file:{a.people}?mode=ro', uri=True, timeout=60).execute('SELECT name FROM person')}
            done_keys = {tk.person_key(n) for (n,) in c.execute('SELECT DISTINCT name FROM query WHERE region=?', (a.region,))}      # per store: a name queried on the US store still needs asking on the UK one
            seen: dict[str, list] = {}
            for role, nm in c.execute('SELECT role, name FROM credit'):
                k = tk.person_key(nm)
                if k and k not in have and k not in done_keys: e = seen.setdefault(k, [nm, set(), 0]); e[1].add(role); e[2] += 1
            rows = sorted(((f'c:{k}', e[0], json.dumps(sorted(e[1])), e[2]) for k, e in seen.items()), key=lambda r: (-r[3], r[0]))
        todo, cutoff = 0, (datetime.now(timezone.utc) - timedelta(days=a.refresh_days)).isoformat() if a.refresh_days else None
        for pid, name, roles, credits in rows:
            if sn > 1 and zlib.crc32(pid.encode()) % sn != si: continue
            have = set(json.loads(roles)); have |= {'Author'} if have & {'Contributor', 'Translator', 'Illustrator'} else set()      # AudiobookDB's other credit roles are mostly authors on Audible (see docs/audible-people-index.md)
            for role in want:
                if role not in have or len(name) < 3: continue
                q = c.execute('SELECT fetched, gv FROM query WHERE person_id=? AND role=? AND region=?', (pid, role, a.region)).fetchone()
                if q and q[1] >= GV and not (cutoff and q[0] < cutoff): continue
                pages, returned, matched, capped = fetch_person(c, api, a.region, pid, name, role); todo += 1; done += 1
                with c: c.execute('INSERT OR REPLACE INTO query (person_id, role, region, name, pages, returned, matched, capped, fetched, gv) VALUES (?,?,?,?,?,?,?,?,?,?)', (pid, role, a.region, name, pages, returned, matched, capped, now(), GV))
                if done % 25 == 0 or a.limit and done >= a.limit:
                    print(f'{now()} queries={done} products={c.execute("SELECT count(*) FROM product WHERE region=?", (a.region,)).fetchone()[0]} last={name!r} ({role}, {matched}/{returned})', flush=True)
                if a.limit and done >= a.limit: return 0
        if not a.follow: print(f'{now()} done: {done} queries, {c.execute("SELECT count(*) FROM product WHERE region=?", (a.region,)).fetchone()[0]} products'); return 0
        if not todo: time.sleep(600)


if __name__ == '__main__':
    sys.exit(main())
