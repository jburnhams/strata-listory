"""A listen's output configuration: for each field of the finished audiobook (title, author, narrator...), every value any source offers, which one is chosen now and who chose it, so a person can pick the source of each field before the plan is made.

Sources: the embedded tags and the folder name (the two `record` parts of the listen), each Audible recording of the accepted work (the local Audible index, by ASIN), the accepted Open Library work, and a value typed by hand. A choice is the listen's `listen_choice` for the field (one active per field); this module only reads them and writes the person's pick."""

from __future__ import annotations

import re
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.store import listens as L
from app.store.models import (
    CoverImage,
    Listen,
    ListenChoice,
    ListenPart,
    PartFile,
    PartFilegroup,
    PartFilegroupMember,
    PartFileLocation,
    PartRecord,
    Work,
    WorkIdentifier,
)

FIELDS = (
    "title",
    "subtitle",
    "author",
    "narrator",
    "series",
    "series_position",
    "year",
    "publisher",
    "language",
    "asin",
    "isbn",
)
LABEL = dict(
    title="Title",
    subtitle="Subtitle",
    author="Author",
    narrator="Narrator",
    series="Series",
    series_position="Series number",
    year="Year",
    publisher="Publisher",
    language="Language",
    asin="ASIN",
    isbn="ISBN",
)
TAG_FIELD = dict(
    title=("album", "title"),
    author=("album_artist", "artist"),
    narrator=("narratedby", "composer"),
    year=("date",),
    publisher=("publisher",),
    language=("language",),
    asin=("audible_asin",),
)


def _key(field: str, value: Any) -> str:
    """What makes two values of a field the same option: equal ignoring case and spacing. Spellings stay distinct (the person picks the exact one: "The Glass Orchard" is not "Glass Orchard")."""
    return " ".join(str(value).lower().split())


def _year(v: Any) -> int | None:
    m = re.search(r"(?<!\d)(1[5-9]\d\d|20\d\d)(?!\d)", str(v or ""))
    return int(m.group(1)) if m else None


class Candidates:
    """Values per field, merged when equal, each with every source that offered it, in the order first met."""

    def __init__(self) -> None:
        self.by_field: dict[str, dict[str, dict]] = {f: {} for f in FIELDS}

    def add(self, field: str, value: Any, source: str, label: str, ref: str = "", note: str = "") -> None:
        if value in (None, "", []):
            return
        if field in ("author", "narrator") and isinstance(value, list):
            value = ", ".join(value)
        if field == "year":
            value = _year(value)
            if not value:
                return
        if field == "series_position":
            value = str(value).strip()
        entry = self.by_field[field].setdefault(_key(field, value), dict(value=value, sources=[]))
        entry["sources"].append(dict(source=source, label=label, ref=ref, note=note))

    def lists(self) -> dict[str, list[dict]]:
        return {f: list(d.values()) for f, d in self.by_field.items()}


def audible_rows(asins: list[str], connect=None) -> list[dict]:
    """The Audible index's row (UK first) and credits for each ASIN."""
    from app.identify import audibleindex

    c = (connect or audibleindex.connect)()
    if c is None or not asins:
        return []
    out = []
    try:
        for asin in asins[:12]:
            row = c.execute("SELECT * FROM book WHERE asin=? ORDER BY region='uk' DESC LIMIT 1", (asin,)).fetchone()
            if not row:
                continue
            credits = c.execute(
                "SELECT role, name FROM credit WHERE asin=? AND region=? ORDER BY role, person_key",
                (asin, row["region"]),
            ).fetchall()
            out.append(
                dict(
                    asin=asin,
                    region=row["region"],
                    title=row["title"],
                    subtitle=row["subtitle"],
                    authors=[r["name"] for r in credits if r["role"] == "Author"],
                    narrators=[r["name"] for r in credits if r["role"] == "Narrator"],
                    series=row["series"],
                    series_position=row["series_pos"],
                    runtime_minutes=row["runtime_min"],
                    language=row["language"],
                    release=row["release"],
                    publisher=row["publisher"],
                    isbn=row["isbn"],
                    abridged=None if row["abridged"] is None else bool(row["abridged"]),
                )
            )
    finally:
        c.close()
    return out


def gather(s: Session, listen: Listen, *, audible=None) -> tuple[Candidates, dict]:
    """Every candidate value for the listen's fields, and facts the page shows beside them (the work, the Audible recordings, and each source column's values)."""
    cands = Candidates()
    orig: dict[str, Any] = dict.fromkeys(FIELDS)
    ol: dict[str, Any] = dict.fromkeys(FIELDS)
    facts: dict[str, Any] = dict(work=None, recordings=[], original=orig, openlibrary=ol)
    for part in s.scalars(
        select(ListenPart)
        .where(ListenPart.listen_id == listen.id, ListenPart.role == "record", ListenPart.unlinked_at.is_(None))
        .order_by(ListenPart.ordinal)
    ):
        rec = s.get(PartRecord, part.record_id) if part.record_id else None
        if rec is None:
            continue
        p = rec.payload
        if rec.source == "embedded_tags":
            for field, tags in TAG_FIELD.items():
                for t in tags:
                    if t in p and p[t].get("value"):
                        cands.add(
                            field,
                            p[t]["value"],
                            "tags",
                            f"Embedded tag “{t}”",
                            t,
                            f"{p[t]['n']} of {p.get('tagged')} tagged files",
                        )
                        if orig[field] is None:
                            v = _year(p[t]["value"]) if field == "year" else p[t]["value"]
                            if v not in (None, ""):
                                orig[field] = v
        elif rec.source == "folder_name":
            for field in ("title", "author", "narrator", "year", "asin"):
                if p.get(field):
                    cands.add(field, p[field], "folder", "Folder name", p.get("folder") or "")
                    if orig[field] is None:
                        v = _year(p[field]) if field == "year" else p[field]
                        if v not in (None, ""):
                            orig[field] = v
    found = s.scalars(
        select(ListenPart)
        .where(
            ListenPart.listen_id == listen.id,
            ListenPart.role.in_(("match", "candidate")),
            ListenPart.state == "accepted",
            ListenPart.unlinked_at.is_(None),
            ListenPart.work_id.is_not(None),
        )
        .order_by(ListenPart.ordinal)
    ).first()
    if found:
        work = s.get(Work, found.work_id)
        ids: dict[str, list[str]] = {}
        for i in s.scalars(select(WorkIdentifier).where(WorkIdentifier.work_id == work.id).order_by(WorkIdentifier.id)):
            ids.setdefault(i.scheme, []).append(i.value)
        d = found.detail or {}
        facts["work"] = dict(
            id=work.id,
            title=work.title,
            authors=d.get("authors") or [],
            year=d.get("year") or work.year,
            confirmed=found.linked_by == "user" or None,
        )
        ol_ref = (ids.get("work") or [""])[0]
        ol["title"] = work.title or None
        ol["author"] = ", ".join(d.get("authors") or []) or None
        ol["year"] = _year(d.get("year") or work.year)
        ol["isbn"] = (ids.get("isbn") or [None])[0]
        cands.add("title", work.title, "openlibrary", "Open Library work", ol_ref)
        cands.add("author", d.get("authors") or [], "openlibrary", "Open Library work", ol_ref)
        cands.add(
            "year",
            d.get("year") or work.year,
            "openlibrary",
            "Open Library work (first published)",
            ol_ref,
        )
        for isbn in ids.get("isbn", [])[:12]:
            cands.add("isbn", isbn, "openlibrary", "Open Library edition", isbn)
        for r in (audible or audible_rows)(ids.get("asin", [])):
            label = f"Audible {r['region'].upper()} · {r['asin']}"
            facts["recordings"].append(r)
            cands.add("title", r["title"], "audible", label, r["asin"])
            cands.add("subtitle", r["subtitle"], "audible", label, r["asin"])
            cands.add("author", r["authors"], "audible", label, r["asin"])
            cands.add("narrator", r["narrators"], "audible", label, r["asin"])
            cands.add("series", r["series"], "audible", label, r["asin"])
            cands.add("series_position", r["series_position"], "audible", label, r["asin"])
            cands.add("year", r["release"], "audible", label, r["asin"])
            cands.add("publisher", r["publisher"], "audible", label, r["asin"])
            cands.add("language", r["language"], "audible", label, r["asin"])
            cands.add("asin", r["asin"], "audible", label, r["asin"])
            cands.add("isbn", r["isbn"], "audible", label, r["asin"])
    return cands, facts


def chosen(s: Session, listen_id: int) -> dict[str, ListenChoice]:
    return {
        c.field: c
        for c in s.scalars(
            select(ListenChoice).where(ListenChoice.listen_id == listen_id, ListenChoice.unchosen_at.is_(None))
        )
    }


def _recording_values(r: dict) -> dict[str, Any]:
    return dict(
        title=r.get("title") or None,
        subtitle=r.get("subtitle") or None,
        author=", ".join(r["authors"]) if r.get("authors") else None,
        narrator=", ".join(r["narrators"]) if r.get("narrators") else None,
        series=r.get("series") or None,
        series_position=str(r["series_position"]).strip() if r.get("series_position") not in (None, "") else None,
        year=_year(r.get("release")),
        publisher=r.get("publisher") or None,
        language=r.get("language") or None,
        asin=r.get("asin") or None,
        isbn=r.get("isbn") or None,
    )


def config(s: Session, listen: Listen, *, audible=None) -> dict:
    """The page's data: each field with its candidates (largest agreement first), the chosen value and how it was chosen (`rule` from tags or the folder name, `model`, `user`), the source columns (Original, each Audible recording, Open Library) with which fields currently use them, and the recordings of the work."""
    cands, facts = gather(s, listen, audible=audible)
    cur = chosen(s, listen.id)
    orig = dict(facts["original"])
    if all(v is None for v in orig.values()):
        for f in FIELDS:
            if (c := cur.get(f)) and c.chosen_by in ("rule", "import") and c.value not in (None, ""):
                orig[f] = c.value
    fields = []
    for f in FIELDS:
        options = cands.lists()[f]
        c = cur.get(f)
        value = c.value if c else None
        match = next((o for o in options if value is not None and _key(f, o["value"]) == _key(f, value)), None)
        if c is not None and match is None:
            options = [
                dict(
                    value=value,
                    sources=[
                        dict(
                            source="typed" if c.chosen_by == "user" else c.chosen_by,
                            label="Typed by hand" if c.chosen_by == "user" else "Chosen earlier",
                            ref="",
                            note="",
                        )
                    ],
                )
            ] + options
            match = options[0]
        for o in options:
            o["chosen"] = o is match
        options.sort(key=lambda o: (not o["chosen"], -len(o["sources"])))
        fields.append(dict(field=f, label=LABEL[f], value=value, chosen_by=c.chosen_by if c else None, options=options))
    sources = [
        dict(id="original", kind="original", label="Original", values=orig),
        *[
            dict(
                id=f"audible:{r['asin']}",
                kind="audible",
                label=f"Audible {r['region'].upper()} · {r['asin']}",
                asin=r["asin"],
                region=r["region"],
                runtime_minutes=r.get("runtime_minutes"),
                abridged=r.get("abridged"),
                values=_recording_values(r),
            )
            for r in facts["recordings"]
        ],
        dict(id="openlibrary", kind="openlibrary", label="Open Library", values=dict(facts["openlibrary"])),
    ]
    for src in sources:
        used_fields = [
            f
            for f in FIELDS
            if (c := cur.get(f)) is not None
            and c.value is not None
            and src["values"].get(f) is not None
            and _key(f, src["values"][f]) == _key(f, c.value)
        ]
        src["used_fields"] = used_fields
        src["used"] = bool(used_fields)
    cover = cur.get("cover")
    return dict(
        listen_id=listen.id,
        label=listen.label,
        kind=listen.kind,
        status=listen.status,
        fields=fields,
        sources=sources,
        cover=cover.value if cover else None,
        work=facts["work"],
        recordings=facts["recordings"],
    )


def choose_field(
    s: Session, listen: Listen, field: str, value: Any, *, source: str = "", actor_ref: str = "web"
) -> ListenChoice | None:
    """The person's pick for a field (a value from a source or typed by hand); an empty value clears the choice."""
    if field not in FIELDS:
        raise ValueError(f"unknown field {field!r}")
    if value in (None, ""):
        old = chosen(s, listen.id).get(field)
        if old:
            from datetime import UTC, datetime

            old.unchosen_at = datetime.now(UTC).replace(tzinfo=None)
            L._event(s, listen, "unchoose", "user", actor_ref=actor_ref, before={"field": field, "value": old.value})
        return None
    if field == "year":
        value = _year(value)
        if not value:
            raise ValueError("the year must be four digits")
    return L.choose(
        s,
        listen,
        field,
        value,
        actor_kind="user",
        actor_ref=actor_ref,
        reason=f"chosen from {source or 'a typed value'}",
    )


def choose_fields(
    s: Session, listen: Listen, values: dict[str, Any], *, source: str = "", actor_ref: str = "web"
) -> None:
    """Apply several field choices at once (such as clicking a source column on the Library page)."""
    for field, value in values.items():
        choose_field(s, listen, field, value, source=source, actor_ref=actor_ref)


def _group_files(s: Session, group_id: int) -> list[tuple[int, str | None]]:
    """Ordered (file_id, group_path) pairs inside a filegroup and its child groups."""
    grp = s.get(PartFilegroup, group_id)
    gpath = grp.path if grp else None
    out: list[tuple[int, str | None]] = []
    for m in s.scalars(
        select(PartFilegroupMember)
        .where(PartFilegroupMember.group_id == group_id)
        .order_by(PartFilegroupMember.ordinal)
    ):
        if m.file_id:
            out.append((m.file_id, gpath))
        elif m.child_group_id:
            out.extend(_group_files(s, m.child_group_id))
    return out


def listen_mtimes(s: Session, ids: list[int] | None = None) -> dict[int, float]:
    """Original source file last-modified timestamp (epoch seconds) for each listen.
    Uses the earliest mtime observed for each file across locations (so a copy synced without preserving mtime does not mask the original source mtime), the latest track within each input part, and the earliest copy when a listen has multiple alternative copies."""
    from sqlalchemy import func

    if ids is not None and not ids:
        return {}
    base_parts = select(
        ListenPart.listen_id.label("listen_id"),
        ListenPart.id.label("part_id"),
        func.coalesce(ListenPart.detail["relation"].astext, "copy").label("rel"),
        ListenPart.file_id.label("file_id"),
        ListenPart.filegroup_id.label("group_id"),
    ).where(ListenPart.role == "input", ListenPart.unlinked_at.is_(None))
    if ids is not None:
        base_parts = base_parts.where(ListenPart.listen_id.in_(ids))
    part_tree = base_parts.cte(name="part_tree", recursive=True)
    child_members = select(
        part_tree.c.listen_id,
        part_tree.c.part_id,
        part_tree.c.rel,
        PartFilegroupMember.file_id,
        PartFilegroupMember.child_group_id.label("group_id"),
    ).join(PartFilegroupMember, PartFilegroupMember.group_id == part_tree.c.group_id)
    part_tree = part_tree.union_all(child_members)
    file_mtime_sq = (
        select(
            part_tree.c.listen_id,
            part_tree.c.part_id,
            part_tree.c.rel,
            func.min(PartFileLocation.mtime_ns).label("mtime_ns"),
        )
        .join(PartFileLocation, PartFileLocation.file_id == part_tree.c.file_id)
        .where(part_tree.c.file_id.is_not(None))
        .group_by(part_tree.c.listen_id, part_tree.c.part_id, part_tree.c.rel, part_tree.c.file_id)
        .subquery()
    )
    part_mtime_sq = (
        select(
            file_mtime_sq.c.listen_id,
            file_mtime_sq.c.rel,
            func.max(file_mtime_sq.c.mtime_ns).label("mtime_ns"),
        )
        .group_by(file_mtime_sq.c.listen_id, file_mtime_sq.c.part_id, file_mtime_sq.c.rel)
        .subquery()
    )
    stmt = select(
        part_mtime_sq.c.listen_id,
        func.coalesce(
            func.max(part_mtime_sq.c.mtime_ns).filter(part_mtime_sq.c.rel == "part"),
            func.min(part_mtime_sq.c.mtime_ns),
        ),
    ).group_by(part_mtime_sq.c.listen_id)
    return {int(lid): float(ns) / 1e9 for lid, ns in s.execute(stmt) if ns is not None}


def _resolve_part_files(s: Session, part: ListenPart) -> list[str] | None:
    """Resolve the complete ordered audio file paths for one input ListenPart, or None if any expected file is unavailable on disk."""
    import os

    from app import details
    from app.process import probe

    entries: list[tuple[int, str | None]] = []
    if part.filegroup_id:
        entries.extend(_group_files(s, part.filegroup_id))
    elif part.file_id:
        entries.append((part.file_id, None))
    if entries:
        fids = [fid for fid, _ in entries]
        locs: dict[int, list[str]] = {}
        for fid, path in s.execute(
            select(PartFileLocation.file_id, PartFileLocation.path)
            .where(PartFileLocation.file_id.in_(fids), PartFileLocation.gone_at.is_(None))
            .order_by(PartFileLocation.id.desc())
        ):
            if os.path.isfile(path):
                locs.setdefault(fid, []).append(path)
        resolved: list[str] = []
        for fid, gpath in entries:
            cands = locs.get(fid, [])
            picked = next((p for p in cands if gpath and os.path.dirname(p) == gpath), cands[0] if cands else "")
            if not picked:
                return None
            resolved.append(picked)
        return resolved
    part_folder = str((part.detail or {}).get("path") or "")
    if part_folder and os.path.isdir(part_folder):
        found = [p for p in sorted(details.audio_files(part_folder), key=probe.natural_key) if os.path.isfile(p)]
        if found:
            return found
    return None


def input_paths(s: Session, listen: Listen) -> tuple[str, list[str]]:
    """The source folder and complete ordered audio file paths for a listen (empty list if any required track or part is unavailable on disk)."""
    inputs = list(
        s.scalars(
            select(ListenPart)
            .where(ListenPart.listen_id == listen.id, ListenPart.role == "input", ListenPart.unlinked_at.is_(None))
            .order_by(ListenPart.ordinal)
        )
    )
    parts = [p for p in inputs if (p.detail or {}).get("relation") == "part"]
    if parts:
        folder = str((parts[0].detail or {}).get("path") or "")
        paths: list[str] = []
        for p in parts:
            part_paths = _resolve_part_files(s, p)
            if not part_paths:
                return folder, []
            paths.extend(part_paths)
        return folder, paths
    for p in inputs:
        folder = str((p.detail or {}).get("path") or "")
        part_paths = _resolve_part_files(s, p)
        if part_paths:
            return folder, part_paths
    folder = str((inputs[0].detail or {}).get("path") or "") if inputs else ""
    return folder, []


def _parse_track_tag(raw: Any) -> tuple[int | None, int | None]:
    """Extract (track_number, track_total) from a tag like '3', '03/12', or '3 of 12'."""
    if raw in (None, ""):
        return None, None
    m = re.match(r"^\s*(\d{1,4})(?:\s*(?:/|of)\s*(\d{1,4}))?\s*$", str(raw), re.I)
    if not m:
        return None, None
    n = int(m.group(1))
    tot = int(m.group(2)) if m.group(2) else None
    if n <= 0 or n >= 1000:
        return None, None
    if tot is not None and (tot <= 0 or tot >= 1000 or tot < n):
        tot = None
    return n, tot


def _filename_track_nums(names: list[str]) -> list[tuple[int | None, int | None]]:
    """Infer per-file (number, expected_total) from basenames when filenames carry chapter/track/episode numbers."""
    import os

    stems = [os.path.splitext(n)[0] for n in names]
    cleaned: list[str] = []
    explicit: list[tuple[int | None, int | None]] = []
    for s in stems:
        sc = re.sub(r"\[[^\]]*\]", " ", s)
        sc = re.sub(r"\b(?:19|20)\d\d[-.]\d\d[-.]\d\d\b", " ", sc)
        sc = re.sub(r"(?<!\d)(?:19|20)\d\d(?!\d)", " ", sc)
        sc = re.sub(r"\b\d+\s*(?:kbps|khz|k|mb|gb|bit)\b", " ", sc, flags=re.I)
        cleaned.append(sc)
        m_of = re.search(r"\b(\d{1,3})\s*(?:of|_of_)\s*(\d{1,3})\b|\((\d{1,3})\s*-\s*(\d{1,3})\)", sc, re.I)
        if m_of:
            k = int(m_of.group(1) or m_of.group(3))
            tot = int(m_of.group(2) or m_of.group(4))
            if 1 <= k <= tot <= 500:
                explicit.append((k, tot))
                continue
        m_se = re.search(r"(?<![a-z0-9])s\d{1,2}[\s._-]*e(\d{1,3})(?![a-z0-9])", sc, re.I)
        if m_se:
            explicit.append((int(m_se.group(1)), None))
            continue
        m_ser = re.search(r"\bseries[\s._-]*\d+[\s._-]+(\d{1,3})(?!\d)", sc, re.I)
        if m_ser:
            explicit.append((int(m_ser.group(1)), None))
            continue
        m_kw = re.search(r"\b(?:chapter|chap|ch|track|trk|episode|ep)[\s._-]*(\d{1,3})\b", sc, re.I)
        if m_kw:
            explicit.append((int(m_kw.group(1)), None))
            continue
        explicit.append((None, None))
    explicit_count = sum(1 for n, _ in explicit if n is not None)
    if explicit_count >= (1 if len(names) == 1 else max(2, int(0.7 * len(names)))):
        return explicit
    if len(names) == 1:
        m1 = re.search(r"(?:part[\s._-]*(\d{1,2})|(?:^|[\s._-])0+(\d{1,2}))\s*$", cleaned[0], re.I)
        if m1:
            v = int(m1.group(1) or m1.group(2))
            if v >= 1:
                return [(v, None)]
        return [(None, None)]
    int_lists = [[int(x) for x in re.findall(r"\d+", c)] for c in cleaned]
    best: list[tuple[int | None, int | None]] | None = None
    best_score = (0, 0)
    max_cap = len(names) * 3
    for col in (0, -1, 1):
        vals = [
            lst[col] if (col >= 0 and len(lst) > col) or (col < 0 and len(lst) >= -col) else None for lst in int_lists
        ]
        present = [v for v in vals if v is not None and 1 <= v <= max_cap]
        if (
            len(present) >= max(2, int(0.7 * len(names)))
            and len(set(present)) >= max(2, int(0.7 * len(present)))
            and min(present) <= 3
        ):
            score = (len(set(present)), len(present))
            if score > best_score:
                best_score = score
                best = [(v if v is not None and 1 <= v <= max_cap else None, None) for v in vals]
    return best if best is not None else [(None, None) for _ in names]


def _check_track_gaps(rows: list[tuple[str, int | None, int | None]], total_listen_files: int) -> list[str]:
    """Flag missing track/chapter numbers (1..max_n) or fewer files than expected max_n in a group."""
    fnums = _filename_track_nums([r[0] for r in rows])
    distinct_tag_nums = {r[1] for r in rows if r[1] is not None}
    use_fn_over_const_tag = len(rows) > 1 and len(distinct_tag_nums) <= 1
    nums: list[int] = []
    totals: set[int] = set()
    for (_, t_num, t_tot), (f_num, f_tot) in zip(rows, fnums):
        num = f_num if (f_num is not None and (t_num is None or use_fn_over_const_tag)) else (t_num or f_num)
        if num is not None:
            nums.append(num)
        if t_tot is not None and t_tot != total_listen_files:
            totals.add(t_tot)
        if f_tot is not None and f_tot != total_listen_files:
            totals.add(f_tot)
    if not nums:
        return []
    max_n = max([*nums, *totals]) if len(totals) == 1 else max(nums)
    if min(nums) > 3 or max_n > max(len(rows) * 3, 4):
        return []
    missing = sorted(set(range(1, max_n + 1)) - set(nums))
    if len(rows) >= max_n and not missing:
        return []
    if missing:
        shown = ", ".join(str(x) for x in missing[:5]) + ("…" if len(missing) > 5 else "")
        noun = "track" if len(missing) == 1 else "tracks"
        fword = "file" if len(rows) == 1 else "files"
        return [f"Missing {noun} {shown} ({len(rows)} {fword}, expected {max_n})"]
    return [f"Only {len(rows)} audio files found, expected {max_n}"]


def source_warnings(s: Session, listen: Listen) -> list[str]:
    """Detect unavailable source files or gaps in track/chapter numbering for a listen in advance of export."""
    import os
    from collections import defaultdict

    inputs = list(
        s.scalars(
            select(ListenPart)
            .where(ListenPart.listen_id == listen.id, ListenPart.role == "input", ListenPart.unlinked_at.is_(None))
            .order_by(ListenPart.ordinal)
        )
    )
    if not inputs:
        return ["No source audio files linked"]
    parts = [p for p in inputs if (p.detail or {}).get("relation") == "part"]
    selected = parts if parts else inputs[:1]
    _, disk_paths = input_paths(s, listen)
    warnings: list[str] = []
    if not disk_paths:
        warnings.append("Source audio files are missing or incomplete on disk")

    entries: list[tuple[int, str | None]] = []
    for p in selected:
        if p.filegroup_id:
            entries.extend(_group_files(s, p.filegroup_id))
        elif p.file_id:
            entries.append((p.file_id, None))
    if not entries:
        return warnings

    fids = [fid for fid, _ in entries]
    pfs = {f.id: f for f in s.scalars(select(PartFile).where(PartFile.id.in_(fids)))}
    locs: dict[int, list[str]] = defaultdict(list)
    for fid, path in s.execute(
        select(PartFileLocation.file_id, PartFileLocation.path)
        .where(PartFileLocation.file_id.in_(fids), PartFileLocation.gone_at.is_(None))
        .order_by(PartFileLocation.id.desc())
    ):
        locs[fid].append(path)

    by_grp: dict[str, list[tuple[str, int | None, int | None, int | None]]] = defaultdict(list)
    for fid, gpath in entries:
        pf = pfs.get(fid)
        tags = ((pf.probe or {}).get("format") or {}).get("tags") or {} if pf else {}
        tags_lower = {str(k).lower(): v for k, v in tags.items()}
        t_num, t_tot = _parse_track_tag(tags_lower.get("track"))
        d_num, _ = _parse_track_tag(tags_lower.get("disc") or tags_lower.get("discnumber"))
        cands = locs.get(fid, [])
        picked = next((p for p in cands if gpath and os.path.dirname(p) == gpath), cands[0] if cands else "")
        by_grp[gpath or ""].append((os.path.basename(picked), t_num, t_tot, d_num))

    for rows in by_grp.values():
        dnums = [r[3] for r in rows]
        if len({d for d in dnums if d is not None}) > 1 and all(d is not None for d in dnums):
            by_disc: dict[int, list[tuple[str, int | None, int | None]]] = defaultdict(list)
            for name, t_num, t_tot, d_num in rows:
                by_disc[int(d_num or 1)].append((name, t_num, t_tot))
            if any(len(v) > 1 for v in by_disc.values()):
                for disc_rows in by_disc.values():
                    warnings.extend(_check_track_gaps(disc_rows, len(entries)))
                continue
        warnings.extend(_check_track_gaps([(r[0], r[1], r[2]) for r in rows], len(entries)))
    return list(dict.fromkeys(warnings))


def selected_metadata(s: Session, listen: Listen, cur: dict[str, ListenChoice] | None = None) -> dict[str, Any]:
    """The chosen output metadata fields and cover path for a listen."""
    import os

    from app.covers import run as covers_run

    cur = cur if cur is not None else chosen(s, listen.id)

    def _str(field: str, default: str = "") -> str:
        c = cur.get(field)
        return str(c.value).strip() if c and c.value not in (None, "") else default

    pos_raw = _str("series_position")
    try:
        series_position: float | None = float(pos_raw) if pos_raw else None
    except ValueError:
        series_position = None
    year_c = cur.get("year")
    year = _year(year_c.value) if year_c and year_c.value is not None else None
    cover_path: str | None = None
    cov = cur.get("cover")
    if cov and isinstance(cov.value, dict) and cov.value.get("image_id") is not None:
        img = s.get(CoverImage, int(cov.value["image_id"]))
        if img:
            cand = covers_run.image_path(img)
            if os.path.isfile(cand):
                cover_path = cand
    return dict(
        title=_str("title", listen.label.strip()),
        subtitle=_str("subtitle"),
        author=_str("author"),
        narrator=_str("narrator"),
        series=_str("series"),
        series_position=series_position,
        year=year,
        publisher=_str("publisher"),
        language=_str("language"),
        asin=_str("asin"),
        isbn=_str("isbn"),
        cover_path=cover_path,
    )


def _export_job_info(listen_id: int, jobs=None) -> dict[str, Any] | None:
    if jobs is None:
        return None
    for t in jobs.tasks("listens"):
        if t.get("kind") == "export" and t.get("options", {}).get("listen_id") == listen_id:
            return dict(
                id=t["id"],
                state=t["state"],
                noise_reduction=bool(t.get("options", {}).get("noise_reduction")),
                progress=t.get("progress"),
                error="",
            )
    for f in jobs.failures(domain="listens"):
        try:
            import json

            opts = json.loads(f.get("options") or "{}")
        except ValueError:
            opts = {}
        if f.get("kind") == "export" and opts.get("listen_id") == listen_id:
            return dict(
                id=f["id"],
                state="failed",
                noise_reduction=bool(opts.get("noise_reduction")),
                progress=None,
                error=f.get("error") or "export failed",
            )
    return None


import threading

_EXPORT_DEST_LOCK = threading.Lock()
_ACTIVE_EXPORTS: dict[str, int] = {}


def _out_dir_belongs_to(
    s: Session,
    listen: Listen,
    cand: str,
    cur: dict[str, ListenChoice] | None = None,
) -> bool:
    """Whether an existing output folder on disk belongs to `listen`."""
    import json
    import os

    meta_file = os.path.join(cand, "metadata.json")
    if not os.path.isfile(meta_file):
        return False
    try:
        with open(meta_file, encoding="utf8") as f:
            data = json.load(f)
    except (OSError, ValueError):
        return False
    book_meta = data.get("book") if isinstance(data, dict) else None
    if not isinstance(book_meta, dict):
        return False
    if book_meta.get("listen_id") is not None:
        try:
            return int(book_meta["listen_id"]) == listen.id
        except (TypeError, ValueError):
            return False
    other = s.scalar(
        select(ListenChoice.listen_id).where(
            ListenChoice.field == "output",
            ListenChoice.unchosen_at.is_(None),
            ListenChoice.listen_id != listen.id,
            ListenChoice.value["out_dir"].astext == cand,
        )
    )
    if other is not None:
        return False
    out_c = (cur or {}).get("output")
    if out_c and isinstance(out_c.value, dict) and out_c.value.get("out_dir") == cand:
        return True
    book_path = str(book_meta.get("path") or "")
    if book_path:
        input_folders = {
            str((p.detail or {}).get("path") or "")
            for p in s.scalars(
                select(ListenPart).where(
                    ListenPart.listen_id == listen.id,
                    ListenPart.role == "input",
                    ListenPart.unlinked_at.is_(None),
                )
            )
            if (p.detail or {}).get("path")
        }
        return book_path in input_folders
    return True


def _out_dir_available_for(
    s: Session,
    listen: Listen,
    cand: str,
    cur: dict[str, ListenChoice] | None = None,
) -> bool:
    """Whether `cand` is free or already owned/reserved by `listen`."""
    import json
    import os

    active_owner = _ACTIVE_EXPORTS.get(cand)
    if active_owner is not None and active_owner != listen.id:
        return False
    lock_file = cand + ".lock"
    if os.path.isfile(lock_file):
        try:
            with open(lock_file, encoding="utf8") as f:
                lock_data = json.load(f)
            if int(lock_data.get("listen_id", -1)) != listen.id:
                return False
        except (OSError, ValueError, TypeError):
            return False
    other = s.scalar(
        select(ListenChoice.listen_id).where(
            ListenChoice.field == "output",
            ListenChoice.unchosen_at.is_(None),
            ListenChoice.listen_id != listen.id,
            ListenChoice.value["out_dir"].astext == cand,
        )
    )
    if other is not None:
        return False
    if os.path.exists(cand):
        return _out_dir_belongs_to(s, listen, cand, cur)
    return True


def output_status(
    s: Session,
    listen: Listen,
    *,
    cur: dict[str, ListenChoice] | None = None,
    jobs=None,
    final_dir: str | None = None,
) -> dict[str, Any] | None:
    """Existing exported output (chapters, loudness, folder) and/or active export job status for a listen."""
    import json
    import os

    from app import settings
    from app.process import plan as P

    cur = cur if cur is not None else chosen(s, listen.id)
    out_dir: str | None = None
    out_c = cur.get("output")
    if out_c and isinstance(out_c.value, dict) and out_c.value.get("out_dir"):
        cand = str(out_c.value["out_dir"])
        if _out_dir_belongs_to(s, listen, cand, cur):
            out_dir = cand
    if out_dir is None:
        meta = selected_metadata(s, listen, cur)
        if meta["title"] and meta["author"]:
            cand = P.out_dir_for(
                dict(title=meta["title"], author=meta["author"]),
                final_dir or settings.final_dir(),
                meta["year"],
            )
            if _out_dir_belongs_to(s, listen, cand, cur):
                out_dir = cand
    job = _export_job_info(listen.id, jobs)
    chapters: list[dict[str, Any]] = []
    res: dict[str, Any] = {}
    if out_dir:
        try:
            with open(os.path.join(out_dir, "metadata.json"), encoding="utf8") as f:
                data = json.load(f)
            res = data.get("result") or {}
            chapters = [
                dict(
                    index=i,
                    file=c["file"],
                    title=c["title"],
                    duration=c["duration"],
                    mode=c.get("mode"),
                    tier=c.get("tier"),
                )
                for i, c in enumerate(data.get("chapters") or [])
            ]
        except (OSError, ValueError, KeyError):
            out_dir = None
    if not chapters and job is None:
        return None
    return dict(
        status=job["state"]
        if job and job["state"] in ("queued", "running")
        else ("done" if chapters else job["state"]),
        out_dir=out_dir if chapters else None,
        finished=res.get("finished"),
        out_bytes=res.get("out_bytes"),
        out_seconds=res.get("out_seconds"),
        loudness_before=res.get("loudness_before"),
        loudness_after=res.get("loudness_after"),
        gain_db=res.get("gain_db"),
        noise_reduction=bool(res.get("enhancement")),
        chapters=chapters,
        job=job,
    )


def export_listen(
    s: Session,
    listen: Listen,
    *,
    noise_reduction: bool = False,
    final_dir: str | None = None,
    actor_ref: str = "web",
) -> dict[str, Any]:
    """Run the output pipeline (MP3 chapters, selected ID3 metadata + cover, loudness normalisation, optional noise reduction) for one listen."""
    import os

    from app import details, settings, sources
    from app.process import plan as P
    from app.process import run as R

    cur = chosen(s, listen.id)
    meta = selected_metadata(s, listen, cur)
    sources.require_identity(meta["title"], meta["author"])
    folder, paths = input_paths(s, listen)
    if not paths:
        raise ValueError("no source audio files are available on disk for this listen")
    side = details.sidecar(folder) if folder and os.path.exists(folder) else None
    audible = details.audible_summary(side) if side else None
    locked = [
        k
        for k in ("title", "author", "narrator", "series", "series_position", "year", "asin")
        if meta.get(k) not in (None, "")
    ]
    book = dict(
        id=listen.id,
        listen_id=listen.id,
        is_listen=True,
        title=meta["title"],
        subtitle=meta["subtitle"],
        author=meta["author"],
        narrator=meta["narrator"],
        series=meta["series"],
        series_position=meta["series_position"],
        year=meta["year"],
        year_source="chosen on Library page" if meta["year"] else "",
        publisher=meta["publisher"],
        language=meta["language"],
        asin=meta["asin"],
        isbn=meta["isbn"],
        path=folder,
        locked=locked,
        cover_path=meta["cover_path"],
    )
    records = dict(
        catalogue=dict(
            title=meta["title"],
            subtitle=meta["subtitle"],
            authors=[a.strip() for a in meta["author"].split(",") if a.strip()],
            narrators=[n.strip() for n in meta["narrator"].split(",") if n.strip()],
            publisher=meta["publisher"],
            series=meta["series"],
            series_position=meta["series_position"],
            original_year=meta["year"],
            year_kind="first_publication" if meta["year"] else "",
            asin=meta["asin"],
            language=meta["language"],
            isbns=[meta["isbn"]] if meta["isbn"] else [],
            genres=[],
        )
    )
    plan = P.make_plan(book, final_dir or settings.final_dir(), audible, None, records, input_paths=paths)
    plan.update(
        title=meta["title"],
        author=meta["author"],
        narrator=meta["narrator"],
        series=meta["series"],
        series_position=meta["series_position"],
        year=meta["year"],
        publisher=meta["publisher"],
    )
    if noise_reduction:
        for ch in plan["chapters"]:
            ch["mode"] = "encode"
            ch["tier"] = "V3"
        plan["est_bytes"] = int(sum(ch["duration"] * P.TIERS["V3"]["kbps"] * 1000 / 8 for ch in plan["chapters"]))
        plan["enhancement"] = dict(
            method="diamond-pause-level-v1",
            quality_rating="medium",
            seed=0,
            device="configured",
            level="gated rolling RMS ±3 dB",
        )
    import json

    base_out_dir = plan["out_dir"]
    cands = [base_out_dir]
    if meta["narrator"]:
        cands.append(f"{base_out_dir} ({P.clean_name(meta['narrator'])})")
    cands.append(f"{base_out_dir} (listen {listen.id})")
    with _EXPORT_DEST_LOCK:
        picked_dir = next((c for c in cands if _out_dir_available_for(s, listen, c, cur)), None)
        if picked_dir is None:
            raise ValueError(f"output folder {base_out_dir} already belongs to another listen")
        out_dir = picked_dir
        plan["out_dir"] = out_dir
        owns_existing = os.path.exists(out_dir) and _out_dir_belongs_to(s, listen, out_dir, cur)
        _ACTIVE_EXPORTS[out_dir] = listen.id
        os.makedirs(os.path.dirname(out_dir), exist_ok=True)
        with open(out_dir + ".lock", "w", encoding="utf8") as f:
            json.dump(dict(listen_id=listen.id), f)
    try:
        summary = R.execute(book, plan, force=owns_existing)
        L.choose(
            s,
            listen,
            "output",
            dict(
                out_dir=summary["out_dir"],
                finished=summary["finished"],
                chapters=summary["chapters"],
                out_bytes=summary["out_bytes"],
                out_seconds=summary["out_seconds"],
                loudness_after=summary["loudness_after"],
                noise_reduction=bool(noise_reduction),
            ),
            actor_kind="user",
            actor_ref=actor_ref,
            reason="exported from Library",
        )
        L.set_status(s, listen, "processed", actor_kind="user", actor_ref=actor_ref, reason="exported from Library")
        return summary
    finally:
        with _EXPORT_DEST_LOCK:
            if _ACTIVE_EXPORTS.get(out_dir) == listen.id:
                _ACTIVE_EXPORTS.pop(out_dir, None)
            try:
                os.remove(out_dir + ".lock")
            except OSError:
                pass


def export_job(listen_id: int, *, noise_reduction: bool = False) -> dict[str, Any]:
    """Background job entrypoint for exporting one listen."""
    from app.store.api import _engine

    with Session(_engine()) as s:
        listen = s.get(Listen, listen_id)
        if listen is None:
            raise ValueError(f"no listen {listen_id}")
        res = export_listen(s, listen, noise_reduction=noise_reduction, actor_ref="job")
        s.commit()
        return res
