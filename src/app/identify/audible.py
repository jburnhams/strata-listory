"""Audible-first stage: look the local item up in the local Audible index (`audibleindex`), then use the found recording's author, title and ISBN to read the item a second way for the Open Library search.

The index returns possible recordings even from a title alone; the *model* decides whether one is a match, from the evidence carried here as features (author, narrator and runtime agreement or conflict, how much of the title matched). Only a *corroborated* hit (`corroborated`) adds the extra *audible* reading that steers the Open Library search, so a bare title guess never changes what is searched. Items that are not on Audible (older tape/CD recordings) have no hit, or one the evidence contradicts, and carry zero or conflicting audiobook features."""

from app.identify import audibleindex
from app.identify import textkeys as tk

FEATURES = (
    "audible_found",
    "audible_asin",
    "audible_strong",
    "audible_weak",
    "audible_title_full",
    "audible_title_main",
    "audible_title_fuzzy",
    "audible_author_match",
    "audible_author_unknown",
    "audible_author_conflict",
    "audible_narrator_match",
    "audible_narrator_conflict",
    "audible_runtime_known",
    "audible_runtime_match",
    "audible_runtime_conflict",
    "audible_runtime_delta",
    "audible_has_isbn",
    "audible_language_match",
    "audible_abridged_known",
    "audible_candidates",
    "audible_series_known",
)
_connection = {}


def connection():
    """One read-only connection per process; None while no index is built."""
    if "c" not in _connection:
        _connection["c"] = audibleindex.connect()
    return _connection["c"]


def reset():
    _connection.pop("c", None)


def lookup(item, c=None):
    """Best Audible candidate for the item, or None. Uses the item's tag/ingest runtime (`runtime_min`), narrators and ASIN when known."""
    c = c if c is not None else connection()
    if c is None:
        return None
    found = audibleindex.find(
        c,
        item.get("title", ""),
        item.get("author", ""),
        tk.people(item.get("narrator", "")),
        item.get("runtime_min"),
        item.get("asin", ""),
    )
    if not found:
        return None
    best = found[0]
    return dict(best, candidates=len(found), local_language=item.get("language", ""))


def corroborated(hit):
    """A hit worth searching Open Library with: an exact ASIN, a strong grade, or a full-title match whose narrator or runtime agrees with nothing conflicting (this is how a one-word title like *Census* with no author is resolved through its audiobook)."""
    if not hit:
        return False
    if hit["grade"] in ("asin", "strong"):
        return True
    ev = hit["evidence"]
    return (
        ev.get("title") == "full"
        and "conflict" not in (ev.get("author"), ev.get("narrators"), ev.get("runtime"))
        and "match" in (ev.get("narrators"), ev.get("runtime"))
    )


def reading(item, hit):
    """The extra reading from a corroborated recording: its canonical title and author, plus its ISBN when it has one."""
    if not corroborated(hit):
        return None
    isbn = hit.get("isbn") or ""
    return dict(
        item,
        title=hit["title"],
        author=", ".join(hit["authors"]),
        reading="audible",
        isbns=[isbn] if tk.isbn_keys(isbn) else [],
        audible_corroborated=True,
    )


def features(hit, item=None):
    """Item-level audiobook features (zeros when nothing was found)."""
    if not hit:
        return dict.fromkeys(FEATURES, 0.0)
    ev = hit["evidence"]
    language = (hit.get("language") or "").casefold()
    local = ((item or {}).get("language") or "").casefold()
    return dict(
        audible_found=1.0,
        audible_asin=float(hit["grade"] == "asin"),
        audible_strong=float(hit["grade"] == "strong"),
        audible_weak=float(hit["grade"] == "weak"),
        audible_title_full=float(ev.get("title") == "full"),
        audible_title_main=float(ev.get("title") == "main"),
        audible_title_fuzzy=float(ev.get("title") == "fuzzy"),
        audible_author_match=float(ev.get("author") == "match"),
        audible_author_unknown=float(ev.get("author") == "unknown"),
        audible_author_conflict=float(ev.get("author") == "conflict"),
        audible_narrator_match=float(ev.get("narrators") == "match"),
        audible_narrator_conflict=float(ev.get("narrators") == "conflict"),
        audible_runtime_known=float("runtime" in ev),
        audible_runtime_match=float(ev.get("runtime") == "match"),
        audible_runtime_conflict=float(ev.get("runtime") == "conflict"),
        audible_runtime_delta=min(ev.get("runtime_delta", 0.0), 1.0),
        audible_has_isbn=float(bool(tk.isbn_keys(hit.get("isbn") or ""))),
        audible_language_match=float(bool(language and local and language == local)),
        audible_abridged_known=float(hit.get("abridged") is not None),
        audible_candidates=min(hit.get("candidates", 1), 20) / 20.0,
        audible_series_known=float(bool(hit.get("series"))),
    )
