"""Fetching: one throttled GET per host. Returns what the server said instead of raising, so a source can report why it found nothing."""

import json
import time
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass
from datetime import UTC, datetime
from email.utils import parsedate_to_datetime

USER_AGENT = "strata-listory/0.1 (personal audiobook catalogue)"
MAX_BYTES = 40_000_000
_last: dict[str, float] = {}
INTERVAL = 0.4


@dataclass
class Reply:
    status: int  # HTTP status, 0 when the server could not be reached
    body: bytes = b""
    error: str = ""
    limited: bool = (
        False  # the host asked us to slow down (429) and still did after the retries, or is being left alone for now
    )

    @property
    def ok(self) -> bool:
        return self.status == 200

    def json(self):
        return json.loads(self.body.decode("utf8"))


ATTEMPTS = 4
MAX_WAIT = 60.0  # the longest a single Retry-After is honoured
COOL_DOWN = 600.0  # a host that still says 429 after the retries is left alone this long, so one rate limit does not make every lookup wait
_cool_until: dict[str, float] = {}


def _retry_after(headers, attempt: int) -> float:
    """Seconds to wait before trying again: the server's Retry-After (a number of seconds or an HTTP date) when it sends one, else 2, 4, 8... seconds."""
    value = headers.get("Retry-After", "") if headers is not None else ""
    try:
        wait = float(value)
    except (TypeError, ValueError):
        try:  # an HTTP date: wait until then
            wait = (parsedate_to_datetime(value) - datetime.now(UTC)).total_seconds()
        except (TypeError, ValueError):
            wait = 2.0**attempt
    return max(0.5, min(wait, MAX_WAIT))


def get(url: str, *, timeout: float = 30) -> Reply:
    """GET `url` (https only), waiting out the per-host interval. A 429 is retried up to ATTEMPTS times, waiting as long as the host says (up to MAX_WAIT); one that still says 429 puts the host on a cool-down (`limited`), during which calls return at once. 5xx and network errors are retried too, with short waits."""
    if not url.startswith("https://"):
        return Reply(0, error="only https addresses are fetched")
    host = urllib.parse.urlsplit(url).netloc
    if _cool_until.get(host, 0) > time.monotonic():
        return Reply(429, error="rate limited a moment ago; not asking again yet", limited=True)
    last = Reply(0, error="unreachable")
    for attempt in range(1, ATTEMPTS + 1):
        time.sleep(max(0, _last.get(host, 0) + INTERVAL - time.monotonic()))
        _last[host] = time.monotonic()
        wait = 0.0
        try:
            with urllib.request.urlopen(
                urllib.request.Request(url, headers={"User-Agent": USER_AGENT}), timeout=timeout
            ) as r:
                return Reply(r.status, r.read(MAX_BYTES))
        except urllib.error.HTTPError as e:
            last = Reply(e.code, error=str(e))
            if e.code == 429:
                last.limited = True
                wait = _retry_after(e.headers, attempt)
            elif e.code in (500, 502, 503, 504):
                wait = 1.0 * attempt
            else:
                return last
        except (OSError, ValueError) as e:
            last, wait = Reply(0, error=str(e)), 1.0 * attempt
        if attempt < ATTEMPTS:
            time.sleep(wait)
    if last.limited:
        _cool_until[host] = time.monotonic() + COOL_DOWN
    return last
