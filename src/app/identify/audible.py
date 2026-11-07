"""Audible-first stage: look the local item up in the local Audible index (`audibleindex`), then use the found recording's author, title and ISBN to read the item a second way for the Open Library search.

Strict by construction: `audibleindex.find` never returns a candidate on title alone, and only `asin`/`strong` grades create the extra *audible* reading; a `weak` hit contributes features (with its disagreements) but never changes what is searched. Items that are not on Audible (older tape/CD recordings) simply have no hit and carry zero audiobook features."""
from app.identify import audibleindex, textkeys as tk

FEATURES = ('audible_found', 'audible_asin', 'audible_strong', 'audible_weak', 'audible_title_full', 'audible_narrator_match', 'audible_narrator_conflict', 'audible_runtime_known', 'audible_runtime_match', 'audible_runtime_conflict', 'audible_runtime_delta',
            'audible_has_isbn', 'audible_language_match', 'audible_abridged_known', 'audible_candidates', 'audible_series_known')
_connection = {}


def connection():
    """One read-only connection per process; None while no index is built."""
    if 'c' not in _connection: _connection['c'] = audibleindex.connect()
    return _connection['c']


def reset(): _connection.pop('c', None)


def lookup(item, c=None):
    """Best Audible candidate for the item, or None. Uses the item's tag/ingest runtime (`runtime_min`), narrators and ASIN when known."""
    c = c if c is not None else connection()
    if c is None: return None
    found = audibleindex.find(c, item.get('title', ''), item.get('author', ''), tk.people(item.get('narrator', '')), item.get('runtime_min'), item.get('asin', ''))
    if not found: return None
    best = found[0]
    return dict(best, candidates=len(found), local_language=item.get('language', ''))


def reading(item, hit):
    """The extra reading from a corroborated recording (`asin`/`strong`): its canonical title and author, plus its ISBN when it has one."""
    if not hit or hit['grade'] not in ('asin', 'strong'): return None
    isbn = hit.get('isbn') or ''
    return dict(item, title=hit['title'], author=', '.join(hit['authors']), reading='audible', isbns=[isbn] if tk.isbn_keys(isbn) else [], audible_corroborated=True)


def features(hit, item=None):
    """Item-level audiobook features (zeros when nothing was found)."""
    if not hit: return {name: 0. for name in FEATURES}
    ev = hit['evidence']; language = (hit.get('language') or '').casefold(); local = ((item or {}).get('language') or '').casefold()
    return dict(audible_found=1., audible_asin=float(hit['grade'] == 'asin'), audible_strong=float(hit['grade'] == 'strong'), audible_weak=float(hit['grade'] == 'weak'), audible_title_full=float(ev.get('title') == 'full'),
                audible_narrator_match=float(ev.get('narrators') == 'match'), audible_narrator_conflict=float(ev.get('narrators') == 'conflict'), audible_runtime_known=float('runtime' in ev), audible_runtime_match=float(ev.get('runtime') == 'match'),
                audible_runtime_conflict=float(ev.get('runtime') == 'conflict'), audible_runtime_delta=min(ev.get('runtime_delta', 0.), 1.), audible_has_isbn=float(bool(tk.isbn_keys(hit.get('isbn') or ''))),
                audible_language_match=float(bool(language and local and language == local)), audible_abridged_known=float(hit.get('abridged') is not None), audible_candidates=min(hit.get('candidates', 1), 20) / 20., audible_series_known=float(bool(hit.get('series'))))
