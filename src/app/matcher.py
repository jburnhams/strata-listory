"""Match scanned books against AudiobookDB (https://audiobookdb.org, a community audiobook catalogue) and record the best candidate as a *suggestion* for review. A suggestion never changes a book until it is confirmed (`catalogue.confirm`).

Terms of use (https://audiobookdb.org/terms): personal, non-commercial use; cached data must be kept only as long as needed and refreshed (responses are cached here for CACHE_DAYS); attribution to audiobookdb.org is shown wherever suggestions are; cover art and descriptions are not used. The API key is read from APP_ADB_KEY or `~/.config/strata-listory/audiobookdb.key`."""
import difflib, json, os, re, time, urllib.error, urllib.request
from datetime import datetime, timedelta, timezone

from app import catalogue, db

API = 'https://audiobookdb.org/api'
USER_AGENT = 'strata-listory/0.1 (personal audiobook catalogue)'
CACHE_DAYS = 7
MIN_SCORE = 0.6          # below this a candidate is not worth showing
GOOD_SCORE = 0.85        # the UI marks suggestions at or above this as likely right
MIN_INTERVAL = 0.1       # seconds between requests (the registered tier allows 10 a second)


class MatchError(RuntimeError):
    """Raised when the lookup service cannot be used (no key, rate limited, unreachable)."""


def key() -> str:
    k = os.environ.get('APP_ADB_KEY') or ''
    if not k:
        try:
            with open(os.path.join(os.path.expanduser('~'), '.config', 'strata-listory', 'audiobookdb.key'), encoding='utf8') as f: k = f.read()
        except OSError: pass
    if not k.strip(): raise MatchError('no AudiobookDB API key: put it in ~/.config/strata-listory/audiobookdb.key (free account at audiobookdb.org, Settings -> API key)')
    return k.strip()


_last = [0.0]


def _request(path: str, body: dict | None = None):
    """One throttled API call; honours Retry-After once on a 429 and retries a 502/503/504 twice (the service is a beta and goes down now and then)."""
    for attempt in (1, 2, 3):
        time.sleep(max(0, _last[0] + MIN_INTERVAL - time.monotonic())); _last[0] = time.monotonic()
        req = urllib.request.Request(API + path, json.dumps(body).encode() if body is not None else None, {'X-API-Key': key(), 'User-Agent': USER_AGENT, 'Content-Type': 'application/json'})
        try:
            with urllib.request.urlopen(req, timeout=30) as r: return json.load(r)
        except urllib.error.HTTPError as e:
            if e.code == 404: return None
            if e.code == 429 and attempt == 1: time.sleep(min(30, int(e.headers.get('Retry-After') or 5))); continue
            if e.code in (502, 503, 504) and attempt < 3: time.sleep(3 * attempt); continue
            raise MatchError(f'AudiobookDB answered {e.code} for {path}')
        except (urllib.error.URLError, OSError) as e: raise MatchError(f'AudiobookDB unreachable: {e}')


def _cached(cache_key: str, fetch):
    c = db.connect()
    try:
        r = c.execute('SELECT fetched, body FROM match_cache WHERE key=?', (cache_key,)).fetchone()
        if r and datetime.now(timezone.utc) - datetime.fromisoformat(r['fetched']) < timedelta(days=CACHE_DAYS): return json.loads(r['body'])
        body = fetch()
        with c: c.execute('INSERT OR REPLACE INTO match_cache (key, fetched, body) VALUES (?, ?, ?)', (cache_key, datetime.now(timezone.utc).isoformat(), json.dumps(body)))
        return body
    finally: c.close()


def image_url(raw: dict) -> str:
    """The cover image URL of an AudiobookDB book (its API says to append `/large.jpg` to the base URL), or ''."""
    img = raw.get('coverImage') or raw.get('image') or {}
    url = img.get('url') if isinstance(img, dict) else img
    return '' if not isinstance(url, str) or not url else url if re.search(r'\.(jpe?g|png|webp)$', url) else url.rstrip('/') + '/large.jpg'


def candidate(raw: dict) -> dict:
    """Reduce an AudiobookDB book to the fields we use."""
    authors = [p['person']['name'] for p in raw.get('people', []) if (p.get('role') or {}).get('name') == 'Author']
    sw = (raw.get('seriesWithId') or [None])[0]
    pos = None
    try: pos = float(sw['ordinal']) if sw else None
    except (TypeError, ValueError, KeyError): pass
    return dict(source='audiobookdb', id=raw.get('id', ''), title=raw.get('title', ''), subtitle=raw.get('subtitle') or '', author=', '.join(authors), authors=authors, series=sw['title'] if sw else '', series_position=pos, cover_url=image_url(raw))


class AudiobookDB:
    """The lookup source: `search(title)` and `by_asin(asin)` return candidates (see `candidate`)."""
    def search(self, title: str) -> list[dict]:
        q = title.strip()[:100]
        raw = _cached('search:' + q.lower(), lambda: _request('/search', dict(q=q, type='books'))) if len(q) >= 3 else []
        return [candidate(b) for b in raw or []]

    def cover_url(self, book_id: str) -> str:
        return image_url(_cached('book:' + book_id, lambda: _request(f'/books/{book_id}')) or {})

    def by_asin(self, asin: str) -> dict | None:
        raw = _cached('asin:' + asin, lambda: _request(f'/audiobooks/external/audible/{asin}'))
        return candidate(raw) if raw else None


def norm(s: str) -> str:
    s = re.sub(r'\b(unabridged|abridged|the|a|an)\b', ' ', (s or '').lower())
    return re.sub(r'\s+', ' ', re.sub(r'[^a-z0-9 ]+', ' ', s.replace('&', ' and '))).strip()


def title_score(wanted: str, cand: dict) -> float:
    w = norm(wanted); best = 0.0
    for t in (cand['title'], f"{cand['title']} {cand['subtitle']}", cand['subtitle']):
        t = norm(t)
        if not t: continue
        r = difflib.SequenceMatcher(None, w, t).ratio()
        if len(t) >= 6 and (t in w or w in t) and min(len(t), len(w)) >= 0.5 * max(len(t), len(w)): r = max(r, 0.9)       # one side is the other plus a subtitle or volume
        best = max(best, r)
    return best


def author_score(wanted: str, cand: dict) -> float:
    """1 when the surname of every wanted author part appears among the candidate's authors, 0.5 when we do not know the author, else 0."""
    w = norm(wanted)
    if not w: return 0.5
    have = ' '.join(norm(a) for a in cand['authors'])
    parts = [p for p in re.split(r'\band\b|;|,', w) if p.strip()] or [w]
    if all(p.split()[-1] in have.split() for p in parts if p.split()): return 1.0
    return 0.3 if difflib.SequenceMatcher(None, w, have).ratio() > 0.6 else 0.0


def score(book: dict, cand: dict) -> float:
    return round(0.7 * title_score(book['title'], cand) + 0.3 * author_score(book['author'], cand), 3)


def match_book(book: dict, source) -> tuple[dict, float | None]:
    """The best suggestion for a scanned book: (suggestion, score), or ({}, None) when nothing reaches MIN_SCORE. An ASIN in the folder name is tried first and wins when found."""
    if book.get('asin') and (c := source.by_asin(book['asin'])): return dict(c, matched_by='asin'), 1.0
    scored = sorted(((score(book, c), c) for c in source.search(book['title'])), key=lambda x: -x[0])
    if not scored or scored[0][0] < MIN_SCORE: return {}, None
    return dict(scored[0][1], matched_by='title'), scored[0][0]


def match_pending(limit: int = 25, source=None) -> dict:
    """Try to match up to `limit` scanned books; returns {tried, suggested, none, remaining}."""
    source = source or AudiobookDB(); tried = suggested = 0
    for b in catalogue.pending_match(limit):
        sug, sc = match_book(b, source); catalogue.set_suggestion(b['id'], sug, sc); tried += 1; suggested += bool(sug)
    return dict(tried=tried, suggested=suggested, none=tried - suggested, remaining=len(catalogue.pending_match(100000)))
