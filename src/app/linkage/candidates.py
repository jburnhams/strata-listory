"""Candidate works for a listen, from the local Open Library index (`identify.sqlite`, works with every edition title) and the local Audible index (`audible/index.sqlite`, recordings). Recall only: nothing here decides.

The two sources are joined into **work clusters**: an Open Library work plus the Audible recordings of it (linked by the recording's ISBN, an Amazon id on an edition, or the same title with a shared author), and Audible recordings that no Open Library work claims form clusters of their own. Duplicate Open Library works of one book (same title, a shared author name) fall into one cluster too. A cluster is the unit the model scores, so different recordings and duplicate catalogue records of one book never compete with each other."""
from __future__ import annotations

import difflib, re
from dataclasses import dataclass, field

from app.identify import candidates as olc, radioindex, textkeys as tk
from app.linkage.query import Query

MAX_READINGS = 6
MAX_OL = 60
MAX_RADIO = 6
MAX_AUDIBLE = 80
AUTHOR_BOOKS = 1500


@dataclass
class Product:
    """One Audible recording (an ASIN; the UK row when both stores list it)."""
    asin: str
    title: str
    subtitle: str
    title_key: str
    main_key: str
    authors: list[str]
    narrators: list[str]
    runtime: int | None
    isbn: str
    year: int | None
    ratings: int
    series: str
    series_pos: str
    fmt: str
    via: set[str] = field(default_factory=set)


@dataclass
class Programme:
    """One radio programme record from the local index (`identify.radioindex`): BBC Programmes or Wikidata, with the cached BBC detail when there is one."""
    source: str
    ref: str
    title: str
    title_key: str
    service: str = ''
    first_broadcast: str = ''
    detail: dict = field(default_factory=dict)


@dataclass
class Cluster:
    """A candidate: Open Library works (described as in `identify.candidates.describe`), Audible products, or the radio programmes of one title. Books and programmes never join: a radio adaptation of a novel is a different candidate from the novel, and which one a listen is depends on the listen (the `form` comparison)."""
    works: list[dict] = field(default_factory=list)
    products: list[Product] = field(default_factory=list)
    programmes: list[Programme] = field(default_factory=list)
    via: set[str] = field(default_factory=set)

    @property
    def key(self) -> str:
        """Stable identity: the most-edition Open Library work, else the lowest ASIN."""
        if self.works: return max(self.works, key=lambda w: (w['editions'], w['key']))['key']
        if not self.products and self.programmes: return 'radio:' + self.programmes[0].title_key.replace(' ', '-')
        return 'asin:' + min(p.asin for p in self.products)

    @property
    def title(self) -> str:
        if self.works: return max(self.works, key=lambda w: w['editions'])['title']
        if not self.products and self.programmes: return self.programmes[0].title
        return max(self.products, key=lambda p: p.ratings).title

    @property
    def authors(self) -> list[str]:
        names = [a['name'] for w in self.works for a in w['authors']] + [a for p in self.products for a in p.authors]
        return list(dict.fromkeys(names))

    @property
    def year(self) -> int | None:
        years = [w['year'] for w in self.works if w.get('year')] + [p.year for p in self.products if p.year] + [y for p in self.programmes if (y := _year(p.first_broadcast))]
        return min(years) if years else None

    def title_keys(self) -> dict[str, int]:
        """Every title key the work has been published under, with how many editions/recordings use it."""
        out: dict[str, int] = {}
        for w in self.works:
            for (norm, _kind), n in w['titles'].items(): out[norm] = out.get(norm, 0) + n
        for p in self.programmes: out[p.title_key] = out.get(p.title_key, 0) + 1
        for p in self.products:
            for k in (p.title_key, tk.title_key(f'{p.title}: {p.subtitle}') if p.subtitle else ''):
                if k: out[k] = out.get(k, 0) + 1
        return out


def main_key(title: str) -> str:
    """The title before a subtitle (colon or spaced dash). Bracketed words are dropped first rather than split at: "Robert Ludlum's (TM) The Bourne Sanction" must not shorten to "robert ludlums"."""
    return tk.title_main(re.sub(r'\([^)]*\)', ' ', str(title or '')))


def _year(v) -> int | None:
    m = re.search(r'(1[5-9]\d\d|20\d\d)', str(v or '')); return int(m[1]) if m else None


def _ratio(a: str, b: str) -> float: return difflib.SequenceMatcher(None, a, b).ratio() if a and b else 0.


def products(ac, asins: list[str]) -> dict[str, Product]:
    """Product rows for these ASINs (UK preferred over US), with credits."""
    out: dict[str, Product] = {}
    asins = list(dict.fromkeys(asins))
    for i in range(0, len(asins), 400):
        batch = asins[i:i + 400]; marks = ','.join('?' * len(batch))
        credits: dict[tuple[str, str], list[tuple[str, str]]] = {}
        for r in ac.execute(f'SELECT asin, region, role, name FROM credit WHERE asin IN ({marks})', batch): credits.setdefault((r['asin'], r['region']), []).append((r['role'], r['name']))
        for r in ac.execute(f"SELECT * FROM book WHERE asin IN ({marks}) ORDER BY region='uk'", batch):        # uk last, so it wins
            cr = credits.get((r['asin'], r['region']), [])
            out[r['asin']] = Product(r['asin'], r['title'] or '', r['subtitle'] or '', r['title_key'] or '', main_key(r['title']), [n for role, n in cr if role == 'Author'], [n for role, n in cr if role == 'Narrator'],
                                     r['runtime_min'], next((k for k in tk.isbn_keys(r['isbn'] or '') if len(k) == 13), ''), _year(r['release']), r['ratings'] or 0, r['series'] or '', r['series_pos'] or '', r['format'] or '')
    return out


def audible_candidates(ac, q: Query) -> dict[str, Product]:
    """Recordings by ASIN, by any title key of any reading, by the credited authors' books with a similar title, and (when nothing else) by full-text title words."""
    if ac is None: return {}
    via: dict[str, set[str]] = {}
    def add(asin, how): via.setdefault(asin, set()).add(how)
    if q.asin:
        for r in ac.execute('SELECT asin FROM book WHERE asin=?', (q.asin,)): add(r['asin'], 'asin')
    readings = q.readings[:MAX_READINGS]
    keys = list(dict.fromkeys(k for r in readings for k in tk.title_variants(r.title)))
    if keys:
        marks = ','.join('?' * len(keys))
        for r in ac.execute(f'SELECT asin FROM book WHERE title_key IN ({marks}) OR main_key IN ({marks}) LIMIT 400', (*keys, *keys)): add(r['asin'], 'title')
    wanted = [tk.title_key(r.title) for r in readings]; mains = [main_key(r.title) for r in readings]
    people = list(dict.fromkeys(v for r in readings for p in tk.people(r.author) for v in tk.person_variants(p)))
    if people:
        marks = ','.join('?' * len(people))
        rows = ac.execute(f"SELECT DISTINCT c.asin, b.title_key, b.main_key FROM credit c JOIN book b ON b.asin=c.asin AND b.region=c.region WHERE c.person_key IN ({marks}) AND c.role='Author' LIMIT {AUTHOR_BOOKS}", people).fetchall()
        scored = sorted(((max(max(_ratio(w, r['title_key']) for w in wanted), max(_ratio(m, r['main_key']) for m in mains)), r['asin']) for r in rows), reverse=True)
        for sim, asin in scored[:25]:
            if sim >= .6: add(asin, 'author')
    if not via:
        words = [w for w in (mains[0] if mains else '').split() if len(w) > 1][:8]
        if words:
            for r in ac.execute('SELECT b.asin FROM book_fts f JOIN book b ON b.rowid=f.rowid WHERE book_fts MATCH ? LIMIT 100', (' '.join(f'"{w}"' for w in words),)): add(r['asin'], 'words')
    found = products(ac, list(via))
    for a, p in found.items(): p.via = via[a]
    ranked = sorted(found.values(), key=lambda p: (-len(p.via & {'asin', 'author'}), -max((_ratio(w, p.title_key) for w in wanted), default=0), -p.ratings))
    return {p.asin: p for p in ranked[:MAX_AUDIBLE]}


def ol_candidates(oc, q: Query, found: dict[str, Product]) -> dict[str, dict]:
    """Open Library work clusters per reading (`identify.candidates.find`), plus works holding the ISBN or ASIN of a found recording."""
    if oc is None: return {}
    hits: dict[str, set[str]] = {}
    for r in q.readings[:MAX_READINGS]:
        for work, how in olc.find(oc, r.title, r.author).items(): hits.setdefault(work, set()).update(how)
    isbns = [k for p in found.values() if p.isbn for k in tk.isbn_keys(p.isbn)]
    for r in olc._in(oc, 'SELECT work FROM work_isbns WHERE isbn IN ({})', isbns): hits.setdefault(r['work'], set()).add('audible_isbn')
    for r in olc._in(oc, 'SELECT work FROM work_asins WHERE asin IN ({})', list(found)): hits.setdefault(r['work'], set()).add('audible_asin')
    mapping = olc.canonical(oc, list(hits)); merged: dict[str, set[str]] = {}
    for k, how in hits.items(): merged.setdefault(mapping[k], set()).update(how)
    order = sorted(merged, key=lambda k: (-len(merged[k] & {'isbn', 'audible_isbn', 'audible_asin', 'title+author'}), k))[:MAX_OL]
    out = {}
    for k in order:
        d = olc.describe(oc, k)
        if d: d['via'] = merged[k]; out[k] = d
    return out


SUFFIXES = {'jr', 'sr', 'ii', 'iii', 'iv', 'junior', 'senior'}


def real(names: list[str]) -> list[str]:
    """Credited names without placeholders ('Anonymous', 'Uncredited', 'Various')."""
    return [n for n in names if tk.people(n)]


def _same_person(a: str, b: str) -> bool:
    ka, kb = tk.person_key(a), tk.person_key(b)
    if not ka or not kb: return False
    if ka == kb or set(tk.person_variants(a)) & set(tk.person_variants(b)): return True
    wa, wb = [w for w in ka.split() if w not in SUFFIXES], [w for w in kb.split() if w not in SUFFIXES]
    if not wa or not wb: return False
    if wa == wb: return True
    return wa[-1] == wb[-1] and len(wa) > 1 and len(wb) > 1 and wa[0][0] == wb[0][0]


def shares_author(xs: list[str], ys: list[str]) -> bool: return any(_same_person(x, y) for x in xs for y in ys)


def volumes(title_key: str) -> set[str]:
    """Numbers in a title key (not years): 'salt roads volume 2' -> {'2'}."""
    return {str(int(n)) for n in re.findall(r'\d+', title_key or '') if len(n) < 4}


def same_book(titles_a: set[str], authors_a: list[str], titles_b: set[str], authors_b: list[str], full_a: str = '', full_b: str = '') -> bool:
    """Two records are one book when a title key (or main title, before any subtitle) is shared and an author is shared; records with no real author on either side join on the title alone. Different numbers in the full titles (`Volume 1` and `Volume 2` of one main title) are different books; `clusters` also stops an unnumbered record from chaining them together."""
    if not titles_a & titles_b: return False
    va, vb = volumes(full_a), volumes(full_b)
    if va and vb and va != vb: return False
    ra, rb = real(authors_a), real(authors_b)
    return shares_author(ra, rb) if ra and rb else not ra and not rb


def clusters(oc, works: dict[str, dict], found: dict[str, Product]) -> list[Cluster]:
    """Union the Open Library works and Audible recordings that are one book (see the module docstring)."""
    nodes = [('ol', k) for k in works] + [('au', a) for a in found]
    parent = {n: n for n in nodes}
    def root(n):
        while parent[n] != n: parent[n] = parent[parent[n]]; n = parent[n]
        return n
    full = {('ol', k): tk.title_key(w['title']) for k, w in works.items()} | {('au', a): p.title_key for a, p in found.items()}
    numbered = {n: ({frozenset(v)} if (v := volumes(full[n])) else set()) for n in nodes}      # per cluster root: the volume numbers its records carry
    def join(a, b, check=False):
        """Union two records' clusters; a title-based join (`check`) is refused when the clusters carry different volume numbers (`Salt Roads` must not chain `Volume 1` to `Volume 2`)."""
        ra, rb = root(a), root(b)
        if ra == rb: return
        if check and any(x != y for x in numbered[ra] for y in numbered[rb]): return
        parent[ra] = rb; numbered[rb] |= numbered[ra]
    by_isbn: dict[str, set[str]] = {}
    isbns = [k for p in found.values() if p.isbn for k in tk.isbn_keys(p.isbn)]
    if oc is not None and works:
        for r in olc._in(oc, 'SELECT isbn, work FROM work_isbns WHERE isbn IN ({})', isbns): by_isbn.setdefault(r['isbn'], set()).add(r['work'])
        mapping = olc.canonical(oc, [w for ws in by_isbn.values() for w in ws])
        by_isbn = {i: {mapping.get(w, w) for w in ws} for i, ws in by_isbn.items()}
        for r in olc._in(oc, 'SELECT asin, work FROM work_asins WHERE asin IN ({})', list(found)):
            w = olc.canonical(oc, [r['work']])[r['work']]
            if w in works: join(('au', r['asin']), ('ol', w))
    ol_names = {k: [a['name'] for a in w['authors']] for k, w in works.items()}
    ol_titles = {k: {tk.title_key(w['title']), main_key(w['title'])} - {''} for k, w in works.items()}
    ol_aliases = {k: {n for (n, _), c in w['titles'].items() if c >= 2} | ol_titles[k] for k, w in works.items()}      # a title on one stray edition does not make a recording this work
    au_titles = {a: {p.title_key, p.main_key} - {''} for a, p in found.items()}
    for a, p in found.items():
        for i in tk.isbn_keys(p.isbn):
            for w in by_isbn.get(i, ()):
                if w in works: join(('au', a), ('ol', w))
        for k in works:
            if same_book(au_titles[a], p.authors, ol_aliases[k], ol_names[k], p.title_key, tk.title_key(works[k]['title'])): join(('au', a), ('ol', k), True)
    plist = list(found.values())
    for i, p in enumerate(plist):
        for p2 in plist[i + 1:]:
            if same_book(au_titles[p.asin], p.authors, au_titles[p2.asin], p2.authors, p.title_key, p2.title_key): join(('au', p.asin), ('au', p2.asin), True)
    keys = list(works)
    for i, k in enumerate(keys):
        for k2 in keys[i + 1:]:
            if same_book(ol_titles[k], ol_names[k], ol_titles[k2], ol_names[k2], tk.title_key(works[k]['title']), tk.title_key(works[k2]['title'])): join(('ol', k), ('ol', k2), True)
    out: dict[tuple, Cluster] = {}
    for n in nodes:
        c = out.setdefault(root(n), Cluster())
        if n[0] == 'ol': c.works.append(works[n[1]]); c.via |= works[n[1]].get('via', set())
        else: c.products.append(found[n[1]]); c.via |= {'audible_' + v for v in found[n[1]].via}
    return list(out.values())


def radio_candidates(q: Query, find=None, details=None) -> list[Cluster]:
    """Radio programmes whose title is a title of the listen (any reading), one cluster per title key. Not proposed at all for a listen the kind classifier calls a book (`q.band == 'book'`): books must not collect radio candidates; a listen with no verdict yet, a close call or a broadcast does. `find`/`details` default to the local radio index and its detail cache (nothing without them)."""
    if q.band == 'book': return []
    find = find or radioindex.find; details = details or radioindex.details
    hits = find([r.title for r in q.readings[:MAX_READINGS]]) or []
    by: dict[str, list[Programme]] = {}
    for h in hits:
        k = tk.title_key(h['title'])
        if k: by.setdefault(k, []).append(Programme(h['source'], h['ref'], h['title'], k, h.get('service') or '', h.get('first_broadcast') or ''))
    extra = details([p.ref for ps in by.values() for p in ps if p.source == 'bbc_radio']) or {}
    for ps in by.values():
        for p in ps: p.detail = extra.get(p.ref, {})
    wanted = {tk.title_key(r.title) for r in q.readings[:MAX_READINGS]}
    order = sorted(by, key=lambda k: (k not in wanted, -len(by[k]), k))[:MAX_RADIO]
    return [Cluster(programmes=by[k], via={'radio_title'}) for k in order]


def for_query(oc, ac, q: Query, radio=radio_candidates) -> list[Cluster]:
    found = audible_candidates(ac, q)
    return clusters(oc, ol_candidates(oc, q, found), found) + (radio(q) if radio else [])
