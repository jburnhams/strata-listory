"""A listen's output configuration: for each field of the finished audiobook (title, author, narrator...), every value any source offers, which one is chosen now and who chose it, so a person can pick the source of each field before the plan is made.

Sources: the embedded tags and the folder name (the two `record` parts of the listen), each Audible recording of the accepted work (the local Audible index, by ASIN), the accepted Open Library work, and a value typed by hand. A choice is the listen's `listen_choice` for the field (one active per field); this module only reads them and writes the person's pick."""

from __future__ import annotations

import re
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.store import listens as L
from app.store.models import Listen, ListenChoice, ListenPart, PartRecord, Work, WorkIdentifier

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
    """Every candidate value for the listen's fields, and facts the page shows beside them (the work, the Audible recordings)."""
    cands = Candidates()
    facts: dict[str, Any] = dict(work=None, recordings=[])
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
        elif rec.source == "folder_name":
            for field in ("title", "author", "narrator", "year", "asin"):
                if p.get(field):
                    cands.add(field, p[field], "folder", "Folder name", p.get("folder") or "")
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
        cands.add("title", work.title, "openlibrary", "Open Library work", (ids.get("work") or [""])[0])
        cands.add("author", d.get("authors") or [], "openlibrary", "Open Library work", (ids.get("work") or [""])[0])
        cands.add(
            "year",
            d.get("year") or work.year,
            "openlibrary",
            "Open Library work (first published)",
            (ids.get("work") or [""])[0],
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


def config(s: Session, listen: Listen, *, audible=None) -> dict:
    """The page's data: each field with its candidates (largest agreement first), the chosen value and how it was chosen (`rule` from tags or the folder name, `model`, `user`), and the recordings of the work."""
    cands, facts = gather(s, listen, audible=audible)
    cur = chosen(s, listen.id)
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
    cover = cur.get("cover")
    return dict(
        listen_id=listen.id,
        label=listen.label,
        kind=listen.kind,
        status=listen.status,
        fields=fields,
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
