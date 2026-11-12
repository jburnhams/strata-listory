"""Step 3: infer what the folders mean (a book, its discs, a series of books) and write that as a new grouping run on top of the folder grouping. Deterministic, with the evidence for every call stored on the group, so the rules can be tuned and the run repeated from scratch.

Classification of each folder, bottom-up (a folder is judged by what is directly in it and by how its sub-folders were judged):
* **disc**: a leaf folder whose siblings are numbered variants of one name ("CD 1", "Disc 02", "Goldfinch 15-26", "one", "two"); the parent is then the **book**.
* **book**: a folder whose sub-folders are all discs; chapter files of one work (`scan.one_work`, or numbered 1..n); or a single whole-book file (.m4b/.m4a/.aac). Files lying next to sub-folders, or several kinds of file in one folder (an mp3 set beside its m4b), are first split into clusters, each judged on its own and written as a group under the folder.
* **series**: a folder whose sub-folders are books (an author folder, "Jack Reacher", "Hitchhiker's Guide").
* **loose**: a pile of files with nothing tying them together (no numbering, no shared title, no dates); these are what a person looks at.
* **programme**: the numbered episodes of one radio series or serial (`S01E03`, `Episode 2`, `Part 1`, `ep04`); `detail.radio` is true.
* **collection**: a container whose contents are a mix (books, series, loose tracks such as radio series); each part is already classified inside it.
* **folder**: anything the rules cannot call at all; `detail.reason` says why. These are what a person or a smarter step looks at next.
Every group of the new run holds the same files as the folder it came from; the folder grouping it was built from is untouched."""

from __future__ import annotations

import os
import re
from dataclasses import dataclass, field

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.scan import one_work
from app.store.filegroups import finish_run, new_run
from app.store.models import PartFile, PartFilegroup, PartFilegroupMember, PartFileLocation, Run

PLAY_MINUTES = 100  # a work of at most MAX_PLAY_FILES files and this many minutes is a play or an episode, not a book
MAX_PLAY_FILES = 4
DURATIONS: dict[int, float] = {}  # file id -> seconds, filled by load_tree (empty in unit tests: the rule is then off)
SMALL_DRAWER = 3  # leftovers up to this many files are each treated as an item of their own
WHOLE_BOOK = {".m4b", ".m4a", ".aac"}
DISC_WORD = re.compile(r"^(?:cd|disc|disk|part|volume|vol|book|tape|side)\s*[-_. ]*\d+\s*(?:of\s*\d+)?$", re.I)
YEAR_RANGE = re.compile(r"\b(?:19|20)\d\d\s*[-–]\s*(?:19|20)\d\d\b")
NUMBER_WORDS = r"one|two|three|four|five|six|seven|eight|nine|ten|eleven|twelve|first|second|third|fourth|fifth"


@dataclass
class Node:
    id: int
    label: str
    path: str | None
    files: list[tuple[int, str]]  # (file_id, file name), listening order
    subs: list[Node] = field(default_factory=list)
    kind: str = "folder"
    detail: dict = field(default_factory=dict)
    members: list = field(default_factory=list)  # filled for synthesised groups (wrapped single files)


def stem(label: str) -> str:
    """The name with its numbers removed ('Goldfinch 15-26' and 'Goldfinch 03-26' share a stem; so do 'CD1' and 'CD2')."""
    s = re.sub(rf"\b({NUMBER_WORDS})\b", " ", label.lower())
    s = re.sub(r"\d+", " ", s)
    return re.sub(r"[\W_]+", " ", s).strip()


PART_PREFIX = re.compile(
    r"^\s*(?:part|cd|disc|disk|tape|side)[\s._-]*\d+\b", re.I
)  # 'Part 1 - Being a Reprint', 'CD 2 - ...'; not 'Book 1' / 'Volume 1', which are usually separate books in a series


def discish(labels: list[str]) -> str | None:
    """Why these sibling folder names look like the discs of one book, or None."""
    if len(labels) < 2 and not (labels and DISC_WORD.match(labels[0].strip())):
        return None
    if all(DISC_WORD.match(l.strip()) for l in labels):
        return "disc names"
    if len(labels) >= 2 and all(re.fullmatch(rf"\s*(\d+|{NUMBER_WORDS})\s*", l, re.I) for l in labels):
        return "numbers or number words"
    DISC_SUFFIX = r"[\[(]\s*(?:disc|cd|disk|part)\s*\d+\s*[\])]"
    bare = [re.sub(DISC_SUFFIX, " ", l, flags=re.I) for l in labels]
    if (
        len(labels) >= 2
        and any(b != l for b, l in zip(bare, labels))
        and len({re.sub(r"[\W_]+", "", b.lower()) for b in bare}) == 1
    ):
        return "same name with a [Disc N] suffix (the bare one is disc 1)"
    stems = {stem(l) for l in labels}
    if len(labels) >= 2 and len(stems) == 1 and any(re.search(r"\d", l) for l in labels):
        return "same name, numbered"
    if len(labels) >= 2 and all(re.search(r"\b(cd|disc|disk)\s*\d+", l, re.I) for l in labels):
        return "disc names inside longer names"
    if len(labels) >= 2 and all(PART_PREFIX.match(l) for l in labels):
        return 'part names ("Part 1 - Title")'
    return None


def _is_run(nums: list[int], n: int) -> bool:
    """The numbers (one per file, `n` files) are distinct and run (about) 1..n, allowing a stray low outlier."""
    if len(nums) < 0.9 * n or len(set(nums)) < 0.9 * n:
        return False
    ordered = sorted(nums)
    for drop_low in range(
        max(1, int(0.05 * n)) + 1
    ):  # a stray 000 'dedication' file ahead of 301..360 should not break the run
        kept = ordered[drop_low:]
        if len(kept) >= 0.9 * n and kept[-1] - kept[0] + 1 <= 1.1 * len(kept) + 2:
            return True
    return False


def _disc_track(ints: list[list[int]], n: int) -> bool:
    """'1-01', '1-02' ... '2-01': pairs (disc, track) where each disc's tracks run 1..k and the discs are consecutive."""
    pairs = [(i[0], i[1]) for i in ints if len(i) > 1]
    if len(pairs) < 0.9 * n or len(set(pairs)) != len(pairs):
        return False
    by: dict[int, list[int]] = {}
    for d, t in pairs:
        by.setdefault(d, []).append(t)
    discs = sorted(by)
    return (
        len(by) >= 2
        and discs[-1] - discs[0] + 1 <= len(discs) + 1
        and sum(len(v) for v in by.values() if sorted(v) == list(range(1, len(v) + 1))) >= 0.9 * len(pairs)
    )


def numbered_run(names: list[str]) -> bool:
    """True when the names carry a track number that runs 1..n with hardly a gap or repeat. The number can be anywhere: 'TP - 007 - Chapter 3' (first), 'Flashman -12- on the March 03 of 13' (the one before 'of'; the first is the volume), '1-07 Title [Disc 1]' (disc-track pair). Dates, years and [ids] do not count."""
    if len(names) < 3 or any(DATE.search(x) for x in names):
        return False
    ints: list[list[int]] = []
    before_of: list[int] = []
    for x in names:
        x = re.sub(
            r"(?<!\d)(?:19|20)\d\d(?!\d)", " ", re.sub(r"\[[^\]]*\]", " ", os.path.splitext(x)[0])
        )  # years and [B0D23Y5KXP]-style ids are not track numbers
        ints.append([int(t) for t in re.findall(r"\d+", x)])
        if m := re.search(r"(\d+)\s*of\s*\d+", x, re.I):
            before_of.append(int(m.group(1)))
    cands = [[i[k] for i in ints if len(i) > k] for k in (0, 1)] + [[i[-1] for i in ints if i]] + [before_of]
    return any(_is_run(c, len(names)) for c in cands if c) or _disc_track(ints, len(names))


DATE = re.compile(
    r"\b(?:19|20)\d\d[-.]\d\d[-.]\d\d\b"
)  # a dated broadcast name (2012-01-03): episodes of a programme, not chapters of one work
EPISODE = re.compile(r"\b(?:ep|episode|part)[\s._-]*(\d+)\b", re.I)
_SE = re.compile(
    r"(?<![a-z0-9])s(\d{1,2})[\s._-]*e(\d{1,3})(?![a-z0-9])|(?<![a-z])series[\s._]*(\d+)[\s._]*-[\s._]*(\d+)(?!\d)",
    re.I,
)  # S03E04, and 'Series 7 - 4. Title'


class _SeasonEpisode:
    """Like a compiled regex with groups (1)=season, (2)=episode and .start(), for either spelling."""

    def search(self, text):
        m = _SE.search(text)
        return None if m is None else _Match(m)


class _Match:
    def __init__(self, m):
        self.m = m

    def group(self, i):
        return self.m.group(i) if self.m.group(1) is not None else self.m.group(i + 2)

    def start(self):
        return self.m.start()


SEASON_EPISODE = _SeasonEpisode()
BROADCAST = re.compile(
    r"\b\d{1,2}(?:st|nd|rd|th)?\s+(?:jan|feb|mar|apr|may|jun|jul|aug|sep|oct|nov|dec)[a-z]*\.?\s+(?:19|20)\d\d\b|\b(?:webrip|dabrip|dab rip)\b|\bbbc[\s._-]*(?:radio[\s._-]*\d|r\d)\b|(?<![a-z0-9])[bpm]0\d[0-9a-z]{5}(?![a-z0-9])|\bbbc\b|\(r[2-7]\s*(?:19|20)\d\d|\b(?:afternoon drama|saturday night theatre|saturday play|friday play|classic serial|book at bedtime|woman.s hour)\b",
    re.I,
)  # '(3rd January 2013)', '[WebRip (mp3)]': a radio broadcast, not a book's chapter
PART_OF = re.compile(r"\((\d+)\s*-\s*(\d+)\)|\b(\d+)[\s_]*of[\s_]*(\d+)\b", re.I)  # 'Rumpole (2-3)', 'Show 3_of_4'


def episode_run(names: list[str]) -> bool:
    """True when the names are the numbered episodes of one programme ('..., Episode 3', 'Show - ep03', 'S02E04'): every name has an episode number, no number repeats and they run (about) 1..n."""
    nums = []
    for x in names:
        if m := SEASON_EPISODE.search(x):
            nums.append((m.group(1), int(m.group(2))))
        elif m := EPISODE.search(x):
            nums.append(("", int(m.group(1))))
        else:
            return False
    seasons = {sn for sn, _ in nums}
    eps = [e for _, e in nums]
    return (
        len(names) >= 2 and len(seasons) == 1 and len(set(eps)) == len(eps) and max(eps) - min(eps) + 1 <= len(eps) + 1
    )


def parts_of_one(names: list[str]) -> bool:
    """'(1-3)', '(2-3)', '(3-3)' or '1_of_4'...: the files say they are the parts of one thing and there are as many as they claim."""
    totals = set()
    for x in names:
        m = PART_OF.search(x)
        if not m:
            return False
        totals.add(int(m.group(2) or m.group(4)))
    return len(totals) == 1 and totals == {len(names)}


def split_by_season(files: list[tuple[int, str]]) -> list[list[tuple[int, str]]]:
    """Flat folders holding several series ('S31E01 ... S34E04', 'Show S1E1 ... Other Show S1E1'): one list per (programme name, season), when most names carry S..E.. and each list has 2+ files."""
    keyed: dict[tuple, list] = {}
    rest = []
    for f in files:
        m = SEASON_EPISODE.search(f[1])
        if m:
            keyed.setdefault((re.sub(r"[\W_\d]+", "", f[1][: m.start()].lower())[:24], int(m.group(1))), []).append(f)
        else:
            rest.append(f)
    if len({season for _, season in keyed}) == 1:
        return [files]  # one season: the same programme under slightly different file names, not several programmes
    valid = [v for v in keyed.values() if len(v) >= 2]
    rest += [f for v in keyed.values() if len(v) < 2 for f in v]  # a lone S01E01 is not a run
    if not valid or (len(valid) == 1 and not rest):
        return [files]
    return valid + ([rest] if rest else [])
    return list(keyed.values()) + ([rest] if rest else [])


def shape(name: str) -> str:
    """How a file name starts, with the numbers blanked: '01 01 - Title.mp3' -> '#', 'APS303 Harry.mp3' -> 'aps#', 'Intro.mp3' -> 'x'. Copies of one book in one folder usually follow different naming schemes."""
    m = re.match(r"^(\D*?)(\d+)", os.path.splitext(name)[0])
    return re.sub(r"[\W_]+", "", m.group(1).lower()) + "#" if m else "x"


def cluster_label(folder: str, files: list[tuple[int, str]], with_shape: bool = False) -> str:
    """'American Psycho (m4b files)' / '(mp3 files, aps#)': what tells the copies in one folder apart."""
    seasons = {m.group(1).lstrip("0") or "0" for f in files if (m := SEASON_EPISODE.search(f[1]))}
    if len(seasons) == 1 and all(SEASON_EPISODE.search(f[1]) for f in files):
        return f"{folder} (season {seasons.pop()})"
    sh = shape(files[0][1])
    return f"{folder} ({os.path.splitext(files[0][1])[1].lstrip('.').lower()} files{', ' + sh if with_shape else ''})"


PROGRAMME_CUT = re.compile(
    r"\bs\d+\s*e\d+|\d{6,}|(?:19|20)\d\d\s*[-.]\s*\d\d\s*[-.]\s*\d\d|\bpart\b|\bep(?:isode)?\b|\s-\s|[(\[]", re.I
)


def programme_title(name: str) -> str:
    """The programme a broadcast file belongs to, as letters only ('Mark.Steels.in.Town.20100407.DAB...' and 'Mark Steel's in Town - S03E01 - Berwick' both give 'marksteelsintown'), or '' when no title is left."""
    x = re.sub(r"[._]+", " ", os.path.splitext(name)[0])
    x = re.sub(r"^\s*(?:bbc\s*(?:radio\s*\d|r\d)x?|rt[eé])\s*[-:]*\s*", "", x, flags=re.I)
    m = PROGRAMME_CUT.search(x)
    x = x[: m.start()] if m else x
    t = re.sub(r"[^a-z]+", "", x.lower())
    return t if len(t) >= 6 else ""


def split_by_title(files: list[tuple[int, str]]) -> list[tuple[str, list[tuple[int, str]]]]:
    """From files judged unrelated: pull out every title shared by 3+ files (one programme under several numbering schemes: S03E01, 20100407, 'Part 1 of 4', plus duplicate copies). Returns [(title, files)...] for those, then ('', leftovers)."""
    by: dict[str, list] = {}
    for f in files:
        by.setdefault(programme_title(f[1]), []).append(f)
    out = [(t, v) for t, v in by.items() if t and len(v) >= 3]
    left = [f for t, v in by.items() if not (t and len(v) >= 3) for f in v]
    return out + ([("", left)] if left else [])


def split_by_shape(files: list[tuple[int, str]]) -> list[list[tuple[int, str]]]:
    """Split chapter files that follow two or more naming schemes into one list per scheme, but only when every scheme has at least 3 files (otherwise it is one set plus a stray intro or credits file)."""
    groups: dict[str, list[tuple[int, str]]] = {}
    for f in files:
        groups.setdefault(shape(f[1]), []).append(f)
    return list(groups.values()) if 2 <= len(groups) <= 4 and all(len(g) >= 3 for g in groups.values()) else [files]


def standalone(f: tuple[int, str], why: str) -> tuple[str, list[tuple[int, str]], dict]:
    """One file that stands on its own in a folder of other things: a programme (radio play, one-off), unless it is as long as a book."""
    mins = DURATIONS.get(f[0], 0) / 60
    if mins > PLAY_MINUTES:
        return "book", [f], {"reason": f"stand-alone file of book length ({mins:.0f} minutes)", "minutes": round(mins)}
    return "programme", [f], {"reason": why, "radio": True}


def sibling_names(full: str, others: list[str]) -> bool:
    """The names back up the lengths: most of the other files share the start of the full file's name ('Book 06.mp3' beside 'Book 06_1.mp3'...), so they are that file split up, not an unrelated long file."""
    stem = re.sub(r"[^a-z0-9]+", "", os.path.splitext(full)[0].lower())
    share = lambda o: os.path.commonprefix([stem, re.sub(r"[^a-z0-9]+", "", os.path.splitext(o)[0].lower())])
    return len(stem) >= 4 and sum(len(share(o)) >= 0.8 * len(stem) for o in others) >= 0.8 * len(others)


BOOK_MINUTES = 150  # every file at least this long: a chapter file is rarely more than two hours, a book is
CODE = re.compile(r"\b[a-z]{0,4}\d+[a-z]?\b")  # numbering and series codes: 01, 1x01, HB04, MH02
NUMBERING_WORDS = re.compile(
    r"\b(?:part|pt|chapter|chap|ch|disc|disk|cd|track|episode|ep|vol|volume|book|unabridged|abridged)\b"
)


def titled_books(files: list[tuple[int, str]]) -> list[tuple[int, str]]:
    """The files themselves when they are several book-length files each named by a title of its own ('Author - Title One.mp3', 'Author - Title Two.mp3'...), or []. Needs durations for every file. Chapter files fail it: they are shorter than `BOOK_MINUTES`, or what differs between their names is only a number ('Part 1', '01', '1x04'), or the same words ('01 Title', '02 Title')."""
    if len(files) < 2 or not all(f[0] in DURATIONS and DURATIONS[f[0]] / 60 >= BOOK_MINUTES for f in files):
        return []
    stems = [os.path.splitext(f[1])[0].lower() for f in files]
    prefix = os.path.commonprefix(stems)
    prefix = (
        prefix[: len(re.match(r".*?(?=[^a-z0-9]*[a-z0-9]*$)", prefix).group(0))] if prefix else ""
    )  # whole words only
    left = [NUMBERING_WORDS.sub(" ", CODE.sub(" ", s[len(prefix) :])) for s in stems]
    keys = [re.sub(r"[^a-z]", "", x) for x in left]
    if any(len(k) < 4 for k in keys) or len(set(keys)) != len(keys):
        return []
    return list(files)


def named_files(
    folder: str | None, file_ids: list[int], names: dict[tuple[str, int], list[str]]
) -> list[tuple[int, str]]:
    """(file id, name) for a group's members. Identical content is one file however many names it has, so a folder holding the same book twice under two naming schemes lists each file id twice (one member per path): the n-th member of an id takes the n-th name it has in this folder, so both schemes reach the structure rules (a missing name falls back to the last one, or to `file <id>`)."""
    seen: dict[int, int] = {}
    out = []
    for fid in file_ids:
        have = names.get((folder or "", fid)) or []
        n = seen.get(fid, 0)
        seen[fid] = n + 1
        out.append((fid, have[min(n, len(have) - 1)] if have else f"file {fid}"))
    return out


def full_beside_its_parts(files: list[tuple[int, str]], tolerance: float = 0.03) -> tuple[int, str] | None:
    """The file whose length is the sum of all the others (at least three) within `tolerance`: one full-length copy lying beside the same book split into chapters. Needs durations (off in unit tests that set none)."""
    if len(files) < 4 or not all(f[0] in DURATIONS for f in files):
        return None
    if parts_of_one(
        [f[1] for f in files]
    ):  # the names say every file is a numbered part of one work ('1_of_4' ... '4_of_4'): one long part is not a full copy
        return None
    total = sum(DURATIONS[f[0]] for f in files)
    for f in files:
        d = DURATIONS[f[0]]
        if d > 0 and abs(d - (total - d)) / d <= tolerance and sibling_names(f[1], [g[1] for g in files if g is not f]):
            return f
    return None


def cluster_files(files: list[tuple[int, str]]) -> list[tuple[str, list[tuple[int, str]], dict]]:
    """Split the audio files directly in one folder into (kind, files, evidence) clusters: every whole-book file (.m4b/.m4a/.aac) is a book of its own, the rest are the chapter files of one work or unrelated tracks."""
    out = []
    rest = []
    whole = [f for f in files if os.path.splitext(f[1])[1].lower() in WHOLE_BOOK]
    if len(whole) >= 3 and (numbered_run([f[1] for f in whole]) or parts_of_one([f[1] for f in whole])):
        out.append(
            ("book", whole, {"reason": "numbered m4b/m4a files are the chapters of one work", "files": len(whole)})
        )
        whole = []
    out += [("book", [f], {"reason": "whole-book file"}) for f in whole]
    rest = [f for f in files if os.path.splitext(f[1])[1].lower() not in WHOLE_BOOK]
    full = full_beside_its_parts(rest)
    if full:  # the same book twice in one folder: whole, and split into chapters; two groups, which the copies step then joins as copies
        out.append(("book", [full], {"reason": "a whole-book file with its split chapters beside it"}))
        rest = [f for f in rest if f is not full]
    books = titled_books(rest)
    if (
        books
    ):  # several long files, each with a title of its own: a folder of one author's books, not the chapters of one
        out += [("book", [f], {"reason": "a long file with a title of its own among several"}) for f in books]
        rest = []
    by_season = split_by_season(rest) if rest else []
    parts = by_season if len(by_season) > 1 else (split_by_shape(rest) if rest else [])
    judged = [judge(p) for p in parts]
    if len(by_season) <= 1 and len(judged) > 1 and any(j[0] == "loose" for j in judged):
        parts, judged = (
            [rest],
            [judge(rest)],
        )  # a split by naming scheme is only worth it when every scheme is something of its own
    for p, (k, ev) in zip(parts, judged):
        titled = split_by_title(p) if k == "loose" and len(p) >= 3 else []
        if len(titled) > 1 or (titled and titled[0][0]):
            for t, v in titled:
                if t:
                    out.append(
                        (
                            "programme",
                            v,
                            {
                                "reason": "files share one programme title under mixed numbering",
                                "files": len(v),
                                "radio": True,
                                "title": t,
                            },
                        )
                    )
                elif len(v) <= SMALL_DRAWER:
                    out += [
                        standalone(f, "stand-alone file left over from a folder of several programmes") for f in v
                    ]  # a couple of one-offs: each is its own item
                else:
                    out.append(("loose", v, judge(v)[1]))
        elif k == "loose" and 2 <= len(p) <= SMALL_DRAWER:
            out += [
                standalone(f, "one of a few stand-alone files, each its own programme") for f in p
            ]  # three plays in a folder are three programmes, not one unrelated pile
        else:
            out.append((k, p, ev))
    return out


def judge(part: list[tuple[int, str]]) -> tuple[str, dict]:
    kind, ev = _judge(part)
    mins = sum(DURATIONS.get(i, 0) for i, _ in part) / 60
    if (
        kind == "book"
        and 2 <= len(part) <= MAX_PLAY_FILES
        and 0 < mins <= PLAY_MINUTES
        and all(i in DURATIONS for i, _ in part)
    ):
        return "programme", {
            "reason": f"only {len(part)} files and {mins:.0f} minutes: play length, not book length",
            "files": len(part),
            "radio": True,
            "minutes": round(mins),
            "was": [kind, ev],
        }
    return kind, ev


def _judge(part: list[tuple[int, str]]) -> tuple[str, dict]:
    names = [YEAR_RANGE.sub(" ", f[1]) for f in part]  # '1999-2001' in a title reads as a date to the radio test
    if len(part) == 1:
        return "book", {"reason": "one file"}
    if all(SEASON_EPISODE.search(x) for x in names) and episode_run(names):
        return "programme", {
            "reason": "numbered episodes of one programme",
            "files": len(part),
            "radio": True,
        }  # S01E03 is unmistakable, so it wins over a shared name prefix
    if sum(bool(BROADCAST.search(x)) for x in names) >= 0.7 * len(names):
        return "programme", {
            "reason": "radio broadcast names (BBC Radio, air dates, WebRip)",
            "files": len(part),
            "radio": True,
        }
    if sum(bool(DATE.search(x)) or bool(re.search(r"\b(?:19|20)\d\d\d\d\d\d\b", x)) for x in names) >= 0.7 * len(names):
        return "programme", {
            "reason": "dated broadcasts: the episodes of one programme, by air date",
            "files": len(part),
            "radio": True,
        }
    if one_work(names):
        return "book", {"reason": "chapter files of one work", "files": len(part)}
    if episode_run(names):
        return "programme", {"reason": "numbered episodes of one programme", "files": len(part), "radio": True}
    if parts_of_one(names):
        if sum(bool(BROADCAST.search(x)) for x in names) >= 0.7 * len(names):
            return "programme", {
                "reason": "numbered 1..n of one radio series (broadcast dates in the names)",
                "files": len(part),
                "radio": True,
            }
        return "book", {"reason": "files say they are parts 1..n of one thing", "files": len(part)}
    if numbered_run(names):
        return "book", {"reason": "numbered tracks 1..n", "files": len(part)}
    return "loose", {"reason": "unrelated tracks", "files": len(part)}


def classify(n: Node) -> Node:
    _classify(n)
    pd = [c for c in n.subs if c.kind == "programme" and "was" in c.detail]
    if pd and len(pd) <= 0.25 * len(
        n.subs
    ):  # one short work among many books (a chapter folder, an Audible short story) is a short book, not a play
        for c in pd:
            c.kind, c.detail = c.detail["was"]
        if n.kind in ("series", "collection") and all(c.kind in ("book", "series", "programme") for c in n.subs):
            n.kind = "series"
    return n


def _classify(n: Node) -> Node:
    for c in n.subs:
        classify(c)
    clusters = cluster_files(n.files)
    if clusters and (n.subs or len(clusters) > 1):
        for (
            kind,
            fl,
            ev,
        ) in clusters:  # files next to sub-folders, or several kinds of file in one folder: each cluster becomes a group of its own under this folder
            n.subs.append(
                Node(
                    -1,
                    os.path.splitext(fl[0][1])[0]
                    if len(fl) == 1
                    else cluster_label(
                        n.label,
                        fl,
                        [
                            x
                            for _, x, _ in clusters
                            if os.path.splitext(x[0][1])[1].lower() == os.path.splitext(fl[0][1])[1].lower()
                        ].__len__()
                        > 1,
                    ),
                    n.path,
                    fl,
                    kind=kind,
                    detail=ev,
                )
            )
        n.files = []
        if all(c.kind in ("book", "series", "programme") for c in n.subs):
            n.kind, n.detail = (
                "series",
                {
                    "reason": "several books or copies of one book side by side"
                    if not any(c.id != -1 for c in n.subs)
                    else "its folders and files are books",
                    "books": len(n.subs),
                },
            )
        else:
            n.kind, n.detail = (
                "collection",
                {"reason": "books mixed with something else", "kinds": sorted({c.kind for c in n.subs})},
            )
        return n
    if clusters:
        n.kind, n.detail = clusters[0][0], clusters[0][2]
        return n
    why = discish([c.label for c in n.subs]) if all(not c.subs for c in n.subs) else None
    if why:
        for c in n.subs:
            c.kind, c.detail = "disc", {"reason": why}
        n.kind, n.detail = "book", {"reason": "its folders are discs", "discs": len(n.subs), "because": why}
    elif all(c.kind in ("book", "series", "programme") for c in n.subs):
        n.kind, n.detail = "series", {"reason": "its folders are books", "books": len(n.subs)}
    else:
        kinds = sorted({c.kind for c in n.subs})
        n.kind, n.detail = (
            ("folder", {"reason": "contains folders the rules could not call", "kinds": kinds})
            if "folder" in kinds
            else ("collection", {"reason": "sub-folders of different kinds", "kinds": kinds})
        )
    return n


def load_tree(s: Session, root_group: PartFilegroup) -> Node:
    groups = {g.id: g for g in s.scalars(select(PartFilegroup).where(PartFilegroup.run_id == root_group.run_id))}
    mem: dict[int, list[PartFilegroupMember]] = {}
    for m in s.scalars(
        select(PartFilegroupMember)
        .where(PartFilegroupMember.group_id.in_(groups))
        .order_by(PartFilegroupMember.group_id, PartFilegroupMember.ordinal)
    ):
        mem.setdefault(m.group_id, []).append(m)
    DURATIONS.clear()
    DURATIONS.update(
        {i: d for i, d in s.execute(select(PartFile.id, PartFile.duration_s).where(PartFile.duration_s.is_not(None)))}
    )
    names: dict[tuple[str, int], list[str]] = {}
    for fid, path in s.execute(
        select(PartFileLocation.file_id, PartFileLocation.path)
        .where(PartFileLocation.gone_at.is_(None))
        .order_by(PartFileLocation.path)
    ):
        names.setdefault((os.path.dirname(path), fid), []).append(os.path.basename(path))

    def build(g: PartFilegroup) -> Node:
        n = Node(
            g.id,
            g.label,
            g.path,
            named_files(g.path, [m.file_id for m in mem.get(g.id, []) if m.file_id], names),
        )
        n.subs = [build(groups[m.child_group_id]) for m in mem.get(g.id, []) if m.child_group_id]
        return n

    return build(root_group)


def write(s: Session, n: Node, run: Run, totals: dict, is_root: bool = False) -> PartFilegroup:
    """Write the classified tree as new groups (counts recomputed)."""
    children = [write(s, c, run, totals) for c in n.subs]
    files = [f for f, _ in n.files]
    size = (
        {i: z for i, z in s.execute(select(PartFile.id, PartFile.size).where(PartFile.id.in_(files)))} if files else {}
    )
    g = PartFilegroup(
        run_id=run.id,
        kind="root" if is_root else n.kind,
        label=n.label,
        path=n.path,
        detail={**n.detail, "from_group": n.id},
        file_count=len(files) + sum(c.file_count for c in children),
        total_bytes=sum(size.values()) + sum(c.total_bytes for c in children),
        group_count=len(children) + sum(c.group_count for c in children),
    )
    s.add(g)
    s.flush()
    o = 0
    for fid in files:
        o += 1
        s.add(PartFilegroupMember(group_id=g.id, ordinal=o, file_id=fid))
    for c in children:
        o += 1
        s.add(PartFilegroupMember(group_id=g.id, ordinal=o, child_group_id=c.id))
    s.flush()
    if not is_root:
        totals[g.kind] = totals.get(g.kind, 0) + 1
    return g


def infer(s: Session, folder_root: PartFilegroup) -> tuple[PartFilegroup, dict]:
    """Build the structure grouping from a folder grouping's root. Returns the new root and counts per kind."""
    run = new_run(s, "infer_structure", {"from_run": folder_root.run_id})
    totals: dict = {}
    tree = classify(load_tree(s, folder_root))
    top = write(s, tree, run, totals, is_root=True)
    finish_run(s, run, "done")
    return top, totals


def snapshot(n: Node, out: dict[str, list[str]] | None = None) -> dict[str, list[str]]:
    """{path (+ '#label' for groups made from files next to folders): [kind, reason]} for a classified tree: the shape a regression check compares."""
    out = {} if out is None else out
    key = (n.path or n.label) + ("" if n.id != -1 else f"#{n.label}")
    while key in out:
        key += "~"  # two clusters can share a label
    out[key] = [n.kind, n.detail.get("reason", "")]
    for c in n.subs:
        snapshot(c, out)
    return out


def diff(old: dict[str, list[str]], new: dict[str, list[str]]) -> list[str]:
    """Readable differences between two snapshots (a rule changed some folder's kind or reason, or folders appeared or went)."""
    lines = []
    for k in sorted(old.keys() | new.keys()):
        a, b = old.get(k), new.get(k)
        if a == b:
            continue
        lines.append(
            f"+ {k}: {b[0]} ({b[1]})"
            if a is None
            else f"- {k}: {a[0]} ({a[1]})"
            if b is None
            else f"~ {k}: {a[0]} -> {b[0]} ({b[1]})"
            if a[0] != b[0]
            else f'~ {k}: {a[0]} reason "{a[1]}" -> "{b[1]}"'
        )
    return lines
