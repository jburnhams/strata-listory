"""Identification index (v5) built from the Open Library dumps: works as clusters of every edition's titles.

Differs from `providers/openlibrary_local` (kept until the new path replaces it) in what it keeps: each work carries *all* titles ever given to it (work title/subtitle, every edition's title, subtitle, full_title, other_titles) with the number of editions that use each, author aliases, series, ISBNs and Amazon identifiers per work, edition counts, and redirects so merged duplicate works collapse. All keys come from `identify.textkeys`.

Tables (all keyed for lookup by normalised text):
  works(key, title, subtitle, year, editions, audio_editions, first_edition_year, ratings, rating_total, subjects)
  authors(key, name) / author_names(norm, author) / work_authors(work, author)
  work_titles(norm, work, kind, n)   kind: 0 work title, 1 work subtitle-joined, 2 edition title, 3 edition title+subtitle, 4 edition full_title, 5 edition other_title
  work_isbns(isbn, work) / work_asins(asin, work) / work_series(norm, work, n) / redirects(old, new)
Built into `identify.sqlite.building`, verified, then atomically published; the old index stays usable meanwhile."""

import gzip
import itertools
import json
import multiprocessing
import re
import sqlite3
import time
from collections import deque
from datetime import UTC, datetime
from pathlib import Path

from app import oslib, settings
from app.identify import textkeys as tk

VERSION = 5
KINDS = dict(work=0, work_full=1, edition=2, edition_full=3, full_title=4, other_title=5)
NO_LANGUAGE = ("/languages/und", "/languages/mul", "/languages/zxx")
CHUNK = 5000


def folder():
    return Path(settings.data_dir()) / "openlibrary"


def status():
    try:
        return json.loads((folder() / "identify-status.json").read_text(encoding="utf8"))
    except (OSError, ValueError):
        return dict(ready=False, stage="not_built")


def _progress(stage, **values):
    root = folder()
    root.mkdir(parents=True, exist_ok=True)
    data = dict(status(), stage=stage, updated=datetime.now(UTC).isoformat(), **values)
    temp = root / "identify-status.json.tmp"
    temp.write_text(json.dumps(data, ensure_ascii=False), encoding="utf8")
    temp.replace(root / "identify-status.json")


def year(value):
    found = re.search(r"\b(1\d{3}|20\d{2})\b", str(value or ""))
    return int(found[1]) if found else None


def keep_edition(languages):
    """English, multilingual-with-English or unlabelled editions (audiobooks are mostly catalogued in English)."""
    known = {v for v in languages if re.fullmatch(r"/languages/[a-z]{3}", v) and v not in NO_LANGUAGE}
    return not known or "/languages/eng" in known


def _text(value):
    return value if isinstance(value, str) else ""


def _strings(value):
    return [v for v in value if isinstance(v, str) and v.strip()] if isinstance(value, list) else []


def _link(value):
    if isinstance(value, dict):
        value = value.get("key")
    return value if isinstance(value, str) else ""


def parse_lines(kind, lines):
    """Pure extraction for one chunk of dump lines; also runs in worker processes."""
    out = dict(
        authors=[],
        names=[],
        works=[],
        work_authors=[],
        titles=[],
        redirects=[],
        editions=[],
        isbns=[],
        asins=[],
        series=[],
    )
    for raw in lines:
        fields = (raw.decode("utf8", "replace") if isinstance(raw, bytes) else raw).rstrip("\n").split("\t", 4)
        if len(fields) != 5:
            continue
        try:
            data = json.loads(fields[4])
        except ValueError:
            continue
        if not isinstance(data, dict):
            continue
        key = fields[1]
        if kind == "authors":
            name = _text(data.get("name")).strip()
            if not name or not key.startswith("/authors/"):
                continue
            out["authors"].append((key, name))
            for alias in dict.fromkeys([name, *_strings(data.get("alternate_names"))[:30]]):
                person = tk.person_key(alias)
                if person:
                    out["names"].append((person, key))
        elif kind == "works":
            if fields[0] == "/type/redirect":
                target = _link(data.get("location"))
                if key.startswith("/works/") and target.startswith("/works/"):
                    out["redirects"].append((key, target))
                continue
            title = _text(data.get("title")).strip()
            if not title or not key.startswith("/works/"):
                continue
            subtitle = _text(data.get("subtitle")).strip()
            people = list(
                dict.fromkeys(
                    p
                    for p in (
                        _link(a.get("author") if isinstance(a, dict) else None) for a in data.get("authors") or []
                    )
                    if p
                )
            )
            out["works"].append(
                (
                    key,
                    title,
                    subtitle,
                    year(data.get("first_publish_date")),
                    json.dumps(_strings(data.get("subjects"))[:8]),
                )
            )
            out["work_authors"].extend((key, p) for p in people)
            norm = tk.title_key(title)
            if norm:
                out["titles"].append((norm, key, KINDS["work"]))
            full = tk.title_key(f"{title}: {subtitle}") if subtitle else ""
            if full:
                out["titles"].append((full, key, KINDS["work_full"]))
        else:
            if fields[0] != "/type/edition" or not isinstance(data.get("title"), str):
                continue
            links = list(
                dict.fromkeys(
                    l for l in (_link(w) for w in data.get("works") or []) if re.fullmatch(r"/works/OL\d+W", l)
                )
            )
            if not links:
                continue
            languages = [_link(v) for v in data.get("languages") or []]
            if not keep_edition(languages):
                continue
            title = data["title"].strip()
            subtitle = _text(data.get("subtitle")).strip()
            aliases = {(tk.title_key(title), KINDS["edition"])}
            if subtitle:
                aliases.add((tk.title_key(f"{title}: {subtitle}"), KINDS["edition_full"]))
            if _text(data.get("full_title")):
                aliases.add((tk.title_key(data["full_title"]), KINDS["full_title"]))
            aliases.update((tk.title_key(t), KINDS["other_title"]) for t in _strings(data.get("other_titles"))[:10])
            aliases = {(n, k) for n, k in aliases if n}
            isbns = {
                k for field in ("isbn_10", "isbn_13") for v in _strings(data.get(field)) for k in tk.isbn_keys(v)[:2]
            }
            amazon = {
                a
                for a in (
                    tk.asin(v)
                    for v in _strings(
                        (data.get("identifiers") or {}).get("amazon")
                        if isinstance(data.get("identifiers"), dict)
                        else []
                    )
                )
                if a
            }
            audio = int(bool(re.search(r"audio|mp3|cassette|cd", _text(data.get("physical_format")), re.I)))
            series = {n for n in (tk.title_key(s) for s in _strings(data.get("series"))[:5]) if n}
            published = year(data.get("publish_date"))
            for work in links:
                out["editions"].append((work, published, audio))
                out["titles"].extend((n, work, k) for n, k in aliases)
                out["isbns"].extend((i, work) for i in isbns)
                out["asins"].extend((a, work) for a in amazon)
                out["series"].extend((s, work) for s in series)
    return out


def start_method():
    """`fork` where it exists (cheap), `spawn` elsewhere (Windows); the worker function is top-level so both work."""
    return "fork" if "fork" in multiprocessing.get_all_start_methods() else "spawn"


def _worker(args):
    return parse_lines(*args)


def _chunks(path, kind, limit):
    with gzip.open(path, "rb") as stream:
        lines = itertools.islice(stream, limit) if limit else stream
        while chunk := list(itertools.islice(lines, CHUNK)):
            yield kind, chunk


def _stream(path, kind, limit, pool, workers):
    """Parsed chunks with bounded memory: at most `workers * 3` chunks are in flight."""
    source = _chunks(path, kind, limit)
    if pool is None:
        for item in source:
            yield parse_lines(*item)
        return
    pending = deque()
    for item in source:
        pending.append(pool.apply_async(_worker, (item,)))
        if len(pending) >= workers * 3:
            yield pending.popleft().get()
    while pending:
        yield pending.popleft().get()


SCHEMA = """PRAGMA journal_mode=OFF; PRAGMA synchronous=OFF; PRAGMA cache_size=-262144; PRAGMA temp_store=FILE;
CREATE TABLE authors(key TEXT PRIMARY KEY, name TEXT NOT NULL);
CREATE TABLE author_names(norm TEXT NOT NULL, author TEXT NOT NULL);
CREATE TABLE works(key TEXT PRIMARY KEY, title TEXT NOT NULL, subtitle TEXT NOT NULL, year INTEGER, subjects TEXT NOT NULL, editions INTEGER NOT NULL DEFAULT 0, audio_editions INTEGER NOT NULL DEFAULT 0, first_edition_year INTEGER, ratings INTEGER NOT NULL DEFAULT 0, rating_total INTEGER NOT NULL DEFAULT 0);
CREATE TABLE work_authors(work TEXT NOT NULL, author TEXT NOT NULL);
CREATE TABLE redirects(old TEXT PRIMARY KEY, new TEXT NOT NULL);
CREATE TABLE stage_titles(norm TEXT NOT NULL, work TEXT NOT NULL, kind INTEGER NOT NULL);
CREATE TABLE stage_isbns(isbn TEXT NOT NULL, work TEXT NOT NULL);
CREATE TABLE stage_asins(asin TEXT NOT NULL, work TEXT NOT NULL);
CREATE TABLE stage_series(norm TEXT NOT NULL, work TEXT NOT NULL);
CREATE TABLE stage_editions(work TEXT NOT NULL, year INTEGER, audio INTEGER NOT NULL);
CREATE TABLE metadata(key TEXT PRIMARY KEY, value TEXT NOT NULL);"""

FINISH = """CREATE TABLE work_titles AS SELECT norm, work, kind, count(*) AS n FROM stage_titles GROUP BY norm, work, kind;
DROP TABLE stage_titles;
CREATE TABLE work_isbns AS SELECT DISTINCT isbn, work FROM stage_isbns; DROP TABLE stage_isbns;
CREATE TABLE work_asins AS SELECT DISTINCT asin, work FROM stage_asins; DROP TABLE stage_asins;
CREATE TABLE work_series AS SELECT norm, work, count(*) AS n FROM stage_series GROUP BY norm, work; DROP TABLE stage_series;
CREATE TEMP TABLE edition_stats AS SELECT work, count(*) AS editions, sum(audio) AS audio, min(year) AS first_year FROM stage_editions GROUP BY work;
CREATE INDEX temp.edition_stats_work ON edition_stats(work);
UPDATE works SET editions=(SELECT editions FROM edition_stats WHERE work=works.key), audio_editions=(SELECT audio FROM edition_stats WHERE work=works.key), first_edition_year=(SELECT first_year FROM edition_stats WHERE work=works.key) WHERE key IN (SELECT work FROM edition_stats);
DROP TABLE stage_editions;
CREATE INDEX work_titles_norm ON work_titles(norm, work);
CREATE INDEX work_titles_work ON work_titles(work);
CREATE INDEX work_isbns_isbn ON work_isbns(isbn);
CREATE INDEX work_isbns_work ON work_isbns(work);
CREATE INDEX work_asins_asin ON work_asins(asin);
CREATE INDEX work_series_work ON work_series(work);
CREATE INDEX author_names_norm ON author_names(norm, author);
CREATE INDEX work_authors_author ON work_authors(author, work);
CREATE INDEX work_authors_work ON work_authors(work, author);"""


def _insert(connection, out):
    with connection:
        connection.executemany("INSERT OR REPLACE INTO authors VALUES(?,?)", out["authors"])
        connection.executemany("INSERT INTO author_names VALUES(?,?)", out["names"])
        connection.executemany(
            "INSERT OR REPLACE INTO works(key,title,subtitle,year,subjects) VALUES(?,?,?,?,?)", out["works"]
        )
        connection.executemany("INSERT INTO work_authors VALUES(?,?)", out["work_authors"])
        connection.executemany("INSERT OR REPLACE INTO redirects VALUES(?,?)", out["redirects"])
        connection.executemany("INSERT INTO stage_titles VALUES(?,?,?)", out["titles"])
        connection.executemany("INSERT INTO stage_isbns VALUES(?,?)", out["isbns"])
        connection.executemany("INSERT INTO stage_asins VALUES(?,?)", out["asins"])
        connection.executemany("INSERT INTO stage_series VALUES(?,?)", out["series"])
        connection.executemany("INSERT INTO stage_editions VALUES(?,?,?)", out["editions"])


def build(dumps=None, *, workers=None, limit=0, output=None):
    """Build and atomically publish `identify.sqlite`. `limit` caps lines per dump (for sampling); `workers=0` parses inline."""
    root = folder()
    root.mkdir(parents=True, exist_ok=True)
    source = Path(dumps) if dumps else root / "dumps"
    paths = {kind: source / f"{kind}.txt.gz" for kind in ("authors", "works", "editions", "ratings")}
    for path in paths.values():
        if not path.is_file():
            raise ValueError(f"missing Open Library dump: {path}")
    workers = max(1, (multiprocessing.cpu_count() or 2) - 2) if workers is None else workers
    published = Path(output) if output else root / "identify.sqlite"
    with (root / "identify-build.lock").open("a+b") as lock, oslib.file_lock(lock):
        target = published.with_name(published.name + ".building")
        target.unlink(missing_ok=True)
        connection = sqlite3.connect(target)
        pool = multiprocessing.get_context(start_method()).Pool(workers) if workers else None
        started = time.monotonic()
        counts = {}
        try:
            connection.executescript(SCHEMA)
            for kind in ("authors", "works", "editions"):
                done = 0
                _progress("reading_" + kind, records=0)
                for out in _stream(paths[kind], kind, limit, pool, workers):
                    _insert(connection, out)
                    done += len(out["authors"] or out["works"] or out["editions"])
                    if done and done // 250000 != (done - CHUNK) // 250000:
                        _progress("reading_" + kind, records=done)
                counts[kind] = done
            _progress("ratings", records=0)
            connection.commit()
            ratings = 0
            with gzip.open(paths["ratings"], "rt", encoding="utf8") as stream:
                rows = {}
                for line in itertools.islice(stream, limit) if limit else stream:
                    fields = line.rstrip("\n").split("\t")
                    if len(fields) == 4 and fields[0].startswith("/works/") and fields[2] in ("1", "2", "3", "4", "5"):
                        total, count = rows.get(fields[0], (0, 0))
                        rows[fields[0]] = (total + int(fields[2]), count + 1)
                        ratings += 1
            with connection:
                connection.executemany(
                    "UPDATE works SET rating_total=?, ratings=? WHERE key=?", [(t, c, k) for k, (t, c) in rows.items()]
                )
            counts["ratings"] = ratings
            _progress("indexing", counts=counts)
            connection.executescript(FINISH)
            summary = dict(
                version=VERSION,
                counts=counts,
                limit=limit,
                built=datetime.now(UTC).isoformat(),
                dumps={k: p.name for k, p in paths.items()},
            )
            connection.execute("INSERT INTO metadata VALUES(?,?)", ("snapshot", json.dumps(summary)))
            connection.commit()
            if (check := connection.execute("PRAGMA quick_check").fetchone()[0]) != "ok":
                raise ValueError(f"identify index verification failed: {check}")
            connection.close()
            connection = None
            with (root / "identify-index.lock").open("a+b") as publication, oslib.file_lock(publication):
                target.replace(published)
            _progress("ready", ready=True, error="", **summary, elapsed=round(time.monotonic() - started, 1))
            return status()
        except BaseException as error:
            if connection is not None:
                connection.close()
            _progress("failed", ready=False, error=str(error))
            raise
        finally:
            if pool is not None:
                pool.terminate()
                pool.join()
