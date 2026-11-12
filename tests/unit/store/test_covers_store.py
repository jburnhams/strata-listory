"""The cover job over the Postgres catalogue: which listens need a search, the search and its records, near-duplicate merging, the preferred cover, and the HTTP API. Sources are fakes; nothing touches the network."""

import hashlib
from datetime import timedelta

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from pictures import jpeg, png
from sqlalchemy import select

from app.covers import api as capi
from app.covers import imaging
from app.covers import run as R
from app.covers import sources as S
from app.store import api as store_api
from app.store import listens as L
from app.store.models import CoverCandidate, CoverImage, ListenEvent, Work, WorkIdentifier


@pytest.fixture
def s(db_session, db_backend):
    if db_backend != "postgres":
        pytest.skip("needs Postgres")
    return db_session


@pytest.fixture(autouse=True)
def content_hash(monkeypatch):
    """Without a decoder the perceptual hash of a made-up picture is a hash of its bytes: different pictures are far apart; a test that wants near-duplicates sets its own."""
    monkeypatch.setattr(imaging, "dhash", lambda b: hashlib.sha256(b).hexdigest()[:16])


def make_listen(s, title="Glass Orchard", asins=("B0A",), isbns=("9780000000002",), accepted=True, kind="book"):
    listen = L.create_listen(s, kind)
    listen.label = title
    w = Work(kind="written", title=title)
    s.add(w)
    s.flush()
    for a in asins:
        s.add(WorkIdentifier(work_id=w.id, scheme="asin", value=a))
    for i in isbns:
        s.add(WorkIdentifier(work_id=w.id, scheme="isbn", value=i))
    s.add(WorkIdentifier(work_id=w.id, scheme="work", value="/works/OL1W"))
    s.flush()
    if accepted:
        L.link(
            s,
            listen,
            "match",
            work_id=w.id,
            state="accepted",
            score=0.99,
            detail={"authors": ["Mara Quill"]},
            actor_kind="model",
        )
    return listen


def later(s, seconds=5):
    """Postgres stamps every event of a transaction with the transaction's start: move the newest event on, as a later transaction would."""
    ev = s.scalars(select(ListenEvent).order_by(ListenEvent.id.desc())).first()
    ev.at = ev.at + timedelta(seconds=seconds)
    s.flush()


def fake_sources(*pictures):
    """Sources that offer the given (source, ref, bytes-or-None) pictures: None means the download 404s."""

    def one(name, items):
        def fn(ctx):
            its = [S.Item(name, ref, "label " + ref, data=data) for ref, data in items]
            return its, S.record(name, its, [])

        return fn

    by = {}
    for name, ref, data in pictures:
        by.setdefault(name, []).append((ref, data))
    return [(n, one(n, v)) for n, v in by.items()]


def test_pending_is_accepted_book_listens_without_a_cover_or_a_search_since_the_last_change(s):
    a = make_listen(s, "A")
    make_listen(s, "B", accepted=False)
    make_listen(s, "Radio", kind="radio_series")
    assert R.pending(s) == [a.id]
    assert R.pending(s, listen_ids=[999]) == []
    run = R.search(s, a, run_sources=fake_sources())
    assert run.sources == [] and R.pending(s) == []  # searched, nothing changed since
    assert R.pending(s, force=True) == [a.id] and R.pending(s, listen_ids=[a.id], force=True) == [a.id]
    L.choose(s, a, "title", "A Better Title")  # a metadata choice is a change: search again
    later(s)
    assert R.pending(s) == [a.id]
    R.search(s, a, run_sources=fake_sources())
    R.choose(s, a, None)  # clearing a cover that was never chosen is a no-op
    assert R.pending(s) == []


def test_choosing_a_cover_does_not_make_the_listen_look_changed_and_takes_it_off_the_list(s):
    a = make_listen(s, "A")
    run = R.search(s, a, run_sources=fake_sources(("x", "1", png(500, 500))))
    img = s.scalars(select(CoverImage)).one()
    before = R.changed_at(s, a.id)
    R.choose(s, a, img)
    assert R.changed_at(s, a.id) == before and R.pending(s, force=True) == []
    assert run.listen_changed_at == before
    R.choose(s, a, None)
    assert R.changed_at(s, a.id) == before and R.pending(s) == []  # cleared: searched already and unchanged


def test_context_collects_the_identifiers_of_the_accepted_work(s):
    a = make_listen(s, "Glass Orchard", asins=("B0A", "B0B"), isbns=("9780000000002",))
    ctx = R.context(s, a)
    assert (ctx.title, ctx.author, ctx.asins, ctx.isbns, ctx.ol_works) == (
        "Glass Orchard",
        "Mara Quill",
        ["B0A", "B0B"],
        ["9780000000002"],
        ["/works/OL1W"],
    )


def test_search_downloads_dedupes_by_content_and_says_why_a_source_found_nothing(s, monkeypatch, tmp_path):
    a = make_listen(s)
    same = png(1000, 1000)
    srcs = fake_sources(
        ("audible", "B0A", same),
        ("audnexus", "B0A", same),
        ("apple", "9", jpeg(1400, 1400)),
        ("apple", "bad", b"junk"),
        ("google", "x", png(50, 50)),
    )
    run = R.search(
        s,
        a,
        run_sources=srcs
        + [
            ("openlibrary", lambda ctx: ([], S.record("openlibrary", [], ["error: down"]))),
            ("boom", lambda ctx: 1 / 0),
        ],
    )
    images = s.scalars(select(CoverImage)).all()
    assert sorted((i.width, i.ext) for i in images) == [
        (1000, ".png"),
        (1400, ".jpg"),
    ]  # the junk and the 50px picture are not covers; the same bytes twice are one image
    assert len(s.scalars(select(CoverCandidate)).all()) == 3  # both sources of the shared picture are kept
    by = {x["source"]: x for x in run.sources}
    assert (
        by["audible"]["status"] == "found"
        and by["apple"]["count"] == 1
        and "bad: not a usable picture" in by["apple"]["detail"]
    )
    assert by["google"]["status"] == "none" and "x: not a usable picture" in by["google"]["detail"]
    assert (
        by["openlibrary"]["status"] == "error"
        and by["boom"]["status"] == "error"
        and "ZeroDivisionError" in by["boom"]["detail"]
    )
    assert all(R.image_path(i) for i in images)
    groups = R.distinct(s, run.id)
    assert [g["image"].width for g in groups] == [1400, 1000]  # one entry per distinct cover, largest first
    assert sorted(x["source"] for g in groups for x in g["sources"]) == ["apple", "audible", "audnexus"]


def test_near_duplicates_of_one_cover_are_one_entry_with_the_largest_standing_for_it(s, monkeypatch):
    monkeypatch.setattr(imaging, "dhash", lambda b: "00000000000000f0")
    a = make_listen(s)
    run = R.search(
        s,
        a,
        run_sources=fake_sources(
            ("audible", "B0A", png(500, 500)), ("apple", "1", png(2400, 2400)), ("google", "2", png(600, 900))
        ),
    )
    groups = R.distinct(s, run.id)
    assert [(g["image"].width, len(g["sources"])) for g in groups] == [
        (2400, 2),
        (600, 1),
    ]  # a different shape is a different cover


def test_downloaded_addresses_are_fetched_once_and_failures_are_recorded(s, monkeypatch):
    a, b = make_listen(s, "A"), make_listen(s, "B")
    fetched = []

    def get(url, **k):
        fetched.append(url)
        return {
            "https://x/ok.png": R.net.Reply(200, png(900, 900)),
            "https://x/gone.png": R.net.Reply(404, error="nf"),
            "https://x/err.png": R.net.Reply(0, error="timed out"),
        }[url]

    monkeypatch.setattr(R.net, "get", get)
    srcs = [
        (
            "audible",
            lambda ctx: (
                [
                    S.Item("audible", "1", "", "https://x/ok.png"),
                    S.Item("audible", "2", "", "https://x/gone.png"),
                    S.Item("audible", "3", "", "https://x/err.png"),
                    S.Item("audible", "1", "", "https://x/ok.png"),
                ],
                S.record("audible", [S.Item("audible", "1")], []),
            ),
        )
    ]
    run = R.search(s, a, run_sources=srcs)
    assert fetched == ["https://x/ok.png", "https://x/gone.png", "https://x/err.png"]
    assert (
        run.sources[0]["count"] == 1
        and "2: no picture there" in run.sources[0]["detail"]
        and "3: timed out" in run.sources[0]["detail"]
    )
    R.search(s, b, run_sources=srcs[:1])
    assert fetched.count("https://x/ok.png") == 1  # the second listen reuses the stored picture for that address
    only_bad = [
        (
            "apple",
            lambda ctx: (
                [S.Item("apple", "9", "", "https://x/gone.png")],
                S.record("apple", [S.Item("apple", "9")], []),
            ),
        )
    ]
    run = R.search(s, a, run_sources=only_bad)
    assert run.sources[0]["status"] == "none" and "9: no picture there" in run.sources[0]["detail"]


def test_api_lists_searched_listens_with_their_covers_and_reasons_and_saves_the_choice(s, monkeypatch, tmp_path):
    a, b, c = make_listen(s, "Alpha"), make_listen(s, "Beta"), make_listen(s, "Gamma")
    R.search(s, a, run_sources=fake_sources(("audible", "B0A", png(800, 800)), ("apple", "1", jpeg(1500, 1500))))
    R.search(
        s,
        b,
        run_sources=[("audible", lambda ctx: ([], S.record("audible", [], [], skipped="no ASIN known for this book")))],
    )
    s.commit()
    app = FastAPI()
    app.include_router(capi.router)
    app.dependency_overrides[store_api.get_session] = lambda: s
    client = TestClient(app)
    d = client.get("/api/covers").json()
    assert (d["total"], d["counts"], d["waiting"]) == (
        2,
        dict(choose=1, chosen=0, none=1),
        1,
    )  # Gamma has not been searched
    by = {r["label"]: r for r in d["rows"]}
    assert [x["width"] for x in by["Alpha"]["covers"]] == [1500, 800] and by["Alpha"]["state"] == "choose"
    assert by["Beta"]["state"] == "none" and by["Beta"]["sources"][0]["detail"] == "no ASIN known for this book"
    big = by["Alpha"]["covers"][0]["image_id"]
    assert client.post(f"/api/covers/{a.id}/choose", json=dict(image_id=big)).json() == dict(ok=True, chosen=big)
    assert client.get("/api/covers", params=dict(state="chosen")).json()["rows"][0]["chosen"] == big
    assert client.get("/api/covers", params=dict(q="beta")).json()["total"] == 1
    assert client.get("/api/covers", params=dict(state="none", limit=1)).json()["rows"][0]["label"] == "Beta"
    assert client.post(f"/api/covers/{a.id}/choose", json=dict(image_id=None)).json()["chosen"] is None
    assert client.post("/api/covers/9999/choose", json=dict(image_id=big)).status_code == 404
    assert client.post(f"/api/covers/{a.id}/choose", json=dict(image_id=9999)).status_code == 404
    img = client.get(f"/api/covers/image/{big}")
    assert img.status_code == 200 and img.content.startswith(b"\xff\xd8\xff")
    assert client.get("/api/covers/image/9999").status_code == 404
    monkeypatch.setattr(capi.shutil, "which", lambda name: None)
    assert client.get(f"/api/covers/image/{big}", params=dict(w=100)).status_code == 200  # no ffmpeg: the full picture


def test_the_job_runs_in_the_background_and_refuses_a_second_start(s, monkeypatch):
    a = make_listen(s, "Alpha")
    s.commit()
    searched = []
    monkeypatch.setattr(capi, "_engine", lambda: s.get_bind())
    monkeypatch.setattr(R, "search", lambda sess, listen, **k: searched.append(listen.id))
    capi.STATE.update(running=False)
    capi._lock.acquire()
    capi.run_job([a.id], False, None)  # what the thread does: releases the lock when it ends
    assert searched == [a.id] and capi.STATE["done"] == 1 and capi.STATE["running"] is False
    monkeypatch.setattr(
        capi.threading, "Thread", lambda target, args, daemon: type("T", (), {"start": lambda self: target(*args)})()
    )
    app = FastAPI()
    app.include_router(capi.router)
    app.dependency_overrides[store_api.get_session] = lambda: s
    client = TestClient(app)
    assert client.post("/api/covers/run", json=dict(listen_ids=[a.id])).status_code == 202
    assert searched == [a.id, a.id]
    capi._lock.acquire()
    try:
        assert client.post("/api/covers/run", json={}).status_code == 409
    finally:
        capi._lock.release()
    monkeypatch.setattr(capi, "_engine", lambda: (_ for _ in ()).throw(RuntimeError("no database")))
    capi._lock.acquire()
    capi.run_job(None, False, None)
    assert "no database" in capi.STATE["error"]
