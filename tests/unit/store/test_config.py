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
        "CREATE TABLE credit (asin TEXT, region TEXT, role TEXT, name TEXT);"
        "INSERT INTO book VALUES ('B0A','us','T-us','', 'english', NULL, 60, '2008-01-01', 'P', NULL, '', '');"
        "INSERT INTO book VALUES ('B0A','uk','T-uk','Sub', 'english', 0, 60, '2008-01-01', 'P', '978', 'S', '1');"
        "INSERT INTO credit VALUES ('B0A','uk','Author','Mara Quill'), ('B0A','uk','Narrator','Ada Penn');"
    )
    db.commit()

    def connect():
        c = sqlite3.connect(path)
        c.row_factory = sqlite3.Row
        return c

    rows = C.audible_rows(["B0A", "B0MISSING"], connect)
    assert [(r["title"], r["region"], r["authors"], r["narrators"], r["abridged"]) for r in rows] == [
        ("T-uk", "uk", ["Mara Quill"], ["Ada Penn"], False)
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
