"""A listen's output configuration over the Postgres catalogue: candidates from tags, folder name, Audible recordings and the Open Library work, merged by value, with the person's pick."""

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy import select

from app.store import api as store_api
from app.store import config as C
from app.store import listens as L
from app.store import metadata as md
from app.store.models import ListenChoice, Work, WorkIdentifier


@pytest.fixture
def s(db_session, db_backend):
    if db_backend != "postgres":
        pytest.skip("needs Postgres")
    return db_session


def audible(asins):
    return [
        dict(
            asin=a,
            region="uk",
            title="The Glass Orchard",
            subtitle="A Novel",
            authors=["Mara Quill"],
            narrators=["Ada Penn"] if a == "B0A" else ["Sam Hart"],
            series="Orchard",
            series_position="2",
            runtime_minutes=600,
            language="english",
            release="2008-11-14",
            publisher="Audible Studios",
            isbn="9780000000002",
            abridged=False,
        )
        for a in asins
    ]


@pytest.fixture
def listen(s):
    l = L.create_listen(s, "book")
    l.label = "Glass Orchard"
    tags = {
        "files": 3,
        "tagged": 3,
        "album": {"value": "Glass Orchard", "n": 3, "share": 1.0},
        "artist": {"value": "M. Quill", "n": 2, "share": 0.67},
        "narratedby": {"value": "Ada Penn", "n": 3, "share": 1.0},
        "date": {"value": "2009-01-01", "n": 3, "share": 1.0},
    }
    for ordinal, (source, kind, payload) in enumerate(
        [
            ("embedded_tags", "tag_summary", tags),
            (
                "folder_name",
                "parsed_name",
                {
                    "title": "The Glass Orchard",
                    "author": "Mara Quill",
                    "year": 2008,
                    "folder": "Mara Quill - The Glass Orchard",
                },
            ),
        ],
        1,
    ):
        L.link(s, l, "record", record_id=md._record(s, source, kind, payload).id, ordinal=ordinal, actor_kind="rule")
    w = Work(kind="written", title="The Glass Orchard", year=2007)
    s.add(w)
    s.flush()
    for sc, v in (("asin", "B0A"), ("asin", "B0B"), ("isbn", "9780000000002"), ("work", "/works/OL1W")):
        s.add(WorkIdentifier(work_id=w.id, scheme=sc, value=v))
    s.flush()
    L.link(
        s,
        l,
        "match",
        work_id=w.id,
        state="accepted",
        score=0.99,
        detail={"authors": ["Mara Quill"], "year": 2007},
        actor_kind="model",
    )
    L.choose(s, l, "title", "Glass Orchard", actor_kind="rule")
    return l


def by_field(cfg):
    return {f["field"]: f for f in cfg["fields"]}


def test_every_source_offers_its_value_merged_when_equal_with_the_chosen_one_first(s, listen):
    cfg = C.config(s, listen, audible=audible)
    f = by_field(cfg)
    title = f["title"]
    assert title["value"] == "Glass Orchard" and title["chosen_by"] == "rule"
    assert [o["value"] for o in title["options"]] == [
        "Glass Orchard",
        "The Glass Orchard",
    ]  # the chosen one, then the value three sources agree on (ignoring case and "The")
    assert [o["chosen"] for o in title["options"]] == [True, False]
    the = title["options"][1]
    assert sorted(x["source"] for x in the["sources"]) == ["audible", "audible", "folder", "openlibrary"]
    assert {o["value"] for o in f["author"]["options"]} == {"M. Quill", "Mara Quill"}
    assert [o["value"] for o in f["narrator"]["options"]] == ["Ada Penn", "Sam Hart"] and f["narrator"]["options"][0][
        "sources"
    ][0]["note"] == "3 of 3 tagged files"
    assert {o["value"] for o in f["year"]["options"]} == {2009, 2008, 2007}
    assert f["series"]["options"][0]["value"] == "Orchard" and f["series_position"]["options"][0]["value"] == "2"
    assert [o["value"] for o in f["asin"]["options"]] == ["B0A", "B0B"] and f["isbn"]["options"][0]["sources"][0][
        "source"
    ] in ("openlibrary", "audible")
    assert (
        f["subtitle"]["value"] is None
        and cfg["recordings"][0]["asin"] == "B0A"
        and cfg["work"]["title"] == "The Glass Orchard"
    )


def test_a_pick_becomes_the_choice_a_typed_value_is_kept_and_empty_clears(s, listen):
    cfg = C.config(s, listen, audible=audible)
    C.choose_field(s, listen, "title", "The Glass Orchard", source="Audible UK")
    f = by_field(C.config(s, listen, audible=audible))["title"]
    assert (f["value"], f["chosen_by"]) == ("The Glass Orchard", "user") and f["options"][0]["chosen"]
    C.choose_field(s, listen, "narrator", "Someone Typed")
    n = by_field(C.config(s, listen, audible=audible))["narrator"]
    assert n["options"][0]["value"] == "Someone Typed" and n["options"][0]["sources"][0]["label"] == "Typed by hand"
    C.choose_field(s, listen, "year", "first published 1999")
    assert by_field(C.config(s, listen, audible=audible))["year"]["value"] == 1999
    with pytest.raises(ValueError, match="four digits"):
        C.choose_field(s, listen, "year", "soon")
    with pytest.raises(ValueError, match="unknown field"):
        C.choose_field(s, listen, "cover", "x")
    C.choose_field(s, listen, "narrator", "")
    assert by_field(C.config(s, listen, audible=audible))["narrator"]["value"] is None
    assert C.choose_field(s, listen, "series", "") is None  # clearing what was never chosen is a no-op
    assert len(cfg["fields"]) == len(C.FIELDS)


def test_a_listen_without_a_match_or_records_still_has_its_choices(s):
    l = L.create_listen(s, "book")
    l.label = "Bare"
    L.choose(s, l, "title", "Bare Title", actor_kind="rule")
    f = by_field(C.config(s, l, audible=audible))
    assert (
        f["title"]["options"][0]["sources"][0]["source"] == "rule"
        and f["author"]["options"] == []
        and C.config(s, l, audible=audible)["work"] is None
    )


def test_audible_rows_reads_the_index_by_asin(tmp_path):
    import sqlite3

    path = tmp_path / "index.sqlite"
    db = sqlite3.connect(path)
    db.executescript(
        "CREATE TABLE book (asin TEXT, region TEXT, title TEXT, subtitle TEXT, language TEXT, abridged INTEGER, runtime_min INTEGER, release TEXT, publisher TEXT, isbn TEXT, series TEXT, series_pos TEXT);"
        "CREATE TABLE credit (asin TEXT NOT NULL, region TEXT NOT NULL, role TEXT NOT NULL, person_key TEXT NOT NULL, name TEXT, PRIMARY KEY (asin, region, role, person_key)) WITHOUT ROWID;"  # as the real index builds it: no rowid to order by
        "INSERT INTO book VALUES ('B0A','us','T-us','', 'english', NULL, 60, '2008-01-01', 'P', NULL, '', '');"
        "INSERT INTO book VALUES ('B0A','uk','T-uk','Sub', 'english', 0, 60, '2008-01-01', 'P', '978', 'S', '1');"
        "INSERT INTO credit VALUES ('B0A','uk','Author','mara quill','Mara Quill'), ('B0A','uk','Narrator','ada penn','Ada Penn'), ('B0A','uk','Narrator','sam hart','Sam Hart');"
    )
    db.commit()

    def connect():
        c = sqlite3.connect(path)
        c.row_factory = sqlite3.Row
        return c

    rows = C.audible_rows(["B0A", "B0MISSING"], connect)
    assert [(r["title"], r["region"], r["authors"], r["narrators"], r["abridged"]) for r in rows] == [
        ("T-uk", "uk", ["Mara Quill"], ["Ada Penn", "Sam Hart"], False)
    ]
    assert C.audible_rows([], connect) == [] and C.audible_rows(["B0A"], lambda: None) == []


def test_api_returns_the_configuration_and_saves_a_pick(s, listen, monkeypatch):
    monkeypatch.setattr(C, "audible_rows", audible)
    s.commit()
    app = FastAPI()
    app.include_router(store_api.listens_router)
    app.dependency_overrides[store_api.get_session] = lambda: s
    client = TestClient(app)
    d = client.get(f"/api/listens/{listen.id}/config").json()
    assert d["label"] == "Glass Orchard" and d["inputs"] == [] and d["work"]["title"] == "The Glass Orchard"
    after = client.post(
        f"/api/listens/{listen.id}/config/title", json=dict(value="The Glass Orchard", source="Audible UK")
    ).json()
    assert by_field(after)["title"]["chosen_by"] == "user"
    assert client.post(f"/api/listens/{listen.id}/config/year", json=dict(value="soon")).status_code == 400
    assert client.post(f"/api/listens/{listen.id}/config/year", json=dict(value=2008)).status_code == 200
    assert client.get("/api/listens/9999/config").status_code == 404
    assert client.post("/api/listens/9999/config/title", json=dict(value="x")).status_code == 404
    assert (
        s.scalars(select(ListenChoice).where(ListenChoice.field == "year", ListenChoice.unchosen_at.is_(None)))
        .one()
        .value
        == 2008
    )


def test_sources_columns_and_library_endpoint(s, listen, monkeypatch):
    from app.linkage import apply
    from app.store.models import ListenPart

    monkeypatch.setattr(C, "audible_rows", audible)
    part1 = s.scalars(select(ListenPart).where(ListenPart.listen_id == listen.id, ListenPart.role == "match")).first()
    apply.decide(s, listen, part1, "approve")

    l2 = L.create_listen(s, "radio_series")
    l2.label = "Alpha Broadcast"
    w2 = Work(kind="radio_programme", title="Alpha Broadcast", year=1995)
    s.add(w2)
    s.flush()
    part2 = L.link(s, l2, "match", work_id=w2.id, state="candidate", actor_kind="model")
    apply.decide(s, l2, part2, "approve")
    L.choose(s, l2, "title", "Alpha Broadcast", actor_kind="rule")
    L.choose(s, l2, "author", "Alice", actor_kind="rule")
    L.choose(s, l2, "year", 1995, actor_kind="rule")
    L.choose(s, listen, "cover", dict(image_id=10, width=1000, height=1000), actor_kind="user")
    L.choose(s, l2, "cover", dict(image_id=11, width=800, height=800), actor_kind="user")

    # Confirmed match without a cover -> counted in without_cover, not in library rows
    l3 = L.create_listen(s, "book")
    l3.label = "No Cover Yet"
    part3 = L.link(s, l3, "match", work_id=w2.id, state="candidate", actor_kind="model")
    apply.decide(s, l3, part3, "approve")

    # Model-accepted match (not user-confirmed) even with a cover -> excluded from library
    l4 = L.create_listen(s, "book")
    l4.label = "Auto Accepted Only"
    L.link(s, l4, "match", work_id=w2.id, state="accepted", actor_kind="model")
    L.choose(s, l4, "cover", dict(image_id=12, width=800, height=800), actor_kind="user")
    s.commit()

    app = FastAPI()
    app.include_router(store_api.listens_router)
    app.dependency_overrides[store_api.get_session] = lambda: s
    client = TestClient(app)

    lib = client.get("/api/listens/library").json()
    assert lib["all_total"] == 2 and lib["total"] == 2 and lib["without_cover"] == 1
    assert [r["label"] for r in lib["rows"]] == ["Alpha Broadcast", "Glass Orchard"]
    go = lib["rows"][1]
    assert [src["id"] for src in go["sources"]] == ["original", "audible:B0A", "audible:B0B", "openlibrary"]
    assert go["sources"][0]["used"] is True and go["sources"][0]["used_fields"] == ["title"]
    assert go["sources"][1]["used"] is False

    # Clicking the Audible B0A column applies its values and highlights it
    aud_values = {k: v for k, v in go["sources"][1]["values"].items() if v is not None}
    updated = client.post(
        f"/api/listens/{listen.id}/config", json=dict(values=aud_values, source="Audible UK · B0A")
    ).json()
    by_src = {src["id"]: src for src in updated["sources"]}
    assert by_src["audible:B0A"]["used"] is True
    assert "title" in by_src["audible:B0A"]["used_fields"] and "narrator" in by_src["audible:B0A"]["used_fields"]
    assert by_src["openlibrary"]["used"] is True and "title" in by_src["openlibrary"]["used_fields"]

    assert client.post("/api/listens/9999/config", json=dict(values=dict(title="x"))).status_code == 404
    assert client.post(f"/api/listens/{listen.id}/config", json=dict(values=dict(year="bad"))).status_code == 400

    # Search, filters, sorting and pagination
    assert client.get("/api/listens/library?q=orchard").json()["total"] == 1
    assert client.get("/api/listens/library?kind=radio_series").json()["total"] == 1
    assert client.get("/api/listens/library?missing=narrator").json()["total"] == 1
    assert client.get("/api/listens/library?missing=edited").json()["total"] == 1
    assert client.get("/api/listens/library?missing=unedited").json()["total"] == 1
    assert [r["listen_id"] for r in client.get("/api/listens/library?sort=title-desc").json()["rows"]] == [
        listen.id,
        l2.id,
    ]
    assert [r["listen_id"] for r in client.get("/api/listens/library?sort=year-asc").json()["rows"]] == [
        l2.id,
        listen.id,
    ]
    assert [r["listen_id"] for r in client.get("/api/listens/library?sort=year-desc").json()["rows"]] == [
        listen.id,
        l2.id,
    ]
    assert [r["listen_id"] for r in client.get("/api/listens/library?sort=author").json()["rows"]] == [
        l2.id,
        listen.id,
    ]
    assert [r["listen_id"] for r in client.get("/api/listens/library?sort=recent").json()["rows"]] == [
        l2.id,
        listen.id,
    ]
    assert [r["listen_id"] for r in client.get("/api/listens/library?sort=oldest").json()["rows"]] == [
        listen.id,
        l2.id,
    ]
    monkeypatch.setattr(C, "listen_mtimes", lambda sess, ids=None: {listen.id: 2000.0, l2.id: 1000.0})
    assert [r["listen_id"] for r in client.get("/api/listens/library?sort=file-recent").json()["rows"]] == [
        listen.id,
        l2.id,
    ]
    assert [r["listen_id"] for r in client.get("/api/listens/library?sort=file-oldest").json()["rows"]] == [
        l2.id,
        listen.id,
    ]
    assert len(client.get("/api/listens/library?limit=1&offset=1").json()["rows"]) == 1


def test_export_listen_and_audio_streaming(s, listen, tmp_path, monkeypatch):
    import json
    import os

    from app import settings
    from app.covers import run as covers_run
    from app.process import plan as P
    from app.process import run as R
    from app.store.models import (
        CoverImage,
        PartFile,
        PartFilegroup,
        PartFilegroupMember,
        PartFileLocation,
        Run,
    )

    monkeypatch.setattr(C, "audible_rows", audible)
    monkeypatch.setattr(settings, "final_dir", lambda: str(tmp_path / "final"))

    src_dir = tmp_path / "src"
    src_dir.mkdir()
    src_mp3 = src_dir / "01.mp3"
    src_mp3.write_bytes(b"ID3-fake-mp3-input")
    cov_file = tmp_path / "cover.jpg"
    cov_file.write_bytes(b"fake-jpg")

    img = CoverImage(
        sha256="f" * 64,
        ext=".jpg",
        width=2000,
        height=2000,
        size=8,
        dhash="0" * 16,
    )
    s.add(img)
    s.flush()
    monkeypatch.setattr(covers_run, "image_path", lambda i: str(cov_file))

    run_row = Run(id="a" * 32, kind="group_folders", outcome="done")
    s.add(run_row)
    pf = PartFile(
        sha256=b"x" * 32, size=18, duration_s=120.0, codec="mp3", channels=1, sample_rate=44100, bit_rate=64000
    )
    s.add(pf)
    s.flush()
    s.add(PartFileLocation(file_id=pf.id, path=str(src_mp3), size=18, mtime_ns=1))
    child_fg = PartFilegroup(
        run_id=run_row.id, kind="disc", label="Disc 1", path=str(src_dir), file_count=1, total_bytes=18
    )
    s.add(child_fg)
    s.flush()
    s.add(PartFilegroupMember(group_id=child_fg.id, ordinal=1, file_id=pf.id))
    fg = PartFilegroup(
        run_id=run_row.id, kind="book", label="Glass Orchard", path=str(src_dir), file_count=1, total_bytes=18
    )
    s.add(fg)
    s.flush()
    s.add(PartFilegroupMember(group_id=fg.id, ordinal=1, child_group_id=child_fg.id))
    L.link(
        s,
        listen,
        "input",
        filegroup_id=fg.id,
        ordinal=1,
        actor_kind="rule",
        detail=dict(path=str(src_dir), files=1, minutes=2, relation="copy"),
    )
    L.choose(s, listen, "author", "Mara Quill", actor_kind="user")
    L.choose(s, listen, "year", 2005, actor_kind="user")
    L.choose(s, listen, "series_position", "not-a-float", actor_kind="user")
    L.choose(s, listen, "cover", dict(image_id=img.id, source="audible", width=2000, height=2000), actor_kind="user")
    s.commit()

    assert C.listen_mtimes(s, []) == {}
    assert C.listen_mtimes(s, [listen.id])[listen.id] == pytest.approx(1e-9)

    def fake_make_plan(book, final_dir, audible_arg, extras, records, *, input_paths=None, **kw):
        assert book["cover_path"] == str(cov_file)
        assert book["series_position"] is None
        out_dir = P.out_dir_for(book, final_dir, book.get("year"))
        return dict(
            book_id=book["id"],
            title=book["title"],
            author=book["author"],
            out_dir=out_dir,
            year=book.get("year"),
            chapters=[
                dict(
                    file="001 - Mara Quill - Glass Orchard.mp3",
                    title="Chapter 1",
                    start=0.0,
                    end=120.0,
                    duration=120.0,
                    mode="copy",
                    tier="copy",
                    kbps=64,
                    segments=[(input_paths[0], 0.0, None)],
                )
            ],
            chapter_source="file boundaries",
            downmix="average",
            duration=120.0,
            loudness=dict(
                I=-18.0, TP=-2.0, LRA=4.0, apply_db=0.0, gain_steps=0, decision="within tolerance", reencoded=False
            ),
            warnings=[],
            in_bytes=18,
            est_bytes=18,
            in_files=1,
            channels=1,
        )

    def fake_execute(book, plan, force=False):
        out_dir = plan["out_dir"]
        os.makedirs(out_dir, exist_ok=True)
        ch_path = os.path.join(out_dir, plan["chapters"][0]["file"])
        with open(ch_path, "wb") as f:
            f.write(b"ID3-exported-chapter")
        summary = dict(
            status="done",
            title=plan["title"],
            author=plan["author"],
            out_dir=out_dir,
            chapters=len(plan["chapters"]),
            out_bytes=20,
            out_seconds=120.0,
            loudness_before=-18.0,
            loudness_after=-18.0,
            gain_db=0.0,
            finished="2026-11-15T16:50:00Z",
            enhancement=plan.get("enhancement"),
        )
        with open(os.path.join(out_dir, "metadata.json"), "w", encoding="utf8") as f:
            json.dump(dict(book=book, result=summary, chapters=plan["chapters"]), f)
        return summary

    monkeypatch.setattr(P, "make_plan", fake_make_plan)
    monkeypatch.setattr(R, "execute", fake_execute)

    app = FastAPI()
    app.include_router(store_api.listens_router)
    app.dependency_overrides[store_api.get_session] = lambda: s
    client = TestClient(app)

    assert client.get(f"/api/listens/{listen.id}/output").status_code == 404
    assert client.get("/api/listens/9999/output").status_code == 404
    assert client.get(f"/api/listens/{listen.id}/audio/0").status_code == 404
    assert client.get("/api/listens/9999/audio/0").status_code == 404

    exported = client.post(f"/api/listens/{listen.id}/export", json=dict(noise_reduction=True)).json()
    assert exported["output"]["status"] == "done"
    assert exported["output"]["noise_reduction"] is True
    assert len(exported["output"]["chapters"]) == 1
    assert exported["output"]["chapters"][0]["title"] == "Chapter 1"

    out_info = client.get(f"/api/listens/{listen.id}/output").json()
    assert out_info["status"] == "done" and out_info["noise_reduction"] is True

    audio_resp = client.get(f"/api/listens/{listen.id}/audio/0")
    assert audio_resp.status_code == 200 and audio_resp.content == b"ID3-exported-chapter"
    assert client.get(f"/api/listens/{listen.id}/audio/9").status_code == 404
    assert client.post("/api/listens/9999/export", json=dict(noise_reduction=False)).status_code == 404

    class FakeJobs:
        threads = [object()]
        submitted = []

        def submit(self, domain, kind, **kw):
            self.submitted.append((domain, kind, kw))

        def tasks(self, domain=None):
            return [
                dict(
                    id=42,
                    domain="listens",
                    kind="export",
                    state="running",
                    options=dict(listen_id=listen.id, noise_reduction=False),
                    progress=dict(step="encode"),
                )
            ]

        def failures(self, domain=None):
            return []

    app.state.jobs = FakeJobs()
    queued_resp = client.post(f"/api/listens/{listen.id}/export", json=dict(noise_reduction=False))
    assert queued_resp.status_code == 202
    assert queued_resp.json()["output"]["status"] == "running"
    assert queued_resp.json()["output"]["job"]["id"] == 42

    class FailedJobs:
        def tasks(self, domain=None):
            return []

        def failures(self, domain=None):
            return [
                dict(
                    id=43,
                    domain="listens",
                    kind="export",
                    options=json.dumps(dict(listen_id=listen.id, noise_reduction=True)),
                    error="ffmpeg failed",
                )
            ]

    app.state.jobs = FailedJobs()
    out_with_err = C.output_status(s, listen, jobs=app.state.jobs)
    assert out_with_err["job"]["state"] == "failed" and out_with_err["job"]["error"] == "ffmpeg failed"

    monkeypatch.setattr(store_api, "_engine", lambda: s.get_bind())
    assert C.export_job(listen.id, noise_reduction=False)["status"] == "done"
    with pytest.raises(ValueError, match="no listen"):
        C.export_job(99999)

    # A second listen with the same author/title/year must not claim or overwrite the first listen's output.
    inbox2 = tmp_path / "inbox" / "Mara Quill - Glass Orchard (Narrator Two)"
    inbox2.mkdir(parents=True)
    track2 = inbox2 / "01.mp3"
    track2.write_bytes(b"ID3-second-recording")
    pf2 = PartFile(
        sha256=b"y" * 32, size=20, duration_s=120.0, codec="mp3", channels=1, sample_rate=44100, bit_rate=64000
    )
    s.add(pf2)
    s.flush()
    s.add(PartFileLocation(file_id=pf2.id, path=str(track2), size=20, mtime_ns=2))
    listen2 = L.create_listen(s, "book")
    listen2.label = "Glass Orchard"
    L.link(
        s,
        listen2,
        "input",
        file_id=pf2.id,
        ordinal=1,
        actor_kind="rule",
        detail=dict(path=str(inbox2), files=1, minutes=2, relation="copy"),
    )
    C.choose_field(s, listen2, "title", "Glass Orchard")
    C.choose_field(s, listen2, "author", "Mara Quill")
    C.choose_field(s, listen2, "year", 2005)
    C.choose_field(s, listen2, "narrator", "Second Narrator")
    L.choose(s, listen2, "cover", dict(image_id=img.id, source="audible", width=2000, height=2000), actor_kind="user")
    s.commit()

    assert C.output_status(s, listen2) is None
    res2 = C.export_listen(s, listen2)
    s.commit()
    out1 = C.output_status(s, listen)
    out2 = C.output_status(s, listen2)
    assert out1 is not None and out2 is not None
    assert res2["out_dir"] != out1["out_dir"]
    assert "Second Narrator" in res2["out_dir"]
    assert out1["out_dir"].endswith("[2005] Glass Orchard")
    assert out2["out_dir"] == res2["out_dir"]


def test_input_paths_rejects_incomplete_tracked_files_and_parts(s, tmp_path):
    from app.store.models import (
        PartFile,
        PartFilegroup,
        PartFilegroupMember,
        PartFileLocation,
        Run,
    )

    inbox = tmp_path / "inbox" / "Incomplete Book"
    inbox.mkdir(parents=True)
    present_track = inbox / "01.mp3"
    present_track.write_bytes(b"ID3-present")
    missing_track = inbox / "02.mp3"

    run_row = Run(id="b" * 32, kind="group_folders", outcome="done")
    s.add(run_row)
    f1 = PartFile(
        sha256=b"a" * 32, size=11, duration_s=60.0, codec="mp3", channels=1, sample_rate=44100, bit_rate=64000
    )
    f2 = PartFile(
        sha256=b"b" * 32, size=11, duration_s=60.0, codec="mp3", channels=1, sample_rate=44100, bit_rate=64000
    )
    s.add_all([f1, f2])
    s.flush()
    s.add_all(
        [
            PartFileLocation(file_id=f1.id, path=str(present_track), size=11, mtime_ns=1_000_000_000),
            PartFileLocation(file_id=f2.id, path=str(missing_track), size=11, mtime_ns=1_000_000_000),
        ]
    )
    fg = PartFilegroup(
        run_id=run_row.id, kind="book", label="Incomplete Book", path=str(inbox), file_count=2, total_bytes=22
    )
    s.add(fg)
    s.flush()
    s.add_all(
        [
            PartFilegroupMember(group_id=fg.id, ordinal=1, file_id=f1.id),
            PartFilegroupMember(group_id=fg.id, ordinal=2, file_id=f2.id),
        ]
    )
    listen = L.create_listen(s, "book")
    listen.label = "Incomplete Book"
    L.link(
        s,
        listen,
        "input",
        filegroup_id=fg.id,
        ordinal=1,
        actor_kind="rule",
        detail=dict(path=str(inbox), files=2, minutes=2, relation="copy"),
    )
    C.choose_field(s, listen, "title", "Incomplete Book")
    C.choose_field(s, listen, "author", "Author")
    s.flush()

    _, paths = C.input_paths(s, listen)
    assert paths == []
    with pytest.raises(ValueError, match="no source audio files"):
        C.export_listen(s, listen, final_dir=str(tmp_path / "final"))

    # Multipart listen where part 1 exists on disk and part 2 is missing must also be rejected.
    mp_listen = L.create_listen(s, "book")
    mp_listen.label = "Multipart Book"
    L.link(
        s,
        mp_listen,
        "input",
        file_id=f1.id,
        ordinal=1,
        actor_kind="rule",
        detail=dict(path=str(inbox), relation="part"),
    )
    L.link(
        s,
        mp_listen,
        "input",
        file_id=f2.id,
        ordinal=2,
        actor_kind="rule",
        detail=dict(path=str(inbox), relation="part"),
    )
    s.flush()
    assert C.input_paths(s, mp_listen)[1] == []
