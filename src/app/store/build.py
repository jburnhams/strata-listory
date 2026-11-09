"""Step 4: make a Listen for every book and programme the structure step found.

A listen is one thing you would listen to in one go; here each one starts as a single listen-part linking to its filegroup (so a book of discs and tracks is one link, not hundreds). Series, collections and folders only contain listens; `loose` groups are left for a person. Every listen, its link and its status are written through `app.store.listens`, so each has its event (actor `rule`, naming the structure grouping it came from). Rerunnable from nothing: `rebuild=True` clears the listen tables first (they are derived data)."""

from __future__ import annotations

import re
from collections import defaultdict

from sqlalchemy import delete, func, select, update
from sqlalchemy.orm import Session

from app.store import listens as L
from app.store.filegroups import finish_run, new_run
from app.store.models import (
    Listen,
    ListenChoice,
    ListenEvent,
    ListenKind,
    ListenPart,
    PartFile,
    PartFilegroup,
    PartFilegroupMember,
    Run,
)

KIND_OF_GROUP = {"book": "book", "programme": "radio_series"}


def clear(s: Session) -> None:
    """Remove every listen and what hangs off it (events, choices, parts); runs stay, detached."""
    for model in (ListenEvent, ListenChoice, ListenPart, ListenKind):
        s.execute(
            delete(model)
        )  # verdicts too: a rebuild starts the book / radio call afresh (a person's choices are labels again once `listens-kinds` runs; none survive the listens they were about)
    s.execute(update(Run).values(listen_id=None))
    s.execute(update(Listen).values(merged_into_id=None))
    s.execute(delete(Listen))
    s.flush()


def build_listens(s: Session, structure_root: PartFilegroup, *, rebuild: bool = False) -> dict[str, int]:
    """Returns {'book': n, 'radio_series': n, 'loose_files_skipped': n}."""
    if s.scalar(select(func.count()).select_from(Listen)):
        if not rebuild:
            raise ValueError("listens already exist; pass rebuild=True to clear and rebuild them")
        clear(s)
    run = new_run(s, "build_listens", {"from_run": structure_root.run_id})
    ref = f"structure:{structure_root.run_id[:8]}"
    groups = {g.id: g for g in s.scalars(select(PartFilegroup).where(PartFilegroup.run_id == structure_root.run_id))}
    kids: dict[int, list[int]] = {}
    for m in s.scalars(
        select(PartFilegroupMember)
        .where(PartFilegroupMember.group_id.in_(groups), PartFilegroupMember.child_group_id.is_not(None))
        .order_by(PartFilegroupMember.group_id, PartFilegroupMember.ordinal)
    ):
        kids.setdefault(m.group_id, []).append(m.child_group_id)
    own_seconds: dict[int, float] = {}
    for gid, secs in s.execute(
        select(PartFilegroupMember.group_id, func.sum(PartFile.duration_s))
        .join(PartFile, PartFile.id == PartFilegroupMember.file_id)
        .where(PartFilegroupMember.group_id.in_(groups))
        .group_by(PartFilegroupMember.group_id)
    ):
        own_seconds[gid] = secs or 0
    seconds: dict[int, float] = {}

    def total(gid: int) -> float:
        if gid not in seconds:
            seconds[gid] = own_seconds.get(gid, 0) + sum(total(c) for c in kids.get(gid, []))
        return seconds[gid]

    counts = {"book": 0, "radio_series": 0, "loose_files_skipped": 0}

    def walk(gid: int) -> None:
        g = groups[gid]
        if g.kind in KIND_OF_GROUP:
            listen = L.create_listen(
                s,
                KIND_OF_GROUP[g.kind],
                actor_kind="rule",
                actor_ref=ref,
                run_id=run.id,
                reason=f"{g.kind} group: {(g.detail or {}).get('reason', '')}",
            )
            listen.label = g.label
            L.link(
                s,
                listen,
                "input",
                filegroup_id=g.id,
                ordinal=1,
                actor_kind="rule",
                actor_ref=ref,
                run_id=run.id,
                reason="the files of this listen",
                detail={
                    "group_kind": g.kind,
                    "files": g.file_count,
                    "bytes": g.total_bytes,
                    "minutes": round(total(gid) / 60),
                    "path": g.path,
                },
            )
            L.set_status(
                s,
                listen,
                "grouped",
                actor_kind="rule",
                actor_ref=ref,
                run_id=run.id,
                reason="made from structure grouping",
            )
            counts[KIND_OF_GROUP[g.kind]] += 1
        elif g.kind == "loose":
            counts["loose_files_skipped"] += g.file_count
        else:
            for c in kids.get(gid, []):
                walk(c)

    walk(structure_root.id)
    finish_run(s, run, "done")
    return counts


def title_key(label: str) -> str:
    """The title as letters and digits only, without brackets, 'unabridged'-style words, file-type words, a leading index ('04 - ') or a year: 'Ian Fleming - Casino Royale_unabridged' and '01 - Ian Fleming - Casino Royale_unabridged' agree. Digits stay, so 'Vol. 2' and 'Vol. 4' do not."""
    x = re.sub(r"\[[^\]]*\]|\([^)]*\)", " ", label.lower())
    x = re.sub(r"\b(unabridged|abridged|audiobook|mp3|m4b|aac|files?)\b", " ", x)
    x = re.sub(r"^\s*\d+\s*[-.]\s*", "", x)
    x = re.sub(r"(?<!\d)(?:19|20)\d\d(?!\d)", " ", x)
    return re.sub(r"[^a-z0-9]+", "", x)


def preference(part: ListenPart, label: str) -> tuple:
    """Which copy to prefer when listening or producing output: a single-container m4b/m4a first, then the larger (higher bit rate), then by path. Provisional: the final choice is made later with real quality measurements."""
    d = part.detail or {}
    container = 0 if re.search(r"m4[ab]", label, re.I) or re.search(r"m4[ab]|aac", d.get("path") or "", re.I) else 1
    return (container, -(d.get("bytes") or 0), d.get("path") or "")


def link_copies(s: Session, *, tolerance: float = 0.04) -> dict[str, int]:
    """Merge book listens that are copies of one recording: the same chosen title (brackets, "unabridged"-style words, a leading index and years ignored; digits kept), authors that do not disagree (equal, or one unknown) and runtimes within `tolerance` of each other. Needs the metadata step first, so the title and author are what the tags say, not what a folder happens to be called. Anything less certain waits until the works are identified. A different runtime under the same title is another recording (narrator or edit) of the same work and stays its own listen; so does the same title by a different author. The preferred copy survives, the others become its later input parts; every merge has its event with the evidence, and `split` undoes it."""
    run = new_run(s, "link_copies", {"tolerance": tolerance})
    authors = {
        lid: v
        for lid, v in s.execute(
            select(ListenChoice.listen_id, ListenChoice.value).where(
                ListenChoice.field == "author", ListenChoice.unchosen_at.is_(None)
            )
        )
    }
    rows = s.execute(
        select(Listen, ListenPart)
        .join(
            ListenPart,
            (ListenPart.listen_id == Listen.id) & (ListenPart.role == "input") & ListenPart.unlinked_at.is_(None),
        )
        .where(Listen.merged_into_id.is_(None), Listen.kind == "book")
    ).all()  # books only: radio series with the same title are different seasons, not copies
    by: dict[str, list[tuple[Listen, ListenPart]]] = defaultdict(list)
    for l, p in rows:
        if title_key(l.label):
            by[title_key(l.label)].append((l, p))
    akey = lambda l: re.sub(r"[^a-z0-9]+", "", str(authors.get(l.id) or "").lower())
    minutes = lambda lp: (lp[1].detail or {}).get("minutes") or 0
    merges = copies = 0
    for key, items in by.items():
        if len(items) < 2:
            continue
        items.sort(key=minutes)
        clusters: list[list[tuple[Listen, ListenPart]]] = []
        cluster: list[tuple[Listen, ListenPart]] = []
        for lp in items:
            m = minutes(lp)
            if cluster and m and minutes(cluster[0]) and (m - minutes(cluster[0])) / m <= tolerance:
                cluster.append(lp)
            else:
                if len(cluster) > 1:
                    clusters.append(cluster)
                cluster = [lp]
        if len(cluster) > 1:
            clusters.append(cluster)
        for cl in clusters:
            by_author: dict[str, list[tuple[Listen, ListenPart]]] = defaultdict(list)
            for lp in cl:
                by_author[akey(lp[0])].append(lp)
            unknown = by_author.pop("", [])
            if len(by_author) == 1:
                next(iter(by_author.values())).extend(unknown)
                unknown = []  # one known author: a copy with no author may join it
            for group in [
                *by_author.values(),
                unknown,
            ]:  # different authors under one title are never copies; copies with no author only join each other
                if len(group) < 2:
                    continue
                group.sort(key=lambda lp: preference(lp[1], lp[0].label))
                survivor = group[0][0]
                for loser, _ in group[1:]:
                    L.merge(
                        s,
                        survivor,
                        loser,
                        actor_kind="rule",
                        actor_ref="copies",
                        run_id=run.id,
                        reason=f'copies of one recording: same title "{key[:60]}", author {akey(survivor) or "unknown"}, runtimes {[minutes(lp) for lp in group]} minutes within {tolerance:.0%}',
                    )
                    merges += 1
                copies += len(group)
    finish_run(s, run, "done")
    return {"listens_with_copies": copies - merges, "copies_merged": merges}
