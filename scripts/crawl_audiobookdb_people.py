#!/usr/bin/env python3
"""Clone AudiobookDB's people list (authors, narrators ...) into a local SQLite file, to use as the list of names to look up in Audible's catalogue.

`GET /api/people` returns 20 people per page ordered by name; the `x-next-cursor` response header is passed back as `?cursor=` for the next page. The service meters requests: a per-second window (`ratelimit-remaining`/`ratelimit-reset`) and a daily budget (`x-ratelimit-budget`, remaining requests). This crawl keeps well under the first (default 4 requests a second) and stops for the day when the budget falls to `--reserve`, leaving that many for the app's normal lookups; it then sleeps until the budget refills (daily) unless `--once` is given. Progress (the cursor) is saved after every page, so it resumes after any stop. Only id, name, roles and credit count are kept (descriptions and covers are not needed), and rows are upserted so a later pass refreshes them.

    scripts/crawl_audiobookdb_people.py [--db data/audiobookdb/people.sqlite] [--rate 4] [--reserve 1000] [--once] [--restart]

The API key is read as in app.providers.audiobookdb (APP_ADB_KEY or ~/.config/strata-listory/audiobookdb.key). AudiobookDB's terms are personal, non-commercial use with attribution; this index is private to this machine."""

import argparse
import json
import os
import sqlite3
import sys
import time
import urllib.error
import urllib.request
from datetime import UTC, datetime

API = "https://audiobookdb.org/api/people"
UA = "strata-listory/0.1 (personal audiobook catalogue; people index)"


def key() -> str:
    k = os.environ.get("APP_ADB_KEY") or ""
    if not k:
        try:
            with open(os.path.expanduser("~/.config/strata-listory/audiobookdb.key"), encoding="utf8") as f:
                k = f.read()
        except OSError:
            pass
    return k.strip()


def now() -> str:
    return datetime.now(UTC).isoformat(timespec="seconds")


def open_db(path: str) -> sqlite3.Connection:
    os.makedirs(os.path.dirname(os.path.abspath(path)), exist_ok=True)
    c = sqlite3.connect(path, timeout=60)
    c.execute("PRAGMA journal_mode=WAL")
    c.execute("PRAGMA synchronous=NORMAL")
    c.executescript("""CREATE TABLE IF NOT EXISTS person (id TEXT PRIMARY KEY, name TEXT NOT NULL, roles TEXT NOT NULL, credits INTEGER, fetched TEXT NOT NULL);
        CREATE INDEX IF NOT EXISTS person_name ON person(name);
        CREATE TABLE IF NOT EXISTS state (k TEXT PRIMARY KEY, v TEXT);""")
    return c


def get_state(c, k, default=None):
    r = c.execute("SELECT v FROM state WHERE k=?", (k,)).fetchone()
    return r[0] if r else default


def set_state(c, k, v):
    c.execute("INSERT INTO state(k,v) VALUES(?,?) ON CONFLICT(k) DO UPDATE SET v=excluded.v", (k, str(v)))


def fetch(cursor: str | None, apikey: str):
    """One page: (rows, next_cursor, daily_budget_remaining). Retries 429/5xx with backoff; raises on anything else."""
    url = API + ("?cursor=" + cursor if cursor else "")
    for attempt in range(1, 7):
        req = urllib.request.Request(url, headers={"X-API-Key": apikey, "User-Agent": UA})
        try:
            with urllib.request.urlopen(req, timeout=40) as r:
                h = r.headers
                budget = h.get("x-ratelimit-budget")
                return (
                    json.load(r),
                    h.get("x-next-cursor") or None,
                    int(budget) if budget and budget.isdigit() else None,
                )
        except urllib.error.HTTPError as e:
            if e.code in (429, 500, 502, 503, 504):
                wait = min(900, int(e.headers.get("Retry-After") or 0) or 5 * 2**attempt)
                print(f"{now()} HTTP {e.code}, waiting {wait}s (attempt {attempt})", flush=True)
                time.sleep(wait)
                continue
            raise
        except (urllib.error.URLError, OSError, ValueError) as e:
            print(f"{now()} {e}, retrying", flush=True)
            time.sleep(5 * attempt)
    raise SystemExit("giving up after repeated failures")


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument(
        "--db", default=os.path.join(os.path.dirname(__file__), "..", "data", "audiobookdb", "people.sqlite")
    )
    ap.add_argument("--rate", type=float, default=4, help="requests per second (default 4)")
    ap.add_argument("--reserve", type=int, default=1000, help="daily requests to leave unused (default 1000)")
    ap.add_argument(
        "--once", action="store_true", help="stop when the daily budget is used instead of waiting for it to refill"
    )
    ap.add_argument("--restart", action="store_true", help="start again from the first page (refresh pass)")
    ap.add_argument("--max-pages", type=int, default=0)
    a = ap.parse_args(argv)
    apikey = key()
    if not apikey:
        print("no AudiobookDB API key", file=sys.stderr)
        return 2
    c = open_db(a.db)
    if a.restart:
        c.execute("DELETE FROM state WHERE k IN ('cursor','done')")
        c.commit()
    if get_state(c, "done"):
        print("crawl already complete; use --restart to refresh")
        return 0
    cursor, pages, last = get_state(c, "cursor"), 0, 0.0
    while True:
        time.sleep(max(0, last + 1 / a.rate - time.monotonic()))
        last = time.monotonic()
        rows, nxt, budget = fetch(cursor, apikey)
        with c:
            c.executemany(
                "INSERT INTO person(id,name,roles,credits,fetched) VALUES(?,?,?,?,?) ON CONFLICT(id) DO UPDATE SET name=excluded.name, roles=excluded.roles, credits=excluded.credits, fetched=excluded.fetched",
                [
                    (p["id"], p.get("name") or "", json.dumps(p.get("roleNames") or []), p.get("creditCount"), now())
                    for p in rows
                ],
            )
            if nxt:
                set_state(c, "cursor", nxt)
            else:
                set_state(c, "done", now())
            set_state(c, "budget", budget)
            set_state(c, "updated", now())
        cursor, pages = nxt, pages + 1
        if pages % 50 == 0:
            print(
                f"{now()} pages={pages} people={c.execute('SELECT count(*) FROM person').fetchone()[0]} budget_left={budget}",
                flush=True,
            )
        if not nxt:
            print(f"{now()} complete: {c.execute('SELECT count(*) FROM person').fetchone()[0]} people", flush=True)
            return 0
        if a.max_pages and pages >= a.max_pages:
            return 0
        if budget is not None and budget <= a.reserve:
            if a.once:
                print(f"{now()} daily budget reached ({budget} left); cursor saved", flush=True)
                return 0
            print(f"{now()} daily budget reached ({budget} left); sleeping an hour", flush=True)
            while True:
                time.sleep(3600)
                rows, nxt2, budget = fetch(
                    cursor, apikey
                )  # the fetched page is re-requested after the sleep, so nothing is lost
                if budget is None or budget > a.reserve:
                    break


if __name__ == "__main__":
    sys.exit(main())
