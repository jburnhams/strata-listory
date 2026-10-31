"""Collect what every source knows about a book as normalised records for merge.py, and turn the merged result into the values the processor uses.

`local_records` reads only what is already stored (the catalogue, the Audible metadata file, the stored match, stored Open Library and Hardcover data). `gather` also asks the sources over the network, with the shared throttled and cached clients: Audnexus and the Audible catalogue by ASIN (or Audible by title and author when there is no ASIN), AudiobookDB by ASIN, and Open Library and Hardcover for the work. A source that is down or does not know the book is simply left out."""
import re

from app import catalogue, enrich, matcher, merge, providers
from app.providers.base import MatchError

ASIN = re.compile(r'^[0-9A-Z]{10}$')


def _year(x):
    m = re.match(r'(1\d{3}|2[01]\d{2})', str(x or '')); return int(m[1]) if m else None


def _names(s: str) -> list[str]:
    return [n.strip() for n in re.split(r'\s*(?:,|;|&|\band\b)\s*', s or '') if n.strip()]


def from_candidate(c: dict) -> dict:
    return dict(title=c.get('title'), authors=c.get('authors') or _names(c.get('author', '')), narrators=c.get('narrators') or [], publisher=c.get('publisher'), series=c.get('series'), series_position=c.get('series_position'), release_year=_year(c.get('release_date')) if c.get('source') in ('audible', 'audnexus', 'audiobookdb', 'applebooks') else None,
                original_year=_year(c.get('original_year')), runtime_minutes=c.get('runtime_minutes'), isbns=c.get('isbns') or [], asin=c.get('asin') if ASIN.match(c.get('asin') or '') else '', language=c.get('language'), genres=c.get('genres') or [], description=c.get('description'),
                abridged=c.get('abridged'), release_date=c.get('release_date'), rating=c.get('rating'), ratings_count=c.get('ratings_count'), url=c.get('url'))


def from_audible_file(a: dict) -> dict:
    s = (a.get('series') or [{}])[0]
    try: pos = float(s['sequence']) if s.get('sequence') else None
    except ValueError: pos = None
    return dict(title=a.get('title'), authors=a.get('authors') or [], narrators=a.get('narrators') or [], publisher=a.get('publisher'), series=s.get('title'), series_position=pos, release_year=_year(a.get('release_date')), runtime_minutes=a.get('runtime_minutes'), asin=a.get('asin'), language=a.get('language'),
                abridged={'abridged': True, 'unabridged': False}.get(a.get('format')), release_date=a.get('release_date'), genres=a.get('categories') or [], description=a.get('summary'), rating=float(a['rating']) if a.get('rating') else None, ratings_count=a.get('rating_count'))


def from_openlibrary(d: dict) -> dict:
    return dict(title=d.get('title'), authors=_names(d.get('author', '')) if d.get('author') else [], original_year=_year(d.get('first_publish_year')), genres=(d.get('subjects') or [])[:12], rating=d.get('rating'), ratings_count=d.get('ratings_count'), url=d.get('url'))


def from_hardcover(d: dict) -> dict:
    s = (d.get('series') or [{}])[0]
    return dict(title=d.get('title'), authors=d.get('authors') or [], original_year=_year(d.get('release_date')), genres=d.get('genres') or [], description=d.get('description'), series=s.get('name'), series_position=s.get('position'), rating=d.get('rating'), ratings_count=d.get('ratings_count'), url=d.get('url'))


def from_catalogue(book: dict) -> dict:
    return dict(title=book.get('title'), authors=_names(book.get('author', '')), narrators=_names(book.get('narrator', '')), series=book.get('series'), series_position=book.get('series_position'), asin=book.get('asin') if ASIN.match(book.get('asin') or '') else '',
                original_year=None, release_year=None)


def local_records(book: dict, audible: dict | None = None, extras: dict | None = None) -> dict:
    """Records from what is stored. `catalogue` is the book as it stands (parsed from the folder name, taken from the match you confirmed, or edited by you); fields you edited are applied by `locked_values`."""
    recs = {'catalogue': from_catalogue(book)}
    if audible: recs['audible_file'] = from_audible_file(audible)
    sug = book.get('suggestion') or {}
    if sug.get('title'): recs[sug.get('source') or 'suggestion'] = from_candidate(sug)
    ex = extras or {}
    for name, fn in (('openlibrary', from_openlibrary), ('hardcover', from_hardcover)):
        data = (ex.get(name) or {}).get('data') or {}
        if data and not data.get('not_found'): recs[name] = fn(data)
    return recs


def gather(book: dict, audible: dict | None = None) -> dict:
    """In two steps, like a person would: first the audiobook sources (and what is stored) say which work this is and who recorded it; then the book catalogues (Open Library, Hardcover) are asked about that work, using the title and authors the audiobook sources agree on rather than a possibly wrong catalogue entry. Never raises for a source that is down."""
    sug = book.get('suggestion') or {}; recs = local_records(book, audible, {})
    asin = next((a for a in (book.get('asin'), sug.get('asin'), (audible or {}).get('asin')) if ASIN.match(a or '')), '')
    for name in ('audnexus', 'audible', 'audiobookdb'):
        try:
            p = providers.get(name)
            if p.unavailable(): continue
            c = p.by_asin(asin) if asin else None
            if c is None and name == 'audible':                                  # no ASIN known: the Audible catalogue can be searched by title and author
                scored = sorted(((matcher.score(book, x), x) for x in p.search(book['title'], book['author'])), key=lambda t: -t[0])
                c = scored[0][1] if scored and scored[0][0] >= matcher.GOOD_SCORE else None
            if c: recs[name] = from_candidate(c)
        except MatchError: continue
    first = merge.merge(recs, locked_values(book))['fields']
    identity = dict(book, title=(first.get('title') or dict(value=book['title']))['value'], author=', '.join((first.get('authors') or dict(value=_names(book.get('author', ''))))['value']))
    return local_records(book, audible, enrich.ensure_year_sources(identity)) | {k: v for k, v in recs.items() if k in ('audnexus', 'audible', 'audiobookdb')}


def locked_values(book: dict) -> dict:
    """The fields you set by hand, as merge() expects them."""
    lk = set(book.get('locked') or []); out = {}
    if 'title' in lk: out['title'] = book['title']
    if 'author' in lk: out['authors'] = _names(book['author'])
    if 'narrator' in lk: out['narrators'] = _names(book['narrator'])
    if 'series' in lk: out['series'] = book['series']
    if 'series_position' in lk: out['series_position'] = book['series_position']
    if 'year' in lk and book.get('year'): out['original_year'] = book['year']
    if 'asin' in lk: out['asin'] = book['asin']
    return out


def resolve(book: dict, records: dict) -> dict:
    """The values the processor uses, each with where it came from: {fields: {name: {value, sources, alternatives, confidence}}, year, year_source, year_note, ...}."""
    m = merge.merge(records, locked_values(book)); f = m['fields']
    title = (f.get('title') or dict(value=book['title']))['value']; authors = (f.get('authors') or dict(value=_names(book.get('author', ''))))['value']
    y, ysrc, ynote = None, '', ''
    if 'original_year' in f:
        y = f['original_year']['value']; ysrc = ' + '.join(_label(s) for s in f['original_year']['sources']) + ' (first published)'
        if f['original_year']['alternatives']: ynote = '; '.join(f"{' + '.join(_label(s) for s in a['sources'])} says {a['value']}" for a in f['original_year']['alternatives'])
        if f['original_year']['sources'] == ['set by hand']: ysrc = 'set by hand'
    elif 'release_year' in f:
        y = f['release_year']['value']; ysrc = ' + '.join(_label(s) for s in f['release_year']['sources']) + ' (audiobook release)'; ynote = 'no original publication year found, so this is the audiobook release year'
    elif book.get('year'):
        y, ysrc, ynote = book['year'], book.get('year_source') or 'folder name', 'no original publication year found; this year was read from the folder name'
    return dict(merged=m, title=title, authors=authors, narrators=(f.get('narrators') or dict(value=[]))['value'], publisher=(f.get('publisher') or dict(value=''))['value'], series=(f.get('series') or dict(value=''))['value'], series_position=(f.get('series_position') or dict(value=None))['value'],
                asin=(f.get('asin') or dict(value=''))['value'], genres=(f.get('genres') or dict(value=[]))['value'], year=y, year_source=ysrc, year_note=ynote)


LABELS = dict(audible_file='Audible metadata file', audnexus='Audnexus', audible='Audible catalogue', audiobookdb='AudiobookDB', openlibrary='Open Library', hardcover='Hardcover', catalogue='the catalogue (folder name or your confirmation)', suggestion='the stored match')


def _label(src: str) -> str:
    return LABELS.get(src, src)


def rows(book: dict, r: dict) -> list[dict]:
    """The Processing page's metadata rows: value, who agrees (and how many sources spoke), and what was outvoted."""
    f = r['merged']['fields']; out = []
    def row(label, value, field, fmt=lambda v: v):
        if value in (None, '', []): return
        e = f.get(field); srcs = ' + '.join(_label(s) for s in e['sources']) if e else ''
        alts = '; '.join(f"{', '.join(map(str, a['value'])) if isinstance(a['value'], list) else a['value']} ({' + '.join(_label(s) for s in a['sources'])})" for a in (e or {}).get('alternatives', []))
        out.append(dict(field=label, value=fmt(value), source=srcs, note=f'others say: {alts}' if alts else ''))
    row('Title', r['title'], 'title'); row('Author', ', '.join(r['authors']), 'authors'); row('Narrator', ', '.join(r['narrators']), 'narrators'); row('Publisher', r['publisher'], 'publisher')
    row('Series', r['series'] + (f" #{r['series_position']:g}" if r['series'] and r['series_position'] is not None else ''), 'series')
    if r['year']: out.append(dict(field='Year', value=str(r['year']), source=r['year_source'], note=r['year_note']))
    row('ASIN', r['asin'], 'asin'); row('Genres', ', '.join(r['genres'][:8]), 'genres')
    return out
