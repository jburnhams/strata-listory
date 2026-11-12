"""One-off repairs of listens that an earlier rule got wrong, each logged as events by a person's request (the rule itself is fixed separately, so a rebuild does not repeat the mistake)."""

from __future__ import annotations

import os

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.store import build, metadata
from app.store import listens as L
from app.store import structure as st
from app.store.filegroups import finish_run, new_run
from app.store.models import (
    Listen,
    ListenEvent,
    ListenPart,
    PartFile,
    PartFilegroup,
    PartFilegroupMember,
    PartFileLocation,
)


def split_input_parts(
    s: Session, listen_id: int, part_ids: list[int], *, reason: str, actor_ref: str = "repair"
) -> Listen:
    """Move the given input parts (files or groups listed as copies of one listen) into a listen of their own, status `grouped`, and read its metadata from its files. Refuses to leave the source with no input."""
    source = s.get(Listen, listen_id)
    if source is None or source.merged_into_id is not None:
        raise ValueError(f"listen {listen_id} is not a live listen")
    inputs = {
        p.id
        for p in s.scalars(
            select(ListenPart).where(
                ListenPart.listen_id == listen_id, ListenPart.role == "input", ListenPart.unlinked_at.is_(None)
            )
        )
    }
    if not part_ids or not set(part_ids) <= inputs or set(part_ids) == inputs:
        raise ValueError("part ids must be some, not all, of the listen's active input parts")
    new = L.split(s, source, part_ids, actor_kind="user", actor_ref=actor_ref, reason=reason)
    L.set_status(s, new, "grouped", actor_kind="user", actor_ref=actor_ref, reason=reason)
    metadata.add_metadata(s, actor_ref=actor_ref)
    return new


def _read_group(s: Session, listen_id: int):
    """(listen, its single input part, that part's group, the clusters the current rules see in its files, {file: (seconds, bytes)}, folder name)."""
    listen = s.get(Listen, listen_id)
    if listen is None or listen.merged_into_id is not None:
        raise ValueError(f"listen {listen_id} is not a live listen")
    inputs = list(
        s.scalars(
            select(ListenPart).where(
                ListenPart.listen_id == listen_id, ListenPart.role == "input", ListenPart.unlinked_at.is_(None)
            )
        )
    )
    if len(inputs) != 1 or not inputs[0].filegroup_id:
        raise ValueError("the listen must have exactly one input group")
    old = s.get(PartFilegroup, inputs[0].filegroup_id)
    members = list(
        s.scalars(
            select(PartFilegroupMember)
            .where(PartFilegroupMember.group_id == inputs[0].filegroup_id)
            .order_by(PartFilegroupMember.ordinal)
        )
    )
    if old is None or not members or any(m.file_id is None for m in members):
        raise ValueError("the input group must hold plain files only")
    names: dict[
        tuple[str, int], list[str]
    ] = {}  # names in this group's own folder, plus (when a file has none here) wherever else its content is kept: `scan_files` deduplicates by content
    elsewhere: dict[int, list[str]] = {}
    for fid, path in s.execute(
        select(PartFileLocation.file_id, PartFileLocation.path)
        .where(PartFileLocation.gone_at.is_(None), PartFileLocation.file_id.in_([m.file_id for m in members]))
        .order_by(PartFileLocation.path)
    ):
        if os.path.dirname(path) == (old.path or ""):
            names.setdefault((old.path or "", fid), []).append(os.path.basename(path))
        else:
            elsewhere.setdefault(fid, []).append(os.path.basename(path))
    for fid, have in elsewhere.items():
        names.setdefault((old.path or "", fid), have[:1])
    files = st.named_files(old.path, [m.file_id for m in members if m.file_id is not None], names)
    durations = {
        fid: (d or 0.0, size)
        for fid, d, size in s.execute(
            select(PartFile.id, PartFile.duration_s, PartFile.size).where(PartFile.id.in_([f[0] for f in files]))
        )
    }
    saved = dict(st.DURATIONS)
    st.DURATIONS.clear()
    st.DURATIONS.update({fid: d for fid, (d, _) in durations.items() if d})
    try:
        clusters = st.cluster_files(files)
    finally:
        st.DURATIONS.clear()
        st.DURATIONS.update(saved)
    if len(clusters) < 2 or any(kind != "book" for kind, _, _ in clusters):
        raise ValueError("the current rules see this listen's files as one group: nothing to restructure")
    folder = os.path.basename(old.path or "") or old.label
    return listen, inputs[0], old, clusters, durations, folder


def _make_groups(s: Session, run, old, clusters, durations, folder) -> list[tuple[PartFilegroup, int]]:
    """One new group per cluster (files as members), with its runtime in minutes."""
    made: list[tuple[PartFilegroup, int]] = []
    for kind, fl, evidence in clusters:
        label = os.path.splitext(fl[0][1])[0] if len(fl) == 1 else st.cluster_label(folder, fl)
        g = PartFilegroup(
            run_id=run.id,
            kind=kind,
            label=label,
            path=old.path,
            detail={
                **evidence,
                "from_group": -1 if len(fl) == 1 else old.id,
                "restructured_from": old.id,
            },  # a lone file in its container folder is named by the file, as structure.py makes them (-1)
            file_count=len(fl),
            total_bytes=sum(durations[f][1] for f, _ in fl),
            group_count=0,
        )
        s.add(g)
        s.flush()
        for i, (fid, _) in enumerate(fl, 1):
            s.add(PartFilegroupMember(group_id=g.id, ordinal=i, file_id=fid))
        made.append((g, round(sum(durations[f][0] for f, _ in fl) / 60)))
    s.flush()
    return made


def restructure_listen(s: Session, listen_id: int, *, actor_ref: str = "restructure") -> list[int]:
    """Re-read one listen's files with the current structure rules, for a rule fixed after the listen was built (a full-length file beside the same book split into chapters, which was one group of double length). The listen, its choices, matches, decisions and events stay; its single input group is replaced by one new group per cluster the rules now see, linked as the listen's inputs (copies, ranked by `build.preference`), and its metadata is read again. Returns the new group ids; raises ValueError when the rules still see one group, or the input is not one group of plain files."""
    listen, old_part, old, clusters, durations, folder = _read_group(s, listen_id)
    run = new_run(s, "restructure", {"listen": listen_id, "from_group": old.id})
    made = _make_groups(s, run, old, clusters, durations, folder)
    s.flush()
    L.unlink(
        s,
        old_part,
        actor_kind="rule",
        actor_ref=actor_ref,
        run_id=run.id,
        reason="restructured: the files are now seen as separate copies",
    )
    parts = []
    for i, (g, minutes) in enumerate(made, 1):
        parts.append(
            L.link(
                s,
                listen,
                "input",
                filegroup_id=g.id,
                ordinal=i,
                actor_kind="rule",
                actor_ref=actor_ref,
                run_id=run.id,
                reason="the files of one copy of this listen",
                detail={
                    "group_kind": g.kind,
                    "files": g.file_count,
                    "bytes": g.total_bytes,
                    "minutes": minutes,
                    "path": g.path,
                },
            )
        )
    labels = {p.id: g.label for p, (g, _) in zip(parts, made)}
    order = [p.id for p in sorted(parts, key=lambda p: build.preference(p, labels[p.id]))]
    L.reorder(s, listen, "input", order, actor_kind="rule", actor_ref=actor_ref, reason="copies ranked by preference")
    metadata.add_metadata(s, actor_ref=actor_ref, redo=[listen_id])
    finish_run(s, run, "done")
    return [g.id for g, _ in made]


def explode_listen(s: Session, listen_id: int, *, actor_ref: str = "explode") -> list[int]:
    """A folder of different books was taken for the chapters of one (`structure.titled_books`): give each long file a listen of its own. The existing listen keeps the first book (its id, events, status) and its metadata is read again; every other file gets a new listen, `grouped`, with its metadata. Refuses a listen a person has decided on (a user match, or a chosen field) and a listen whose files the rules still see as one group; model candidates are replaced by the next `listens-identify`. Returns the listen ids, the existing one first."""
    listen, old_part, old, clusters, durations, folder = _read_group(s, listen_id)
    if any(kind != "book" or len(fl) != 1 for kind, fl, _ in clusters):
        raise ValueError("the current rules do not see one book per file here")
    person = s.scalar(
        select(func.count())
        .select_from(ListenEvent)
        .where(ListenEvent.listen_id == listen_id, ListenEvent.actor_kind == "user")
    )
    if person:  # a chosen field, a confirmed or rejected work, a status: anything a person did on the listen belongs to the book it was about
        raise ValueError("a person has decided on this listen: split it by hand")
    run = new_run(s, "explode", {"listen": listen_id, "from_group": old.id})
    made = _make_groups(s, run, old, clusters, durations, folder)
    L.unlink(
        s,
        old_part,
        actor_kind="rule",
        actor_ref=actor_ref,
        run_id=run.id,
        reason="exploded: each long file is a book of its own",
    )
    ids = [listen.id]
    for n, (g, minutes) in enumerate(made):
        target = (
            listen
            if n == 0
            else L.create_listen(
                s,
                "book",
                actor_kind="rule",
                actor_ref=actor_ref,
                run_id=run.id,
                reason="a long file with a title of its own, one of several in a folder",
            )
        )
        if n:
            target.label = g.label
            ids.append(target.id)
        L.link(
            s,
            target,
            "input",
            filegroup_id=g.id,
            ordinal=1,
            actor_kind="rule",
            actor_ref=actor_ref,
            run_id=run.id,
            reason="the file of this book",
            detail={
                "group_kind": g.kind,
                "files": g.file_count,
                "bytes": g.total_bytes,
                "minutes": minutes,
                "path": g.path,
            },
        )
        if n:
            L.set_status(
                s,
                target,
                "grouped",
                actor_kind="rule",
                actor_ref=actor_ref,
                run_id=run.id,
                reason="made from a folder of several books",
            )
    metadata.add_metadata(s, actor_ref=actor_ref, redo=[listen_id])
    metadata.add_metadata(s, actor_ref=actor_ref)  # the new listens have no metadata yet
    finish_run(s, run, "done")
    return ids


def discard_listen(s: Session, listen_id: int, into_id: int, *, reason: str, actor_ref: str = "discard") -> None:
    """Retire a listen that is not a book of its own (a second copy of one chapter of another listen's set, a stray duplicate file): it is merged into `into_id`, which keeps its own inputs, and the files it brought are unlinked again, so no listen carries them. Refuses a listen a person did anything on."""
    loser, survivor = s.get(Listen, listen_id), s.get(Listen, into_id)
    if loser is None or survivor is None or loser.merged_into_id is not None or survivor.merged_into_id is not None:
        raise ValueError("both must be live listens")
    if s.scalar(
        select(func.count())
        .select_from(ListenEvent)
        .where(ListenEvent.listen_id == listen_id, ListenEvent.actor_kind == "user")
    ):
        raise ValueError("a person has decided on this listen: handle it by hand")
    brought = [
        p.id
        for p in s.scalars(
            select(ListenPart).where(
                ListenPart.listen_id == listen_id, ListenPart.role == "input", ListenPart.unlinked_at.is_(None)
            )
        )
    ]
    L.merge(s, survivor, loser, actor_kind="user", actor_ref=actor_ref, reason=reason)
    for p in s.scalars(select(ListenPart).where(ListenPart.id.in_(brought))):
        L.unlink(s, p, actor_kind="user", actor_ref=actor_ref, reason=reason)
