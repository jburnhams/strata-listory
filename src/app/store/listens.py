"""Mutating a Listen and its listen-parts. Every function writes the change and its ListenEvent in the caller's session/transaction, so a link can never change without its audit row."""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

from sqlalchemy import select, update
from sqlalchemy.orm import Session

from app.store.models import Listen, ListenChoice, ListenEvent, ListenPart


def _now() -> datetime:
    return datetime.now(UTC)


def _event(
    s: Session,
    listen: Listen,
    action: str,
    actor_kind: str,
    *,
    part: ListenPart | None = None,
    actor_ref: str = "",
    run_id: str | None = None,
    reason: str = "",
    before: dict[str, Any] | None = None,
    after: dict[str, Any] | None = None,
) -> ListenEvent:
    e = ListenEvent(
        listen_id=listen.id,
        action=action,
        actor_kind=actor_kind,
        listen_part_id=part.id if part else None,
        actor_ref=actor_ref,
        run_id=run_id,
        reason=reason,
        before=before,
        after=after,
    )
    s.add(e)
    s.flush()
    return e


def create_listen(
    s: Session,
    kind: str = "book",
    *,
    actor_kind: str = "import",
    actor_ref: str = "",
    run_id: str | None = None,
    reason: str = "",
) -> Listen:
    l = Listen(kind=kind)
    s.add(l)
    s.flush()
    _event(s, l, "create", actor_kind, actor_ref=actor_ref, run_id=run_id, reason=reason, after={"kind": kind})
    return l


def link(
    s: Session,
    listen: Listen,
    role: str,
    *,
    file_id: int | None = None,
    record_id: int | None = None,
    work_id: int | None = None,
    filegroup_id: int | None = None,
    ordinal: int | None = None,
    state: str = "active",
    score: float | None = None,
    detail: dict | None = None,
    actor_kind: str = "user",
    actor_ref: str = "",
    run_id: str | None = None,
    reason: str = "",
) -> ListenPart:
    p = ListenPart(
        listen_id=listen.id,
        role=role,
        file_id=file_id,
        record_id=record_id,
        work_id=work_id,
        filegroup_id=filegroup_id,
        ordinal=ordinal,
        state=state,
        score=score,
        detail=detail,
        linked_by=actor_kind,
        run_id=run_id,
        reason=reason,
    )
    s.add(p)
    s.flush()
    _event(
        s,
        listen,
        "link",
        actor_kind,
        part=p,
        actor_ref=actor_ref,
        run_id=run_id,
        reason=reason,
        after={"role": role, "state": state, "ordinal": ordinal, "score": score},
    )
    return p


def unlink(
    s: Session,
    part: ListenPart,
    *,
    actor_kind: str = "user",
    actor_ref: str = "",
    run_id: str | None = None,
    reason: str = "",
) -> None:
    if part.unlinked_at is not None:
        return
    part.unlinked_at = _now()
    s.flush()
    _event(
        s,
        s.get(Listen, part.listen_id),
        "unlink",
        actor_kind,
        part=part,
        actor_ref=actor_ref,
        run_id=run_id,
        reason=reason,
        before={"role": part.role, "state": part.state, "ordinal": part.ordinal},
    )


def set_state(
    s: Session,
    part: ListenPart,
    state: str,
    *,
    actor_kind: str = "user",
    actor_ref: str = "",
    run_id: str | None = None,
    reason: str = "",
    after: dict | None = None,
) -> None:
    """accept / reject / revoke a candidate or match. `after` carries the evidence for automatic decisions (score, margin, model version, policy revision)."""
    if part.state == state:
        return
    action = {"accepted": "accept", "rejected": "reject"}.get(state, "revoke")
    before = {"state": part.state}
    part.state = state
    s.flush()
    _event(
        s,
        s.get(Listen, part.listen_id),
        action,
        actor_kind,
        part=part,
        actor_ref=actor_ref,
        run_id=run_id,
        reason=reason,
        before=before,
        after={"state": state, **(after or {})},
    )


def reorder(
    s: Session,
    listen: Listen,
    role: str,
    order: list[int],
    *,
    actor_kind: str = "user",
    actor_ref: str = "",
    reason: str = "",
) -> None:
    """Give the active parts of (listen, role) the ordinals 1..n in the given part-id order. Cleared first because the active ordinal is unique."""
    parts = {
        p.id: p
        for p in s.scalars(
            select(ListenPart).where(
                ListenPart.listen_id == listen.id, ListenPart.role == role, ListenPart.unlinked_at.is_(None)
            )
        )
    }
    if set(order) != set(parts):
        raise ValueError("order must list exactly the active parts of this role")
    before = {str(i): p.ordinal for i, p in parts.items()}
    for p in parts.values():
        p.ordinal = None
    s.flush()
    for n, i in enumerate(order, 1):
        parts[i].ordinal = n
    s.flush()
    _event(
        s,
        listen,
        "reorder",
        actor_kind,
        actor_ref=actor_ref,
        reason=reason,
        before=before,
        after={str(i): n for n, i in enumerate(order, 1)},
    )


def merge(
    s: Session,
    survivor: Listen,
    loser: Listen,
    *,
    actor_kind: str = "user",
    actor_ref: str = "",
    run_id: str | None = None,
    reason: str = "",
) -> None:
    """Move the loser's active parts to the survivor (appended after the survivor's own ordinals per role) and retire the loser. Parts themselves are untouched."""
    if survivor.id == loser.id:
        raise ValueError("cannot merge a listen into itself")
    if loser.merged_into_id is not None or survivor.merged_into_id is not None:
        raise ValueError("both listens must be live")
    moved = list(
        s.scalars(
            select(ListenPart)
            .where(ListenPart.listen_id == loser.id, ListenPart.unlinked_at.is_(None))
            .order_by(ListenPart.role, ListenPart.ordinal, ListenPart.id)
        )
    )
    top: dict[str, int] = {}
    for role, mx in s.execute(
        select(ListenPart.role, ListenPart.ordinal).where(
            ListenPart.listen_id == survivor.id, ListenPart.unlinked_at.is_(None), ListenPart.ordinal.is_not(None)
        )
    ):
        top[role] = max(top.get(role, 0), mx)
    for p in moved:
        if p.ordinal is not None:
            top[p.role] = top.get(p.role, 0) + 1
            p.ordinal = top[p.role]
        p.listen_id = survivor.id
    s.execute(
        update(ListenChoice)
        .where(ListenChoice.listen_id == loser.id, ListenChoice.unchosen_at.is_(None))
        .values(unchosen_at=_now())
    )
    loser.merged_into_id = survivor.id
    loser.retired_at = _now()
    s.flush()
    _event(
        s,
        survivor,
        "merge",
        actor_kind,
        actor_ref=actor_ref,
        run_id=run_id,
        reason=reason,
        after={"absorbed": loser.id, "parts": [p.id for p in moved]},
    )
    _event(
        s,
        loser,
        "retire",
        actor_kind,
        actor_ref=actor_ref,
        run_id=run_id,
        reason=reason,
        after={"merged_into": survivor.id},
    )


def split(
    s: Session,
    source: Listen,
    part_ids: list[int],
    kind: str | None = None,
    *,
    actor_kind: str = "user",
    actor_ref: str = "",
    run_id: str | None = None,
    reason: str = "",
) -> Listen:
    """Move the given active parts into a new listen (ordinals renumbered from 1 per role)."""
    parts = list(
        s.scalars(
            select(ListenPart)
            .where(ListenPart.id.in_(part_ids), ListenPart.listen_id == source.id, ListenPart.unlinked_at.is_(None))
            .order_by(ListenPart.role, ListenPart.ordinal, ListenPart.id)
        )
    )
    if len(parts) != len(set(part_ids)):
        raise ValueError("every part must be an active part of the source listen")
    new = create_listen(
        s, kind or source.kind, actor_kind=actor_kind, actor_ref=actor_ref, run_id=run_id, reason=reason
    )
    n: dict[str, int] = {}
    for p in parts:
        p.ordinal = None
    s.flush()
    for p in parts:
        p.listen_id = new.id
    for p in parts:
        n[p.role] = n.get(p.role, 0) + 1
        p.ordinal = n[p.role]
    s.flush()
    _event(
        s,
        source,
        "split",
        actor_kind,
        actor_ref=actor_ref,
        run_id=run_id,
        reason=reason,
        after={"new_listen": new.id, "parts": [p.id for p in parts]},
    )
    return new


def set_status(
    s: Session,
    listen: Listen,
    status: str,
    *,
    actor_kind: str = "user",
    actor_ref: str = "",
    run_id: str | None = None,
    reason: str = "",
) -> None:
    if listen.status == status:
        return
    before = {"status": listen.status}
    listen.status = status
    s.flush()
    _event(
        s,
        listen,
        "status_change",
        actor_kind,
        actor_ref=actor_ref,
        run_id=run_id,
        reason=reason,
        before=before,
        after={"status": status},
    )


def choose(
    s: Session,
    listen: Listen,
    field: str,
    value: Any,
    *,
    part: ListenPart | None = None,
    actor_kind: str = "user",
    actor_ref: str = "",
    run_id: str | None = None,
    reason: str = "",
) -> ListenChoice:
    """Set the chosen value for an output field, closing the previous choice."""
    old = s.scalars(
        select(ListenChoice).where(
            ListenChoice.listen_id == listen.id, ListenChoice.field == field, ListenChoice.unchosen_at.is_(None)
        )
    ).first()
    if old:
        old.unchosen_at = _now()
        s.flush()
    c = ListenChoice(
        listen_id=listen.id, field=field, value=value, listen_part_id=part.id if part else None, chosen_by=actor_kind
    )
    s.add(c)
    s.flush()
    _event(
        s,
        listen,
        "choose",
        actor_kind,
        part=part,
        actor_ref=actor_ref,
        run_id=run_id,
        reason=reason,
        before={"field": field, "value": old.value} if old else None,
        after={"field": field, "value": value},
    )
    return c
