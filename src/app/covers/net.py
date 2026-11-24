"""Fetching: one throttled GET per host (strata-python's `Http`: a gap per host, Retry-After, a cool-down for a host that keeps saying 429, retries of 5xx). Returns what the server said instead of raising, so a source can report why it found nothing.

`Reply` stays the app's own small shape (status, body, error, limited) because the sources and their test doubles are written against it."""

import json
from dataclasses import dataclass

from strata_python.http import Http

USER_AGENT = "strata-listory/0.1 (personal audiobook catalogue)"
MAX_BYTES = 40_000_000
INTERVAL = 0.4
ATTEMPTS = 4
MAX_WAIT = 60.0  # the longest a single Retry-After is honoured
COOL_DOWN = 600.0  # a host that still says 429 after the retries is left alone this long, so one rate limit does not make every lookup wait

# One client for the process, so the gap between requests to a host is shared by every source and thread.
HTTP = Http(
    USER_AGENT,
    min_interval=INTERVAL,
    attempts=ATTEMPTS,
    max_wait=MAX_WAIT,
    cool_down=COOL_DOWN,
    max_bytes=MAX_BYTES,
    https_only=True,
)


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
        return self.status == 200 and not self.error

    def json(self):
        return json.loads(self.body.decode("utf8"))


def request(
    url: str,
    *,
    method: str = "GET",
    headers: dict[str, str] | None = None,
    body: bytes | None = None,
    timeout: float = 30,
) -> Reply:
    """Send an https request (`GET` or `POST`), waiting out the per-host interval, with 429 Retry-After/cool-down and 5xx retries. Every call here only reads (a POST is a search query), so each is retried alike."""
    got = HTTP.request(method, url, headers=headers, data=body, timeout=timeout, idempotent=True)
    return Reply(got.status, got.body, got.error, got.limited)


def get(url: str, *, timeout: float = 30) -> Reply:
    """GET `url` (https only), waiting out the per-host interval. A 429 is retried up to ATTEMPTS times, waiting as long as the host says (up to MAX_WAIT); one that still says 429 puts the host on a cool-down (`limited`), during which calls return at once. 5xx and network errors are retried too, with short waits."""
    return request(url, timeout=timeout)
