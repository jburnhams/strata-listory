"""Separate explicitly labelled contribution roles from the work's authors."""

import re
from difflib import SequenceMatcher

from app import merge

ROLES = r"forewords?|introductions?|afterwords?|translator|translation|editor|illustrator|narrator|reader"
SUFFIX = re.compile(rf"^(.*?)\s*(?:\s[-–—]\s*({ROLES})|\(({ROLES})\))\s*$", re.I)
PREFIX = re.compile(rf"^({ROLES})\s+by\s+(.+)$", re.I)
READ_BY = re.compile(r"^(?:narrated|read)\s+by\s*:?\s+(.+)$", re.I)


def name_key(value):
    """Comparison only: retain the provider's original credit for display/audit."""
    words = merge.norm(value).split()
    while len(words) > 1 and words[0] in {
        "mr",
        "mrs",
        "ms",
        "miss",
        "dr",
        "doctor",
        "prof",
        "professor",
        "sir",
        "dame",
        "rev",
        "reverend",
    }:
        words.pop(0)
    return " ".join(words)


def one_edit(a, b):
    if a == b:
        return True
    if len(a) == len(b):
        different = [i for i, (x, y) in enumerate(zip(a, b)) if x != y]
        return len(different) == 1 or (
            len(different) == 2
            and different[1] == different[0] + 1
            and a[different[0]] == b[different[1]]
            and a[different[1]] == b[different[0]]
        )
    if abs(len(a) - len(b)) != 1:
        return False
    short, long = sorted((a, b), key=len)
    return any(long[:i] + long[i + 1 :] == short for i in range(len(long)))


def same_person(a, b):
    """Bounded variants: never fuzz both the given name and surname."""
    a = name_key(a).split()
    b = name_key(b).split()
    if a == b:
        return bool(a)
    if len(a) < 2 or len(a) != len(b):
        return False
    if a[:-1] == b[:-1]:
        return min(len(a[-1]), len(b[-1])) >= 6 and one_edit(a[-1], b[-1])
    if a[-1] != b[-1]:
        return False
    aliases = (
        {"sue", "susan", "suzanne"},
        {"bob", "robert"},
        {"bill", "william"},
        {"jim", "james"},
        {"liz", "elizabeth"},
        {"mike", "michael"},
        {"charles", "chuck"},
    )

    def given(x, y):
        return (
            x == y
            or ((len(x) == 1 or len(y) == 1) and x[0] == y[0])
            or any(x in group and y in group for group in aliases)
            or (min(len(x), len(y)) >= 4 and one_edit(x, y))
        )

    return all(given(x, y) for x, y in zip(a[:-1], b[:-1]))


def omitted_middle_initial(a, b):
    """Only one initial may be missing; both full outer names must be exact."""
    a = name_key(a).split()
    b = name_key(b).split()
    if sorted((len(a), len(b))) == [2, 3]:
        short, long = sorted((a, b), key=len)
        if min(map(len, short)) > 1 and short == [long[0], long[-1]] and len(long[1]) == 1 and long[1].isalpha():
            return True
    return False


def same_author(a, b, strong_title=False):
    """A strong book-title match supplies context absent from a bare person name."""
    if same_person(a, b):
        return True
    # Do not combine an omitted initial with spelling changes or nicknames.
    if strong_title and omitted_middle_initial(a, b):
        return True
    a = name_key(a).split()
    b = name_key(b).split()
    if not strong_title or len(a) < 2 or len(a) != len(b) or a[1:] != b[1:]:
        return False
    x, y = a[0], b[0]
    return min(len(x), len(y)) >= 3 and x[0] == y[0] and (x[:2] == y[:2] or SequenceMatcher(None, x, y).ratio() >= 0.6)


def parse(value):
    values = (
        re.split(r"\s*(?:,|;|&|\band\b)\s*", value or "")
        if isinstance(value, str)
        else [part for credit in value or [] for part in re.split(r"\s*(?:;|&|\band\b)\s*", credit)]
    )
    # Some audiobook artist tags join a writer and reader with '/'. Retain both
    # credits; only corroborating providers decide the primary work authors.
    values = [
        part
        for credit in values
        for part in (
            credit.split("/")
            if "://" not in credit and all(len(name_key(p).split()) >= 2 for p in credit.split("/"))
            else [credit]
        )
    ]
    out = []
    for raw in values:
        raw = raw.strip()
        if not raw:
            continue
        if narrator := READ_BY.match(raw):
            out.append(dict(name=narrator[1].strip(), role="narrator"))
            continue
        suffix = SUFFIX.match(raw)
        prefix = PREFIX.match(raw)
        name, role = (
            (suffix[1].strip(), (suffix[2] or suffix[3]).lower())
            if suffix
            else (prefix[2].strip(), prefix[1].lower())
            if prefix
            else (raw, "author")
        )
        if name:
            out.append(
                dict(
                    name=name,
                    role="translator"
                    if role == "translation"
                    else role.rstrip("s")
                    if role in ("forewords", "introductions", "afterwords")
                    else role,
                )
            )
    return out


def authors(value, known_roles=()):
    # Only an explicit role can explain an extra contributor. Never drop an
    # unknown coauthor merely to make two provider records agree.
    auxiliary = {merge.norm(c["name"]) for c in known_roles if c["role"] != "author"}
    return [c["name"] for c in parse(value) if c["role"] == "author" and merge.norm(c["name"]) not in auxiliary]


def auxiliary(value):
    return [c for c in parse(value) if c["role"] != "author"]
