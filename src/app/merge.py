"""One record per book, built from every source that knows it, choosing the best of each.

Two layers of sources describe different things. The *audiobook* layer (the Audible metadata file, Audnexus, the Audible catalogue, AudiobookDB) knows the recording: its ASIN, narrators, publisher, runtime, release date. The *work* layer (Open Library, Hardcover) knows the original book: when it was first published, its subjects, ratings, description. The audiobook sources identify the work (title and author, ASIN) and the work sources are asked about it; ISBN would be the cleanest link but audiobook ISBNs are rarely known to the book catalogues (measured: Audnexus has one for 11 of 30 of the library's Audible books, Open Library knew none of them), so the link is title and author, scored.

Each field is then decided by weighted agreement between the sources that can speak for it, not by a fixed fallback order: a name or title that several sources give beats one that a single source gives (that is how "CBS Radio Workshop, Aldous Huxley" from one provider loses to "Aldous Huxley" from four). Every merged value keeps the sources that support it and the alternatives that were outvoted, so the Processing page can show both. A field you set by hand is locked and always wins."""
import re
import unicodedata

# how much a source's word counts; the same for every field except where FIELD_SOURCES limits who may speak
WEIGHT = dict(manual=100.0, audible_file=3.0, audnexus=2.5, openlibrary=2.5, audible=2.0, hardcover=2.0, audiobookdb=1.0, suggestion=1.0, catalogue=1.0)
AUDIOBOOK = ('audible_file', 'audnexus', 'audible', 'audiobookdb')
WORK = ('openlibrary', 'hardcover')
FIELD_SOURCES = dict(narrators=AUDIOBOOK, publisher=AUDIOBOOK, runtime_minutes=AUDIOBOOK, release_year=AUDIOBOOK, original_year=WORK)
LOCKABLE = ('title', 'author', 'narrator', 'series', 'series_position', 'asin', 'year')


def strip_accents(s: str) -> str:
    return ''.join(c for c in unicodedata.normalize('NFKD', s or '') if not unicodedata.combining(c))


def norm(s) -> str:
    s = re.sub(r'\((?:un)?abridged[^)]*\)|\bunabridged\b', ' ', strip_accents(str(s or '')).lower())
    return re.sub(r'\s+', ' ', re.sub(r'[^a-z0-9 ]+', ' ', s.replace('&', ' and '))).strip()


def person_key(name: str) -> tuple[str, frozenset]:
    """(surname, initials of the other name parts): 'F. Scott Fitzgerald' and 'Scott Fitzgerald' are the same person, 'Frank Herbert' and 'Brian Herbert' are not."""
    t = norm(name).split()
    return (t[-1] if t else '', frozenset(x[0] for x in t[:-1]))


def same_person(a: tuple, b: tuple) -> bool:
    return a[0] == b[0] and (not a[1] or not b[1] or bool(a[1] & b[1]))


def main_title(t: str) -> str:
    return norm(re.split(r'\s*[:(–—]|\s-\s', re.sub(r'\[[^\]]*\]', ' ', t or ''), maxsplit=1)[0])


def _clean_title(t: str) -> str:
    return re.sub(r'\s+', ' ', re.sub(r'\s*\((?:un)?abridged[^)]*\)|\s*\[[^\]]*\]', '', t or '')).strip(' -:')


def vote(cands: list[tuple], key=norm, pick=None) -> dict | None:
    """cands: (value, source, weight). Groups equal keys, the heaviest group wins. Returns {value, sources, alternatives, confidence}."""
    groups: dict = {}
    for value, source, weight in cands:
        if value in (None, '', [], 0): continue
        g = groups.setdefault(key(value), dict(values=[], sources=[], weight=0.0)); g['values'].append(value); g['sources'].append(source); g['weight'] += weight
    if not groups: return None
    ranked = sorted(groups.values(), key=lambda g: -g['weight']); total = sum(g['weight'] for g in ranked); best = ranked[0]
    value = pick(best['values']) if pick else max(set(best['values']), key=best['values'].count)
    return dict(value=value, sources=list(dict.fromkeys(best['sources'])), alternatives=[dict(value=pick(g['values']) if pick else g['values'][0], sources=list(dict.fromkeys(g['sources']))) for g in ranked[1:]], confidence=round(best['weight'] / total, 2))


def vote_title(cands: list[tuple]) -> dict | None:
    """Titles agree when their main part (before a subtitle) does; the cleanest, shortest wording of the winning title is used."""
    return vote([(_clean_title(v), s, w) for v, s, w in cands if _clean_title(v)], key=main_title, pick=lambda vs: min(vs, key=len))


def vote_people(cands: list[tuple]) -> dict | None:
    """cands: (list of names, source, weight). A person is kept when sources worth at least a quarter of the weight that spoke for the field name them (or when only one source spoke); order follows the heaviest source that has them."""
    spoke = [(v, s, w) for v, s, w in cands if v]
    if not spoke: return None
    total = sum(w for _, _, w in spoke); people: list[dict] = []
    for names, source, weight in sorted(spoke, key=lambda c: -c[2]):
        for n in names:
            k = person_key(n); p = next((p for p in people if same_person(p['key'], k)), None)
            if p is None: people.append(dict(key=k, name=n, sources=[source], weight=weight))
            else:
                p['sources'].append(source); p['weight'] += weight
                if len(n) > len(p['name']) and norm(n).startswith(norm(p['name']).split()[0][:1]): p['name'] = n if len(n.split()) >= len(p['name'].split()) else p['name']       # the fuller spelling of the same person
    keep = [p for p in people if len(spoke) == 1 or p['weight'] >= total * 0.25]; drop = [p for p in people if p not in keep]
    if not keep:                                           # nobody dominates: trust the heaviest source's own list
        lead = {person_key(n) for n in max(spoke, key=lambda c: c[2])[0]}; keep = [p for p in people if any(same_person(p['key'], k) for k in lead)]; drop = [p for p in people if p not in keep]
    return dict(value=[p['name'] for p in keep], sources=list(dict.fromkeys(s for p in keep for s in p['sources'])), alternatives=[dict(value=[p['name']], sources=list(dict.fromkeys(p['sources']))) for p in drop],
                confidence=round(sum(p['weight'] for p in keep) / (sum(p['weight'] for p in people) or 1), 2))


def vote_list(cands: list[tuple], limit: int = 10) -> dict | None:
    """Union of lists (genres, ISBNs), ranked by how many sources name each item."""
    counts: dict = {}
    for items, source, weight in cands:
        for it in items or []:
            e = counts.setdefault(norm(it), dict(value=it, sources=[], weight=0.0))
            if source not in e['sources']: e['sources'].append(source); e['weight'] += weight
    if not counts: return None
    ranked = sorted(counts.values(), key=lambda e: -e['weight'])[:limit]
    return dict(value=[e['value'] for e in ranked], sources=list(dict.fromkeys(s for e in ranked for s in e['sources'])), alternatives=[], confidence=1.0)


def merge(records: dict[str, dict], locked: dict | None = None) -> dict:
    """records: source -> normalised record (keys title, authors, narrators, publisher, series, series_position, original_year, release_year, runtime_minutes, isbns, asin, language, genres, description, rating, ratings_count, url).
    locked: field -> value the user set by hand (fields: title, authors, narrators, series, series_position, year, asin). Returns {field: {value, sources, alternatives, confidence}} plus `ratings` and `sources`."""
    locked = locked or {}
    def cands(field, sources=None):
        allowed = FIELD_SOURCES.get(field) if sources is None else sources
        return [(r.get(field), s, WEIGHT.get(s.split(':')[0], 1.0)) for s, r in records.items() if r.get(field) not in (None, '', []) and (allowed is None or s.split(':')[0] in allowed)]
    out: dict = {}
    def put(field, v):
        if v: out[field] = v
    put('title', vote_title(cands('title'))); put('authors', vote_people(cands('authors'))); put('narrators', vote_people(cands('narrators')))
    put('publisher', vote(cands('publisher'))); put('language', vote(cands('language'))); put('asin', vote(cands('asin')))
    put('runtime_minutes', vote([(round(v / 5) * 5, s, w) for v, s, w in cands('runtime_minutes')], key=lambda x: x, pick=lambda vs: vs[0]))      # within a few minutes is the same recording
    put('series', vote(cands('series'))); put('series_position', vote(cands('series_position'), key=lambda x: float(x)))
    put('original_year', vote(cands('original_year'), key=lambda x: int(x), pick=lambda vs: int(vs[0]))); put('release_year', vote(cands('release_year'), key=lambda x: int(x), pick=lambda vs: int(vs[0])))
    put('genres', vote_list(cands('genres', None), 12)); put('isbns', vote_list(cands('isbns', None), 8)); put('description', vote(cands('description'), key=lambda x: x[:80], pick=lambda vs: max(vs, key=len)))
    for field, value in locked.items():
        if value in (None, ''): continue
        prev = out.get(field)
        out[field] = dict(value=value, sources=['set by hand'], alternatives=([dict(value=prev['value'], sources=prev['sources'])] if prev and prev['value'] != value else []) + (prev['alternatives'] if prev else []), confidence=1.0)
    ratings = [dict(source=s, rating=r.get('rating'), count=r.get('ratings_count')) for s, r in records.items() if r.get('rating')]
    return dict(fields=out, ratings=ratings, sources=sorted(records))
