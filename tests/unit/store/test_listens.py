"""The listen layer on a real Postgres (engine = "postgres": partial unique indexes and JSONB). Skipped when no server is reachable, as in CI without a service container."""
import pytest

pytest.importorskip("omarchy_db")      # first: the standard CI test environment has neither omarchy-db nor SQLAlchemy
from sqlalchemy import select, text
from sqlalchemy.exc import IntegrityError

from app.store import listens as L
from app.store.models import ListenEvent, ListenPart, PartFile, PartRecord


@pytest.fixture
def s(db_session, db_backend):
    if db_backend != "postgres": pytest.skip("needs Postgres")
    return db_session


def f(s, n):
    p = PartFile(sha256=bytes([n]) * 32, size=n); s.add(p); s.flush(); return p


def actions(s, listen): return [e.action for e in s.scalars(select(ListenEvent).where(ListenEvent.listen_id == listen.id).order_by(ListenEvent.id))]


def test_link_unlink_and_events(s):
    l = L.create_listen(s); p = L.link(s, l, "input", file_id=f(s, 1).id, ordinal=1); L.unlink(s, p, reason="wrong file")
    assert p.unlinked_at is not None and actions(s, l) == ["create", "link", "unlink"]


def test_one_target_and_unique_active_ordinal(s):
    l = L.create_listen(s); a, b = f(s, 1), f(s, 2)
    with pytest.raises(IntegrityError), s.begin_nested(): L.link(s, l, "input", ordinal=1)           # no target
    p1 = L.link(s, l, "input", file_id=a.id, ordinal=1)
    with pytest.raises(IntegrityError), s.begin_nested(): L.link(s, l, "input", file_id=b.id, ordinal=1)
    L.unlink(s, p1); L.link(s, l, "input", file_id=b.id, ordinal=1)                                  # the ordinal is free once unlinked


def test_reorder(s):
    l = L.create_listen(s); ps = [L.link(s, l, "input", file_id=f(s, n).id, ordinal=n) for n in (1, 2, 3)]
    L.reorder(s, l, "input", [ps[2].id, ps[0].id, ps[1].id])
    assert [p.ordinal for p in ps] == [2, 3, 1]
    with pytest.raises(ValueError): L.reorder(s, l, "input", [ps[0].id])


def test_merge_moves_parts_and_retires_loser(s):
    a, b = L.create_listen(s), L.create_listen(s)
    pa = L.link(s, a, "input", file_id=f(s, 1).id, ordinal=1); pb = L.link(s, b, "input", file_id=f(s, 2).id, ordinal=1)
    L.merge(s, a, b, reason="same book")
    assert pb.listen_id == a.id and (pa.ordinal, pb.ordinal) == (1, 2) and b.merged_into_id == a.id and b.retired_at
    assert "merge" in actions(s, a) and actions(s, b)[-1] == "retire"
    with pytest.raises(ValueError): L.merge(s, a, b)


def test_split_creates_new_listen(s):
    a = L.create_listen(s); ps = [L.link(s, a, "input", file_id=f(s, n).id, ordinal=n) for n in (1, 2, 3)]
    new = L.split(s, a, [ps[1].id, ps[2].id])
    assert [p.listen_id for p in ps] == [a.id, new.id, new.id] and [p.ordinal for p in ps] == [1, 1, 2] and "split" in actions(s, a)


def test_auto_accept_event_carries_evidence(s):
    from app.store.models import Work
    l = L.create_listen(s); w = Work(kind="written", title="Red Dragon"); s.add(w); s.flush()
    p = L.link(s, l, "match", work_id=w.id, state="candidate", score=0.99, actor_kind="model")
    L.set_state(s, p, "accepted", actor_kind="auto_policy", actor_ref="policy-1", after={"score": 0.99, "margin": 0.4, "model": "v9"})
    e = s.scalars(select(ListenEvent).where(ListenEvent.action == "accept")).one()
    assert e.after == {"state": "accepted", "score": 0.99, "margin": 0.4, "model": "v9"} and e.before == {"state": "candidate"} and e.actor_kind == "auto_policy"
    L.set_state(s, p, "active", reason="withdrawn"); assert actions(s, l)[-1] == "revoke"


def test_choose_replaces_previous_choice(s):
    l = L.create_listen(s); L.choose(s, l, "title", "Red Dragon"); L.choose(s, l, "title", "The Red Dragon")
    assert s.execute(text("select count(*) from listen_choice where unchosen_at is null")).scalar() == 1
    assert actions(s, l).count("choose") == 2


def test_records_are_stored_once(s):
    s.add(PartRecord(source="audible", kind="product", payload={"a": 1}, payload_hash=b"x" * 32)); s.flush()
    with pytest.raises(IntegrityError), s.begin_nested(): s.add(PartRecord(source="audible", kind="product", payload={"a": 1}, payload_hash=b"x" * 32)); s.flush()


def test_schema_matches_models(db_engine, db_backend):
    from omarchy_db import drift
    from app.store.models import Base
    assert drift(db_engine, Base.metadata) == []
