"""Comparison vectors: for one (reading of a listen, work cluster) pair, a discrete agreement level per field, in the style of Fellegi-Sunter record linkage. A level of `None` means the field cannot be compared (one side does not know it) and contributes nothing, so a missing author is not evidence against a match, while a different author is.

Levels are deliberately coarse and few: the model learns from the data how often each level happens among true matches (m) and among other candidates (u), so no level is a rule. Fields: title (crossed with how common the title is), author, narrator, runtime, asin, year, numbers in the title, which sources know the work, and popularity."""

from __future__ import annotations

import difflib
import re

from app.identify import textkeys as tk
from app.linkage.candidates import Cluster, _same_person, main_key, real, shares_author
from app.linkage.query import Query, Reading

FIELDS = (
    "title",
    "author",
    "narrator",
    "runtime",
    "asin",
    "year",
    "numbers",
    "sources",
    "popularity",
    "channel",
    "form",
)
LEVELS = {
    "title": ("exact_rare", "exact_common", "exact_generic", "main", "close", "partial", "weak"),
    "author": ("all", "some", "surname", "conflict"),
    "narrator": ("match", "conflict"),
    "runtime": ("1pct", "4pct", "12pct", "30pct", "far"),
    "asin": ("match", "other"),
    "year": ("same", "close", "later", "earlier"),
    "numbers": ("agree", "local_only", "other_only", "differ"),
    "sources": ("both", "radio", "openlibrary", "audible"),
    "popularity": ("high", "medium", "low", "none"),
    "channel": (
        "match",
        "conflict",
    ),  # radio programmes only: the listen's tags and names point to the programme's BBC service, or to another
    "form": (
        "agree",
        "neutral",
        "conflict",
    ),  # the kind classifier's call for the listen against the candidate's type (a programme or a book): the prior that keeps books from radio candidates and radio from book ones
}


def _ratio(a: str, b: str) -> float:
    return difflib.SequenceMatcher(None, a, b).ratio() if a and b else 0.0


def _numbers(text: str) -> set[str]:
    return {str(int(n)) for n in re.findall(r"\d+", text or "") if len(n) < 4}  # years are not volume numbers


def title_level(reading: Reading, titles: dict[str, int], homonyms: int) -> tuple[str | None, str]:
    """(level, best matching alias) of the reading's title against every title key of the cluster. `homonyms` = how many different works carry exactly this title."""
    full = tk.title_key(reading.title)
    main = main_key(reading.title)
    if not full or not titles:
        return None, ""
    if full in titles or full.replace(" ", "") in {t.replace(" ", "") for t in titles}:
        return ("exact_rare" if homonyms <= 2 else "exact_common" if homonyms <= 15 else "exact_generic"), full
    mains = {main_key(t): t for t in titles}
    if main in titles or main in mains or full in mains:
        return "main", mains.get(main) or mains.get(full) or main
    best, alias = max(((_ratio(full, t), t) for t in titles), default=(0.0, ""))
    best_main, alias_main = max(((_ratio(main, m), t) for m, t in mains.items()), default=(0.0, ""))
    if best_main > best:
        best, alias = best_main, alias_main
    if best >= 0.9:
        return "close", alias
    words = set(main.split())
    overlap = max((len(words & set(t.split())) / max(len(words), 1) for t in mains), default=0.0)
    if best >= 0.75 or (overlap >= 0.8 and len(words) >= 2):
        return "partial", alias
    return "weak", alias


def author_level(reading: Reading, cluster: Cluster) -> str | None:
    wanted = tk.people(reading.author)
    have = real(cluster.authors)
    if not wanted or not have:
        return None
    exact = [w for w in wanted if any(set(tk.person_variants(w)) & set(tk.person_variants(h)) for h in have)]
    if len(exact) == len(wanted):
        return "all"
    if exact:
        return "some"
    if shares_author(wanted, have) or any(tk.surname(w) == tk.surname(h) for w in wanted for h in have):
        return "surname"
    return "conflict"


def narrator_level(q: Query, cluster: Cluster) -> str | None:
    have = [n for p in cluster.products for n in p.narrators]
    if not have:
        return None
    if q.hints and any(_same_person(h, n) for h in q.hints for n in have):
        return "match"
    if not q.narrators:
        return None
    return "match" if any(_same_person(w, n) for w in q.narrators for n in have) else "conflict"


def runtime_level(q: Query, cluster: Cluster) -> tuple[str | None, str]:
    """Closest Audible recording to any copy of the listen: (level, its ASIN)."""
    pairs = [(abs(p.runtime - m) / max(m, 1.0), p.asin) for p in cluster.products if p.runtime for m in q.minutes if m]
    if not pairs:
        return None, ""
    d, asin = min(pairs)
    return (
        "1pct" if d <= 0.015 else "4pct" if d <= 0.04 else "12pct" if d <= 0.12 else "30pct" if d <= 0.3 else "far"
    ), asin


def year_level(q: Query, cluster: Cluster) -> str | None:
    """The listen's year (often the recording's) against the work's first year: a recording can be much later than the book, but not earlier."""
    y = cluster.year
    if not q.year or not y:
        return None
    if abs(q.year - y) <= 1:
        return "same"
    if abs(q.year - y) <= 5:
        return "close"
    return "later" if q.year > y else "earlier"


def numbers_level(reading: Reading, alias: str) -> str | None:
    a, b = _numbers(tk.title_key(reading.title)), _numbers(alias)
    if not a and not b:
        return None
    if a == b:
        return "agree"
    if not b:
        return "local_only"
    if not a:
        return "other_only"
    return "agree" if a & b else "differ"


def popularity_level(cluster: Cluster) -> str:
    editions = sum(w["editions"] for w in cluster.works)
    ratings = sum(p.ratings for p in cluster.products)
    if editions >= 20 or ratings >= 1000:
        return "high"
    if editions >= 4 or ratings >= 50:
        return "medium"
    if editions or ratings or cluster.products:
        return "low"
    return "none"


def channel_level(q: Query, cluster: Cluster) -> str | None:
    services = {p.service or p.detail.get("service") for p in cluster.programmes} - {None, ""}
    if not cluster.programmes or not q.channels or not services:
        return None
    return (
        "match" if any(svc.startswith(c) or c.startswith(svc) for svc in services for c in q.channels) else "conflict"
    )


def form_level(q: Query, cluster: Cluster) -> str | None:
    """Is the candidate the right *kind* of thing for this listen? Unknown when the classifier has not run."""
    if not q.band:
        return None
    if q.band == "review":
        return "neutral"
    broadcast = q.band == "broadcast"
    radio = bool(cluster.programmes) and not cluster.works and not cluster.products
    return "agree" if broadcast == radio else "conflict"


def compare(q: Query, reading: Reading, cluster: Cluster, homonyms: int) -> dict:
    """{field: level or None} plus `_alias` (best matching title) and `_asin` (closest recording) for display."""
    title, alias = title_level(reading, cluster.title_keys(), homonyms)
    runtime, asin = runtime_level(q, cluster)
    asins = {p.asin for p in cluster.products}
    return dict(
        title=title,
        author=author_level(reading, cluster),
        narrator=narrator_level(q, cluster),
        runtime=runtime,
        asin=None if not q.asin else "match" if q.asin in asins else "other" if cluster.products else None,
        year=year_level(q, cluster),
        numbers=numbers_level(reading, alias),
        sources="radio"
        if cluster.programmes and not cluster.works and not cluster.products
        else "both"
        if cluster.works and cluster.products
        else "openlibrary"
        if cluster.works
        else "audible",
        popularity=popularity_level(cluster),
        channel=channel_level(q, cluster),
        form=form_level(q, cluster),
        _alias=alias,
        _asin=q.asin if q.asin in asins else asin,
        _reading=reading.source,
    )
