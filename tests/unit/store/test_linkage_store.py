"""Identification written to the Postgres catalogue (`app.linkage.apply`, `query.load`, `files`, catalogue labels). Invented folder names only."""

import pytest
from sqlalchemy import func, select

from app.linkage import apply, files
from app.linkage import dataset as D
from app.linkage import model as M
from app.linkage import query as Q
from app.store import build
from app.store import filegroups as fg
from app.store import listens as L
from app.store import metadata as md
from app.store import structure as st
from app.store.models import Listen, ListenEvent, ListenPart, Work, WorkIdentifier


@pytest.fixture
def s(db_session, db_backend):
    if db_backend != "postgres":
        pytest.skip("needs Postgres")
    return db_session


@pytest.fixture
def listens(s, tmp_path):
    """Two copies of one recording (same runtime), another narration of the same book, and an unrelated book; names say title and author."""
    minutes = {
        "Books/Mara Quill - Glass Orchard": 600,
        "Audible/Mara Quill - Glass Orchard": 606,
        "Books/Mara Quill - Glass Orchard (Ada Penn)": 450,
        "Books/Ben Ash - Salt Roads": 300,
    }
    for folder in minutes:
        for i in range(1, 4):
            p = tmp_path / folder / f"{i:02d}.mp3"
            p.parent.mkdir(parents=True, exist_ok=True)
            p.write_bytes(f"{folder}{i}".encode())
    ids = fg.scan_files(s, str(tmp_path))
    for path, fid in ids.items():
        rel = path[len(str(tmp_path)) + 1 :].rsplit("/", 1)[0]
        s.get(fg.PartFile, fid).duration_s = minutes[rel] * 60 / 3
    s.flush()
    top, _ = st.infer(s, fg.group_by_folder(s, str(tmp_path), ids))
    build.build_listens(s, top)
    md.add_metadata(s)
    return {
        p.detail["path"][len(str(tmp_path)) + 1 :]: s.get(Listen, p.listen_id)
        for p in s.scalars(select(ListenPart).where(ListenPart.role == "input"))
    }  # keyed by folder


def params():
    return M.train(
        [
            M.Case(
                i,
                {
                    "ok": [dict(title="exact_rare", author="all", runtime="1pct", sources="both")],
                    "bad": [dict(title="weak", author="conflict", sources="openlibrary")],
                },
            )
            for i in range(30)
        ]
    )


GLASS = dict(
    key="/works/OL1W",
    title="Glass Orchard",
    authors=["Mara Quill"],
    year=2001,
    ol=["/works/OL1W", "/works/OL7W"],
    asins=["B0AAAAAAA1"],
    isbns=["9780306406157"],
)
SALT = dict(
    key="asin:B0AAAAAAA9",
    title="Salt Roads",
    authors=["Ben Ash", "Anonymous"],
    year=None,
    ol=[],
    asins=["B0AAAAAAA9"],
    isbns=[],
)


def data_for(listens, sure=True):
    good = dict(
        title="exact_rare",
        author="all",
        runtime="1pct",
        sources="both",
        _alias="glass orchard",
        _asin="B0AAAAAAA1",
        _reading="folder",
    )
    out = {}
    for label, l in listens.items():
        if "Salt" in label:
            out[str(l.id)] = dict(
                clusters={SALT["key"]: SALT, GLASS["key"]: GLASS},
                vectors={
                    SALT["key"]: [dict(good, _alias="salt roads")],
                    GLASS["key"]: [dict(title="weak", author="conflict", sources="both")] if sure else [good],
                },
            )  # unsure: two works fit equally well
        else:
            out[str(l.id)] = dict(
                clusters={GLASS["key"]: GLASS, SALT["key"]: SALT},
                vectors={GLASS["key"]: [good], SALT["key"]: [dict(title="weak", author="conflict", sources="audible")]},
            )
    return out


def test_load_reads_readings_runtime_and_files(s, listens):
    q = next(q for q in Q.load(s) if q.listen_id == listens["Books/Mara Quill - Glass Orchard (Ada Penn)"].id)
    assert (
        q.minutes == [450.0]
        and q.hints == ["Ada Penn"]
        and any(r.title == "Glass Orchard" and r.author == "Mara Quill" for r in q.readings)
    )
    fs, paths = files.listen_files(s)
    assert sorted(len(v) for v in fs.values()) == [3, 3, 3, 3] and all(p for p in paths.values())
    assert len(Q.load(s, ids=[q.listen_id])) == 1


def test_apply_accepts_links_one_work_per_book_and_reruns_quietly(s, listens):
    p = params()
    counts = apply.apply(s, data_for(listens), p)
    assert counts["accept"] == 4
    works = s.scalars(select(Work)).all()
    assert sorted(w.title for w in works) == ["Glass Orchard", "Salt Roads"]
    glass = next(w for w in works if w.title == "Glass Orchard")
    assert {
        (i.scheme, i.value) for i in s.scalars(select(WorkIdentifier).where(WorkIdentifier.work_id == glass.id))
    } >= {("work", "/works/OL1W"), ("ol_work", "/works/OL7W"), ("asin", "B0AAAAAAA1"), ("isbn", "9780306406157")}
    match = s.scalars(select(ListenPart).where(ListenPart.role == "match")).first()
    assert (
        match.state == "accepted"
        and match.linked_by == "model"
        and match.score > 0.95
        and match.detail["recording"] == "B0AAAAAAA1"
        and match.detail["vector"]["runtime"] == "1pct"
    )
    assert {l.status for l in s.scalars(select(Listen))} == {"identified"}
    assert [
        e.action
        for e in s.scalars(select(ListenEvent).where(ListenEvent.listen_part_id == match.id).order_by(ListenEvent.id))
    ] == ["link", "accept"]
    assert len(apply.same_work_groups(s)[glass.id]) == 3  # copies and the other narration all link to one work
    assert apply.apply(s, data_for(listens), p)["unchanged"] == 4


def test_review_candidates_then_a_person_decides_and_is_kept(s, listens):
    p = params()
    apply.apply(s, data_for(listens, sure=False), p)
    salt = next(l for k, l in listens.items() if "Salt" in k)
    cands = s.scalars(
        select(ListenPart)
        .where(ListenPart.listen_id == salt.id, ListenPart.role == "candidate", ListenPart.unlinked_at.is_(None))
        .order_by(ListenPart.ordinal)
    ).all()
    assert salt.status == "review" and cands and all(c.state == "candidate" for c in cands)
    L.set_state(s, cands[0], "accepted", actor_kind="user", actor_ref="tester")
    labels = D.catalogue_labels(s)
    assert labels[salt.id]["origin"] == "user" and labels[salt.id]["target"]["key"] == cands[0].detail["candidate"]
    assert apply.apply(s, data_for(listens), p)["kept"] == 1  # a person's decision is never replaced
    assert (
        apply.clear(s) == 3
        and s.scalar(select(func.count()).select_from(ListenPart).where(ListenPart.role == "match")) == 0
    )
    assert (
        s.scalars(select(ListenPart).where(ListenPart.listen_id == salt.id, ListenPart.role == "candidate")).first()
        is not None
    )


def test_merge_copies_only_merges_the_same_recording(s, listens):
    apply.apply(s, data_for(listens), params())
    merged = apply.merge_copies(s)
    assert len(merged) == 1
    live = {l.id for l in s.scalars(select(Listen).where(Listen.merged_into_id.is_(None)))}
    assert (
        len(live) == 3 and listens["Books/Mara Quill - Glass Orchard (Ada Penn)"].id in live
    )  # the other narration (450 min) stays its own listen
    assert apply.merge_copies(s) == []


def test_all_labels_read_the_old_catalogue_file(s, listens, tmp_path, monkeypatch):
    monkeypatch.setattr(D, "folder", lambda: tmp_path)
    assert D.all_labels(s) == {}
    (tmp_path / "old-labels.json").write_text('{"5": {"target": {"work": "/works/OL1W"}, "origin": "manual"}}')
    assert D.all_labels(s) == {5: {"target": {"work": "/works/OL1W"}, "origin": "manual"}}


def test_decisions_confirm_reject_and_none_feed_labels_and_the_next_run(s, listens):
    p = params()
    data = data_for(listens)
    apply.apply(s, data, p)
    glass = listens["Books/Mara Quill - Glass Orchard"]
    salt = listens["Books/Ben Ash - Salt Roads"]
    other = listens["Books/Mara Quill - Glass Orchard (Ada Penn)"]
    match = lambda l: s.scalars(
        select(ListenPart).where(
            ListenPart.listen_id == l.id,
            ListenPart.role.in_(("match", "candidate")),
            ListenPart.unlinked_at.is_(None),
            ListenPart.state != "rejected",
        )
    ).first()
    apply.decide(s, glass, match(glass), "approve")  # confirming an automatic acceptance
    assert [
        e.actor_kind
        for e in s.scalars(
            select(ListenEvent).where(ListenEvent.listen_part_id == match(glass).id, ListenEvent.action == "accept")
        )
    ] == ["auto_policy", "user"]
    apply.decide(s, salt, match(salt), "reject")
    assert salt.status == "review"
    apply.decide(s, other, None, "none")
    assert other.status == apply.NOT_FOUND
    labels = D.catalogue_labels(s)
    assert (
        labels[glass.id]["target"]["key"] == GLASS["key"]
        and labels[salt.id]["rejects"] == [SALT["key"]]
        and labels[other.id]["none"]
    )
    counts = apply.apply(s, data, p)
    assert counts["kept"] == 2  # the confirmed and the "none of these" listen
    nxt = match(salt)
    assert (
        nxt is not None and nxt.detail["candidate"] == GLASS["key"]
    )  # the rejected work is out of the running, the rest is re-decided
    with pytest.raises(ValueError):
        apply.decide(s, salt, None, "approve")
    with pytest.raises(ValueError):
        apply.decide(s, salt, nxt, "maybe")


def test_match_queue_api_pages_easiest_first_and_records_answers(s, listens, monkeypatch):
    from fastapi import FastAPI
    from fastapi.testclient import TestClient

    from app.linkage import api as A
    from app.linkage import service

    apply.apply(s, data_for(listens, sure=False), params())
    app = FastAPI()
    app.include_router(A.router)
    app.dependency_overrides[A.get_session] = lambda: s
    c = TestClient(app)
    first = c.get("/api/matches?limit=2").json()
    assert (
        first["summary"]["waiting"] == 4
        and len(first["items"]) == 2
        and first["items"][0]["p"] >= first["items"][1]["p"]
    )
    it = first["items"][0]
    assert (
        it["copies"][0]["minutes"]
        and it["chosen"]["title"]
        and it["candidates"][0]["recordings"] == []
        and it["candidates"][0]["evidence"]["title"] == "exact_rare"
    )
    rest = c.get("/api/matches", params=dict(after=first["items"][-1]["cursor"])).json()["items"]
    assert {i["listen_id"] for i in first["items"]} | {i["listen_id"] for i in rest} == {
        l.id for l in listens.values()
    } and len(rest) == 2
    assert c.get("/api/matches?after=nonsense").status_code == 400
    started = []
    monkeypatch.setattr(service, "start", lambda factory, **kw: started.append(1) or True)
    monkeypatch.setattr(A, "AUTO_RETRAIN", 2)
    r = c.post(
        "/api/matches/decide",
        json=dict(listen_id=it["listen_id"], action="approve", part_id=it["candidates"][0]["part_id"]),
    ).json()
    assert r["ok"] and r["status"] == "identified"
    r = c.post(
        "/api/matches/decide",
        json=dict(listen_id=rest[0]["listen_id"], action="reject", part_id=rest[0]["candidates"][0]["part_id"]),
    ).json()
    assert r["retraining"] and started == [1]  # every AUTO_RETRAIN answers the model relearns
    after = c.get("/api/matches").json()
    assert after["summary"]["confirmed"] == 1 and it["listen_id"] not in [i["listen_id"] for i in after["items"]]
    assert c.post("/api/matches/decide", json=dict(listen_id=999999, action="none")).status_code == 404
    assert c.post("/api/matches/decide", json=dict(listen_id=it["listen_id"], action="sideways")).status_code == 400
    assert c.post("/api/matches/retrain").json() == {"started": True}


def test_match_queue_api_filters_sorts_and_pages_within_the_filter(s, listens):
    from fastapi import FastAPI
    from fastapi.testclient import TestClient

    from app.linkage import api as A

    apply.apply(s, data_for(listens, sure=False), params())
    salt = listens["Books/Ben Ash - Salt Roads"]
    salt.kind = "radio_series"
    s.flush()
    app = FastAPI()
    app.include_router(A.router)
    app.dependency_overrides[A.get_session] = lambda: s
    c = TestClient(app)
    whole = c.get("/api/matches").json()
    assert (
        whole["total"] == 4
        and whole["facets"]["kind"] == {"book": 3, "radio_series": 1, "not-book": 1}
        and sum(whole["facets"]["certainty"].values()) == 4
    )
    assert all(it["kind"] and it["status"] and it["added"] for it in whole["items"]) and "text" not in whole["items"][0]
    assert (
        abs(whole["items"][0]["added"] - float(s.scalar(select(func.extract("epoch", func.now()))))) < 3600
    )  # the instant the database added it, whatever zone the API host runs in
    ps = [it["p"] for it in c.get("/api/matches", params=dict(sort="uncertain")).json()["items"]]
    assert ps == sorted(ps)
    recent = [it["listen_id"] for it in c.get("/api/matches", params=dict(sort="recent")).json()["items"]]
    assert recent == sorted(recent, reverse=True)  # built together, so the newest listen first
    radio = c.get("/api/matches", params=dict(kind="radio_series")).json()
    assert (
        [it["listen_id"] for it in radio["items"]] == [salt.id]
        and radio["total"] == 1
        and radio["summary"]["waiting"] == 4
    )  # the summary is the whole queue
    found = c.get("/api/matches", params=dict(q="ben ash")).json()  # the folder and the chosen author are searched
    assert [it["listen_id"] for it in found["items"]] == [salt.id]
    assert (
        c.get("/api/matches", params=dict(q="glass orchard")).json()["total"] == 4
    )  # every listen has Glass Orchard as a candidate
    skip = c.get("/api/matches", params=dict(auto="skip")).json()
    assert (
        all(not it["candidates"][0]["auto"] for it in skip["items"])
        and skip["total"] == whole["facets"]["auto"]["skip"]
    )
    first = c.get("/api/matches", params=dict(sort="uncertain", limit=3)).json()["items"]
    rest = c.get("/api/matches", params=dict(sort="uncertain", after=first[-1]["cursor"])).json()
    assert [it["listen_id"] for it in first + rest["items"]] == [
        it["listen_id"] for it in c.get("/api/matches", params=dict(sort="uncertain")).json()["items"]
    ] and rest["total"] == 4
    assert c.get("/api/matches", params=dict(sort="random")).status_code == 400
    assert c.get("/api/matches", params=dict(certainty="certain")).status_code == 400


def test_retrain_fits_saves_and_reapplies(s, listens, tmp_path, monkeypatch):
    from contextlib import nullcontext

    from app.linkage import service

    monkeypatch.setattr(D, "folder", lambda: tmp_path)
    with pytest.raises(ValueError):
        service.train(s)
    D.save_cache(data_for(listens))
    counts = service.retrain_and_apply(lambda: nullcontext(s), validate=True)
    assert counts["accept"] == 4 and service.load_params() is not None and (tmp_path / "report.json").exists()
    assert service.STATE["running"] is False and service.STATE["report"]["labels"]["labelled"] == 0


def test_rebuild_of_some_listens_keeps_the_others(s, listens):
    apply.apply(s, data_for(listens), params())
    one = listens["Books/Ben Ash - Salt Roads"]
    assert apply.clear(s, ids={one.id}) == 1
    assert (
        s.scalar(select(func.count()).select_from(ListenPart).where(ListenPart.role == "match")) == 3
        and one.status == "grouped"
    )


def test_match_queue_carries_each_listens_kind_verdict_and_filters_by_it(s, listens):
    from fastapi import FastAPI
    from fastapi.testclient import TestClient

    from app.linkage import api as A
    from app.store import kinds_db

    apply.apply(s, data_for(listens, sure=False), params())
    salt = listens["Books/Ben Ash - Salt Roads"]
    glass = listens["Books/Mara Quill - Glass Orchard"]
    kinds_db.set_kind(s, salt.id, "radio_series", actor_ref="test")  # a person's call
    s.add(
        kinds_db.ListenKind(
            listen_id=glass.id,
            kind="book",
            guess="play",
            p_broadcast=0.5,
            confidence=0.5,
            band="review",
            signals=[dict(name="play length", weight=0.6, detail="")],
            version=1,
        )
    )
    s.flush()
    app = FastAPI()
    app.include_router(A.router)
    app.dependency_overrides[A.get_session] = lambda: s
    c = TestClient(app)
    items = {it["listen_id"]: it for it in c.get("/api/matches").json()["items"]}
    assert (
        items[salt.id]["kind_info"]["decided_by"] == "user"
        and items[glass.id]["kind_info"]["band"] == "review"
        and any(it["kind_info"] is None for it in items.values())
    )
    review = c.get("/api/matches?kind=review").json()
    assert (
        [it["listen_id"] for it in review["items"]] == [glass.id]
        and review["facets"]["kind"]["review"] == 1
        and review["facets"]["kind"]["not-book"] == 1
    )
    assert [it["listen_id"] for it in c.get("/api/matches?kind=not-book").json()["items"]] == [salt.id]
    assert [it["listen_id"] for it in c.get("/api/matches?kind=radio_series").json()["items"]] == [salt.id]
    unsure = [it["listen_id"] for it in c.get("/api/matches?sort=kind-uncertain").json()["items"]]
    assert unsure[0] == glass.id  # the close call comes first
    assert c.get("/api/matches?sort=bogus").status_code == 400


def test_an_answer_on_a_candidate_a_relearn_replaced_follows_the_work_to_its_new_link(s, listens):
    """The queue is not redrawn when the model relearns, so a card can still carry the id of a link the relearn unlinked and re-created: the answer is about the work, so it lands on the current link (a vanished work: reject is a no-op, approve is refused)."""
    from datetime import datetime

    from fastapi import FastAPI
    from fastapi.testclient import TestClient

    from app.linkage import api as A

    apply.apply(s, data_for(listens, sure=False), params())
    app = FastAPI()
    app.include_router(A.router)
    app.dependency_overrides[A.get_session] = lambda: s
    c = TestClient(app)
    a, b = c.get("/api/matches").json()["items"][:2]
    ca, cb = a["candidates"][0]["part_id"], b["candidates"][0]["part_id"]
    old = s.get(ListenPart, ca)
    old.unlinked_at = datetime.now()
    s.flush()
    fresh = ListenPart(
        listen_id=old.listen_id, role="candidate", work_id=old.work_id, ordinal=99, state="candidate", score=old.score
    )
    s.add(fresh)
    gone = s.get(ListenPart, cb)
    gone.unlinked_at = datetime.now()
    s.flush()
    assert (
        c.post("/api/matches/decide", json=dict(listen_id=a["listen_id"], action="approve", part_id=ca)).json()[
            "status"
        ]
        == "identified"
    )
    assert fresh.state == "accepted"
    assert c.post("/api/matches/decide", json=dict(listen_id=b["listen_id"], action="reject", part_id=cb)).json()["ok"]
    assert (
        c.post("/api/matches/decide", json=dict(listen_id=b["listen_id"], action="approve", part_id=cb)).status_code
        == 409
    )


def test_rerun_after_a_rejected_first_candidate_numbers_the_new_candidates_past_it(s, listens):
    """A person rejects the top review candidate; the rejection stays linked (it is the record of the decision) and keeps its ordinal, so the next run's candidates must not reuse it (`listen_part_active_ordinal`)."""
    p = params()
    apply.apply(s, data_for(listens, sure=False), p)
    salt = listens["Books/Ben Ash - Salt Roads"]
    active = lambda: s.scalars(
        select(ListenPart)
        .where(ListenPart.listen_id == salt.id, ListenPart.role == "candidate", ListenPart.unlinked_at.is_(None))
        .order_by(ListenPart.ordinal)
    ).all()
    first = active()[0]
    assert first.ordinal == 1 and salt.status == "review"
    apply.decide(s, salt, first, "reject")
    data = data_for(listens, sure=False)
    data[str(salt.id)]["vectors"][GLASS["key"]] = [
        dict(title="weak", author="conflict", sources="both")
    ]  # still not sure about the work that is left
    data[str(salt.id)]["clusters"]["/works/OL3W"] = dict(
        GLASS, key="/works/OL3W", title="Salt Road", ol=["/works/OL3W"]
    )
    data[str(salt.id)]["vectors"]["/works/OL3W"] = [dict(title="weak", author="conflict", sources="both")]
    apply.apply(s, data, p)
    s.flush()
    parts = active()
    ordinals = [x.ordinal for x in parts]
    assert len(ordinals) == len(set(ordinals)) and parts[0].id == first.id and first.state == "rejected"
    fresh = [x for x in parts if x.state == "candidate"]
    assert (
        fresh
        and min(x.ordinal for x in fresh) > first.ordinal
        and [x.ordinal for x in sorted(fresh, key=lambda x: -x.score)] == sorted(x.ordinal for x in fresh)
    )  # still ranked
    assert apply.apply(s, data, p)["unchanged"] >= 1  # and a rerun with the same answer changes nothing


def test_a_second_identification_run_is_refused_while_one_holds_the_lock(s, listens, db_engine):
    from sqlalchemy import text
    from sqlalchemy.orm import Session

    with Session(db_engine) as other:
        other.execute(
            text("select pg_advisory_xact_lock(hashtext('listory.identify'))")
        )  # a run in another process, not yet committed
        with pytest.raises(RuntimeError, match="in progress"):
            apply.apply(s, data_for(listens), params())
    assert apply.apply(s, data_for(listens), params())["accept"] == 4  # the lock goes with the other run's transaction


HARBOUR = dict(
    key="radio:harbour-lights",
    title="Harbour Lights",
    authors=[],
    year=2012,
    ol=[],
    asins=[],
    isbns=[],
    programmes=[
        dict(
            source="bbc_radio",
            ref="b0test01",
            title="Harbour Lights",
            service="bbc_radio_four",
            channel="BBC Radio 4",
            first_broadcast="2012-05-01",
            type="series",
            episodes=12,
            genres=["Comedy"],
            synopsis="",
        ),
        dict(
            source="wikidata_drama",
            ref="Q99",
            title="Harbour Lights",
            service="",
            channel="",
            first_broadcast="",
            type="",
            episodes=None,
            genres=[],
            synopsis="",
        ),
    ],
)


def test_a_radio_programme_is_a_work_with_pid_and_wikidata_ids_and_a_card_that_shows_it(s, listens):
    from fastapi import FastAPI
    from fastapi.testclient import TestClient

    from app.linkage import api as A

    salt = next(l for k, l in listens.items() if "Salt" in k)
    radio = dict(
        title="exact_rare", sources="radio", channel="match", form="agree", _alias="harbour lights", _reading="folder"
    )
    apply.apply(
        s,
        {
            str(salt.id): dict(
                clusters={HARBOUR["key"]: HARBOUR, GLASS["key"]: GLASS},
                vectors={
                    HARBOUR["key"]: [radio],
                    GLASS["key"]: [dict(title="weak", author="conflict", sources="both", form="conflict")],
                },
            )
        },
        params(),
    )
    w = s.scalars(select(Work).where(Work.kind == "radio_programme")).one()
    assert w.title == "Harbour Lights" and {
        (i.scheme, i.value) for i in s.scalars(select(WorkIdentifier).where(WorkIdentifier.work_id == w.id))
    } >= {("work", "radio:harbour-lights"), ("bbc_pid", "b0test01"), ("wikidata", "Q99")}
    part = s.scalars(
        select(ListenPart).where(
            ListenPart.listen_id == salt.id,
            ListenPart.role.in_(("match", "candidate")),
            ListenPart.unlinked_at.is_(None),
        )
    ).first()
    assert (
        part.work_id == w.id
        and part.detail["candidate"] == "radio:harbour-lights"
        and part.detail["programmes"][0]["channel"] == "BBC Radio 4"
    )
    apply.apply(
        s, {str(salt.id): dict(clusters={HARBOUR["key"]: HARBOUR}, vectors={HARBOUR["key"]: [radio]})}, params()
    )  # a second listen of the same programme finds the same work row
    assert len(s.scalars(select(Work).where(Work.kind == "radio_programme")).all()) == 1
    app = FastAPI()
    app.include_router(A.router)
    app.dependency_overrides[A.get_session] = lambda: s
    c = TestClient(app)
    card = next(it for it in c.get("/api/matches").json()["items"] if it["listen_id"] == salt.id)["candidates"][0]
    assert (
        card["key"] == "radio:harbour-lights"
        and card["programmes"][0]["ref"] == "b0test01"
        and card["recordings"] == []
    )


def test_a_radio_programme_accepted_as_the_match_is_a_broadcast_label_for_kind_learning(s, listens):
    from app.store import kinds_db

    salt = next(l for k, l in listens.items() if "Salt" in k)
    glass = next(l for k, l in listens.items() if k.endswith("Glass Orchard"))
    radio = dict(title="exact_rare", sources="radio", channel="match", form="agree", _alias="harbour lights")
    data = {
        str(salt.id): dict(clusters={HARBOUR["key"]: HARBOUR}, vectors={HARBOUR["key"]: [radio]}),
        str(glass.id): dict(
            clusters={GLASS["key"]: GLASS},
            vectors={GLASS["key"]: [dict(title="exact_rare", author="all", sources="both")]},
        ),
    }
    apply.apply(s, data, params())
    feats = kinds_db.gather(s, [salt.id, glass.id], audible=lambda a: {}, radio=lambda t: [])
    lab = kinds_db.labels(s, [salt.id, glass.id], feats)
    assert (
        lab[salt.id] == (True, kinds_db.IMPLICIT_WEIGHT, "radio match") and glass.id not in lab
    )  # an automatic acceptance counts half; a book match implies nothing without an Audible recording
    part = s.scalars(select(ListenPart).where(ListenPart.listen_id == salt.id, ListenPart.role == "match")).one()
    apply.decide(s, salt, part, "approve")
    assert kinds_db.labels(s, [salt.id], feats)[salt.id] == (
        True,
        1.0,
        "radio match",
    )  # a person's acceptance counts in full


def test_a_persons_approved_radio_candidate_is_a_label_and_radio_listens_are_never_merged_as_copies(s, listens):
    from app.store import kinds_db

    a = next(l for k, l in listens.items() if "Salt" in k)
    b = next(l for k, l in listens.items() if k.endswith("Glass Orchard"))
    radio = dict(title="exact_rare", sources="radio", channel="match", form="agree", _alias="harbour lights")
    data = {
        str(l.id): dict(
            clusters={HARBOUR["key"]: HARBOUR, GLASS["key"]: GLASS},
            vectors={
                HARBOUR["key"]: [radio],
                GLASS["key"]: [dict(title="weak", author="conflict", sources="both", form="conflict")],
            },
        )
        for l in (a, b)
    }
    apply.apply(s, data, params())
    part = next(
        c
        for c in s.scalars(
            select(ListenPart).where(
                ListenPart.listen_id == a.id,
                ListenPart.role.in_(("match", "candidate")),
                ListenPart.unlinked_at.is_(None),
            )
        )
        if c.detail["candidate"] == "radio:harbour-lights"
    )
    part.role = "candidate"
    part.state = "candidate"
    s.flush()  # as the queue shows it before a person answers
    apply.decide(s, a, part, "approve")
    assert (
        part.role == "candidate" and part.state == "accepted"
    )  # a person's approval keeps the role: the label query must look at both
    feats = kinds_db.gather(s, [a.id], audible=lambda x: {}, radio=lambda t: [])
    assert kinds_db.labels(s, [a.id], feats)[a.id] == (True, 1.0, "radio match")
    for l in (a, b):  # two accepted radio matches of one programme with the same length: still two broadcasts
        for p in s.scalars(
            select(ListenPart).where(
                ListenPart.listen_id == l.id,
                ListenPart.role.in_(("match", "candidate")),
                ListenPart.unlinked_at.is_(None),
            )
        ):
            if (p.detail or {}).get("candidate") == "radio:harbour-lights":
                p.role = "match"
                p.state = "accepted"
    s.flush()
    assert (
        apply.same_work_groups(s)
        and apply.merge_copies(s) == []
        and a.merged_into_id is None
        and b.merged_into_id is None
    )


def test_changing_a_kind_identifies_that_listen_again(monkeypatch, tmp_path):
    """reidentify_listen: with no model it does nothing; with one it builds that listen's vectors, clears the model's earlier answer and applies again for that listen only."""
    from contextlib import nullcontext

    from app.identify import audibleindex
    from app.identify import candidates as olc
    from app.linkage import apply, dataset, query, service

    monkeypatch.setattr(dataset, "folder", lambda: tmp_path)
    monkeypatch.setattr(service, "load_params", lambda: None)
    assert service.reidentify_listen(lambda: nullcontext(object()), 7) is None
    calls = []

    class S:
        def commit(self):
            calls.append("commit")

    monkeypatch.setattr(service, "load_params", lambda: "params")
    monkeypatch.setattr(olc, "connect", lambda: (_ for _ in ()).throw(FileNotFoundError()))
    monkeypatch.setattr(audibleindex, "connect", lambda: None)
    monkeypatch.setattr(dataset, "index_stamp", lambda oc, ac: "none")
    monkeypatch.setattr(
        query, "load", lambda s, ids=None, **kw: [] if ids == [8] else [type("Q", (), dict(listen_id=7))()]
    )
    monkeypatch.setattr(dataset, "build", lambda oc, ac, qs, cache, indexes="": {"7": {"sig": "x"}})
    monkeypatch.setattr(apply, "clear", lambda s, ids=None: calls.append(("clear", ids)))
    monkeypatch.setattr(
        apply,
        "apply",
        lambda s, data, params, threshold, ids: calls.append(("apply", sorted(data), params, ids)) or {"accept": 1},
    )
    assert service.reidentify_listen(lambda: nullcontext(S()), 8) is None and calls == []  # no such live listen
    assert service.reidentify_listen(lambda: nullcontext(S()), 7) == {"accept": 1}
    assert calls == [("clear", {7}), ("apply", ["7"], "params", {7}), "commit"] and dataset.load_cache() == {
        "7": {"sig": "x"}
    }


def _parts_listens(s, tmp_path, names):
    for rel in names:
        p = tmp_path / rel
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_bytes(rel.encode())
    ids = fg.scan_files(s, str(tmp_path))
    for path, fid in ids.items():
        f = s.get(fg.PartFile, fid)
        f.duration_s = names[path[len(str(tmp_path)) + 1 :]] * 60
        f.probe = {
            "format": {"tags": {"album": "Delta Title", "artist": "Pat Author"}},
            "streams": [{"codec_type": "audio", "tags": {}}],
        }
    s.flush()
    top, _ = st.infer(s, fg.group_by_folder(s, str(tmp_path), ids))
    build.build_listens(s, top)
    md.add_metadata(s)
    return {
        p.detail["minutes"]: s.get(Listen, p.listen_id)
        for p in s.scalars(select(ListenPart).where(ListenPart.role == "input"))
    }


DELTA = dict(
    key="asin:B0DELTA0001",
    title="Delta Title",
    authors=["Pat Author"],
    year=None,
    ol=[],
    asins=["B0DELTA0001"],
    isbns=[],
    recordings=[dict(asin="B0DELTA0001", title="Delta Title", narrators=[], minutes=565, fmt="", year=None)],
)


def _decide(s, by_minutes):
    good = dict(
        title="exact_rare",
        author="all",
        runtime="far",
        sources="audible",
        _alias="delta title",
        _asin="B0DELTA0001",
        _reading="chosen",
    )
    data = {
        str(l.id): dict(clusters={DELTA["key"]: DELTA}, vectors={DELTA["key"]: [good]}) for l in by_minutes.values()
    }
    apply.apply(s, data, params())


def test_numbered_parts_whose_runtimes_add_up_to_the_recording_become_one_listen(s, tmp_path):
    by = _parts_listens(s, tmp_path, {"Books/Delta Title 01.m4b": 284, "Books/Delta Title 02.m4b": 281})
    _decide(s, by)
    joined = apply.merge_parts(s)
    assert len(joined) == 1
    live = s.scalars(select(Listen).where(Listen.merged_into_id.is_(None))).all()
    assert len(live) == 1
    parts = s.scalars(
        select(ListenPart)
        .where(ListenPart.listen_id == live[0].id, ListenPart.role == "input", ListenPart.unlinked_at.is_(None))
        .order_by(ListenPart.ordinal)
    ).all()
    assert [(p.ordinal, p.detail["minutes"], p.detail["relation"]) for p in parts] == [
        (1, 284, "part"),
        (2, 281, "part"),
    ]  # in listening order, marked as parts
    q = next(q for q in Q.load(s) if q.listen_id == live[0].id)
    assert q.minutes == [565.0]  # one runtime for identification, not two alternative copies
    assert apply.merge_parts(s) == []


def test_parts_that_do_not_add_up_or_are_other_books_are_not_joined(s, tmp_path):
    by = _parts_listens(
        s, tmp_path, {"Books/Delta Title 01.m4b": 284, "Books/Delta Title 02.m4b": 120}
    )  # 404 minutes: not the recording
    _decide(s, by)
    assert apply.merge_parts(s) == []
    s.rollback()


def test_copies_of_one_recording_are_not_parts(s, tmp_path):
    by = _parts_listens(s, tmp_path, {"Books/Delta Title.m4b": 565, "Audible/Delta Title.m4b": 563})
    _decide(s, by)
    assert apply.merge_parts(s) == []  # no numbers in the names, and each runs as long as the book


def test_a_leading_index_without_a_separator_and_a_listen_merged_as_copies(s, tmp_path):
    from app.store import build as B

    assert (
        B.part_key("01 Delta Title Part 1")
        == B.part_key("02 Delta Title Part 2")
        == B.part_key("Delta Title 03")
        == "deltatitle"
    )
    assert B.part_key("01 Alpha Book") != B.part_key("02 Beta Book")  # numbered books of a series are different books
    by = _parts_listens(s, tmp_path, {"Books/01 Delta Title Part 1.m4b": 284, "Books/02 Delta Title Part 2.m4b": 281})
    a, b = sorted(by.values(), key=lambda lst: lst.id)
    L.merge(s, a, b, actor_kind="rule", actor_ref="copies", reason="merged as copies before the numbering rule")
    _decide(s, {1: a})
    assert apply.merge_parts(s) == [
        (a.id, a.id)
    ]  # one listen whose two inputs were taken for copies: converted in place
    parts = s.scalars(
        select(ListenPart)
        .where(ListenPart.listen_id == a.id, ListenPart.role == "input", ListenPart.unlinked_at.is_(None))
        .order_by(ListenPart.ordinal)
    ).all()
    assert [(p.detail["minutes"], p.detail["relation"]) for p in parts] == [(284, "part"), (281, "part")]


def test_a_full_copy_beside_joined_parts_does_not_inflate_the_runtime(s, tmp_path):
    by = _parts_listens(
        s, tmp_path, {"Books/Delta Title 01.m4b": 284, "Books/Delta Title 02.m4b": 281, "Audible/Delta Title.m4b": 565}
    )
    parts_listens = [l for m, l in by.items() if m != 565]
    full = by[565]
    _decide(s, {i: l for i, l in enumerate(parts_listens)})
    assert len(apply.merge_parts(s)) == 1
    survivor = s.scalars(
        select(Listen).where(Listen.merged_into_id.is_(None), Listen.id.in_([l.id for l in parts_listens]))
    ).one()
    L.merge(s, survivor, full, actor_kind="model", reason="a full-length copy of the same recording")
    inputs = s.scalars(
        select(ListenPart)
        .where(ListenPart.listen_id == survivor.id, ListenPart.role == "input", ListenPart.unlinked_at.is_(None))
        .order_by(ListenPart.ordinal)
    ).all()
    assert [p.detail.get("relation") for p in inputs] == ["part", "part", None]
    assert sorted(p.detail["minutes"] for p in inputs) == [281, 284, 565]
    assert next(q for q in Q.load(s) if q.listen_id == survivor.id).minutes == [
        565.0,
        565.0,
    ]  # the parts' sum, then the alternative copy: never 1130
    assert apply.listen_minutes(s, survivor.id)[1] == 565.0


def test_the_listens_api_counts_parts_apart_from_copies(s, tmp_path):
    from fastapi import FastAPI
    from fastapi.testclient import TestClient

    from app.store import api

    by = _parts_listens(s, tmp_path, {"Books/Delta Title 01.m4b": 284, "Books/Delta Title 02.m4b": 281})
    _decide(s, by)
    apply.merge_parts(s)
    app = FastAPI()
    app.include_router(api.listens_router)
    app.dependency_overrides[api.get_session] = lambda: s
    rows = TestClient(app).get("/api/listens").json()["rows"]
    assert len(rows) == 1 and rows[0]["parts"] == 2 and rows[0]["copies"] == 1


def _chapter_folders(s, tmp_path, folders):
    """A book folder holding numbered sub-folders with titles of their own ('01 The Arrest'), each a few mp3s; {folder: minutes}."""
    for folder in folders:
        for i in range(1, 4):
            p = tmp_path / "Pat Author - Delta Title - Chapterized" / folder / f"{i:02d}.mp3"
            p.parent.mkdir(parents=True, exist_ok=True)
            p.write_bytes(f"{folder}{i}".encode())
    ids = fg.scan_files(s, str(tmp_path))
    for path, fid in ids.items():
        s.get(fg.PartFile, fid).duration_s = folders[path[len(str(tmp_path)) + 1 :].split("/")[1]] * 60 / 3
    s.flush()
    top, _ = st.infer(s, fg.group_by_folder(s, str(tmp_path), ids))
    build.build_listens(s, top)
    md.add_metadata(s)
    return {l.label: l for l in s.scalars(select(Listen).where(Listen.merged_into_id.is_(None)))}


def _decide_all(s, listens):
    good = dict(
        title="exact_rare",
        author="all",
        runtime="far",
        sources="audible",
        _alias="delta title",
        _asin="B0DELTA0001",
        _reading="chosen",
    )
    apply.apply(
        s,
        {str(l.id): dict(clusters={DELTA["key"]: DELTA}, vectors={DELTA["key"]: [good]}) for l in listens.values()},
        params(),
    )


def test_numbered_chapter_folders_of_one_book_become_one_listen(s, tmp_path):
    listens = _chapter_folders(
        s, tmp_path, {"01 The Arrest": 150, "02 Sorry": 140, "03 The Arraignment": 130, "04 Yellow": 145}
    )  # 565 minutes, the recording's length
    assert len(listens) == 4
    _decide_all(s, listens)
    assert len(apply.merge_parts(s)) == 3
    live = s.scalars(select(Listen).where(Listen.merged_into_id.is_(None))).all()
    assert len(live) == 1
    parts = s.scalars(
        select(ListenPart)
        .where(ListenPart.listen_id == live[0].id, ListenPart.role == "input", ListenPart.unlinked_at.is_(None))
        .order_by(ListenPart.ordinal)
    ).all()
    assert [p.detail["minutes"] for p in parts] == [150, 140, 130, 145] and {p.detail["relation"] for p in parts} == {
        "part"
    }
    assert next(q for q in Q.load(s) if q.listen_id == live[0].id).minutes == [565.0]


def test_numbered_folders_that_are_not_a_whole_book_are_left_alone(s, tmp_path):
    listens = _chapter_folders(
        s, tmp_path, {"01 The Arrest": 150, "02 Sorry": 140, "03 The Arraignment": 130}
    )  # 420 minutes: not the recording
    _decide_all(s, listens)
    assert apply.merge_parts(s) == []
    s.rollback()


def test_numbered_folders_with_gaps_or_only_two_are_left_alone(s, tmp_path):
    listens = _chapter_folders(s, tmp_path, {"01 The Arrest": 280, "03 Sorry": 285})  # a gap, and two only
    _decide_all(s, listens)
    assert apply.merge_parts(s) == []


def test_a_set_found_by_both_groupings_is_joined_once(s, tmp_path, monkeypatch):
    from app.store import build as B

    monkeypatch.setattr(
        B, "part_key", lambda label: "deltatitle"
    )  # as if the folder names also gave one part key: the same listens are a keyed group and a chapter sequence
    listens = _chapter_folders(
        s, tmp_path, {"01 The Arrest": 150, "02 Sorry": 140, "03 The Arraignment": 130, "04 Yellow": 145}
    )
    _decide_all(s, listens)
    assert len(apply.merge_parts(s)) == 3
    assert len(s.scalars(select(Listen).where(Listen.merged_into_id.is_(None))).all()) == 1


def test_numbered_folders_crossing_a_decimal_boundary_are_joined(s, tmp_path):
    listens = _chapter_folders(s, tmp_path, {"08 The Arrest": 190, "09 Sorry": 190, "10 Yellow": 185})
    _decide_all(s, listens)
    assert len(apply.merge_parts(s)) == 2


def test_a_merged_listen_keeps_one_answer_not_one_per_absorbed_listen(s, tmp_path):
    by = _parts_listens(s, tmp_path, {"Books/01 Delta Title Part 1.m4b": 284, "Books/02 Delta Title Part 2.m4b": 281})
    _decide(s, by)
    apply.merge_parts(s)
    survivor = s.scalars(select(Listen).where(Listen.merged_into_id.is_(None))).one()
    matches = s.scalars(
        select(ListenPart).where(
            ListenPart.listen_id == survivor.id, ListenPart.role == "match", ListenPart.unlinked_at.is_(None)
        )
    ).all()
    assert (
        len(matches) == 1
    )  # the loser's accepted match stayed behind (it used to move over: two accepted matches for one work)


def test_discard_listen_retires_a_stray_file_without_leaving_it_in_any_listen(s, tmp_path):
    from app.store import repair

    by = _parts_listens(s, tmp_path, {"Books/Delta Title.m4b": 565, "Audible/Delta Title Extra.m4b": 42})
    a, stray = by[565], by[42]
    repair.discard_listen(s, stray.id, a.id, reason="a second copy of one chapter")
    live = s.scalars(select(Listen).where(Listen.merged_into_id.is_(None))).all()
    assert [l.id for l in live] == [a.id]
    inputs = s.scalars(
        select(ListenPart).where(
            ListenPart.listen_id == a.id, ListenPart.role == "input", ListenPart.unlinked_at.is_(None)
        )
    ).all()
    assert len(inputs) == 1 and inputs[0].detail["minutes"] == 565  # the stray's file is in no listen
    with pytest.raises(ValueError):
        repair.discard_listen(s, stray.id, a.id, reason="again")


def test_a_match_a_person_confirmed_survives_a_merge(s, tmp_path):
    by = _parts_listens(s, tmp_path, {"Books/Delta Title.m4b": 565, "Audible/Delta Title Again.m4b": 563})
    _decide(s, by)
    keep, absorbed = by[565], by[563]
    match = s.scalars(
        select(ListenPart).where(
            ListenPart.listen_id == absorbed.id, ListenPart.role == "match", ListenPart.unlinked_at.is_(None)
        )
    ).one()
    apply.decide(s, absorbed, match, "approve")  # a person's answer is an event on the part: `linked_by` stays "model"
    assert match.linked_by == "model"
    L.merge(s, keep, absorbed, actor_kind="user", reason="copies")
    assert s.get(ListenPart, match.id).listen_id == keep.id and s.get(ListenPart, match.id).unlinked_at is None
