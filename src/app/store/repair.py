"""One-off repairs of listens that an earlier rule got wrong, each logged as events by a person's request (the rule itself is fixed separately, so a rebuild does not repeat the mistake)."""

from __future__ import annotations

import os

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.store import build, metadata
from app.store import listens as L
from app.store import structure as st
from app.store.filegroups import finish_run, new_run
from app.store.models import Listen, ListenPart, PartFile, PartFilegroup, PartFilegroupMember, PartFileLocation


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


def restructure_listen(s: Session, listen_id: int, *, actor_ref: str = "restructure") -> list[int]:
    """Re-read one listen's files with the current structure rules, for a rule fixed after the listen was built (a full-length file beside the same book split into chapters, which was one group of double length). The listen, its choices, matches, decisions and events stay; its single input group is replaced by one new group per cluster the rules now see, linked as the listen's inputs (copies, ranked by `build.preference`), and its metadata is read again. Returns the new group ids; raises ValueError when the rules still see one group, or the input is not one group of plain files."""
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
    here: dict[
        int, str
    ] = {}  # the file's name in this group's own folder; a file's content can also sit elsewhere under another name (`scan_files` deduplicates by content)
    elsewhere: dict[int, str] = {}
    for fid, path in s.execute(
        select(PartFileLocation.file_id, PartFileLocation.path).where(
            PartFileLocation.gone_at.is_(None), PartFileLocation.file_id.in_([m.file_id for m in members])
        )
    ):
        (here if os.path.dirname(path) == (old.path or "") else elsewhere).setdefault(fid, os.path.basename(path))
    names = {**elsewhere, **here}
    files = [(m.file_id, names.get(m.file_id, f"file {m.file_id}")) for m in members if m.file_id is not None]
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
    run = new_run(s, "restructure", {"listen": listen_id, "from_group": old.id})
    made: list[tuple[PartFilegroup, int]] = []
    for kind, fl, evidence in clusters:
        label = os.path.splitext(fl[0][1])[0] if len(fl) == 1 else st.cluster_label(folder, fl)
        g = PartFilegroup(
            run_id=run.id,
            kind=kind,
            label=label,
            path=old.path,
            detail={**evidence, "from_group": old.id},
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
    L.unlink(
        s,
        inputs[0],
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
