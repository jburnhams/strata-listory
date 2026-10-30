"""Hardcover (https://hardcover.app): a book-tracking community (a Goodreads alternative) with a GraphQL API. Used for title/author search and ASIN lookup like the other providers, and as the source of community data for the books we have: rating, ratings and reviews counts, genres, moods, content warnings, tags, a description, series, audiobook editions and the most-liked public reviews (`details`).

Needs a free API token (hardcover.app account settings): `APP_HARDCOVER_KEY` or `~/.config/strata-listory/hardcover.key`. The API is rate limited (60 requests a minute as far as I know; we keep to about one a second) and is for personal use here; reviews are the readers' own words, kept in the private catalogue with the reviewer's name and a link back."""
import os

from app.providers.base import Http, MatchError, Provider, candidate

API = 'https://api.hardcover.app/v1/graphql'
SEARCH = 'query($q: String!, $n: Int) { search(query: $q, query_type: "Book", per_page: $n, page: 1) { results } }'
BY_ASIN = 'query($a: String!) { editions(where: {asin: {_eq: $a}}, limit: 1) { book { id title subtitle slug rating ratings_count release_date cached_image cached_contributors } } }'
DETAILS = '''query($id: Int!) {
  books(where: {id: {_eq: $id}}, limit: 1) { id title subtitle slug description pages release_date rating ratings_count reviews_count users_count users_read_count lists_count cached_tags cached_contributors book_series { position series { name } } }
  user_books(where: {book_id: {_eq: $id}, has_review: {_eq: true}}, limit: 5, order_by: {likes_count: desc}) { rating review_raw review_has_spoilers likes_count reviewed_at user { username } }
  editions(where: {book_id: {_eq: $id}, reading_format_id: {_eq: 2}}, limit: 5) { asin audio_seconds edition_format release_date publisher { name } }
}'''


def key() -> str:
    k = os.environ.get('APP_HARDCOVER_KEY') or ''
    if not k:
        try:
            with open(os.path.join(os.path.expanduser('~'), '.config', 'strata-listory', 'hardcover.key'), encoding='utf8') as f: k = f.read()
        except OSError: pass
    return k.strip().removeprefix('Bearer ').strip()


def _tags(cached: dict | None, kind: str, spoilers: bool = False) -> list[str]:
    return [t['tag'] for t in sorted((cached or {}).get(kind) or [], key=lambda t: -t.get('count', 0)) if spoilers or t.get('spoiler_ratio', 0) < 0.5]


def hit_to_candidate(d: dict) -> dict:
    series = d.get('featured_series') or {}
    sname = (series.get('series') or {}).get('name') or (d.get('series_names') or [''])[0]
    return candidate('hardcover', id=str(d.get('id', '')), title=d.get('title', ''), subtitle=d.get('subtitle') or '', authors=d.get('author_names') or [], series=sname or '', series_position=series.get('position') if isinstance(series.get('position'), (int, float)) else None,
                     cover_url=(d.get('image') or {}).get('url') or '', release_date=str(d.get('release_year') or ''), rating=d.get('rating'), ratings_count=d.get('ratings_count'), genres=(d.get('genres') or [])[:8], pages=d.get('pages'), url=f"https://hardcover.app/books/{d['slug']}" if d.get('slug') else '') | dict(original_year=d.get('release_year'))


class Hardcover(Provider):
    name = 'hardcover'; label = 'Hardcover'; site = 'https://hardcover.app'
    min_interval = 1.1; cache_days = 14             # about 60 requests a minute is the documented limit

    def __init__(self): self.http = Http('Hardcover', self.min_interval)

    def unavailable(self):
        return None if key() else 'no API token: put it in ~/.config/strata-listory/hardcover.key (hardcover.app account settings -> API)'

    def _gql(self, query: str, variables: dict) -> dict:
        if not key(): raise MatchError('no Hardcover API token')
        r = self.http.request(API, dict(query=query, variables=variables), {'Authorization': f'Bearer {key()}'})
        if not r: raise MatchError('Hardcover returned nothing')
        if r.get('errors'): raise MatchError('Hardcover: ' + str(r['errors'][0].get('message', r['errors']))[:200])
        return r.get('data') or {}

    def search(self, title: str, author: str = '') -> list[dict]:
        q = f'{title} {author}'.strip()[:120]
        if len(q) < 2: return []
        res = self.cache('search:' + q.lower(), lambda: self._gql(SEARCH, dict(q=q, n=8)))
        return [hit_to_candidate(h['document']) for h in ((res.get('search') or {}).get('results') or {}).get('hits', [])]

    def by_asin(self, asin: str) -> dict | None:
        res = self.cache('asin:' + asin, lambda: self._gql(BY_ASIN, dict(a=asin)))
        eds = res.get('editions') or []
        if not eds: return None
        b = eds[0]['book']; b['author_names'] = [c['author']['name'] for c in b.get('cached_contributors') or [] if c.get('contribution') in ('Author', None) and c.get('author')]
        b['image'] = b.get('cached_image') if isinstance(b.get('cached_image'), dict) else {}
        return hit_to_candidate(b)

    def cover_url(self, book_id: str) -> str:
        res = self.cache('cover:' + book_id, lambda: self._gql('query($id: Int!) { books(where: {id: {_eq: $id}}, limit: 1) { cached_image } }', dict(id=int(book_id))))
        img = ((res.get('books') or [{}])[0] or {}).get('cached_image') or {}
        return img.get('url', '') if isinstance(img, dict) else ''

    def details(self, book_id: str) -> dict | None:
        """The community record for a Hardcover book id: ratings, counts, genres/moods/warnings/tags, description, series, audiobook editions and the most-liked public reviews."""
        res = self.cache('details:' + book_id, lambda: self._gql(DETAILS, dict(id=int(book_id))))
        books = res.get('books') or []
        if not books: return None
        b = books[0]; ct = b.get('cached_tags')
        return dict(source='hardcover', id=str(b['id']), url=f"https://hardcover.app/books/{b['slug']}" if b.get('slug') else '', title=b.get('title', ''), subtitle=b.get('subtitle') or '', description=b.get('description') or '', pages=b.get('pages'), release_date=b.get('release_date') or '',
                    rating=b.get('rating'), ratings_count=b.get('ratings_count'), reviews_count=b.get('reviews_count'), readers=b.get('users_count'), finished=b.get('users_read_count'), lists=b.get('lists_count'),
                    genres=_tags(ct, 'Genre'), moods=_tags(ct, 'Mood'), tags=_tags(ct, 'Tag'), content_warnings=_tags(ct, 'Content Warning', spoilers=True),
                    series=[dict(name=(s.get('series') or {}).get('name', ''), position=s.get('position')) for s in b.get('book_series') or []],
                    audiobook_editions=[dict(asin=e.get('asin') or '', hours=round(e['audio_seconds'] / 3600, 1) if e.get('audio_seconds') else None, publisher=(e.get('publisher') or {}).get('name', ''), released=e.get('release_date') or '', format=e.get('edition_format') or '') for e in res.get('editions') or []],
                    reviews=[dict(user=(r.get('user') or {}).get('username', ''), rating=r.get('rating'), text=(r.get('review_raw') or '')[:3000], spoilers=bool(r.get('review_has_spoilers')), likes=r.get('likes_count') or 0, date=(r.get('reviewed_at') or '')[:10]) for r in res.get('user_books') or []])
