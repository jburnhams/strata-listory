"""One-off repairs of listens that an earlier rule got wrong, each logged as events by a person's request (the rule itself is fixed separately, so a rebuild does not repeat the mistake)."""

from __future__ import annotations

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.store import listens as L
from app.store import metadata
from app.store.models import Listen, ListenPart


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
