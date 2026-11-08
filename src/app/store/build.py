"""Step 4: make a Listen for every book and programme the structure step found.

A listen is one thing you would listen to in one go; here each one starts as a single listen-part linking to its filegroup (so a book of discs and tracks is one link, not hundreds). Series, collections and folders only contain listens; `loose` groups are left for a person. Every listen, its link and its status are written through `app.store.listens`, so each has its event (actor `rule`, naming the structure grouping it came from). Rerunnable from nothing: `rebuild=True` clears the listen tables first (they are derived data)."""
from __future__ import annotations

from sqlalchemy import delete, func, select, update
from sqlalchemy.orm import Session

from app.store import listens as L
from app.store.filegroups import finish_run, new_run
from app.store.models import Listen, ListenChoice, ListenEvent, ListenPart, PartFile, PartFilegroup, PartFilegroupMember, Run

KIND_OF_GROUP = {'book': 'book', 'programme': 'radio_series'}


def clear(s: Session) -> None:
    """Remove every listen and what hangs off it (events, choices, parts); runs stay, detached."""
    for model in (ListenEvent, ListenChoice, ListenPart): s.execute(delete(model))
    s.execute(update(Run).values(listen_id=None)); s.execute(update(Listen).values(merged_into_id=None)); s.execute(delete(Listen)); s.flush()


def build_listens(s: Session, structure_root: PartFilegroup, *, rebuild: bool = False) -> dict[str, int]:
    """Returns {'book': n, 'radio_series': n, 'loose_files_skipped': n}."""
    if s.scalar(select(func.count()).select_from(Listen)):
        if not rebuild: raise ValueError('listens already exist; pass rebuild=True to clear and rebuild them')
        clear(s)
    run = new_run(s, 'build_listens', {'from_run': structure_root.run_id}); ref = f'structure:{structure_root.run_id[:8]}'
    groups = {g.id: g for g in s.scalars(select(PartFilegroup).where(PartFilegroup.run_id == structure_root.run_id))}
    kids: dict[int, list[int]] = {}
    for m in s.scalars(select(PartFilegroupMember).where(PartFilegroupMember.group_id.in_(groups), PartFilegroupMember.child_group_id.is_not(None)).order_by(PartFilegroupMember.group_id, PartFilegroupMember.ordinal)): kids.setdefault(m.group_id, []).append(m.child_group_id)
    own_seconds: dict[int, float] = {}
    for gid, secs in s.execute(select(PartFilegroupMember.group_id, func.sum(PartFile.duration_s)).join(PartFile, PartFile.id == PartFilegroupMember.file_id).where(PartFilegroupMember.group_id.in_(groups)).group_by(PartFilegroupMember.group_id)): own_seconds[gid] = secs or 0
    seconds: dict[int, float] = {}

    def total(gid: int) -> float:
        if gid not in seconds: seconds[gid] = own_seconds.get(gid, 0) + sum(total(c) for c in kids.get(gid, []))
        return seconds[gid]

    counts = {'book': 0, 'radio_series': 0, 'loose_files_skipped': 0}

    def walk(gid: int) -> None:
        g = groups[gid]
        if g.kind in KIND_OF_GROUP:
            listen = L.create_listen(s, KIND_OF_GROUP[g.kind], actor_kind='rule', actor_ref=ref, run_id=run.id, reason=f'{g.kind} group: {(g.detail or {}).get("reason", "")}')
            listen.label = g.label
            L.link(s, listen, 'input', filegroup_id=g.id, ordinal=1, actor_kind='rule', actor_ref=ref, run_id=run.id, reason='the files of this listen',
                   detail={'group_kind': g.kind, 'files': g.file_count, 'bytes': g.total_bytes, 'minutes': round(total(gid) / 60), 'path': g.path})
            L.set_status(s, listen, 'grouped', actor_kind='rule', actor_ref=ref, run_id=run.id, reason='made from structure grouping')
            counts[KIND_OF_GROUP[g.kind]] += 1
        elif g.kind == 'loose': counts['loose_files_skipped'] += g.file_count
        else:
            for c in kids.get(gid, []): walk(c)

    walk(structure_root.id); finish_run(s, run, 'done'); return counts
