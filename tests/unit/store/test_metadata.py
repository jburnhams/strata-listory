"""Choosing a listen's metadata from tag records and folder names."""
import pytest

pytest.importorskip("omarchy_db")      # first: the standard CI test environment has neither omarchy-db nor SQLAlchemy
from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy import select

from app.store import api, build, filegroups as fg, metadata as md, structure as st
from app.store.models import Listen, ListenChoice, ListenEvent, ListenPart, PartFile, PartRecord


def probe(**tags): return {"format": {"tags": tags}, "streams": [{"codec_type": "audio", "tags": {}}]}


def test_tag_summary_takes_the_most_common_value_and_says_how_many_agree():
    p = [probe(album="Red Dragon", artist="Thomas Harris", date="1981", track=str(i)) for i in range(1, 5)] + [probe(album="Other", artist="Thomas Harris"), None]
    t = md.tag_summary(p)
    assert t["files"] == 6 and t["tagged"] == 5 and t["album"] == {"value": "Red Dragon", "n": 4, "share": 0.8} and t["artist"]["share"] == 1.0 and "narratedby" not in t


def test_choices_prefer_agreeing_tags_then_the_folder_name():
    tags = md.tag_summary([probe(album="Red Dragon", artist="Thomas Harris/Thomas Harris", date="1981-06-01", narratedby="Steven Weber", audible_asin="B0001") for _ in range(4)])
    got = md.choose_fields(tags, {"title": "red dragon cd", "author": "Folder Author", "year": 1999})
    assert got == {"title": ("Red Dragon", "tags"), "author": ("Thomas Harris", "tags"), "narrator": ("Steven Weber", "tags"), "year": (1981, "tags"), "asin": ("B0001", "tags")}
    weak = md.tag_summary([probe(album="Track 01", artist="Unknown Artist")] * 3)                          # generic tags are not trusted
    assert md.choose_fields(weak, {"title": "Great Expectations", "author": "Charles Dickens"}) == {"title": ("Great Expectations", "folder_name"), "author": ("Charles Dickens", "folder_name")}
    assert md.choose_fields(md.tag_summary([None]), {}) == {}


def test_a_composer_is_a_narrator_only_when_the_files_carry_an_audible_asin():
    assert md.choose_fields(md.tag_summary([probe(composer="Some Narrator")] * 3), {}) == {}
    assert md.choose_fields(md.tag_summary([probe(composer="Some Narrator", audible_asin="B1")] * 3), {})["narrator"] == ("Some Narrator", "tags")


@pytest.fixture
def s(db_session, db_backend):
    if db_backend != "postgres": pytest.skip("needs Postgres")
    return db_session


def test_metadata_is_added_once_with_records_choices_and_events(s, tmp_path):
    for rel in ("Thomas Harris/Red Dragon/01.mp3", "Thomas Harris/Red Dragon/02.mp3", "Thomas Harris/Red Dragon/03.mp3", "Anon/Some Book by Jane Doe/01.mp3", "Anon/Some Book by Jane Doe/02.mp3", "Anon/Some Book by Jane Doe/03.mp3"):
        p = tmp_path / rel; p.parent.mkdir(parents=True, exist_ok=True); p.write_bytes(rel.encode())
    ids = fg.scan_files(s, str(tmp_path))
    for path, fid in ids.items():
        if "Red Dragon" in path: s.get(PartFile, fid).probe = probe(album="Red Dragon", artist="Thomas Harris", date="1981")
    s.flush(); folders = fg.group_by_folder(s, str(tmp_path), ids); top, _ = st.infer(s, folders); build.build_listens(s, top)
    n = md.add_metadata(s)
    assert n["listens"] == 2 and n["from_tags"] == 3 and n["from_folder"] >= 2
    red = s.scalars(select(Listen).where(Listen.label == "Red Dragon")).one()
    chosen = {c.field: c.value for c in s.scalars(select(ListenChoice).where(ListenChoice.listen_id == red.id, ListenChoice.unchosen_at.is_(None)))}
    assert chosen == {"title": "Red Dragon", "author": "Thomas Harris", "year": 1981}
    kinds = [r.source for r in s.scalars(select(PartRecord).join(ListenPart, ListenPart.record_id == PartRecord.id).where(ListenPart.listen_id == red.id).order_by(ListenPart.ordinal))]
    assert kinds == ["embedded_tags", "folder_name"]
    assert [e.action for e in s.scalars(select(ListenEvent).where(ListenEvent.listen_id == red.id).order_by(ListenEvent.id))].count("choose") == 3
    other = s.scalars(select(Listen).where(Listen.label == "Some Book")).one()          # no tags: title and author come from the folder name
    assert {c.field: c.value for c in s.scalars(select(ListenChoice).where(ListenChoice.listen_id == other.id))} == {"title": "Some Book", "author": "Jane Doe"}
    assert md.add_metadata(s) == {"listens": 0, "choices": 0, "from_tags": 0, "from_folder": 0}      # nothing new on a rerun
    app = FastAPI(); app.include_router(api.listens_router); app.dependency_overrides[api.get_session] = lambda: s; c = TestClient(app)
    rows = {r["label"]: r for r in c.get("/api/listens").json()["rows"]}
    assert rows["Red Dragon"]["author"] == "Thomas Harris" and rows["Red Dragon"]["year"] == 1981
    assert [r["label"] for r in c.get("/api/listens?q=harris").json()["rows"]] == ["Red Dragon"]        # the author is searchable
    assert [r["label"] for r in c.get("/api/listens?q=jane doe").json()["rows"]] == ["Some Book"]
