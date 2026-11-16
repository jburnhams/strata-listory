"""Where ratings, reviews, and synopses come from. Each source takes what the listen is known to be (`Context`) and returns normalized `Result` items plus a status dict, never raising."""

import html
import json
import os
import re
import sqlite3
import urllib.parse
import zlib
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any

from app import details, matcher, settings
from app.covers import net
from app.covers.sources import MAX_ASINS, MAX_ISBNS, failed_note, google_key

AUDIBLE_GROUPS = "rating,product_desc,reviews,review_attrs,product_attrs"


@dataclass
class Context:
    """What is known about the listen's identified book: its Audible ASINs, ISBNs, Open Library work keys, title/author, and folder."""

    title: str = ""
    author: str = ""
    asins: list[str] = field(default_factory=list)
    isbns: list[str] = field(default_factory=list)
    ol_works: list[str] = field(default_factory=list)
    folder: str = ""
    local_audible: set[str] = field(default_factory=set)  # ASINs already satisfied by local Audible archive
    local_ol_rating: bool = False
    local_ol_synopsis: bool = False


@dataclass
class Result:
    source: str
    ref: str = ""
    url: str | None = None
    rating: float | None = None
    ratings_count: int | None = None
    reviews_count: int | None = None
    synopsis: str | None = None
    reviews: list[dict[str, Any]] = field(default_factory=list)
    extra: dict[str, Any] = field(default_factory=dict)

    @property
    def has_data(self) -> bool:
        return bool(
            self.rating is not None
            or self.ratings_count is not None
            or (self.synopsis and self.synopsis.strip())
            or self.reviews
            or any(self.extra.values())
        )


def clean_text(raw: Any) -> str:
    """Plain text from HTML or Open Library `{type, value}` description dicts."""
    if isinstance(raw, dict):
        raw = raw.get("value") or ""
    if not isinstance(raw, str) or not raw.strip():
        return ""
    text = re.sub(r"<br\s*/?>", "\n", raw, flags=re.I)
    text = re.sub(r"</(?:p|div|li|blockquote)\s*>", "\n\n", text, flags=re.I)
    text = re.sub(r"<[^>]+>", "", text)
    text = html.unescape(text)
    lines = [" ".join(line.split()) for line in text.splitlines()]
    return re.sub(r"\n{3,}", "\n\n", "\n".join(lines)).strip()


def record(source: str, items: list[Result], notes: list[str], *, skipped: str = "") -> dict[str, Any]:
    if skipped:
        return dict(source=source, status="skipped", detail=skipped, count=0)
    if items:
        return dict(source=source, status="found", detail="; ".join(notes[:3]), count=len(items))
    if notes and any(n.startswith("rate limited") for n in notes):
        return dict(source=source, status="limited", detail="; ".join(notes[:4]), count=0)
    errors = [n for n in notes if n.startswith("error")]
    return dict(
        source=source,
        status="error" if errors and len(errors) == len(notes) else "none",
        detail="; ".join(notes[:4]),
        count=0,
    )


def _audible_reviews(raw_reviews: list[dict] | None) -> list[dict[str, Any]]:
    out = []
    for r in (raw_reviews or [])[:8]:
        body = clean_text(r.get("body") or r.get("review_text") or "")
        if not body:
            continue
        scores = r.get("ratings") or {}
        overall = scores.get("overall_rating") or r.get("overall_rating")
        out.append(
            dict(
                user=r.get("author_display_name") or r.get("customer_id") or "Audible listener",
                rating=float(overall) if overall is not None else None,
                title=clean_text(r.get("title") or ""),
                text=body[:3000],
                date=(r.get("submission_date") or "")[:10],
                likes=int(r.get("helpful_votes") or 0),
            )
        )
    return out


def _audible_result(source: str, asin: str, region: str, p: dict) -> Result:
    rating_obj = p.get("rating") or {}
    overall = rating_obj.get("overall_distribution") or {}
    avg = overall.get("average_rating") or overall.get("display_average_rating")
    cnt = overall.get("num_ratings")
    synopsis = clean_text(p.get("publisher_summary") or p.get("merchandising_summary") or "")
    reviews = _audible_reviews(p.get("customer_reviews"))
    domain = "co.uk" if region == "uk" else "com"
    extra: dict[str, Any] = {}
    if rating_obj:
        extra["rating_details"] = {
            k: rating_obj[k]
            for k in ("overall_distribution", "performance_distribution", "story_distribution")
            if k in rating_obj
        }
    return Result(
        source=source,
        ref=asin,
        url=f"https://www.audible.{domain}/pd/{asin}",
        rating=round(float(avg), 2) if avg is not None and cnt else None,
        ratings_count=int(cnt) if cnt else None,
        reviews_count=len(reviews) if reviews else None,
        synopsis=synopsis or None,
        reviews=reviews,
        extra=extra,
    )


# ---- local sources (no network)


def openlibrary_local(ctx: Context) -> tuple[list[Result], dict[str, Any]]:
    """Ratings from `data/openlibrary/identify.sqlite` and synopsis from `data/openlibrary/detail.sqlite`."""
    from app.identify import candidates as ident
    from app.identify import oldetail

    if not ctx.ol_works:
        return [], record("openlibrary_local", [], [], skipped="no Open Library work known")
    has_ident = ident.path().is_file()
    has_detail = oldetail.path().is_file()
    if not has_ident and not has_detail:
        return [], record("openlibrary_local", [], [], skipped="no local Open Library stores")

    items: list[Result] = []
    notes: list[str] = []
    c = det = None
    try:
        c = ident.connect() if has_ident else None
        det = oldetail.connect() if has_detail else None
        keys = [k for k in ctx.ol_works if re.fullmatch(r"/works/OL\d+W", k)]
        mapped = ident.canonical(c, keys) if c and keys else {k: k for k in keys}
        seen: set[str] = set()
        for orig in keys[:3]:
            key = mapped.get(orig, orig)
            if key in seen:
                continue
            seen.add(key)
            rating = ratings_count = None
            synopsis = ""
            if c is not None:
                row = c.execute("SELECT ratings, rating_total FROM works WHERE key=?", (key,)).fetchone()
                if row and row["ratings"]:
                    ratings_count = int(row["ratings"])
                    rating = round(float(row["rating_total"]) / ratings_count, 2)
            if det is not None:
                doc = det.get(key)
                if doc:
                    synopsis = clean_text(doc.get("description"))
            res = Result(
                source="openlibrary_local",
                ref=key,
                url=f"https://openlibrary.org{key}",
                rating=rating,
                ratings_count=ratings_count,
                synopsis=synopsis or None,
            )
            if res.has_data:
                items.append(res)
                if rating is not None:
                    ctx.local_ol_rating = True
                if synopsis:
                    ctx.local_ol_synopsis = True
            else:
                notes.append(f"{key}: no rating or description locally")
    except (OSError, sqlite3.Error) as e:
        notes.append(f"error: local Open Library unreadable ({e})")
    finally:
        if c is not None:
            c.close()
        if det is not None:
            det.close()
    return items, record("openlibrary_local", items, notes)


def audible_local(ctx: Context, *, archive: str | None = None) -> tuple[list[Result], dict[str, Any]]:
    """Ratings, synopsis, and any stored reviews from the local Audible archive (`data/audible/audible.sqlite`)."""
    from app.identify import audibleindex

    if not ctx.asins:
        return [], record("audible_local", [], [], skipped="no ASIN known")
    path = archive or audibleindex.archive_path()
    if not os.path.exists(path):
        return [], record("audible_local", [], [], skipped="no local Audible archive")
    items: list[Result] = []
    notes: list[str] = []
    try:
        db = sqlite3.connect(f"file:{path}?mode=ro", uri=True, timeout=5)
        try:
            for asin in ctx.asins[:MAX_ASINS]:
                res = None
                for region, value in db.execute(
                    "SELECT region, raw FROM product WHERE asin=? ORDER BY region='uk' DESC", (asin,)
                ):
                    try:
                        p = audibleindex.raw(value)
                        p = p.get("product", p)
                    except (ValueError, zlib.error, TypeError):
                        continue
                    cand = _audible_result("audible_local", asin, region, p)
                    if cand.has_data:
                        res = cand
                        break
                if res:
                    items.append(res)
                    if res.rating is not None and res.synopsis and res.reviews:
                        ctx.local_audible.add(asin)
                else:
                    notes.append(f"{asin}: not in local archive")
        finally:
            db.close()
    except sqlite3.Error as e:
        notes.append(f"error: archive unreadable ({e})")
    return items, record("audible_local", items, notes)


def sidecar(ctx: Context) -> tuple[list[Result], dict[str, Any]]:
    """Summary and rating from an Audible `*.metadata.json` sidecar in the listen's folder."""
    if not ctx.folder:
        return [], record("sidecar", [], [], skipped="no folder known")
    if not os.path.exists(ctx.folder):
        return [], record("sidecar", [], [], skipped="folder not on this machine")
    raw = details.sidecar(ctx.folder)
    if not raw:
        return [], record("sidecar", [], ["no *.metadata.json sidecar"])
    s = details.audible_summary(raw)
    res = Result(
        source="sidecar",
        ref=s.get("asin") or os.path.basename(ctx.folder),
        rating=float(s["rating"]) if s.get("rating") is not None and s.get("rating_count") else None,
        ratings_count=int(s["rating_count"]) if s.get("rating_count") else None,
        synopsis=clean_text(s.get("summary")) or None,
    )
    if res.has_data:
        return [res], record("sidecar", [res], [])
    return [], record("sidecar", [], ["sidecar has no summary or rating"])


# ---- online sources


def hardcover(ctx: Context) -> tuple[list[Result], dict[str, Any]]:
    """Ratings, synopsis, tags/genres/moods/warnings, and top public reviews from Hardcover."""
    from app.providers import hardcover as hc

    token = hc.key()
    if not token:
        return [], record("hardcover", [], [], skipped="no Hardcover API key")
    if not ctx.asins and not ctx.title:
        return [], record("hardcover", [], [], skipped="no ASIN or title known")

    headers = {"Authorization": f"Bearer {token}", "Content-Type": "application/json"}

    def gql(query: str, variables: dict[str, Any]) -> tuple[dict | None, str | None]:
        body = json.dumps(dict(query=query, variables=variables)).encode("utf8")
        r = net.request(hc.API, method="POST", headers=headers, body=body)
        if not r.ok:
            return None, failed_note("graphql", r)
        try:
            payload = r.json()
        except ValueError:
            return None, "error: unreadable Hardcover response"
        if payload.get("errors"):
            msg = str(payload["errors"][0].get("message", payload["errors"]))[:160]
            return None, f"error: {msg}"
        return payload.get("data") or {}, None

    book_id: int | None = None
    notes: list[str] = []
    for asin in ctx.asins[:3]:
        data, err = gql(hc.BY_ASIN, dict(a=asin))
        if err:
            notes.append(err)
            if err.startswith("rate limited"):
                return [], record("hardcover", [], notes)
            continue
        eds = (data or {}).get("editions") or []
        if eds and (eds[0].get("book") or {}).get("id"):
            book_id = int(eds[0]["book"]["id"])
            break

    if book_id is None and ctx.title:
        q = f"{ctx.title} {ctx.author}".strip()[:120]
        data, err = gql(hc.SEARCH, dict(q=q, n=8))
        if err:
            notes.append(err)
            return [], record("hardcover", [], notes)
        hits = ((data or {}).get("search") or {}).get("results") or {}
        book_ref = dict(title=ctx.title, author=ctx.author)
        scored = sorted(
            (
                (matcher.score(book_ref, hc.hit_to_candidate(h["document"])), h["document"])
                for h in hits.get("hits", [])
            ),
            key=lambda x: -x[0],
        )
        if scored and scored[0][0] >= matcher.GOOD_SCORE and scored[0][1].get("id"):
            book_id = int(scored[0][1]["id"])

    if book_id is None:
        return [], record("hardcover", [], notes or ["book not matched on Hardcover"])

    data, err = gql(hc.DETAILS, dict(id=book_id))
    if err:
        notes.append(err)
        return [], record("hardcover", [], notes)
    books = (data or {}).get("books") or []
    if not books:
        return [], record("hardcover", [], ["book id returned no details"])
    b = books[0]
    ct = b.get("cached_tags")
    reviews = [
        dict(
            user=(r.get("user") or {}).get("username") or "Hardcover reader",
            rating=r.get("rating"),
            text=(r.get("review_raw") or "")[:3000],
            spoilers=bool(r.get("review_has_spoilers")),
            likes=int(r.get("likes_count") or 0),
            date=(r.get("reviewed_at") or "")[:10],
        )
        for r in (data or {}).get("user_books") or []
        if (r.get("review_raw") or "").strip()
    ]
    res = Result(
        source="hardcover",
        ref=str(b["id"]),
        url=f"https://hardcover.app/books/{b['slug']}" if b.get("slug") else "https://hardcover.app",
        rating=round(float(b["rating"]), 2) if b.get("rating") is not None else None,
        ratings_count=int(b["ratings_count"]) if b.get("ratings_count") is not None else None,
        reviews_count=int(b["reviews_count"]) if b.get("reviews_count") is not None else None,
        synopsis=clean_text(b.get("description")) or None,
        reviews=reviews,
        extra={
            k: v
            for k, v in dict(
                genres=hc._tags(ct, "Genre"),
                moods=hc._tags(ct, "Mood"),
                tags=hc._tags(ct, "Tag")[:12],
                content_warnings=hc._tags(ct, "Content Warning", spoilers=True),
                readers=b.get("users_count"),
                finished=b.get("users_read_count"),
                lists=b.get("lists_count"),
                pages=b.get("pages"),
            ).items()
            if v not in (None, [], {})
        },
    )
    if res.has_data:
        return [res], record("hardcover", [res], [])
    return [], record("hardcover", [], ["Hardcover record had no ratings, reviews, or description"])


def audible_online(ctx: Context) -> tuple[list[Result], dict[str, Any]]:
    """Audible's catalogue API for ASINs missing or incomplete in the local archive."""
    from app.providers.audible import HOSTS

    if not ctx.asins:
        return [], record("audible", [], [], skipped="no ASIN known")
    to_ask = [a for a in ctx.asins[:3] if a not in ctx.local_audible]
    if not to_ask:
        return [], record("audible", [], [], skipped="already covered by local Audible archive")
    region = settings.audible_region()
    host = HOSTS.get(region, HOSTS["uk"])
    items: list[Result] = []
    notes: list[str] = []
    for asin in to_ask:
        url = f"https://{host}/1.0/catalog/products/{urllib.parse.quote(asin)}?" + urllib.parse.urlencode(
            dict(response_groups=AUDIBLE_GROUPS)
        )
        r = net.get(url)
        if r.status == 404:
            notes.append(f"{asin}: not on Audible ({region})")
            continue
        if not r.ok:
            notes.append(failed_note(asin, r))
            if r.limited:
                break
            continue
        try:
            p = r.json().get("product") or {}
        except ValueError:
            notes.append(f"error: {asin}: unreadable answer")
            continue
        res = _audible_result("audible", asin, region, p)
        if res.has_data:
            items.append(res)
        else:
            notes.append(f"{asin}: no rating, summary, or reviews")
    return items, record("audible", items, notes)


def audnexus(ctx: Context) -> tuple[list[Result], dict[str, Any]]:
    """Synopsis and genres from Audnexus (`api.audnex.us/books/{asin}`)."""
    if not ctx.asins:
        return [], record("audnexus", [], [], skipped="no ASIN known")
    region = settings.audible_region()
    items: list[Result] = []
    notes: list[str] = []
    for asin in ctx.asins[:3]:
        r = net.get(f"https://api.audnex.us/books/{urllib.parse.quote(asin)}?region={region}")
        if r.status == 404:
            notes.append(f"{asin}: not on Audnexus")
            continue
        if not r.ok:
            notes.append(failed_note(asin, r))
            if r.limited:
                break
            continue
        try:
            d = r.json()
        except ValueError:
            notes.append(f"error: {asin}: unreadable answer")
            continue
        synopsis = clean_text(d.get("summary") or d.get("description") or "")
        genres = [g["name"] for g in d.get("genres") or [] if g.get("type") == "genre" and g.get("name")]
        res = Result(
            source="audnexus",
            ref=asin,
            synopsis=synopsis or None,
            extra=dict(genres=genres) if genres else {},
        )
        if res.has_data:
            items.append(res)
            break
        notes.append(f"{asin}: no summary")
    return items, record("audnexus", items, notes)


def openlibrary_online(ctx: Context) -> tuple[list[Result], dict[str, Any]]:
    """Fallback to Open Library's web API when local stores did not have both rating and description."""
    if not ctx.ol_works:
        return [], record("openlibrary", [], [], skipped="no Open Library work known")
    if ctx.local_ol_rating and ctx.local_ol_synopsis:
        return [], record("openlibrary", [], [], skipped="already covered by local Open Library stores")
    m = re.search(r"OL\d+W", ctx.ol_works[0])
    if not m:
        return [], record("openlibrary", [], [], skipped="no valid Open Library work id")
    wid = m.group()
    key = f"/works/{wid}"
    notes: list[str] = []
    synopsis = ""
    rating = ratings_count = None
    if not ctx.local_ol_synopsis:
        r = net.get(f"https://openlibrary.org{key}.json")
        if r.ok:
            try:
                synopsis = clean_text(r.json().get("description"))
            except ValueError:
                notes.append("error: unreadable work JSON")
        elif r.status != 404:
            notes.append(failed_note("work", r))
    if not ctx.local_ol_rating:
        r = net.get(f"https://openlibrary.org{key}/ratings.json")
        if r.ok:
            try:
                summary = r.json().get("summary") or {}
                if summary.get("count"):
                    ratings_count = int(summary["count"])
                    rating = round(float(summary["average"]), 2) if summary.get("average") is not None else None
            except (ValueError, TypeError):
                notes.append("error: unreadable ratings JSON")
        elif r.status != 404:
            notes.append(failed_note("ratings", r))
    res = Result(
        source="openlibrary",
        ref=key,
        url=f"https://openlibrary.org{key}",
        rating=rating,
        ratings_count=ratings_count,
        synopsis=synopsis or None,
    )
    if res.has_data:
        return [res], record("openlibrary", [res], notes)
    return [], record("openlibrary", [], notes or [f"{wid}: no rating or description"])


def googlebooks(ctx: Context) -> tuple[list[Result], dict[str, Any]]:
    """Synopsis and ratings from Google Books by ISBN."""
    if not ctx.isbns:
        return [], record("googlebooks", [], [], skipped="no ISBN known")
    key = google_key() or os.environ.get("APP_GOOGLE_BOOKS_KEY", "").strip()
    items: list[Result] = []
    notes: list[str] = []
    for isbn in ctx.isbns[: min(MAX_ISBNS, 3)]:
        url = f"https://www.googleapis.com/books/v1/volumes?q=isbn:{urllib.parse.quote(isbn)}&maxResults=2" + (
            f"&key={urllib.parse.quote(key)}" if key else ""
        )
        r = net.get(url)
        if not r.ok:
            notes.append(failed_note(isbn, r))
            if r.limited:
                break
            continue
        try:
            vols = r.json().get("items") or []
        except ValueError:
            notes.append(f"error: {isbn}: unreadable answer")
            continue
        for v in vols:
            info = v.get("volumeInfo") or {}
            res = Result(
                source="googlebooks",
                ref=isbn,
                url=info.get("infoLink") or None,
                rating=float(info["averageRating"]) if info.get("averageRating") is not None else None,
                ratings_count=int(info["ratingsCount"]) if info.get("ratingsCount") is not None else None,
                synopsis=clean_text(info.get("description")) or None,
                extra=dict(genres=info["categories"]) if info.get("categories") else {},
            )
            if res.has_data:
                items.append(res)
                break
        if items:
            break
        notes.append(f"{isbn}: no description or rating")
    return items, record("googlebooks", items, notes)


def applebooks(ctx: Context) -> tuple[list[Result], dict[str, Any]]:
    """Audiobook synopsis from Apple Books for results whose title matches."""
    if not ctx.title:
        return [], record("applebooks", [], [], skipped="no title known")
    from app.identify import textkeys as tk

    term = urllib.parse.quote(f"{ctx.title} {ctx.author}".strip())
    r = net.get(f"https://itunes.apple.com/search?term={term}&media=audiobook&entity=audiobook&limit=8")
    if not r.ok:
        return [], record("applebooks", [], [failed_note("search", r)])
    try:
        results = r.json().get("results") or []
    except ValueError:
        return [], record("applebooks", [], ["error: unreadable answer"])
    want = tk.title_key(ctx.title)
    for x in results:
        got = x.get("collectionName") or x.get("trackName") or ""
        key = tk.title_key(got)
        if want and key and (want == key or want in key or key in want):
            synopsis = clean_text(x.get("description"))
            if synopsis:
                res = Result(
                    source="applebooks",
                    ref=str(x.get("collectionId") or ""),
                    url=x.get("collectionViewUrl") or None,
                    synopsis=synopsis,
                )
                return [res], record("applebooks", [res], [])
    return [], record("applebooks", [], [f"{len(results)} results, none with a matching title and description"])


SOURCES: list[tuple[str, Callable[[Context], tuple[list[Result], dict[str, Any]]]]] = [
    ("openlibrary_local", openlibrary_local),
    ("audible_local", audible_local),
    ("sidecar", sidecar),
    ("hardcover", hardcover),
    ("audible", audible_online),
    ("audnexus", audnexus),
    ("openlibrary", openlibrary_online),
    ("googlebooks", googlebooks),
    ("applebooks", applebooks),
]
