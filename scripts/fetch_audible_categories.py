#!/usr/bin/env python3
"""List an Audible store's whole catalogue by walking its genre tree, into the same archive as scripts/fetch_audible_people.py (data/audible/audible.sqlite, keyed by ASIN and region).

`category_id` does filter (every returned product's category ladder contains it), and leaf categories are small (tens to a few hundred products), so each leaf is listed in one sorted query of up to ~500 products. A leaf that fills one sort order is listed again in the other sort orders (newest, oldest, A-Z, Z-A); a leaf still full after all four is recorded as `capped` for a later split. Earlier doubts about this route came from `total_results`, which is not reliable; only products actually returned are counted here. Complements the people-driven fetch, which cannot find authors or narrators that AudiobookDB's list lacks.

    scripts/fetch_audible_categories.py --region uk [--rate 1] [--shard I/N] [--limit N] [--db ...] [--refresh-days D]

Progress is a row per leaf in `query` (person_id `cat:<id>`, role `Category`), so it resumes."""
import argparse, importlib.util, json, os, sqlite3, sys, time, urllib.parse, urllib.request, zlib

HERE = os.path.dirname(os.path.abspath(__file__))
spec = importlib.util.spec_from_file_location('fetch_audible_people', os.path.join(HERE, 'fetch_audible_people.py'))
fp = importlib.util.module_from_spec(spec); spec.loader.exec_module(fp)


def leaves(region: str, internal: bool = False) -> list[tuple[str, str]]:
    """(category id, path) of every leaf in the store's Genres tree; with `internal`, of every node (a product can be attached to a parent category only, and then appears in no leaf)."""
    url = f'https://api.audible.{fp.TLD[region]}/1.0/catalog/categories?root=Genres&categories_num_levels=8'
    for attempt in range(1, 5):
        try:
            with urllib.request.urlopen(urllib.request.Request(url, headers={'User-Agent': fp.UA}), timeout=90) as r: tree = json.load(r)['categories']
            break
        except OSError: time.sleep(5 * attempt)
    else: raise SystemExit('could not read the category tree')
    out = []
    def walk(n, path):
        if n.get('children'):
            if internal: out.append((n['id'], path + n['name']))
            for ch in n['children']: walk(ch, path + n['name'] + ' > ')
        else: out.append((n['id'], path + n['name']))
    for n in tree: walk(n, '')
    return out


SORTS = ('-ReleaseDate', 'ReleaseDate', 'Title', '-Title', 'BestSellers', 'RuntimeLength', '-RuntimeLength', 'AvgRating')      # each full sort reaches a different ~550; measured on a 1,328-product category the first four give 97% and the rest add the last 3%


def fetch_leaf(c, api, region: str, cid: str) -> tuple:
    """Every product in one category. A sort order lists at most ~550; when one comes back full the next order is tried, and listing stops as soon as a full order adds under 3% new products. `capped` is 1 when the last order was still adding."""
    seen, pages, capped = set(), 0, 0
    for sort in SORTS:
        got, pg, before = set(), -1, len(seen)
        while pg < 20:
            pg += 1; pages += 1; ps = api.page('category_id', cid, pg, sort); fresh = [p for p in ps if p['asin'] not in got]; got.update(p['asin'] for p in ps)
            with c:
                for p in fresh:
                    if p['asin'] not in seen: fp.store(c, region, p)
                    seen.add(p['asin'])
            if len(ps) < 50 or not fresh: break
        if len(got) < fp.CAP: break      # not full: this order showed the whole category
        added = len(seen) - before
        if sort != SORTS[0] and added < 0.03 * len(seen): break
        capped = int(sort == SORTS[-1])
    return pages, len(seen), len(seen), capped


def newest_leaf(c, api, region: str, cid: str) -> tuple:
    """Weekly refresh of one category: list it newest first and stop at the first full page whose products are all in the archive already. Returns (pages, new products, churned): churned is 1 when 20 pages were still turning up new products (more arrived since the last run than one sorted listing holds), and the caller then runs the full multi-sort listing instead."""
    pages = new = 0
    for pg in range(21):
        ps = api.page('category_id', cid, pg, '-ReleaseDate'); pages += 1; known = 0
        with c:
            for p in ps:
                if c.execute('SELECT 1 FROM product WHERE asin=? AND region=?', (p['asin'], region)).fetchone(): known += 1
                else: fp.store(c, region, p); new += 1
        if len(ps) < 50 or known == len(ps): return pages, new, 0
    return pages, new, 1


STOP = {'the', 'and', 'for', 'with', 'from', 'your', 'you', 'that', 'this', 'are', 'not', 'how', 'who', 'what', 'its', 'his', 'her', 'was'}


def vocabulary(c, region: str, size: int = 600) -> list[str]:
    """Title words by frequency across the archive, most common first: each makes a `keywords=` filter that cuts a different ~550 out of a big category."""
    import collections, re
    n = collections.Counter(w for (t,) in c.execute('SELECT title FROM product WHERE region=?', (region,)) for w in set(re.findall(r'[^\W\d_]{3,}', (t or '').casefold())) if w not in STOP)
    return [w for w, _ in n.most_common(size)]


def split_leaf(c, api, region: str, cid: str, words: list[str], patience: int = 25, floor: float = 0.005) -> tuple:
    """For a capped category: list it filtered by one title word after another (newest first), storing what is new, until `patience` words in a row add under `floor` of the products seen. Returns (pages, words tried, new products, products seen, saturated)."""
    seen: set[str] = set()      # novelty is judged against this run's own seen set (category membership is not indexed)
    pages = new = quiet = tried = 0; flat = False
    for w in words:
        tried += 1; added, got, pg = 0, set(), -1
        while pg < 20:
            pg += 1; pages += 1; ps = api.page('category_id', cid, pg, '-ReleaseDate', '&keywords=' + urllib.parse.quote(w)); fresh = [p for p in ps if p['asin'] not in got]; got.update(p['asin'] for p in ps)
            with c:
                for p in fresh:
                    if p['asin'] not in seen:
                        seen.add(p['asin']); added += 1
                        if not c.execute('SELECT 1 FROM product WHERE asin=? AND region=?', (p['asin'], region)).fetchone(): fp.store(c, region, p); new += 1
            if len(ps) < 50 or not fresh: break
        quiet = quiet + 1 if added < floor * max(len(seen), 1) else 0
        if quiet >= patience: flat = True; break
    return pages, tried, new, len(seen), int(flat)


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split('\n')[0])
    ap.add_argument('--db', default=os.path.join(HERE, '..', 'data', 'audible', 'audible.sqlite'))
    ap.add_argument('--region', default='uk', choices=sorted(fp.TLD))
    ap.add_argument('--rate', type=float, default=1); ap.add_argument('--limit', type=int, default=0)
    ap.add_argument('--shard', default='0/1'); ap.add_argument('--refresh-days', type=float, default=0)
    ap.add_argument('--newest', action='store_true', help='weekly refresh: each category newest first, stopping at products already stored (falls back to the full listing when a category churned)')
    ap.add_argument('--internal', action='store_true', help='also sweep the non-leaf categories')
    ap.add_argument('--split', action='store_true', help='instead of the sweep, split the categories recorded as capped by title-word keywords')
    a = ap.parse_args(argv)
    c, api = fp.open_db(a.db), fp.Audible(a.region, a.rate); si, sn = (int(x) for x in a.shard.split('/')); done = 0
    cutoff = (fp.datetime.now(fp.timezone.utc) - fp.timedelta(days=a.refresh_days)).isoformat() if a.refresh_days else None
    if a.newest:
        t0, total_new, churned = time.time(), 0, 0
        for cid, path in leaves(a.region, True):
            if sn > 1 and zlib.crc32(cid.encode()) % sn != si: continue
            pages, new, churn = newest_leaf(c, api, a.region, cid); done += 1; total_new += new
            if churn:
                churned += 1; pages2, returned, stored, capped = fetch_leaf(c, api, a.region, cid); total_new += 0
                with c: c.execute("INSERT OR REPLACE INTO query (person_id, role, region, name, pages, returned, matched, capped, fetched, gv) VALUES (?,?,?,?,?,?,?,?,?,?)", (f'cat:{cid}', 'Category', a.region, path, pages2, returned, stored, capped, fp.now(), fp.GV))
            if new or churn: print(f'{fp.now()} {path!r}: {new} new{" (churned: full listing)" if churn else ""}', flush=True)
            if a.limit and done >= a.limit: break
        print(f'{fp.now()} newest {a.region}: {done} categories, {total_new} new products, {churned} churned, {round(time.time() - t0)}s'); return 0
    if a.split:      # capped categories (and parents) only, each cut by title words
        words = vocabulary(c, a.region)
        todo = [(r[0][4:], r[1]) for r in c.execute("SELECT person_id, name FROM query WHERE role='Category' AND region=? AND capped=1 ORDER BY person_id", (a.region,))]
        for cid, path in todo:
            if sn > 1 and zlib.crc32(cid.encode()) % sn != si: continue
            q = c.execute("SELECT fetched FROM query WHERE person_id=? AND role='CategoryKeywords' AND region=?", (f'catkw:{cid}', a.region)).fetchone()
            if q and not (cutoff and q[0] < cutoff): continue      # done before, unless --refresh-days says it is stale
            pages, tried, new, seen, flat = split_leaf(c, api, a.region, cid, words); done += 1
            with c: c.execute("INSERT OR REPLACE INTO query (person_id, role, region, name, pages, returned, matched, capped, fetched, gv) VALUES (?,?,?,?,?,?,?,?,?,?)", (f'catkw:{cid}', 'CategoryKeywords', a.region, path, pages, seen, new, 0 if flat else 1, fp.now(), fp.GV))      # matched = new products found, capped = ran out of words before going quiet
            print(f'{fp.now()} split {path!r}: {tried} words, {seen} seen, {new} new{"" if flat else ", NOT saturated"}', flush=True)
            if a.limit and done >= a.limit: return 0
        print(f'{fp.now()} done: {done} categories split'); return 0
    for cid, path in leaves(a.region, a.internal):
        if sn > 1 and zlib.crc32(cid.encode()) % sn != si: continue
        q = c.execute("SELECT fetched, gv FROM query WHERE person_id=? AND role='Category' AND region=?", (f'cat:{cid}', a.region)).fetchone()
        if q and q[1] >= fp.GV and not (cutoff and q[0] < cutoff): continue
        pages, returned, stored, capped = fetch_leaf(c, api, a.region, cid); done += 1
        with c: c.execute("INSERT OR REPLACE INTO query (person_id, role, region, name, pages, returned, matched, capped, fetched, gv) VALUES (?,?,?,?,?,?,?,?,?,?)", (f'cat:{cid}', 'Category', a.region, path, pages, returned, stored, capped, fp.now(), fp.GV))
        if done % 10 == 0 or a.limit and done >= a.limit:
            print(f'{fp.now()} categories={done} products={c.execute("SELECT count(*) FROM product WHERE region=?", (a.region,)).fetchone()[0]} last={path!r} ({returned}{", capped" if capped else ""})', flush=True)
        if a.limit and done >= a.limit: return 0
    print(f'{fp.now()} done: {done} categories'); return 0


if __name__ == '__main__':
    sys.exit(main())
