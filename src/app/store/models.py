"""The Postgres catalogue (schema `listory` in the shared omarchy database).

Two layers:
* **Parts** are immutable and append-only: files by content hash (`PartFile`), metadata claims exactly as received (`PartRecord`), and the reference works/people we have met. Nothing here is ever edited.
* A **Listen** is the one self-contained thing you would listen to in one go (an audiobook, a radio series, a stand-alone radio show). It is the only mutable object, and it is made of **listen-parts** (`ListenPart`): link rows to files, records or works that can be added and unlinked as processing proceeds. Merge and split only move link rows.
Every create/change/removal of a Listen or ListenPart is recorded in `ListenEvent` (see `app.store.listens`, which writes the change and its event in one transaction)."""
from __future__ import annotations

from datetime import datetime
from typing import Any

import sqlalchemy as sa
from sqlalchemy import CheckConstraint, ForeignKey, Index, func, text
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from omarchy_db import make_base

Base = make_base()

LISTEN_KINDS = ("book", "radio_series", "radio_show", "other")
PART_ROLES = ("input", "output", "reference", "sidecar", "record", "candidate", "match")
PART_STATES = ("active", "candidate", "accepted", "rejected")
ACTOR_KINDS = ("user", "auto_policy", "model", "rule", "import")
EVENT_ACTIONS = ("create", "link", "unlink", "relink", "reorder", "merge", "split", "status_change", "accept", "reject", "revoke", "choose", "unchoose", "retire")


def _in(col: str, values: tuple[str, ...]) -> str:
    return f"{col} in (" + ", ".join(f"'{v}'" for v in values) + ")"


# ---------------------------------------------------------------- parts (immutable)

class PartFile(Base):
    """One row per distinct file content. Locations live in filecat.files (same sha256, current=1); no cross-schema foreign key (app roles cannot see each other's schemas)."""
    __tablename__ = "part_file"
    id: Mapped[int] = mapped_column(primary_key=True, init=False)
    sha256: Mapped[bytes] = mapped_column(sa.LargeBinary, unique=True)
    size: Mapped[int] = mapped_column(sa.BigInteger)
    duration_s: Mapped[float | None] = mapped_column(default=None)
    codec: Mapped[str | None] = mapped_column(default=None)
    probe: Mapped[dict[str, Any] | None] = mapped_column(JSONB, default=None)       # ffprobe output: tags, chapters, channels, bit rate
    created_at: Mapped[datetime] = mapped_column(server_default=func.now(), init=False)


class PartRecord(Base):
    """A metadata claim exactly as received: embedded tags, a folder name, an Audible/Open Library/Hardcover response, a user's typed correction."""
    __tablename__ = "part_record"
    id: Mapped[int] = mapped_column(primary_key=True, init=False)
    source: Mapped[str] = mapped_column(index=True)                 # embedded_tags | folder_name | audible | openlibrary | hardcover | audnexus | applebooks | user
    kind: Mapped[str]                                               # what shape the payload has: tags, product, work, edition, name...
    payload: Mapped[dict[str, Any]] = mapped_column(JSONB)
    payload_hash: Mapped[bytes] = mapped_column(sa.LargeBinary)     # sha256 of the canonical payload: the same claim is stored once
    part_file_id: Mapped[int | None] = mapped_column(ForeignKey("part_file.id"), index=True, default=None)   # the file it describes, if any
    external_id: Mapped[str | None] = mapped_column(default=None)   # ASIN, OL id... for lookups
    fetched_at: Mapped[datetime] = mapped_column(server_default=func.now(), init=False)
    __table_args__ = (sa.UniqueConstraint("source", "kind", "payload_hash"), Index("part_record_external", "source", "external_id"))


class Person(Base):
    __tablename__ = "person"
    id: Mapped[int] = mapped_column(primary_key=True, init=False)
    name: Mapped[str]
    key: Mapped[str] = mapped_column(index=True)                    # normalised name (textkeys)
    ol_id: Mapped[str | None] = mapped_column(unique=True, default=None)


class Work(Base):
    """A written work, an audiobook edition, or a radio programme we have met. Reference data: the full Open Library/Audible indexes stay outside."""
    __tablename__ = "work"
    id: Mapped[int] = mapped_column(primary_key=True, init=False)
    kind: Mapped[str]                                               # written | audiobook | radio_programme
    title: Mapped[str]
    subtitle: Mapped[str] = mapped_column(server_default="", default="")
    language: Mapped[str | None] = mapped_column(default=None)
    year: Mapped[int | None] = mapped_column(default=None)
    abridged: Mapped[bool | None] = mapped_column(default=None)
    runtime_s: Mapped[int | None] = mapped_column(default=None)
    __table_args__ = (CheckConstraint(_in("kind", ("written", "audiobook", "radio_programme")), name="work_kind"),)


class WorkIdentifier(Base):
    __tablename__ = "work_identifier"
    id: Mapped[int] = mapped_column(primary_key=True, init=False)
    work_id: Mapped[int] = mapped_column(ForeignKey("work.id", ondelete="CASCADE"), index=True)
    scheme: Mapped[str]                                             # asin | isbn | ol_work | ol_edition | hardcover | audible_series
    value: Mapped[str]
    __table_args__ = (sa.UniqueConstraint("scheme", "value", "work_id"), Index("work_identifier_lookup", "scheme", "value"))


class WorkCredit(Base):
    __tablename__ = "work_credit"
    id: Mapped[int] = mapped_column(primary_key=True, init=False)
    work_id: Mapped[int] = mapped_column(ForeignKey("work.id", ondelete="CASCADE"), index=True)
    person_id: Mapped[int] = mapped_column(ForeignKey("person.id"), index=True)
    role: Mapped[str]                                               # author | narrator | translator | cast
    position: Mapped[int] = mapped_column(server_default="0", default=0)
    __table_args__ = (sa.UniqueConstraint("work_id", "person_id", "role"),)


class WorkLink(Base):
    """audiobook_of, edition_of, part_of, translation_of, sequel_of."""
    __tablename__ = "work_link"
    id: Mapped[int] = mapped_column(primary_key=True, init=False)
    work_id: Mapped[int] = mapped_column(ForeignKey("work.id", ondelete="CASCADE"), index=True)
    other_work_id: Mapped[int] = mapped_column(ForeignKey("work.id", ondelete="CASCADE"), index=True)
    kind: Mapped[str]
    position: Mapped[float | None] = mapped_column(default=None)    # series position
    __table_args__ = (sa.UniqueConstraint("work_id", "other_work_id", "kind"), CheckConstraint("work_id <> other_work_id", name="work_link_distinct"))


# ---------------------------------------------------------------- runs

class Run(Base):
    """One execution of a pipeline over a listen (ingest, identify, regroup, split, process). Replaces ingest_runs."""
    __tablename__ = "run"
    id: Mapped[str] = mapped_column(primary_key=True)               # the existing 32-char hex ids are kept
    kind: Mapped[str]
    listen_id: Mapped[int | None] = mapped_column(ForeignKey("listen.id"), index=True, default=None)
    options: Mapped[dict[str, Any] | None] = mapped_column(JSONB, default=None)
    outcome: Mapped[str | None] = mapped_column(default=None)       # done | failed | interrupted
    started_at: Mapped[datetime] = mapped_column(server_default=func.now(), init=False)
    finished_at: Mapped[datetime | None] = mapped_column(default=None)


class RunStage(Base):
    """Start/done/failed per stage with metrics. Progress ticks are deliberately not stored."""
    __tablename__ = "run_stage"
    id: Mapped[int] = mapped_column(primary_key=True, init=False)
    run_id: Mapped[str] = mapped_column(ForeignKey("run.id", ondelete="CASCADE"), index=True)
    stage: Mapped[str]
    status: Mapped[str]                                             # running | done | failed
    elapsed_s: Mapped[float | None] = mapped_column(default=None)
    metrics: Mapped[dict[str, Any] | None] = mapped_column(JSONB, default=None)
    error: Mapped[str | None] = mapped_column(default=None)
    at: Mapped[datetime] = mapped_column(server_default=func.now(), init=False)


# ---------------------------------------------------------------- the mutable layer

class Listen(Base):
    __tablename__ = "listen"
    id: Mapped[int] = mapped_column(primary_key=True, init=False)
    kind: Mapped[str] = mapped_column(server_default="book", default="book")
    label: Mapped[str] = mapped_column(server_default="", default="")        # cached display title, derived from the chosen metadata
    status: Mapped[str] = mapped_column(server_default="new", default="new", index=True)   # cache of the newest status_change event: new | grouped | review | approved | processing | processed | held | failed
    merged_into_id: Mapped[int | None] = mapped_column(ForeignKey("listen.id"), index=True, default=None)
    retired_at: Mapped[datetime | None] = mapped_column(default=None)
    created_at: Mapped[datetime] = mapped_column(server_default=func.now(), init=False)
    __table_args__ = (CheckConstraint(_in("kind", LISTEN_KINDS), name="listen_kind"), CheckConstraint("merged_into_id is null or merged_into_id <> id", name="listen_merge_self"))


class ListenPart(Base):
    """A link from a listen to one part: a file, a metadata record or a work. Added and unlinked (never deleted) as processing proceeds.
    `ordinal` is the listening order for files and the priority/relevance (lower = more trusted) for records and works; unique among active links of a (listen, role)."""
    __tablename__ = "listen_part"
    id: Mapped[int] = mapped_column(primary_key=True, init=False)
    listen_id: Mapped[int] = mapped_column(ForeignKey("listen.id"), index=True)
    role: Mapped[str]
    file_id: Mapped[int | None] = mapped_column(ForeignKey("part_file.id"), index=True, default=None)
    record_id: Mapped[int | None] = mapped_column(ForeignKey("part_record.id"), index=True, default=None)
    work_id: Mapped[int | None] = mapped_column(ForeignKey("work.id"), index=True, default=None)
    ordinal: Mapped[int | None] = mapped_column(default=None)
    state: Mapped[str] = mapped_column(server_default="active", default="active")
    score: Mapped[float | None] = mapped_column(default=None)
    detail: Mapped[dict[str, Any] | None] = mapped_column(JSONB, default=None)   # candidate features, model margin, the reading used...
    linked_by: Mapped[str] = mapped_column(server_default="user", default="user")
    run_id: Mapped[str | None] = mapped_column(ForeignKey("run.id"), index=True, default=None)
    reason: Mapped[str] = mapped_column(server_default="", default="")
    linked_at: Mapped[datetime] = mapped_column(server_default=func.now(), init=False)
    unlinked_at: Mapped[datetime | None] = mapped_column(default=None)
    __table_args__ = (
        CheckConstraint("(file_id is not null)::int + (record_id is not null)::int + (work_id is not null)::int = 1", name="listen_part_one_target"),
        CheckConstraint(_in("role", PART_ROLES), name="listen_part_role"),
        CheckConstraint(_in("state", PART_STATES), name="listen_part_state"),
        CheckConstraint(_in("linked_by", ACTOR_KINDS), name="listen_part_linked_by"),
        Index("listen_part_active_ordinal", "listen_id", "role", "ordinal", unique=True, postgresql_where=text("unlinked_at is null and ordinal is not null")),
        Index("listen_part_active", "listen_id", "role", postgresql_where=text("unlinked_at is null")),
    )


class ListenChoice(Base):
    """The best bit chosen for each output field (title, author, narrator, series, year, cover, audio source...). One active row per (listen, field)."""
    __tablename__ = "listen_choice"
    id: Mapped[int] = mapped_column(primary_key=True, init=False)
    listen_id: Mapped[int] = mapped_column(ForeignKey("listen.id"), index=True)
    field: Mapped[str]
    value: Mapped[Any] = mapped_column(JSONB)
    listen_part_id: Mapped[int | None] = mapped_column(ForeignKey("listen_part.id"), default=None)   # where the value came from; null for a typed value
    chosen_by: Mapped[str] = mapped_column(server_default="user", default="user")
    chosen_at: Mapped[datetime] = mapped_column(server_default=func.now(), init=False)
    unchosen_at: Mapped[datetime | None] = mapped_column(default=None)
    __table_args__ = (Index("listen_choice_active", "listen_id", "field", unique=True, postgresql_where=text("unchosen_at is null")),)


class ListenEvent(Base):
    """Audit log of changes to listens and listen-parts, written in the same transaction as the change. Not a progress log."""
    __tablename__ = "listen_event"
    id: Mapped[int] = mapped_column(primary_key=True, init=False)
    listen_id: Mapped[int] = mapped_column(ForeignKey("listen.id"), index=True)
    action: Mapped[str]
    actor_kind: Mapped[str]
    listen_part_id: Mapped[int | None] = mapped_column(ForeignKey("listen_part.id"), index=True, default=None)
    actor_ref: Mapped[str] = mapped_column(server_default="", default="")       # user name, model version, policy revision
    run_id: Mapped[str | None] = mapped_column(ForeignKey("run.id"), index=True, default=None)
    reason: Mapped[str] = mapped_column(server_default="", default="")
    before: Mapped[dict[str, Any] | None] = mapped_column(JSONB, default=None)
    after: Mapped[dict[str, Any] | None] = mapped_column(JSONB, default=None)   # auto accepts: score, margin, model version, policy revision
    at: Mapped[datetime] = mapped_column(server_default=func.now(), init=False)
    __table_args__ = (CheckConstraint(_in("action", EVENT_ACTIONS), name="listen_event_action"), CheckConstraint(_in("actor_kind", ACTOR_KINDS), name="listen_event_actor"))
