"""Community data for the books we have: Hardcover ratings, review counts, genres, moods, content warnings, tags, description, series, audiobook editions and the most-liked public reviews. Stored per book (`book_extra`), shown on the book page and written to the book's mirror `metadata.json`.

Which Hardcover book is ours is decided in order: the match suggestion when Hardcover found it, an ASIN lookup, then a title and author search accepted only when it scores like a likely match. A book that cannot be placed is remembered as not found (and retried after STALE_DAYS) so it is not searched again every run."""
from app import catalogue, matcher, mirror, providers
from app.providers.base import MatchError

STALE_DAYS = 30


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
    catalogue.set_extra(book['id'], 'hardcover', data or dict(not_found=True)); mirror.write(catalogue.get_book(book['id']))
    return data


def enrich_pending(limit: int = 25, plist: list | None = None) -> dict:
    """Enrich up to `limit` confirmed books that have no recent Hardcover data; returns {tried, enriched, not_found, remaining}."""
    tried = enriched = 0
    for b in catalogue.pending_extra('hardcover', STALE_DAYS, limit):
        tried += 1; enriched += enrich_book(b, plist) is not None
    return dict(tried=tried, enriched=enriched, not_found=tried - enriched, remaining=len(catalogue.pending_extra('hardcover', STALE_DAYS, 100000)))
