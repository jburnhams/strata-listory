"""AudiobookDB (https://audiobookdb.org): a community audiobook catalogue (beta). Title search and ASIN lookup. Terms (https://audiobookdb.org/terms): personal, non-commercial use; cached data kept only as long as needed; attribution wherever its data is shown. The API key is read from APP_ADB_KEY or `~/.config/strata-listory/audiobookdb.key`."""
import os, re

from app.providers.base import Http, MatchError, Provider, candidate

API = 'https://audiobookdb.org/api'


def key() -> str:
    k = os.environ.get('APP_ADB_KEY') or ''
    if not k:
        try:
            with open(os.path.join(os.path.expanduser('~'), '.config', 'strata-listory', 'audiobookdb.key'), encoding='utf8') as f: k = f.read()
        except OSError: pass
    return k.strip()


def image_url(raw: dict) -> str:
    """The cover image URL of an AudiobookDB book (its API says to append `/large.jpg` to the base URL), or ''."""
    img = raw.get('coverImage') or raw.get('image') or {}
    url = img.get('url') if isinstance(img, dict) else img
    return '' if not isinstance(url, str) or not url else url if re.search(r'\.(jpe?g|png|webp)$', url) else url.rstrip('/') + '/large.jpg'


def to_candidate(raw: dict) -> dict:
    authors = [p['person']['name'] for p in raw.get('people', []) if (p.get('role') or {}).get('name') == 'Author']
    sw = (raw.get('seriesWithId') or [None])[0]
    pos = None
    try: pos = float(sw['ordinal']) if sw else None
    except (TypeError, ValueError, KeyError): pass
    return candidate('audiobookdb', id=raw.get('id', ''), title=raw.get('title', ''), subtitle=raw.get('subtitle') or '', authors=authors, series=sw['title'] if sw else '', series_position=pos, cover_url=image_url(raw))


class AudiobookDB(Provider):
    name = 'audiobookdb'; label = 'AudiobookDB'; site = 'https://audiobookdb.org'
    min_interval = 0.15; cache_days = 7          # registered tier: 10 requests a second and a daily budget; its terms want cached data refreshed

    def __init__(self): self.http = Http('AudiobookDB', self.min_interval)

    def unavailable(self):
        return None if key() else 'no API key: put it in ~/.config/strata-listory/audiobookdb.key (free account at audiobookdb.org, Settings -> API key)'

    def _call(self, path: str, body: dict | None = None):
        if not key(): raise MatchError('no AudiobookDB API key')
        return self.http.request(API + path, body, {'X-API-Key': key()})

    def search(self, title: str, author: str = '') -> list[dict]:
        q = title.strip()[:100]
        raw = self.cache('search:' + q.lower(), lambda: self._call('/search', dict(q=q, type='books'))) if len(q) >= 3 else []      # the service needs three characters
        return [to_candidate(b) for b in raw or []]

    def by_asin(self, asin: str) -> dict | None:
        raw = self.cache('asin:' + asin, lambda: self._call(f'/audiobooks/external/audible/{asin}'))
        return to_candidate(raw) if raw else None

    def cover_url(self, book_id: str) -> str:
        return image_url(self.cache('book:' + book_id, lambda: self._call(f'/books/{book_id}')) or {})
