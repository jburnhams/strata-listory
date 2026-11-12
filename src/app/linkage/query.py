"""What a listen says about itself, read from the Postgres catalogue: its embedded-tag and folder-name records, its chosen fields and its runtime, turned into several *readings* (title, author) because file and folder names are ambiguous (`Title - Author` and `Author - Title` look alike, tags can be album-level or track-level). The comparison step scores every reading and keeps the best per candidate."""

from __future__ import annotations

import os
import re
from dataclasses import dataclass, field

from app import scan
from app.identify import local
from app.identify import textkeys as tk

NOISE = re.compile(
    r"\((?:un)?abridged[^)]*\)|[_\s]+(?:un)?abridged\b|\b(?:\d+(?:st|nd|rd|th)\s+)?anniversary(?:\s+edition)?\b|\((?:mp3|m4b|aac|audiobook|audio\s*book|retail)\)|\b(?:mp3|m4b)\b|\bunabridged\b",
    re.I,
)
ALL_KINDS = ("book", "radio_series", "radio_show", "play", "other")
GENERIC = re.compile(
    r"^\s*(track\s*\d*|unknown\b.*|audiobooks?|disc\s*\d*|chapter\s*\d*|\d+|album(?:\s+artist)?|artist|title|untitled|no\s+title|various(?:\s+artists)?)\s*$",
    re.I,
)


@dataclass
class Reading:
    title: str
    author: str = ""
    source: str = "chosen"  # chosen | tags | folder | swapped | stripped | split


@dataclass
class Query:
    listen_id: int
    kind: str
    label: str = ""
    readings: list[Reading] = field(default_factory=list)
    narrators: list[str] = field(default_factory=list)
    asin: str = ""
    year: int | None = None
    minutes: list[float] = field(default_factory=list)  # one per copy (input part), first = preferred copy
    language: str = ""
    path: str = ""
    hints: list[str] = field(
        default_factory=list
    )  # bracketed words in names, often a narrator: 'Great Expectations (Stephen Fry)'
    band: str = ""  # the kind classifier's call: broadcast | review | book, '' when it has not run (`store/kinds.py`)
    channels: list[str] = field(
        default_factory=list
    )  # BBC services the listen's tags and names point to (`channels_in`), e.g. bbc_radio_four


CHANNELS = (  # (pattern, service id prefix in the BBC programme list)
    (r"radio[\s._-]*4[\s._-]*extra|\br4x\b", "bbc_radio_four_extra"),
    (r"radio[\s._-]*4\b|\br4\b|radio four", "bbc_radio_four"),
    (r"radio[\s._-]*3\b|\br3\b", "bbc_radio_three"),
    (r"radio[\s._-]*2\b|\br2\b", "bbc_radio_two"),
    (r"radio[\s._-]*1\b|\br1\b", "bbc_radio_one"),
    (r"radio[\s._-]*5[\s._-]*live|\br5l\b", "bbc_radio_five_live"),
    (r"6[\s._-]*music", "bbc_6music"),
    (r"world service", "bbc_world_service"),
    (r"radio scotland", "bbc_radio_scotland"),
    (r"radio wales|radio cymru", "bbc_radio_wales"),
)


def channels_in(*texts: str) -> list[str]:
    """The BBC services named in these texts (Radio 4, R3, World Service...), as the ids the programme list uses; a more specific name (Radio 4 Extra) hides the plainer one it contains."""
    t = " ".join(x for x in texts if x).lower()
    found = [svc for pat, svc in CHANNELS if re.search(pat, t)]
    return [svc for svc in found if not any(o != svc and o.startswith(svc + "_") for o in found)]


def clean_title(title: str) -> str:
    """Drop recording labels and file-type words that are never part of a title: '(Unabridged)', '_unabridged', '10th Anniversary Edition', '(MP3)'."""
    t = NOISE.sub(" ", str(title or "")).replace("_", " ")
    return re.sub(r"\s+", " ", t).strip(" -.,:")


def author_text(value: str) -> str:
    return ", ".join(tk.people(value))


def common_title(names: list[str]) -> str:
    """The title shared by the file names of a multi-file listen ('Invented Book 01.mp3', 'Invented Book 02.mp3'...): leading track numbers, extensions and trailing part/chapter/disc numbers stripped, then the longest common prefix cut at a word boundary. '' unless at least three files share at least two words (or a long single one) that are not generic."""
    if len(names) < 3:
        return ""
    stems = [re.sub(r"^\s*\d+\s*[-._)]*\s+", "", os.path.splitext(n)[0].replace("_", " ")) for n in names]
    stems = [re.sub(r"\s+", " ", re.sub(r"[\[(][^\])]*[\])]", " ", s)).strip() for s in stems]
    words = [s.split() for s in stems]
    n = 0
    while n < min(map(len, words)) and len({w[n].lower() for w in words}) == 1:
        n += 1  # whole words only, so 'Chapter 1' and 'Chapter 2' stop before the digit
    cut = " ".join(words[0][:n])
    for _ in range(4):
        cut = re.sub(
            r"[\s\-._:,]*(?:\b(?:part|pt|chapter|ch|chap|disc|disk|cd|track)\b\.?\s*\d*|\b(?:book|vol|volume)\b\.?\s*\d+|\d+)$",
            "",
            cut,
            flags=re.I,
        )  # trailing part/disc/track numbers, repeatedly ('Disc 1 Track'); 'Book 5' only with its number
    cut = cut.strip(" -._:,")
    if not cut or GENERIC.match(cut) or cut.lower() in scan.GENERIC or (len(cut.split()) < 2 and len(cut) < 8):
        return ""
    return cut


SERIES_CODE = re.compile(
    r"^[A-Za-z]{1,5}\d{1,3}(?=[\s._:-]+\S)[\s._:-]*"
)  # 'RW01 ', 'HB04: ', 'MH2 - ' in front of a title

NUMBERED = re.compile(
    r"^(?:(?:series|season|episode|ep|part|pt|vol|volume|book|chapter|disc|disk|cd|collection)\.?\s*\d+\b|\d)", re.I
)  # a numbered label ('Series 3', 'Part 2') or a bare number; a title that merely starts with one of these words ('Book of the Dead') is a title


def peel(title: str, authors: list[str]) -> str:
    """The title without a leading author name and a leading series code: 'Pat Author RW01 The Invented Echo' -> 'The Invented Echo'. Unchanged when nothing sensible is left: the whole title is never peeled away, what remains after taking off only an author must be two words or more ('Pat Author Upload' stays), and a bare number, 'Series 3' or 'Part 2' is not a title."""
    original = title.strip()
    t, author_peeled, code_peeled = original, False, False
    for a in sorted({a.strip() for a in authors if a and a.strip()}, key=len, reverse=True):
        if len(a) >= 3 and t.lower().startswith(a.lower() + " ") and len(t) - len(a) > 2:
            t, author_peeled = t[len(a) :].strip(" -._:"), True
            break
    if (m := SERIES_CODE.match(t)) and len(re.sub(r"[^A-Za-z]", "", t[m.end() :])) >= 4:
        t, code_peeled = t[m.end() :].strip(" -._:"), True
    if t == original:
        return original
    words = [w for w in re.findall(r"[A-Za-z']+", re.sub(r"^(?:the|a|an)\s+", "", t, flags=re.I)) if w]
    if NUMBERED.match(t) or len("".join(words)) < 4 or (author_peeled and not code_peeled and len(words) < 2):
        return original  # what is left is only a number, 'Series 3', 'the book', or one word taken off an author prefix: not a title
    return t


PART_WORDS = re.compile(
    r"part|pt|chapter|chap|ch|disc|disk|cd|track|episode|ep"
)  # not "book" or "volume": those end real titles


def shared_title(titles: list[str]) -> str:
    """The title the parts of one listen share ('The Invented Book-01 The Start', 'The Invented Book-02 Next' -> 'The Invented Book'): whole words in common at the start, taken apart at every separator, trailing numbers and Part/Chapter words dropped. '' unless two or more titles share at least two words (or a long one)."""
    toks = [re.findall(r"[^\W_]+(?:'[^\W_]+)?", t) for t in titles if t]
    if len(toks) < 2:
        return ""
    n = 0
    while n < min(map(len, toks)) and len({w[n].lower() for w in toks}) == 1:
        n += 1
    words = toks[0][:n]
    while words and (words[-1].isdigit() or PART_WORDS.fullmatch(words[-1].lower())):
        words.pop()
    cut = " ".join(words)
    return cut if cut and not GENERIC.match(cut) and (len(words) >= 2 or len(cut) >= 8) else ""


def make_readings(
    choices: dict, tags: dict, folder: dict, file: dict | None = None, prefix: str = "", parts_title: str = ""
) -> list[Reading]:
    """Readings in order of trust (`file`: the parsed name of a single-file listen's file, which is the title itself, not a chapter name, and is read even when tags give a different title), duplicates (same title and author key) removed. Each yields its own numbered/stripped variants via `identify.local`."""

    def tag(f, minimum=0.5):
        t = tags.get(f) or {}
        v = t.get("value") if isinstance(t, dict) and (t.get("share") or 0) >= minimum else None
        return v if v and not GENERIC.match(v) else ""

    raw: list[Reading] = []
    if choices.get("title") and not GENERIC.match(
        str(choices["title"])
    ):  # a placeholder ("Album") chosen from tags says nothing
        author = str(choices.get("author") or "")
        raw.append(Reading(str(choices["title"]), "" if GENERIC.match(author) else author, "chosen"))
    if (
        parts_title
    ):  # the numbered parts of one listen share a title, which none of the parts' own tags and names say alone
        raw.append(Reading(parts_title, str(choices.get("author") or ""), "parts"))
    for title_field in ("album", "title"):
        if (t := tag(title_field)) and (title_field == "album" or (tags.get("tagged") or 0) <= 3):
            raw.append(Reading(t, tag("album_artist") or tag("artist"), "tags"))
    if file and file.get("title") and file["title"].strip().lower() not in scan.GENERIC:
        raw.append(Reading(file["title"], file.get("author") or "", "file"))
    if prefix:
        raw.append(Reading(prefix, "", "prefix"))
    if folder.get("title") and folder["title"].strip().lower() not in scan.GENERIC:
        raw.append(Reading(folder["title"], folder.get("author") or "", "folder"))
        if folder.get("author"):
            raw.append(
                Reading(folder["author"], folder["title"], "swapped")
            )  # 'The Secret History - Donna Tartt' parses as author 'The Secret History'
    if folder.get("folder") and folder["folder"].strip().lower() not in scan.GENERIC:
        raw.append(Reading(folder["folder"], "", "split"))
    known = [str(a) for a in (choices.get("author"), folder.get("author"), tag("album_artist"), tag("artist")) if a]
    with_peeled: list[Reading] = []
    for r in raw:  # the title as it would be without the author in front and a series code ('Author RW01 The Title' -> 'The Title'), right after the reading it came from
        with_peeled.append(r)
        if r.source != "swapped" and (p := peel(r.title, known)) != r.title:
            with_peeled.append(Reading(p, r.author or (known[0] if known else ""), "peeled"))
    raw = with_peeled
    out: list[Reading] = []
    seen = set()
    for r in raw:
        author = author_text(r.author)
        title = clean_title(r.title)
        bare = re.sub(r"\s*\([^)]*\)\s*", " ", title).strip()
        variants = local.readings(dict(title=title, author=author)) + (
            local.readings(dict(title=bare, author=author)) if bare and bare != title else []
        )
        for v in variants:
            key = (tk.title_key(v["title"]), tk.person_key(v["author"]))
            if not key[0] or key in seen:
                continue
            seen.add(key)
            src = r.source if v["reading"] == "raw" else ("stripped" if v["reading"] == "stripped" else "split")
            out.append(Reading(v["title"], v["author"], src))
    return out[:12]


def input_minutes(inputs: list[dict]) -> list[float]:
    """The runtimes a listen's inputs offer for comparison: numbered parts (`relation` "part") run one after the other, so they count once as their sum; every other input is an alternative copy and counts on its own. Parts first, as they are the preferred recording."""
    parts = [float(i["minutes"]) for i in inputs if i.get("relation") == "part" and i.get("minutes")]
    return ([sum(parts)] if parts else []) + [
        float(i["minutes"]) for i in inputs if i.get("relation") != "part" and i.get("minutes")
    ]


def from_parts(
    listen_id: int,
    kind: str,
    label: str,
    choices: dict,
    tags: dict,
    folder: dict,
    inputs: list[dict],
    band: str = "",
    filename: str = "",
    names: list[str] | None = None,
    part_titles: list[str] | None = None,
) -> Query:
    """A Query from the listen's pieces (kept separate from the database read so tests can build one directly)."""
    narr = choices.get("narrator") or (tags.get("narratedby") or {}).get("value") or folder.get("narrator") or ""
    asin = tk.asin(choices.get("asin") or (tags.get("audible_asin") or {}).get("value") or folder.get("asin") or "")
    year = choices.get("year") if isinstance(choices.get("year"), int) else None
    hints = [
        h.strip()
        for t in (choices.get("title"), folder.get("folder"), (tags.get("album") or {}).get("value"))
        if t
        for h in re.findall(r"\(([^)]{3,60})\)", str(t))
        if not NOISE.fullmatch(f"({h})") and not re.fullmatch(r"[\d\s-]+", h)
    ]
    return Query(
        listen_id,
        kind,
        label,
        make_readings(
            choices,
            tags,
            folder,
            scan.parse_name(filename) if filename else None,
            common_title(names or []),
            shared_title([label, *(part_titles or [])]) if any(i.get("relation") == "part" for i in inputs) else "",
        ),
        tk.people(narr),
        asin,
        year,
        input_minutes(inputs),
        str(choices.get("language") or ""),
        (inputs[0].get("path") or "") if inputs else "",
        list(dict.fromkeys(hints)),
        band,
        channels_in(
            label,
            str(choices.get("title") or ""),
            str(choices.get("author") or ""),
            str(folder.get("folder") or ""),
            *[
                str((tags.get(f) or {}).get("value") or "")
                for f in ("album", "artist", "album_artist", "composer", "publisher", "genre")
            ],
        ),
    )


def load(
    s, kinds=ALL_KINDS, ids=None
) -> list[Query]:  # pragma: no cover - Postgres: tests/unit/store/test_linkage_store.py
    """Queries for every live listen of these kinds (all of them by default: a radio programme is a candidate source too), or the given ids."""
    from sqlalchemy import text

    where = "l.merged_into_id is null and l.retired_at is null and l.kind = any(:kinds)" + (
        " and l.id = any(:ids)" if ids else ""
    )
    rows = s.execute(
        text(f"""select l.id, l.kind, l.label,
        (select jsonb_object_agg(c.field, c.value) from listen_choice c where c.listen_id = l.id and c.unchosen_at is null) as choices,
        (select jsonb_object_agg(r.source, r.payload) from listen_part p join part_record r on r.id = p.record_id where p.listen_id = l.id and p.role = 'record' and p.unlinked_at is null) as records,
        (select jsonb_agg(p.detail order by p.ordinal) from listen_part p where p.listen_id = l.id and p.role = 'input' and p.unlinked_at is null) as inputs,
        (select band from listen_kind k where k.listen_id = l.id) as band,
        (select loc.path from listen_part p join part_filegroup fg on fg.id = p.filegroup_id and fg.file_count = 1 join part_filegroup_member m on m.group_id = fg.id and m.file_id is not null join part_file_location loc on loc.file_id = m.file_id and loc.gone_at is null
          where p.listen_id = l.id and p.role = 'input' and p.unlinked_at is null order by p.ordinal limit 1) as file_path,
        (select array_agg(regexp_replace(loc.path, '^.*/', '') order by loc.path) from (select p.filegroup_id from listen_part p where p.listen_id = l.id and p.role = 'input' and p.unlinked_at is null and p.filegroup_id is not null order by p.ordinal limit 1) f
          join part_filegroup_member m on m.group_id = f.filegroup_id and m.file_id is not null join part_file_location loc on loc.file_id = m.file_id and loc.gone_at is null) as names
        , (select array_agg(a.label order by a.id) from listen a where a.merged_into_id = l.id) as absorbed
        from listen l where {where} order by l.id"""),
        dict(kinds=list(kinds), ids=list(ids or [])),
    ).all()
    out = []
    for lid, kind, label, choices, records, inputs, band, file_path, names, absorbed in rows:
        records = records or {}
        out.append(
            from_parts(
                lid,
                kind,
                label or "",
                choices or {},
                records.get("embedded_tags") or {},
                records.get("folder_name") or {},
                inputs or [],
                band or "",
                os.path.basename(file_path or ""),
                names or [],
                absorbed or [],
            )
        )
    return out


def relpath(path: str) -> str:
    """The library-relative path (after the audio root), for matching listens to labels made under another mount point."""
    parts = os.path.normpath(path or "").split(os.sep)
    for i, p in enumerate(parts):
        if p in ("audio", "audio-local"):
            return "/".join(parts[i + 1 :])
    return "/".join(parts[-2:])
