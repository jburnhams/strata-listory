"""What every metadata provider shares: the candidate shape, a polite HTTP client (throttle, retries, 429 handling), and the response cache.

A *candidate* is a dict: source (provider name), id, asin, title, subtitle, author, authors, narrators, series, series_position, runtime_minutes, cover_url, language, release_date. Providers fill what they know and leave the rest empty (rating, ratings_count, genres, pages and url only come from community sites)."""
import json, time, urllib.error, urllib.request
from datetime import datetime, timedelta, timezone

from app import db

USER_AGENT = 'strata-listory/0.1 (personal audiobook catalogue)'


class MatchError(RuntimeError):
    """Raised when a provider cannot be used right now (no key, rate limited, down)."""


def candidate(source: str, **f) -> dict:
    out = dict(source=source, id='', asin='', title='', subtitle='', author='', authors=[], narrators=[], series='', series_position=None, runtime_minutes=None, cover_url='', language='', release_date='', rating=None, ratings_count=None, genres=[], pages=None, url='')
    out.update({k: v for k, v in f.items() if k in out})
    out['author'] = out['author'] or ', '.join(out['authors'])
    return out


class Http:
    """A throttled JSON client for one provider. `request` returns the parsed body, None for a 404, and raises MatchError otherwise."""
    def __init__(self, label: str, min_interval: float = 0.1, headers: dict | None = None):
        self.label, self.min_interval, self.headers, self._last = label, min_interval, headers or {}, 0.0

    def request(self, url: str, body: dict | None = None, headers: dict | None = None):
        for attempt in (1, 2, 3):       # a 429 is retried once after Retry-After, a 502/503/504 twice (these services are betas and go down now and then)
            time.sleep(max(0, self._last + self.min_interval - time.monotonic())); self._last = time.monotonic()
            req = urllib.request.Request(url, json.dumps(body).encode() if body is not None else None, {'User-Agent': USER_AGENT, 'Content-Type': 'application/json', **self.headers, **(headers or {})})
            try:
                with urllib.request.urlopen(req, timeout=30) as r: return json.load(r)
            except urllib.error.HTTPError as e:
                if e.code == 404: return None
                if e.code == 429 and attempt == 1: time.sleep(min(30, int(e.headers.get('Retry-After') or 5))); continue
                if e.code in (502, 503, 504) and attempt < 3: time.sleep(3 * attempt); continue
                raise MatchError(f'{self.label} answered {e.code}')
            except (urllib.error.URLError, OSError, ValueError) as e: raise MatchError(f'{self.label} unreachable: {e}')


def cached(key: str, fetch, days: float = 7):
    """`fetch()` once per `days` for this key (None results are cached too: a book that is not there stays not there for that long)."""
    c = db.connect()
    try:
        r = c.execute('SELECT fetched, body FROM match_cache WHERE key=?', (key,)).fetchone()
        if r and datetime.now(timezone.utc) - datetime.fromisoformat(r['fetched']) < timedelta(days=days): return json.loads(r['body'])
        body = fetch()
        with c: c.execute('INSERT OR REPLACE INTO match_cache (key, fetched, body) VALUES (?, ?, ?)', (key, datetime.now(timezone.utc).isoformat(), json.dumps(body)))
        return body
    finally: c.close()


class Provider:
    """A source of book metadata. Override `search` and/or `by_asin`; `unavailable` explains why it cannot run (for example no API key)."""
    name = ''; label = ''; site = ''
    min_interval = 0.2       # seconds between requests to this service (its rate limit, with margin)
    cache_days = 7           # how long its responses are reused; terms ask for cached data to be kept only as long as needed and refreshed

    def unavailable(self) -> str | None: return None
    def by_asin(self, asin: str) -> dict | None: return None
    def search(self, title: str, author: str = '') -> list[dict]: return []
    def cover_url(self, book_id: str) -> str: return ''
    def cache(self, key: str, fetch): return cached(f'{self.name}:{key}', fetch, self.cache_days)
