"""More metadata for the books we have, from two places. Open Library: the year the work was first published (what the folder name uses), reader rating and subjects. Hardcover: Hardcover ratings, review counts, genres, moods, content warnings, tags, description, series, audiobook editions and the most-liked public reviews. Both are stored per book (`book_extra`), shown on the book page and written to the book's mirror `metadata.json`.

Which Hardcover book is ours is decided in order: the match suggestion when Hardcover found it, an ASIN lookup, then a title and author search accepted only when it scores like a likely match. A book that cannot be placed is remembered as not found (and retried after STALE_DAYS) so it is not searched again every run."""
from datetime import datetime, timedelta, timezone

from app import catalogue, matcher, mirror, providers
from app.providers.base import MatchError

STALE_DAYS = 30


def stamp(book: dict) -> str:
    """What a source was asked about: if the title or authors change (a wrong author corrected), a remembered answer, including \"not found\", is asked again."""
    return matcher.norm(book['title']) + '|' + matcher.norm(book.get('author', ''))


def hardcover_id(book: dict, hc) -> str | None:
    sug = book.get('suggestion') or {}
    if sug.get('source') == 'hardcover' and sug.get('id'): return str(sug['id'])
    if (prev := catalogue.get_extras(book['id']).get('hardcover')) and prev['data'].get('id'): return prev['data']['id']
    asin = book.get('asin') or sug.get('asin') or ''
    if asin and (c := hc.by_asin(asin)): return c['id']
    scored = sorted(((matcher.score(book, c), c) for c in hc.search(book['title'], book['author'])), key=lambda x: -x[0])
    return scored[0][1]['id'] if scored and scored[0][0] >= matcher.GOOD_SCORE else None


def enrich_book(book: dict, plist: list | None = None) -> dict | None:
    """Fetch and store Hardcover's community data for one book; returns it, or None when the book could not be placed on Hardcover. Raises MatchError when Hardcover cannot be used."""
    hc = next((p for p in (providers.enabled(['hardcover']) if plist is None else plist) if p.name == 'hardcover'), None)
    if hc is None: raise MatchError('Hardcover is not available: ' + (providers.get('hardcover').unavailable() or 'not selected'))
    hid = hardcover_id(book, hc); data = hc.details(hid) if hid else None
    catalogue.set_extra(book['id'], 'hardcover', dict(data or dict(not_found=True), asked=stamp(book))); mirror.write(catalogue.get_book(book['id']))
    return data


def enrich_pending(limit: int = 25, plist: list | None = None) -> dict:
    """Enrich up to `limit` confirmed books that have no recent Hardcover data; returns {tried, enriched, not_found, remaining}."""
    tried = enriched = 0
    for b in catalogue.pending_extra('hardcover', STALE_DAYS, limit):
        tried += 1; enriched += enrich_book(b, plist) is not None
    return dict(tried=tried, enriched=enriched, not_found=tried - enriched, remaining=len(catalogue.pending_extra('hardcover', STALE_DAYS, 100000)))


def openlibrary_work(book: dict, ol) -> dict | None:
    """The Open Library work for a book, accepted only when its title and author score like a likely match (otherwise we would attach another book's first-published year)."""
    scored = sorted(((matcher.score(book, c), c) for c in ol.search(book['title'], book['author'])), key=lambda x: -x[0])
    if not scored or scored[0][0] < matcher.GOOD_SCORE: return None
    c = scored[0][1]
    return dict(source='openlibrary', id=c['id'], url=c['url'], title=c['title'], author=c['author'], first_publish_year=c['original_year'], rating=c['rating'], ratings_count=c['ratings_count'], subjects=c['subjects'], edition_count=c['edition_count'], cover_url=c['cover_url'], match_score=scored[0][0])


def enrich_openlibrary(book: dict, ol=None) -> dict | None:
    """Fetch and store Open Library's data for one book (`not_found` is remembered like for Hardcover). Raises MatchError when Open Library cannot be reached."""
    ol = ol or providers.get('openlibrary'); data = openlibrary_work(book, ol)
    catalogue.set_extra(book['id'], 'openlibrary', dict(data or dict(not_found=True), asked=stamp(book))); mirror.write(catalogue.get_book(book['id']))
    return data


def ensure_year_sources(book: dict) -> dict:
    """Make sure the sources of the original publication year have been asked (Open Library, and Hardcover when it is available) and return the book's stored extras. A source that is down or unavailable is skipped: the plan then says the year is missing or weaker."""
    have = catalogue.get_extras(book['id'])
    fresh = lambda src: src in have and have[src]['fetched'] > (datetime.now(timezone.utc) - timedelta(days=STALE_DAYS)).isoformat() and have[src]['data'].get('asked') in (None, stamp(book))
    for src, fn, ok in (('openlibrary', enrich_openlibrary, True), ('hardcover', enrich_book, providers.get('hardcover').unavailable() is None)):
        if ok and not fresh(src):
            try: fn(book)
            except MatchError: pass
    return catalogue.get_extras(book['id'])
