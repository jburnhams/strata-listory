"""The single definition of how titles, people, ISBNs and ASINs are normalised for identification.

Index build and queries both use these functions, so a key written into the index is the key a query looks up. Keys only help *find* candidates and are features for the model; none of them is an acceptance rule."""

import re
import unicodedata

HONORIFICS = {"mr", "mrs", "ms", "miss", "dr", "doctor", "prof", "professor", "sir", "dame", "rev", "reverend"}
ARTICLES = {"the", "a", "an"}
RECORDING_LABELS = {"unabridged", "abridged"}
PLACEHOLDERS = {
    "unknown",
    "unknown artist",
    "unknown author",
    "various",
    "various artists",
    "various authors",
    "anonymous",
    "anon",
    "uncredited",
    "audiobook",
    "audiobooks",
    "narrator",
}
ROLE_WORDS = (
    r"narrators?|narrated|read|reader|translators?|editors?|illustrators?|foreword|introduction|afterword|performed"
)
ROLE_PREFIX = re.compile(rf"^(?:{ROLE_WORDS})(?:\s+by)?\s*[:\-]\s*|^(?:narrated|read|performed)\s+by\s+", re.I)
ROLE_SUFFIX = re.compile(rf"(?:\s*[,\-–]\s*|\s*\()(?:{ROLE_WORDS})\)?\s*$", re.I)
SUBTITLE_SPLIT = re.compile(r"\s*(?::|\s[-–—]\s|\()")
BRACKETS = re.compile(r"\[[^\]]*\]")


def fold(value):
    """Accent-stripped, case-folded word tokens; `&` is `and`, apostrophes vanish (don't → dont)."""
    text = "".join(
        c for c in unicodedata.normalize("NFKD", str(value or "")) if not unicodedata.combining(c)
    ).casefold()
    text = text.replace("&", " and ").replace("'", "").replace("’", "").replace("‘", "")
    return re.findall(r"[^\W_]+", text)


def title_key(value):
    """Canonical title: no brackets, no (un)abridged label, no leading article. Volume and number words are kept (a different volume is a different work)."""
    words = [w for w in fold(BRACKETS.sub(" ", str(value or ""))) if w not in RECORDING_LABELS]
    if len(words) > 1 and words[0] in ARTICLES:
        words = words[1:]
    return " ".join(words)


def title_main(value):
    """The title before any subtitle (colon, spaced dash or bracket)."""
    return title_key(SUBTITLE_SPLIT.split(BRACKETS.sub(" ", str(value or "")), maxsplit=1)[0])


def title_variants(value):
    """Every key a query should try, most specific first (full, compact, main, compact main)."""
    out = []
    for key in (
        title_key(value),
        title_key(value).replace(" ", ""),
        title_main(value),
        title_main(value).replace(" ", ""),
    ):
        if key and key not in out:
            out.append(key)
    return out


def person_key(value):
    """Canonical person name: `Tolkien, J.R.R.` → `j r r tolkien`; honorifics dropped."""
    text = str(value or "").strip()
    parts = text.split(",")
    if len(parts) == 2 and all(p.strip() for p in parts) and " and " not in text.casefold() and "&" not in text:
        text = parts[1] + " " + parts[0]
    words = fold(text)
    while len(words) > 1 and words[0] in HONORIFICS:
        words.pop(0)
    return " ".join(words)


def person_variants(value):
    """Name keys to look up: as written, initials joined (`jrr tolkien`), and first+last only."""
    key = person_key(value)
    words = key.split()
    out = [key] if key else []
    initials = 0
    while initials < len(words) - 1 and len(words[initials]) == 1:
        initials += 1
    if initials > 1:
        out.append("".join(words[:initials]) + " " + " ".join(words[initials:]))
    if len(words) > 2:
        out.append(words[0] + " " + words[-1])
    return list(dict.fromkeys(v for v in out if v))


def surname(value):
    words = person_key(value).split()
    return words[-1] if words else ""


def isbn_keys(value):
    """Checksum-validated ISBN-10 and its 978 ISBN-13 (979 stays ISBN-13); [] when invalid."""
    key = re.sub(r"[-\s]", "", str(value or "")).upper()
    if re.fullmatch(r"\d{9}[\dX]", key):
        if sum((10 - i) * (10 if c == "X" else int(c)) for i, c in enumerate(key)) % 11:
            return []
        base = "978" + key[:9]
        return [key, base + str((-sum(int(c) * (1 if i % 2 == 0 else 3) for i, c in enumerate(base))) % 10)]
    if not re.fullmatch(r"97[89]\d{10}", key) or sum(int(c) * (1 if i % 2 == 0 else 3) for i, c in enumerate(key)) % 10:
        return []
    if key.startswith("979"):
        return [key]
    check = (-sum((10 - i) * int(c) for i, c in enumerate(key[3:12]))) % 11
    return [key, key[3:12] + ("X" if check == 10 else str(check))]


def asin(value):
    """A strict Audible/Amazon ASIN: ten upper-case alphanumerics that are not a valid ISBN-10 (those are books, not recordings)."""
    key = str(value or "").strip().upper()
    return key if re.fullmatch(r"[A-Z0-9]{10}", key) and not isbn_keys(key) else ""


def people(value):
    """Split a credit line into individual names: `A and B`, `A; B`, `A & B`, `A, B, C`; but `Tolkien, J.R.R.` stays one name."""
    if isinstance(value, (list, tuple)):
        return [p for v in value for p in people(v)]
    raw = str(value or "").strip()
    text = ROLE_SUFFIX.sub("", ROLE_PREFIX.sub("", raw)).strip(" ,;")
    if text != raw.strip(" ,;"):
        return []  # a credit labelled narrator/translator/editor... is not the author; leave the author unknown rather than guess
    if not text or re.fullmatch(ROLE_WORDS, text, re.I) or text.casefold() in PLACEHOLDERS:
        return []
    parts = [p.strip() for p in re.split(r"\s*(?:;|&|/|\band\b)\s*", text, flags=re.I) if p.strip()]
    out = []
    for part in parts:
        pieces = [p.strip() for p in part.split(",") if p.strip()]
        out.extend([part] if len(pieces) < 3 and (len(pieces) < 2 or len(pieces[0].split()) == 1) else pieces)
    return out
