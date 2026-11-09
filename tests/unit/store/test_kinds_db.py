"""Classifying listens in the database and the API around it (Postgres). Every title and name is invented."""
import hashlib

import pytest

pytest.importorskip("omarchy_db")      # first: the standard CI test environment has neither omarchy-db nor SQLAlchemy
from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy import select

from app.store import api, filegroups as fg, kinds as K, kinds_db, kinds_model as KM, listens as L
from app.store.models import Listen, ListenEvent, ListenKind, ListenPart, PartFile, PartFilegroup, PartFilegroupMember, PartRecord, Work

NO_AUDIBLE, NO_RADIO = (lambda asins: {}), (lambda titles: [])


@pytest.fixture
def s(db_session, db_backend):
    if db_backend != "postgres": pytest.skip("needs Postgres")
    return db_session


def tag(value): return dict(value=value, n=3, share=1.0)


def make(s, label, *, tags=None, folder=None, part_minutes=(30, 30, 30), group_kind="book", choices=None, candidates=()):
    """A listen with one input group of files of the given lengths, an embedded-tags record, a folder record, chosen fields and optional model candidates [(p, recordings)]."""
    run = fg.new_run(s, "test")
    g = PartFilegroup(run_id=run.id, kind=group_kind, label=label, path=f"/audio/{label}", file_count=len(part_minutes)); s.add(g); s.flush()
    for i, m in enumerate(part_minutes, 1):
        f = PartFile(sha256=hashlib.sha256(f"{label}{i}".encode()).digest(), size=1, duration_s=m * 60); s.add(f); s.flush(); s.add(PartFilegroupMember(group_id=g.id, ordinal=i, file_id=f.id))
    l = L.create_listen(s, "radio_series" if group_kind == "programme" else "book", actor_kind="rule", reason=f"{group_kind} group: made for the test"); l.label = label; s.flush()
    L.link(s, l, "input", filegroup_id=g.id, ordinal=1, actor_kind="rule", detail=dict(group_kind=group_kind, files=len(part_minutes), minutes=sum(part_minutes), path=f"/audio/{label}"))
    for n, (source, payload) in enumerate([("embedded_tags", dict(files=len(part_minutes), tagged=len(part_minutes), **{k: tag(v) for k, v in (tags or {}).items()})), ("folder_name", dict(title=label, folder=folder or label))]):
        r = PartRecord(source=source, kind="tag_summary", payload=payload, payload_hash=hashlib.sha256(f"{label}{n}".encode()).digest()); s.add(r); s.flush(); L.link(s, l, "record", record_id=r.id, ordinal=n + 1, actor_kind="rule")
    for field, value in (choices or {}).items(): L.choose(s, l, field, value, actor_kind="rule")
    for n, (p, recordings) in enumerate(candidates, 1):
        w = Work(kind="written", title=label); s.add(w); s.flush()
        L.link(s, l, "candidate", work_id=w.id, ordinal=n, state="candidate", score=p, detail=dict(recordings=recordings), actor_kind="model")
    return l


def kind_of(s, l): s.expire_all(); return s.get(Listen, l.id).kind


def test_a_radio_series_filed_as_a_book_is_reclassified_with_its_reasons_and_an_event(s):
    radio = make(s, "Greenhouse Series 1", tags=dict(artist="BBC Radio 4", album="Greenhouse", genre="Comedy"), choices=dict(title="Greenhouse: Series 1", author="BBC Radio"), part_minutes=(28, 28, 28, 28))
    book = make(s, "The Glass Orchard", tags=dict(artist="Mara Quill", genre="Audiobook"), choices=dict(title="The Glass Orchard (Unabridged)", author="Mara Quill"), part_minutes=(60,) * 10)
    res = {r["id"]: r for r in kinds_db.classify_listens(s, audible=NO_AUDIBLE, radio=NO_RADIO)}
    assert (res[radio.id]["kind"], res[radio.id]["band"], res[radio.id]["changed"]) == ("radio_series", "broadcast", True)
    assert kind_of(s, radio) == "radio_series" and kind_of(s, book) == "book"
    row = s.get(ListenKind, radio.id)
    assert row.kind == "radio_series" and row.band == "broadcast" and row.decided_by == "rule" and {x["name"] for x in row.signals} >= {"channel", "series number"}
    ev = s.scalars(select(ListenEvent).where(ListenEvent.listen_id == radio.id, ListenEvent.action == "reclassify")).one()
    assert ev.before == {"kind": "book"} and ev.after["kind"] == "radio_series" and ev.actor_kind == "rule" and "channel" in ev.reason
    assert s.scalars(select(ListenEvent).where(ListenEvent.listen_id == book.id, ListenEvent.action == "reclassify")).first() is None
    assert s.get(ListenKind, book.id).band == "book"


def test_a_rerun_with_nothing_new_changes_nothing(s):
    make(s, "Greenhouse Series 1", tags=dict(artist="BBC Radio 4"), part_minutes=(28, 28, 28, 28))
    kinds_db.classify_listens(s, audible=NO_AUDIBLE, radio=NO_RADIO)
    before = s.scalar(select(__import__("sqlalchemy").func.count()).select_from(ListenEvent).where(ListenEvent.action == "reclassify"))
    again = kinds_db.classify_listens(s, audible=NO_AUDIBLE, radio=NO_RADIO)
    assert not any(r["changed"] for r in again) and s.scalar(select(__import__("sqlalchemy").func.count()).select_from(ListenEvent).where(ListenEvent.action == "reclassify")) == before


def test_a_dry_run_writes_nothing(s):
    l = make(s, "Greenhouse Series 1", tags=dict(artist="BBC Radio 4"), part_minutes=(28, 28, 28, 28))
    res = kinds_db.classify_listens(s, apply=False, audible=NO_AUDIBLE, radio=NO_RADIO)
    assert res[0]["kind"] == "radio_series" and res[0]["changed"] and kind_of(s, l) == "book" and s.get(ListenKind, l.id) is None


def test_a_persons_choice_is_never_overwritten(s):
    l = make(s, "Greenhouse Series 1", tags=dict(artist="BBC Radio 4"), part_minutes=(28, 28, 28, 28))
    kinds_db.set_kind(s, l.id, "book", actor_ref="me")
    res = kinds_db.classify_listens(s, audible=NO_AUDIBLE, radio=NO_RADIO)
    assert res[0]["user"] and res[0]["kind"] == "book" and not res[0]["changed"] and kind_of(s, l) == "book"
    row = s.get(ListenKind, l.id); assert row.decided_by == "user" and row.signals[0]["name"] == "your decision"
    kinds_db.set_kind(s, l.id, "play"); assert kind_of(s, l) == "play" and s.get(ListenKind, l.id).guess == "play"
    assert [e.actor_kind for e in s.scalars(select(ListenEvent).where(ListenEvent.listen_id == l.id, ListenEvent.action == "reclassify").order_by(ListenEvent.id))] == ["user"]      # book -> book was no change


def test_set_kind_refuses_unknown_kinds_and_listens(s):
    l = make(s, "Plain Novel")
    with pytest.raises(ValueError, match="unknown kind"): kinds_db.set_kind(s, l.id, "podcast")
    with pytest.raises(LookupError): kinds_db.set_kind(s, 999999, "book")
    kinds_db.set_kind(s, l.id, "book"); assert s.scalars(select(ListenEvent).where(ListenEvent.listen_id == l.id, ListenEvent.action == "reclassify")).first() is None      # unchanged: no event


def test_audible_and_the_radio_index_feed_the_classifier_when_a_match_is_close(s):
    rec = dict(asin="B0TESTAAAA", fmt="unabridged", minutes=88, title="Quiet Rooms", narrators=[])
    l = make(s, "Quiet Rooms", tags=dict(genre="Drama"), part_minutes=(88,), candidates=[(0.9, [rec])])
    seen = {}
    def audible(asins): seen["asins"] = set(asins); return {"B0TESTAAAA": dict(content_type="Radio/TV Program", keywords=["dramatizations"], publisher="BBC Audio")}
    def radio(titles): seen["titles"] = titles; return [dict(source="bbc_radio", ref="b001", title="Quiet Rooms", service="bbc_radio_three", first_broadcast="")]
    f = kinds_db.gather(s, [l.id], audible=audible, radio=radio)[l.id]
    assert "B0TESTAAAA" in seen["asins"] and "Quiet Rooms" in seen["titles"]
    assert f.audible[0]["content_type"] == "Radio/TV Program" and f.audible[0]["diff"] == 0 and f.audible[0]["p"] == 0.9 and f.minutes == 88 and f.part_minutes == [88]
    res = kinds_db.classify_listens(s, [l.id], audible=audible, radio=radio)[0]
    assert res["band"] == "broadcast" and res["kind"] == "play" and {x["name"] for x in res["signals"]} >= {"audible content type", "programme catalogue", "audible keywords"}


def test_gather_reads_tags_folder_choices_and_the_structure_prior(s):
    l = make(s, "Greenhouse", group_kind="programme", tags=dict(artist="BBC Radio 4", album_artist="Hana Vell", composer="Ida Fenn", genre="Comedy", publisher="BBC Audio", title="Part 1", audible_asin="B0X"), folder="Greenhouse S01", choices=dict(title="Greenhouse", author="Hana Vell", narrator="Ada Penn", publisher="A Press"))
    f = kinds_db.gather(s, [l.id], audible=NO_AUDIBLE, radio=NO_RADIO)[l.id]
    assert (f.artist, f.album_artist, f.composer, f.genre, f.tag_publisher, f.track_title, f.asin) == ("BBC Radio 4", "Hana Vell", "Ida Fenn", "Comedy", "BBC Audio", "Part 1", "B0X")
    assert (f.label, f.author, f.narrator, f.publisher, f.folder, f.structure_kind, f.structure_reason) == ("Greenhouse", "Hana Vell", "Ada Penn", "A Press", "Greenhouse S01", "programme", "made for the test")


def test_the_listens_api_filters_by_kind_and_saves_a_choice(s, monkeypatch):
    from app.linkage import service as linkage_service
    again = []; monkeypatch.setattr(linkage_service, "start_reidentify", lambda factory, lid: again.append(lid) or True)
    radio = make(s, "Greenhouse Series 1", tags=dict(artist="BBC Radio 4"), part_minutes=(28, 28, 28, 28))
    close = make(s, "One Voice", tags=dict(genre="Comedy"), part_minutes=(60,))
    plain = make(s, "Plain Novel", part_minutes=(400,))
    kinds_db.classify_listens(s, audible=NO_AUDIBLE, radio=NO_RADIO)
    app = FastAPI(); app.include_router(api.listens_router); app.dependency_overrides[api.get_session] = lambda: s; c = TestClient(app)
    review = c.get("/api/listens?kind=review").json(); assert [r["id"] for r in review["rows"]] == [close.id] and review["review"] == 1
    assert review["rows"][0]["kind_info"]["band"] == "review" and review["rows"][0]["kind_info"]["guess"] == "radio_show"
    assert [r["id"] for r in c.get("/api/listens?kind=not-book").json()["rows"]] == [radio.id]
    info = next(r for r in c.get("/api/listens").json()["rows"] if r["id"] == plain.id)["kind_info"]; assert info["band"] == "book" and info["signals"][0]["weight"] != 0
    ok = c.post(f"/api/listens/{radio.id}/kind", json={"kind": "book"}); assert ok.status_code == 200 and ok.json()["kind_info"]["decided_by"] == "user" and kind_of(s, radio) == "book"
    assert ok.json()["reidentifying"] and again == [radio.id]      # a new kind may change which sources supply candidates, so the match is worked out again
    assert c.post(f"/api/listens/{radio.id}/kind", json={"kind": "nonsense"}).status_code == 400 and c.post("/api/listens/999999/kind", json={"kind": "book"}).status_code == 404


def test_implied_labels_come_only_from_safe_accepted_audible_matches():
    f = lambda **a: K.Features(audible=[dict(asin="B0X", diff=0.005, p=0.99, **a)])
    assert kinds_db.implied(f(content_type="Product", publisher="Tantor Audio"), True) is False
    assert kinds_db.implied(f(content_type="Product", publisher="BBC Audio"), True) is None            # BBC files readings under every type
    assert kinds_db.implied(f(content_type="Radio/TV Program", publisher="BBC Audio", keywords=["dramatizations"]), True) is True
    assert kinds_db.implied(f(content_type="Radio/TV Program", publisher="BBC Audio"), True) is None   # could be a reading
    assert kinds_db.implied(f(content_type="Product", publisher="Tantor Audio"), False) is None         # not accepted
    assert kinds_db.implied(K.Features(audible=[dict(asin="B0X", diff=0.2, p=0.99, content_type="Product", publisher="Tantor Audio")]), True) is None      # a different length
    assert kinds_db.implied(K.Features(audible=[dict(asin="B0X", diff=0.0, p=0.5, content_type="Product", publisher="Tantor Audio")]), True) is None      # a doubtful match


def test_a_persons_choices_and_confirmations_are_labels_and_learning_refits_the_weights(s, tmp_path, monkeypatch):
    monkeypatch.setenv("APP_DATA", str(tmp_path))
    radio = [make(s, f"Greenhouse Series {i}", tags=dict(artist="BBC Radio 4"), part_minutes=(28, 28, 28, 28)) for i in range(1, 4)]
    odd = make(s, "One Voice", tags=dict(genre="Comedy"), part_minutes=(60,))
    plain = make(s, "Plain Novel", part_minutes=(400,))
    kinds_db.classify_listens(s, audible=NO_AUDIBLE, radio=NO_RADIO, params=None)
    for l in radio[:2]: kinds_db.set_kind(s, l.id, "radio_series")             # "the kind is right": a confirmation is the same answer as the current kind
    kinds_db.set_kind(s, odd.id, "book")
    params, counts = kinds_db.learn(s, audible=NO_AUDIBLE, radio=NO_RADIO)
    assert counts == {"user": 3, "audible match": 0, "radio match": 0, "broadcast": 2, "book": 1}
    assert params.seen["channel"] == 2 and params.seen["programme genre"] == 1 and params.mult["programme genre"] < 1.0 and params.mult["channel"] > 1.0
    assert KM.load().version() == params.version()
    out = kinds_db.learn_and_classify(s, audible=NO_AUDIBLE, radio=NO_RADIO)
    assert out["labels"]["user"] == 3 and out["version"] == params.version()
    row = s.get(ListenKind, radio[2].id); assert row.decided_by == "rule" and "prior" in {x["name"]: x for x in row.signals}["channel"]      # the third series is re-judged with learned weights, which the reasons show against the prior
    assert s.get(ListenKind, odd.id).decided_by == "user" and kind_of(s, odd) == "book" and kind_of(s, plain) == "book"


def test_an_accepted_audible_match_is_a_half_weight_label_whose_own_evidence_is_left_out(s, tmp_path, monkeypatch):
    monkeypatch.setenv("APP_DATA", str(tmp_path))
    rec = dict(asin="B0TESTBBBB", fmt="unabridged", minutes=400, title="Plain Novel", narrators=[])
    l = make(s, "Plain Novel", part_minutes=(400,), candidates=[(0.99, [rec])])
    part = s.scalars(select(ListenPart).where(ListenPart.listen_id == l.id, ListenPart.role == "candidate")).one()
    part.role = "match"; part.state = "accepted"; s.flush()
    audible = lambda asins: {"B0TESTBBBB": dict(content_type="Product", keywords=[], publisher="Tantor Audio")}
    params, counts = kinds_db.learn(s, audible=audible, radio=NO_RADIO)
    assert counts == {"user": 0, "audible match": 1, "radio match": 0, "broadcast": 0, "book": 1}
    assert "audible audiobook" not in params.seen          # learned from the length and tags, not from the evidence that created the label
    assert "long" in params.seen


def test_a_rebuild_clears_the_verdicts_with_the_listens(s):
    from app.store import build
    make(s, "Greenhouse Series 1", tags=dict(artist="BBC Radio 4"), part_minutes=(28, 28, 28, 28))
    kinds_db.classify_listens(s, audible=NO_AUDIBLE, radio=NO_RADIO, params=None)
    assert s.scalars(select(ListenKind)).all()
    build.clear(s)
    assert not s.scalars(select(ListenKind)).all() and not s.scalars(select(Listen)).all()
