"""Fetching: one throttled GET per host. Returns what the server said instead of raising, so a source can report why it found nothing."""

import json
import time
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass

USER_AGENT = "strata-listory/0.1 (personal audiobook catalogue)"
MAX_BYTES = 40_000_000
_last: dict[str, float] = {}
INTERVAL = 0.4


@dataclass
class Reply:
    status: int  # HTTP status, 0 when the server could not be reached
    body: bytes = b""
    error: str = ""

    @property
    def ok(self) -> bool:
        return self.status == 200

    def json(self):
        return json.loads(self.body.decode("utf8"))


def get(url: str, *, timeout: float = 30) -> Reply:
    """GET `url` (https only), waiting out the per-host interval; 429 and 5xx are retried once."""
    if not url.startswith("https://"):
        return Reply(0, error="only https addresses are fetched")
    host = urllib.parse.urlsplit(url).netloc
    for attempt in (1, 2):
        time.sleep(max(0, _last.get(host, 0) + INTERVAL - time.monotonic()))
        _last[host] = time.monotonic()
        try:
            with urllib.request.urlopen(
                urllib.request.Request(url, headers={"User-Agent": USER_AGENT}), timeout=timeout
            ) as r:
                return Reply(r.status, r.read(MAX_BYTES))
        except urllib.error.HTTPError as e:
            if e.code in (429, 500, 502, 503, 504) and attempt == 1:
                time.sleep(2)
                continue
            return Reply(e.code, error=str(e))
        except (OSError, ValueError) as e:
            if attempt == 1:
                time.sleep(1)
                continue
            return Reply(0, error=str(e))
    return Reply(0, error="unreachable")
