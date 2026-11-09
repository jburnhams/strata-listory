"""Offline Open Library lookups for the matching providers, served by the two published stores: `identify.sqlite` (title/author/ISBN → work, ratings; `identify.olindex`) and `detail.sqlite` (full dump records, edition → work links; `identify.oldetail`). Both are built from the monthly dumps and replaced atomically."""

import json
import re
import unicodedata

from app import book_titles
from app.identify import candidates as ident
from app.identify import oldetail, olindex
from app.ingest.observe import emit
from app.providers.base import candidate


def folder():
    return olindex.folder()


def normalise(value):
    text = "".join(c for c in unicodedata.normalize("NFKD", value.casefold()) if not unicodedata.combining(c))
    return " ".join(re.findall(r"[^\W_]+", text))


def status():
    """Readiness of the two stores (the identify index's own status plus whether the detail store is published)."""
    state = dict(olindex.status())
    state["detail"] = oldetail.path().is_file()
    return state


def year(value):
    found = re.search(r"\b(1\d{3}|20\d{2})\b", str(value or ""))
    return int(found[1]) if found else None


def keep_edition(languages):
    known = {
        value
        for value in languages
        if re.fullmatch(r"/languages/[a-z]{3}", value)
        and value not in ("/languages/und", "/languages/mul", "/languages/zxx")
    }
    return not known or "/languages/eng" in known


def isbn_keys(value):
    """Validated, equivalent ISBN-10/13 keys; never infer other format identifiers."""
    key = re.sub(r"[-\s]", "", str(value or "")).upper()
    if re.fullmatch(r"\d{9}[\dX]", key):
        if sum((10 - i) * (10 if c == "X" else int(c)) for i, c in enumerate(key)) % 11:
            return []
        base = "978" + key[:9]
        check = (-sum(int(c) * (1 if i % 2 == 0 else 3) for i, c in enumerate(base))) % 10
        return [key, base + str(check)]
    if not re.fullmatch(r"97[89]\d{10}", key) or sum(int(c) * (1 if i % 2 == 0 else 3) for i, c in enumerate(key)) % 10:
        return []
    if key.startswith("979"):
        return [key]
    base = key[3:12]
    check = (-sum((10 - i) * int(c) for i, c in enumerate(base))) % 11
    return [key, base + ("X" if check == 10 else str(check))]


def _reader():
    """Brief cross-process lock: the identify index is replaced while readers may hold it (Windows cannot replace an open SQLite file)."""
    return (folder() / "identify-index.lock").open("a+b")


def _snapshot(connection):
    row = connection.execute("SELECT value FROM metadata WHERE key='snapshot'").fetchone()
    return json.loads(row[0]) if row else {}


def _candidate(connection, detail, work, *, cover=False):
    """The provider candidate for one work: identify row for the facts, detail record for the cover."""
    row = connection.execute("SELECT * FROM works WHERE key=?", (work,)).fetchone()
    if row is None:
        return None
    authors = [
        r[0]
        for r in connection.execute(
            "SELECT a.name FROM work_authors w JOIN authors a ON a.key=w.author WHERE w.work=?", (work,)
        )
    ]
    count = row["ratings"]
    covers = []
    if cover and detail is not None and (record := detail.get(work)):
        covers = [v for v in record.get("covers") or [] if isinstance(v, int) and v > 0]
    return candidate(
        "openlibrary",
        id=work,
        title=row["title"],
        authors=authors,
        original_year=row["year"],
        genres=json.loads(row["subjects"] or "[]"),
        cover_url=f"https://covers.openlibrary.org/b/id/{covers[0]}-L.jpg" if covers else "",
        rating=row["rating_total"] / count if count else None,
        ratings_count=count if count else None,
        url="https://openlibrary.org" + work,
    )


def work_candidates(keys):
    """Exact linked work lookups; ISBN evidence must not depend on title search ranking."""
    keys = list(dict.fromkeys(k for k in keys if re.fullmatch(r"/works/OL\d+W", k or "")))[:300]
    if not keys or not ident.path().is_file():
        return []
    with _reader():
        c = ident.connect()
        try:
            mapped = ident.canonical(c, keys)
            result = [x for key in dict.fromkeys(mapped[k] for k in keys) if (x := _candidate(c, None, key))]
            emit(
                source="Open Library offline linked works",
                query=dict(works=keys),
                cached=True,
                raw=dict(candidates=result, snapshot=_snapshot(c)),
            )
            return result
        finally:
            c.close()


def _edition(record, work_key):
    works = [w["key"] for w in record.get("works") or [] if isinstance(w, dict) and w.get("key")]
    languages = [dict(key=v["key"]) for v in record.get("languages") or [] if isinstance(v, dict) and v.get("key")]
    isbns = list(dict.fromkeys(str(v) for field in ("isbn_10", "isbn_13") for v in record.get(field) or []))
    return dict(
        key=record["key"],
        title=record.get("title") or "",
        subtitle=record.get("subtitle") or "",
        work_key=work_key or (works[0] if len(works) == 1 else None),
        works=works,
        year=year(record.get("publish_date")),
        publish_date=record.get("publish_date") or "",
        publishers=[str(v) for v in record.get("publishers") or []],
        languages=languages,
        physical_format=record.get("physical_format") or "",
        isbns=isbns,
    )


def editions(work_key=None, *, isbn=None, limit=300):
    """Bounded edition facts, with work links and snapshot provenance. None means unavailable."""
    detail = oldetail.connect()
    if detail is None or not ident.path().is_file():
        return None
    if work_key is not None and not re.fullmatch(r"/works/OL\d+W", work_key):
        return []
    if isbn is not None:
        isbn = re.sub(r"[-\s]", "", str(isbn)).upper()
        if not re.fullmatch(r"\d{9}[\dX]|\d{13}", isbn):
            return []
    if work_key is None and isbn is None:
        return []
    limit = max(1, min(limit, 300))
    with _reader():
        c = ident.connect()
        try:
            wanted = set(isbn_keys(isbn) or [isbn]) if isbn is not None else None
            works = (
                [work_key]
                if work_key is not None
                else [
                    r[0]
                    for r in c.execute(
                        f"SELECT DISTINCT work FROM work_isbns WHERE isbn IN ({','.join('?' for _ in wanted)})",
                        list(wanted),
                    )
                ][:20]
            )
            records = []
            for work in works:
                for (edition,) in detail.c.execute("SELECT edition FROM edition_work WHERE work=? LIMIT 2000", (work,)):
                    if (record := detail.get(edition)) is None:
                        continue
                    if wanted is not None and not wanted & {
                        str(v) for field in ("isbn_10", "isbn_13") for v in record.get(field) or []
                    }:
                        continue
                    records.append(record)
            records = list({r["key"]: r for r in records}.values())
            result = [_edition(r, work_key) for r in records]
            result.sort(key=lambda e: (e["year"] is None, e["year"] or 0, e["key"]))
            result = result[:limit]
            emit(
                source="Open Library offline editions",
                query=dict(work=work_key, isbn=isbn),
                cached=True,
                raw=dict(editions=result, snapshot=_snapshot(c)),
            )
            return result
        finally:
            c.close()
            detail.close()


def search(title, author=""):
    if not ident.path().is_file():
        return []
    with _reader():
        return _search(title, author)


def _search(title, author=""):
    normal = normalise(title)
    if not normal:
        return []
    connection = ident.connect()
    detail = oldetail.connect()
    try:
        found = ident.find(connection, title, author)
        main = normalise(book_titles.parts(title)[0])
        variants = list(dict.fromkeys([normal, normal.replace(" ", ""), main, main.replace(" ", "")]))
        results = [x for work in found if (x := _candidate(connection, detail, work))]
        # Provider acceptance still uses the app's full title/author identity checks.
        from app import matcher

        book = dict(title=title, author=author)

        def author_volume(c):
            title = normalise(c["title"])
            return any(
                re.fullmatch(re.escape(normalise(person)) + r"\s+(?:[ivxlcdm]+|\d+)", title) for person in c["authors"]
            )

        results = sorted(
            results,
            key=lambda c: (
                normalise(c["title"]) == normal,
                normalise(c["title"]) in variants,
                author_volume(c),
                matcher.score(book, c),
                c.get("ratings_count") or 0,
                bool(c.get("original_year")),
            ),
            reverse=True,
        )[:5]
        results = [_candidate(connection, detail, c["id"], cover=True) for c in results]
        emit(
            source="Open Library offline",
            query=dict(title=title, author=author),
            cached=True,
            raw=dict(candidates=results, snapshot=_snapshot(connection)),
        )
        return results
    finally:
        connection.close()
        if detail is not None:
            detail.close()
