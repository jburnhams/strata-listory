"""Open Library (https://openlibrary.org): an open, free book catalogue with a documented API and public bulk dumps (personal use; identify yourself in the User-Agent, which the shared client does). It is about books, not audiobooks, so it has no narrators or runtimes, but it knows when a work was first published, which the audiobook sources do not, plus reader ratings and subjects. Used to find the original publication year (for the folder name) and as extra metadata; it is not part of the default title matching."""
import urllib.parse

from app.providers.base import Http, Provider, candidate

FIELDS = 'key,title,author_name,first_publish_year,ratings_average,ratings_count,subject,edition_count,cover_i'


def to_candidate(d: dict) -> dict:
    cover = f"https://covers.openlibrary.org/b/id/{d['cover_i']}-L.jpg" if d.get('cover_i') else ''
    c = candidate('openlibrary', id=d.get('key', ''), title=d.get('title', ''), authors=d.get('author_name') or [], cover_url=cover, rating=d.get('ratings_average'), ratings_count=d.get('ratings_count'), genres=(d.get('subject') or [])[:8],
                  url=f"https://openlibrary.org{d['key']}" if d.get('key') else '')
    c['original_year'] = d.get('first_publish_year')
    c['subjects'] = (d.get('subject') or [])[:25]; c['edition_count'] = d.get('edition_count')
    return c


class OpenLibrary(Provider):
    name = 'openlibrary'; label = 'Open Library'; site = 'https://openlibrary.org'
    min_interval = 0.5; cache_days = 30; matching = False          # a work's first publication year does not change: a month is plenty

    def __init__(self): self.http = Http('Open Library', self.min_interval)

    def search(self, title: str, author: str = '') -> list[dict]:
        t = title.strip()[:120]
        if len(t) < 2: return []
        q = dict(title=t, limit=5, fields=FIELDS, **({'author': author.strip()[:60]} if author.strip() else {}))
        raw = self.cache(f'search:{t.lower()}:{author.strip().lower()}', lambda: self.http.request('https://openlibrary.org/search.json?' + urllib.parse.urlencode(q)))
        return [to_candidate(d) for d in (raw or {}).get('docs', [])]
