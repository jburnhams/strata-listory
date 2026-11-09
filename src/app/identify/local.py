"""Alternative readings of a local title/author. File and folder names pack author, title, disc and track numbers into one string; rather than guess which parse is right, every plausible reading is searched and scored, and the model sees which reading matched (`reading_*` features)."""

import re

from app.identify import textkeys as tk

TRAILING_NUMBERS = re.compile(
    r"(?:[\s_.\-–]*(?:cd|disc|disk|part|pt|chapter|chap|ch|track|trk|episode|ep)?[\s_.\-–]*\d{1,3}(?:\s*(?:of|/)\s*\d{1,3})?)+\s*$",
    re.I,
)
SEPARATOR = re.compile(r"\s+[-–—]\s+")
READINGS = ("raw", "stripped", "author_title", "title_author")


def strip_numbers(title):
    """`The Red Dragon CD01 07` → `The Red Dragon`; empty if nothing but digits would remain."""
    out = TRAILING_NUMBERS.sub("", str(title or "")).strip(" _.-–")
    return out if tk.title_key(out) and re.search(r"[^\W\d_]", out) else ""


def strip_parts(title):
    """Like `strip_numbers`, but only for disc/track/part markers (`CD01 07`, `Part 3`, `13 of 16`, two trailing numbers); a bare trailing number (`Discworld 3`) is a volume and stays."""
    match = TRAILING_NUMBERS.search(str(title or ""))
    if match and (re.search(r"[a-z]", match[0], re.I) or len(re.findall(r"\d+", match[0])) >= 2):
        return strip_numbers(title)
    return str(title or "").strip()


def readings(item):
    """[item with a 'reading' name], raw first; duplicates (same title/author key) are dropped."""
    title = str(item.get("title") or "")
    author = ", ".join(tk.people(item.get("author", "")))
    base = dict(item, author=author)
    out = [dict(base, reading="raw")]
    seen = {(tk.title_key(title), tk.person_key(author))}

    def add(name, t, a):
        key = (tk.title_key(t), tk.person_key(a))
        if tk.title_key(t) and key not in seen:
            seen.add(key)
            out.append(dict(base, title=t, author=a, reading=name))

    stripped = strip_numbers(title)
    if stripped:
        add("stripped", stripped, author)
    for source in dict.fromkeys(t for t in (stripped, title) if t):
        parts = SEPARATOR.split(source, maxsplit=1)
        if len(parts) == 2:
            left, right = (p.strip() for p in parts)
            add("author_title", right, left if not author or tk.person_key(left) else author)
            if not author:
                add("title_author", left, right)
    return out
