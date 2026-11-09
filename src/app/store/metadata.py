"""Step 5: say what each listen is, from evidence, without overwriting any of it.

For each listen two metadata *records* (immutable parts) are linked as `record` listen-parts: what the files' embedded tags agree on (`embedded_tags`, with how many of the files say it) and what the folder name reads as (`folder_name`, via `scan.parse_name`). From them the *chosen* metadata (`listen_choice`: title, author, narrator, year, asin, language, publisher) is picked by simple rules, each choice pointing at the record it came from and logged as an event. Rules, in order of trust: tags that most files agree on, then the folder name. A person (or a later model step) can replace any choice; the records stay."""

from __future__ import annotations

import hashlib
import json
import os
import re
from collections import Counter

from sqlalchemy import select, text
from sqlalchemy.orm import Session

from app import scan
from app.store import listens as L
from app.store.models import Listen, ListenChoice, ListenPart, PartFilegroup, PartRecord

TAG_FIELDS = (
    "album",
    "title",
    "artist",
    "album_artist",
    "composer",
    "narratedby",
    "publisher",
    "copyright",
    "date",
    "genre",
    "language",
    "audible_asin",
)
GENERIC_TITLE = re.compile(r"^\s*(track\s*\d*|unknown\b.*|audiobook|disc\s*\d*|chapter\s*\d*|\d+)\s*$", re.I)


def canon(v: str) -> str:
    return re.sub(r"\s+", " ", v).strip()


def tag_summary(probes: list[dict | None]) -> dict:
    """{field: {'value', 'n', 'share'}} for the most common non-empty value of each tag among the files that have tags (container tags first, then the audio stream's), plus how many files were looked at."""
    counts: dict[str, Counter] = {f: Counter() for f in TAG_FIELDS}
    tagged = 0
    for p in probes:
        if not p:
            continue
        tags: dict[str, str] = {}
        for src in [
            *((st.get("tags") or {}) for st in p.get("streams", [])),
            (p.get("format") or {}).get("tags") or {},
        ]:
            tags.update({k.lower(): str(v) for k, v in src.items()})
        if not tags:
            continue
        tagged += 1
        for f in TAG_FIELDS:
            if v := canon(tags.get(f, "")):
                counts[f][v] += 1
    out: dict = {"files": len(probes), "tagged": tagged}
    for f, c in counts.items():
        if c:
            v, n = c.most_common(1)[0]
            out[f] = {"value": v, "n": n, "share": round(n / max(1, tagged), 2)}
    return out


def year_of(v: str | None) -> int | None:
    m = re.search(r"(?<!\d)(1[89]\d\d|20\d\d)(?!\d)", v or "")
    return int(m.group(1)) if m else None


def people(v: str) -> str:
    """'Henry Marsh/Henry Marsh' (a tag written twice) -> 'Henry Marsh'; different people stay joined."""
    seen: list[str] = []
    for part in re.split(r"\s*/\s*", v):
        if part and part.lower() not in [x.lower() for x in seen]:
            seen.append(part)
    return " / ".join(seen)


def choose_fields(tags: dict, name: dict) -> dict[str, tuple[object, str]]:
    """{field: (value, 'tags'|'folder_name')} by the rules in the module docstring."""

    def strong(f, minimum=0.5):
        t = tags.get(f)
        return t["value"] if t and t["share"] >= minimum else None

    out: dict[str, tuple[object, str]] = {}
    title = next(
        (
            v
            for f in ("album", "title")
            if (v := strong(f)) and not GENERIC_TITLE.match(v) and (f == "album" or tags["tagged"] <= 3)
        ),
        None,
    )
    if title:
        out["title"] = (title, "tags")
    elif name.get("title"):
        out["title"] = (name["title"], "folder_name")
    author = strong("album_artist") or strong("artist")
    if author and not GENERIC_TITLE.match(author):
        out["author"] = (people(author), "tags")
    elif name.get("author"):
        out["author"] = (name["author"], "folder_name")
    narrator = strong("narratedby") or (strong("composer") if tags.get("audible_asin") else None)
    if narrator:
        out["narrator"] = (people(narrator), "tags")
    elif name.get("narrator"):
        out["narrator"] = (name["narrator"], "folder_name")
    year = year_of(strong("date")) or None
    if year:
        out["year"] = (year, "tags")
    elif name.get("year"):
        out["year"] = (name["year"], "folder_name")
    asin = strong("audible_asin")
    if asin:
        out["asin"] = (asin, "tags")
    elif name.get("asin"):
        out["asin"] = (name["asin"], "folder_name")
    for f, key in (("language", "language"), ("publisher", "publisher")):
        if v := strong(f):
            out[key] = (v, "tags")
    return out


def _record(s: Session, source: str, kind: str, payload: dict) -> PartRecord:
    h = hashlib.sha256(json.dumps(payload, sort_keys=True, ensure_ascii=False).encode()).digest()
    r = s.scalars(
        select(PartRecord).where(PartRecord.source == source, PartRecord.kind == kind, PartRecord.payload_hash == h)
    ).first()
    if r is None:
        r = PartRecord(source=source, kind=kind, payload=payload, payload_hash=h)
        s.add(r)
        s.flush()
    return r


FILES_BELOW = text("""with recursive g(id) as (select cast(:gid as int) union all select m.child_group_id from part_filegroup_member m join g on m.group_id = g.id where m.child_group_id is not null)
select f.probe from g join part_filegroup_member m on m.group_id = g.id join part_file f on f.id = m.file_id""")


def name_parts(path: str, label: str, group: PartFilegroup | None) -> tuple[str, str, str]:
    """(name to parse, its parent folder, folder to record) for a listen. A folder's own name is what a folder-listen is called, but a stand-alone file (a whole-book m4b lying in a container folder, `from_group` -1) has the *container's* path with the file's name as label: reading the container made `Books/The Title.m4b` a book called "Books", and every file in a folder named after an author a book called by the author. The file's own name is the name; the container is then its parent (author fallback unless generic)."""
    if group is not None and group.file_count == 1 and (group.detail or {}).get("from_group") == -1 and group.label:
        return group.label, os.path.basename(path), group.label
    return os.path.basename(path) or label, os.path.basename(os.path.dirname(path)), os.path.basename(path)


def add_metadata(s: Session, *, actor_ref: str = "metadata-v1", redo: list[int] | None = None) -> dict[str, int]:
    """Records and choices for every live listen that has none yet (so a rerun after more listens appear only does the new ones). Returns counts. `redo`: listen ids whose metadata is read again after a rule change (their record parts are unlinked and new ones linked; fields a person chose are kept)."""
    for part in s.scalars(
        select(ListenPart).where(
            ListenPart.listen_id.in_(redo or []), ListenPart.role == "record", ListenPart.unlinked_at.is_(None)
        )
    ):
        L.unlink(s, part, actor_kind="rule", actor_ref=actor_ref, reason="metadata read again after a rule change")
    s.flush()
    done = {
        r
        for (r,) in s.execute(
            select(ListenPart.listen_id).where(ListenPart.role == "record", ListenPart.unlinked_at.is_(None))
        )
    }
    n = {"listens": 0, "choices": 0, "from_tags": 0, "from_folder": 0}
    for l in s.scalars(
        select(Listen)
        .where(Listen.merged_into_id.is_(None), *([Listen.id.in_(redo)] if redo else []))
        .order_by(Listen.id)
    ):
        if l.id in done:
            continue
        inputs = list(
            s.scalars(
                select(ListenPart)
                .where(ListenPart.listen_id == l.id, ListenPart.role == "input", ListenPart.unlinked_at.is_(None))
                .order_by(ListenPart.ordinal)
            )
        )
        probes = [p for ip in inputs if ip.filegroup_id for (p,) in s.execute(FILES_BELOW, {"gid": ip.filegroup_id})]
        first = (inputs[0].detail or {}) if inputs else {}
        path = first.get("path") or ""
        group = s.get(PartFilegroup, inputs[0].filegroup_id) if inputs and inputs[0].filegroup_id else None
        what, parent, folder = name_parts(path, l.label, group)
        tags = tag_summary(probes)
        name = scan.parse_name(what, parent)
        name = {k: v for k, v in name.items() if v not in ("", None)}
        tp = L.link(
            s,
            l,
            "record",
            record_id=_record(s, "embedded_tags", "tag_summary", tags).id,
            ordinal=1,
            actor_kind="rule",
            actor_ref=actor_ref,
            reason="what the embedded tags of the files agree on",
        )
        fp = L.link(
            s,
            l,
            "record",
            record_id=_record(s, "folder_name", "parsed_name", {**name, "folder": folder}).id,
            ordinal=2,
            actor_kind="rule",
            actor_ref=actor_ref,
            reason="what the folder name reads as",
        )
        by_person = {
            c.field
            for c in s.scalars(
                select(ListenChoice).where(
                    ListenChoice.listen_id == l.id, ListenChoice.unchosen_at.is_(None), ListenChoice.chosen_by == "user"
                )
            )
        }
        for field, (value, src) in choose_fields(tags, name).items():
            if field in by_person:
                continue
            L.choose(
                s,
                l,
                field,
                value,
                part=tp if src == "tags" else fp,
                actor_kind="rule",
                actor_ref=actor_ref,
                reason=f"from {src.replace('_', ' ')}",
            )
            n["choices"] += 1
            n["from_tags" if src == "tags" else "from_folder"] += 1
            if field == "title":
                l.label = str(value)
        n["listens"] += 1
    s.flush()
    return n
