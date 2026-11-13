"""The cover job over the Postgres catalogue: which listens need a search, the search and its records, near-duplicate merging, the preferred cover, and the HTTP API. Sources are fakes; nothing touches the network."""

import hashlib
from datetime import timedelta
from types import SimpleNamespace

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from pictures import jpeg, png
from sqlalchemy import select

from app.covers import api as capi
from app.covers import fit, imaging
from app.covers import run as R
from app.covers import sources as S
from app.jobs import JobQueue
from app.store import api as store_api
from app.store import listens as L
from app.store.models import CoverCandidate, CoverImage, ListenChoice, ListenEvent, Work, WorkIdentifier


@pytest.fixture
def s(db_session, db_backend):
    if db_backend != "postgres":
        pytest.skip("needs Postgres")
    return db_session


@pytest.fixture(autouse=True)
def relearns(monkeypatch):
    """Choosing a cover refits the ranking in a background thread on its own session; here it only records that it was asked (the fit itself is tested through `R.learn`)."""
    calls = []
    monkeypatch.setattr(capi, "relearn", lambda: calls.append(1))
    return calls


@pytest.fixture(autouse=True)
def isolated_model(monkeypatch, tmp_path):
    monkeypatch.setattr(fit.settings, "data_dir", lambda: str(tmp_path))


@pytest.fixture(autouse=True)
def content_hash(monkeypatch):
    """Without a decoder the perceptual hash of a made-up picture is a hash of its bytes: different pictures are far apart; a test that wants near-duplicates sets its own."""
    monkeypatch.setattr(imaging, "dhash", lambda b: hashlib.sha256(b).hexdigest()[:16])


def make_listen(
    s, title="Glass Orchard", asins=("B0A",), isbns=("9780000000002",), accepted=True, kind="book", detail=None, ids=()
):
    listen = L.create_listen(s, kind)
    listen.label = title
    w = Work(kind="written" if kind == "book" else "radio_programme", title=title)
    s.add(w)
    s.flush()
    for a in asins:
        s.add(WorkIdentifier(work_id=w.id, scheme="asin", value=a))
    for i in isbns:
        s.add(WorkIdentifier(work_id=w.id, scheme="isbn", value=i))
    s.add(WorkIdentifier(work_id=w.id, scheme="work", value="/works/OL1W"))
    for scheme, value in ids:
        s.add(WorkIdentifier(work_id=w.id, scheme=scheme, value=value))
    s.flush()
    if accepted:
        L.link(
            s,
            listen,
            "match",
            work_id=w.id,
            state="accepted",
            score=0.99,
            detail={"authors": ["Mara Quill"], **(detail or {})},
            actor_kind="model",
        )
    return listen


def input_part(s, listen, minutes, ordinal=1):
    """An input part (a folder of files) whose detail carries the copy's runtime."""
    from app.store import filegroups as fg

    run = s.scalars(select(fg.PartFilegroup)).first()
    g = fg.PartFilegroup(
        run_id=run.run_id if run else fg.new_run(s, "group_folders").id,
        kind="folder",
        label=f"copy {listen.id}-{ordinal}",
    )
    s.add(g)
    s.flush()
    return L.link(s, listen, "input", filegroup_id=g.id, ordinal=ordinal, detail={"minutes": minutes})


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
    make_listen(s, "Other", kind="other")  # not a book or a programme: nothing to find a cover for
    radio = make_listen(s, "Radio", kind="radio_series")  # a programme with an accepted match is searched too
    assert R.pending(s) == [a.id, radio.id]
    assert R.pending(s, listen_ids=[a.id]) == [a.id]
    R.search(s, radio, run_sources=fake_sources())
    assert R.pending(s) == [a.id]
    assert R.pending(s, listen_ids=[999]) == []
    run = R.search(s, a, run_sources=fake_sources())
    assert run.sources == [] and R.pending(s, listen_ids=[a.id]) == []  # searched, nothing changed since
    assert R.pending(s, force=True) == [a.id, radio.id] and R.pending(s, listen_ids=[a.id], force=True) == [a.id]
    L.choose(s, a, "title", "A Better Title")  # a metadata choice is a change: search again
    later(s)
    assert R.pending(s, listen_ids=[a.id]) == [a.id]
    R.search(s, a, run_sources=fake_sources())
    R.choose(s, a, None)  # clearing a cover that was never chosen is a no-op
    assert R.pending(s, listen_ids=[a.id]) == []


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
    app.state.jobs = JobQueue(None)
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


def test_searches_go_on_the_job_queue_one_per_listen_and_run_as_jobs(s, monkeypatch, tmp_path):
    from app import db, settings
    from app.server import tasks

    monkeypatch.setattr(settings, "data_dir", lambda: str(tmp_path))
    a, b = make_listen(s, "Alpha"), make_listen(s, "Beta")
    s.commit()
    jobs = JobQueue(lambda domain, kind, options: tasks.execute(jobs, domain, kind, options))
    app = FastAPI()
    app.include_router(capi.router)
    app.state.jobs = jobs
    app.dependency_overrides[store_api.get_session] = lambda: s
    client = TestClient(app)
    d = client.post("/api/covers/run", json={}).json()
    assert d == dict(queued=2, listen_ids=[a.id, b.id])
    assert client.post("/api/covers/run", json={}).json()["queued"] == 2  # still waiting: the same jobs, not four
    assert [t["options"] for t in jobs.tasks("covers")] == [dict(listen_id=a.id), dict(listen_id=b.id)]
    assert client.get("/api/covers").json()["job"] == dict(running=True, queued=2, current="", error="", failed=[])
    c = db.connect()
    try:
        assert [r[0] for r in c.execute("SELECT resources FROM jobs ORDER BY created")] == [
            f'["listen:{a.id}"]',
            f'["listen:{b.id}"]',
        ]
    finally:
        c.close()
    searched = []
    monkeypatch.setattr(store_api, "_engine", lambda: s.get_bind())

    def fake(sess, listen, **k):
        searched.append(listen.id)
        return SimpleNamespace(id=1, sources=[dict(source="apple", status="none")])

    monkeypatch.setattr(R, "search", fake)
    monkeypatch.setattr(R, "distinct", lambda sess, run_id: [])
    assert jobs.run_one() and jobs.run_one() and not jobs.run_one()
    assert searched == [a.id, b.id]
    assert jobs.status("covers")["last"]["search"] == dict(
        listen_id=b.id, label="Beta", run_id=1, covers=0, sources=dict(apple="none")
    )
    assert client.get("/api/covers").json()["job"]["running"] is False
    assert (
        client.post("/api/covers/run", json=dict(listen_ids=[a.id])).json()["queued"] == 1
    )  # searched again on request
    jobs.submit("covers", "search", listen_id=9999)
    monkeypatch.undo()
    monkeypatch.setattr(store_api, "_engine", lambda: s.get_bind())
    monkeypatch.setattr(settings, "data_dir", lambda: str(tmp_path))
    while jobs.run_one():
        pass
    assert "no listen 9999" in client.get("/api/covers").json()["job"]["error"]


def test_a_second_source_offering_the_same_address_keeps_its_candidate_without_a_second_download(s, monkeypatch):
    a = make_listen(s)
    fetched = []

    def get(url, **k):
        fetched.append(url)
        return R.net.Reply(200, png(900, 900))

    monkeypatch.setattr(R.net, "get", get)
    same = "https://m.media-amazon.com/images/I/x.jpg"

    def one(name):
        return (name, lambda ctx: ([S.Item(name, "B0A", "", same)], S.record(name, [S.Item(name, "B0A")], [])))

    run = R.search(s, a, run_sources=[one("audible"), one("audnexus")])
    assert fetched == [same]
    assert {x["source"]: x["status"] for x in run.sources} == {"audible": "found", "audnexus": "found"}
    assert sorted(c.source for c in s.scalars(select(CoverCandidate))) == ["audible", "audnexus"]


def test_the_first_file_is_found_through_disc_folders(s, tmp_path):
    from app.store import filegroups as fg

    for rel in ("Book/CD2/02.mp3", "Book/CD1/01.mp3", "Book/CD1/02.mp3"):
        p = tmp_path / rel
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_bytes(rel.encode())
    ids = fg.scan_files(s, str(tmp_path))
    top = fg.group_by_folder(s, str(tmp_path), ids)
    book = s.scalars(select(fg.PartFilegroup).where(fg.PartFilegroup.label == "Book")).one()
    fid = R.first_file(s, book.id)
    assert fid == ids[str(tmp_path / "Book/CD1/01.mp3")]  # CD1 before CD2, track 1 first
    listen = make_listen(s)
    L.link(s, listen, "input", filegroup_id=top.id, ordinal=1, detail={"path": str(tmp_path / "Book")})
    ctx = R.context(s, listen)
    assert ctx.folder == str(tmp_path / "Book") and ctx.files == [str(tmp_path / "Book/CD1/01.mp3")]
    empty = fg.PartFilegroup(run_id=top.run_id, kind="folder", label="Empty")
    s.add(empty)
    s.flush()
    assert R.first_file(s, empty.id) is None


def test_rebuilding_listens_clears_their_cover_searches_and_keeps_the_pictures(s):
    from app.store import build

    a = make_listen(s)
    R.search(s, a, run_sources=fake_sources(("apple", "1", png(700, 700))))
    s.flush()
    build.clear(s)
    assert s.scalars(select(CoverCandidate)).all() == [] and len(s.scalars(select(CoverImage)).all()) == 1


def client_for(s):
    app = FastAPI()
    app.include_router(capi.router)
    app.state.jobs = JobQueue(None)
    app.dependency_overrides[store_api.get_session] = lambda: s
    return TestClient(app)


def test_radio_listens_are_searched_with_the_programme_ids_of_their_accepted_work(s):
    prog = make_listen(
        s, "The Glass Hour", asins=(), isbns=(), kind="radio_show", ids=(("bbc_pid", "b0aaaaaa"), ("wikidata", "Q7"))
    )
    make_listen(s, "Unidentified Show", kind="radio_series", accepted=False)
    make_listen(s, "Not a programme", kind="other")
    assert R.pending(s) == [prog.id]
    ctx = R.context(s, prog)
    assert (ctx.radio, ctx.bbc_pids, ctx.wikidata, ctx.title) == (True, ["b0aaaaaa"], ["Q7"], "The Glass Hour")
    assert R.context(s, make_listen(s, "A Book")).radio is False


def test_the_target_is_the_listen_its_work_and_its_closest_recording_with_the_listens_runtime(s):
    a = make_listen(
        s,
        "Glass Orchard",
        detail={
            "recording": "B0A",
            "recordings": [{"asin": "B0A", "minutes": 598}, {"asin": "B0B", "minutes": 800}, {"asin": "B0C"}],
        },
    )
    input_part(s, a, 600.0)
    input_part(s, a, 610.0, 2)
    t = R.target(s, a)
    assert (t.kind, t.title, t.minutes, t.recording, t.recordings) == (
        "book",
        "Glass Orchard",
        600.0,
        "B0A",
        {"B0A": 598.0, "B0B": 800.0},
    )
    radio = make_listen(s, "The Glass Hour", kind="play", asins=(), isbns=())
    assert R.target(s, radio).kind == "radio" and R.target(s, radio).minutes == 0.0


def test_the_page_lists_the_best_fit_first_with_its_reasons_and_each_sources_size(s):
    a = make_listen(s, detail={"recording": "B0A", "recordings": [{"asin": "B0A", "minutes": 600}]})
    input_part(s, a, 601.0)
    R.search(
        s,
        a,
        run_sources=fake_sources(
            ("openlibrary", "9780000000002", png(3000, 4500)),  # large, but a print edition's
            ("audible", "B0A", png(1200, 1200)),
        ),
    )
    s.commit()
    row = client_for(s).get("/api/covers").json()["rows"][0]
    assert row["kind"] == "book" and [c["width"] for c in row["covers"]] == [1200, 3000]
    best, other = row["covers"]
    assert best["score"] > other["score"] and best["share"] > 0.5 and abs(best["share"] + other["share"] - 1) < 0.01
    names = {g["name"] for g in best["signals"]}
    assert {"audiobook cover", "this recording", "recording runtime", "audiobook shape"} <= names
    assert {g["name"] for g in other["signals"]} >= {"print edition only", "resolution"}
    assert best["sources"][0]["width"] == 1200 and best["sources"][0]["bytes"] > 0


def test_accepting_the_suggestion_and_choosing_another_are_remembered_differently_and_refit_the_ranking(s, relearns):
    a = make_listen(s)
    R.search(
        s, a, run_sources=fake_sources(("audible", "B0A", png(600, 600)), ("openlibrary", "isbn", png(3000, 4500)))
    )
    s.commit()
    client = client_for(s)
    covers = client.get("/api/covers").json()["rows"][0]["covers"]
    assert [c["width"] for c in covers] == [600, 3000]
    client.post(f"/api/covers/{a.id}/choose", json=dict(image_id=covers[0]["image_id"], via="accept"))
    assert s.scalars(select(ListenChoice).where(ListenChoice.unchosen_at.is_(None))).one().value["via"] == "accept"
    client.post(f"/api/covers/{a.id}/choose", json=dict(image_id=covers[1]["image_id"]))
    assert s.scalars(select(ListenChoice).where(ListenChoice.unchosen_at.is_(None))).one().value["via"] == "pick"
    client.post(f"/api/covers/{a.id}/choose", json=dict(image_id=None))
    assert len(relearns) == 3  # a cleared choice refits too: its influence must leave the weights


def test_learning_from_chosen_covers_moves_the_weights_and_the_page_ranks_with_them(s):
    listens = [make_listen(s, f"Book {n}") for n in range(6)]
    for listen in listens:
        R.search(
            s,
            listen,
            run_sources=fake_sources(("audible", "B0A", png(600, 600)), ("openlibrary", "isbn", png(3000, 4500))),
        )
    s.commit()
    client = client_for(s)
    before = client.get("/api/covers").json()["rows"][0]["covers"]
    assert before[0]["width"] == 600
    for listen in listens:  # this person always takes the large print cover
        big = max(
            client.get("/api/covers", params=dict(q=listen.label)).json()["rows"][0]["covers"], key=lambda c: c["width"]
        )
        client.post(f"/api/covers/{listen.id}/choose", json=dict(image_id=big["image_id"]))
    s.commit()
    params = R.learn(s)
    assert (
        params.labels == dict(chosen=6, accepted=0)
        and params.mult["resolution"] > 1
        and params.mult["audiobook cover"] < 1
    )
    assert fit.load() == params
    after = client.get("/api/covers", params=dict(state="chosen")).json()["rows"][0]["covers"]
    share = lambda cs: next(c["share"] for c in cs if c["width"] == 3000)
    assert share(after) > share(before)
    assert any(g["learned"] for c in after for g in c["signals"])


def test_learning_skips_listens_with_nothing_to_compare_and_cleared_choices(s):
    only = make_listen(s, "Only One")
    R.search(s, only, run_sources=fake_sources(("audible", "B0A", png(601, 601))))
    two = make_listen(s, "Two")
    R.search(s, two, run_sources=fake_sources(("audible", "B0A", png(602, 602)), ("apple", "1", png(703, 703))))
    cleared = make_listen(s, "Cleared")
    R.search(s, cleared, run_sources=fake_sources(("audible", "B0A", png(604, 604)), ("apple", "1", png(705, 705))))
    by_width = {i.width: i for i in s.scalars(select(CoverImage))}
    R.choose(s, only, by_width[601], via="accept")  # one picture: nothing to choose between
    R.choose(s, two, by_width[602], via="accept")
    R.choose(s, cleared, by_width[604])
    R.choose(s, cleared, None)
    params = R.learn(s)
    assert params.labels == dict(chosen=0, accepted=1) and params.seen["audiobook cover"] == 1


def test_the_target_runtime_adds_up_numbered_parts_of_one_recording(s):
    a = make_listen(s, "Glass Orchard")
    for n, minutes in enumerate((200.0, 190.0, 210.0), 1):
        input_part(s, a, minutes, n).detail = {"minutes": minutes, "relation": "part"}
    s.flush()
    assert R.target(s, a).minutes == 600.0


def test_confirming_a_match_queues_its_cover_search_once(s):
    from app.linkage import api as linkage_api
    from app.store.models import ListenPart

    a = make_listen(s, "Alpha")
    s.commit()
    part = s.scalars(select(ListenPart).where(ListenPart.listen_id == a.id, ListenPart.role == "match")).one()
    app = FastAPI()
    app.include_router(linkage_api.router)
    app.state.jobs = jobs = JobQueue(None)
    app.dependency_overrides[store_api.get_session] = lambda: s
    client = TestClient(app)
    body = dict(listen_id=a.id, action="approve", part_id=part.id)
    assert client.post("/api/matches/decide", json=body).json()["ok"]
    assert [t["options"] for t in jobs.tasks("covers")] == [dict(listen_id=a.id)]
    client.post("/api/matches/decide", json=body)
    assert len(jobs.tasks("covers")) == 1  # the same job, not a second
