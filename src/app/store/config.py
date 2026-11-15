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
    from sqlalchemy.orm import aliased

    if ids is not None and not ids:
        return {}
    m1 = aliased(PartFilegroupMember)
    m2 = aliased(PartFilegroupMember)
    fid = func.coalesce(ListenPart.file_id, m1.file_id, m2.file_id)
    file_mtime = (
        select(
            ListenPart.listen_id.label("listen_id"),
            ListenPart.id.label("part_id"),
            func.coalesce(ListenPart.detail["relation"].astext, "copy").label("rel"),
            func.min(PartFileLocation.mtime_ns).label("mtime_ns"),
        )
        .outerjoin(m1, m1.group_id == ListenPart.filegroup_id)
        .outerjoin(m2, m2.group_id == m1.child_group_id)
        .join(PartFileLocation, PartFileLocation.file_id == fid)
        .where(ListenPart.role == "input", ListenPart.unlinked_at.is_(None))
    )
    if ids is not None:
        file_mtime = file_mtime.where(ListenPart.listen_id.in_(ids))
    file_mtime_sq = file_mtime.group_by(ListenPart.listen_id, ListenPart.id, fid).subquery()
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


def input_paths(s: Session, listen: Listen) -> tuple[str, list[str]]:
    """The source folder and ordered audio file paths for a listen."""
    import os

    from app import details
    from app.process import probe

    inputs = list(
        s.scalars(
            select(ListenPart)
            .where(ListenPart.listen_id == listen.id, ListenPart.role == "input", ListenPart.unlinked_at.is_(None))
            .order_by(ListenPart.ordinal)
        )
    )
    parts = [p for p in inputs if (p.detail or {}).get("relation") == "part"]
    selected = parts if parts else inputs[:1]
    folder = str((selected[0].detail or {}).get("path") or "") if selected else ""
    file_entries: list[tuple[int, str | None]] = []
    for p in selected:
        if p.filegroup_id:
            file_entries.extend(_group_files(s, p.filegroup_id))
    paths: list[str] = []
    if file_entries:
        fids = [fid for fid, _ in file_entries]
        locs: dict[int, list[str]] = {}
        for fid, path in s.execute(
            select(PartFileLocation.file_id, PartFileLocation.path)
            .where(PartFileLocation.file_id.in_(fids), PartFileLocation.gone_at.is_(None))
            .order_by(PartFileLocation.id.desc())
        ):
            locs.setdefault(fid, []).append(path)
        for fid, gpath in file_entries:
            cands = locs.get(fid, [])
            picked = next((p for p in cands if gpath and os.path.dirname(p) == gpath), cands[0] if cands else "")
            if picked and os.path.isfile(picked):
                paths.append(picked)
    if not paths and folder and os.path.isdir(folder):
        paths = [p for p in sorted(details.audio_files(folder), key=probe.natural_key) if os.path.isfile(p)]
    return folder, paths


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
        if os.path.isfile(os.path.join(cand, "metadata.json")):
            out_dir = cand
    if out_dir is None:
        meta = selected_metadata(s, listen, cur)
        if meta["title"] and meta["author"]:
            cand = P.out_dir_for(
                dict(title=meta["title"], author=meta["author"]),
                final_dir or settings.final_dir(),
                meta["year"],
            )
            if os.path.isfile(os.path.join(cand, "metadata.json")):
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
    summary = R.execute(book, plan, force=True)
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
