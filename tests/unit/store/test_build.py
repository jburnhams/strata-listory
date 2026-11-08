"""Building listens from the structure grouping, and the listens API (Postgres)."""
import pytest

pytest.importorskip("omarchy_db")      # first: the standard CI test environment has neither omarchy-db nor SQLAlchemy
from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy import func, select

from app.store import api, build, filegroups as fg, structure as st
from app.store.models import Listen, ListenEvent, ListenPart, PartFilegroup


@pytest.fixture
def s(db_session, db_backend):
    if db_backend != "postgres": pytest.skip("needs Postgres")
    return db_session


@pytest.fixture
def structure(s, tmp_path):
    for rel in ("A/Book One/CD1/01.mp3", "A/Book One/CD1/02.mp3", "A/Book One/CD2/01.mp3", "A/Book Two/01.mp3", "A/Book Two/02.mp3", "A/Book Two/03.mp3",
                "Radio/Show S01E01.mp3", "Radio/Show S01E02.mp3", "Radio/Show S01E03.mp3", "Mix/Bohemian.mp3", "Mix/Imagine.mp3", "Mix/Yesterday.mp3", "Mix/Hey Jude.mp3"):
        p = tmp_path / rel; p.parent.mkdir(parents=True, exist_ok=True); p.write_bytes(rel.encode())
    ids = fg.scan_files(s, str(tmp_path)); folders = fg.group_by_folder(s, str(tmp_path), ids); top, _ = st.infer(s, folders); return top


def test_listens_are_made_for_books_and_programmes_only_with_events(s, structure):
    counts = build.build_listens(s, structure)
    assert counts == {"book": 2, "radio_series": 1, "loose_files_skipped": 4}
    labels = {l.label: (l.kind, l.status) for l in s.scalars(select(Listen))}
    assert labels == {"Book One": ("book", "grouped"), "Book Two": ("book", "grouped"), "Radio": ("radio_series", "grouped")}
    book = s.scalars(select(Listen).where(Listen.label == "Book One")).one()
    part = s.scalars(select(ListenPart).where(ListenPart.listen_id == book.id)).one()
    group = s.get(PartFilegroup, part.filegroup_id)
    assert group.kind == "book" and part.role == "input" and part.detail["files"] == 3 and part.linked_by == "rule"      # one link to the book group, whose discs and tracks sit below it
    assert [e.action for e in s.scalars(select(ListenEvent).where(ListenEvent.listen_id == book.id).order_by(ListenEvent.id))] == ["create", "link", "status_change"]
    covered = sum(p.detail["files"] for p in s.scalars(select(ListenPart))); assert covered + counts["loose_files_skipped"] == 13      # every file is in exactly one listen or left over


def test_rebuild_is_needed_to_run_again_and_starts_from_nothing(s, structure):
    build.build_listens(s, structure)
    with pytest.raises(ValueError): build.build_listens(s, structure)
    build.build_listens(s, structure, rebuild=True)
    assert s.scalar(select(func.count()).select_from(Listen)) == 3 and s.scalar(select(func.count()).select_from(ListenEvent)) == 9


def test_api_lists_filters_and_shows_one_listen(s, structure):
    build.build_listens(s, structure)
    app = FastAPI(); app.include_router(api.listens_router); app.dependency_overrides[api.get_session] = lambda: s; c = TestClient(app)
    r = c.get("/api/listens").json(); assert r["total"] == 3 and r["kinds"] == {"book": 2, "radio_series": 1} and [x["label"] for x in r["rows"]] == ["Book One", "Book Two", "Radio"]
    assert [x["label"] for x in c.get("/api/listens?q=two").json()["rows"]] == ["Book Two"]
    assert [x["label"] for x in c.get("/api/listens?kind=radio_series").json()["rows"]] == ["Radio"]
    assert c.get("/api/listens?status=approved").json()["total"] == 0
    one = c.get(f"/api/listens/{r['rows'][0]['id']}").json()
    assert one["listen"]["files"] == 3 and one["listen"]["group_id"] and [e["action"] for e in one["events"]] == ["create", "link", "status_change"] and one["parts"][0]["role"] == "input"
    assert c.get("/api/listens/999999").status_code == 404


@pytest.fixture
def copies(s, tmp_path):
    """Two copies of one recording (one with an author prefix), a different narration of the same title, and a shorter title that merely starts the same way."""
    layout = {"Library/Alpha Title": 3, "Backup/Jane Doe - Alpha Title": 3, "Library/Beta Title (Fry)": 3, "Library/Beta Title (Jarvis)": 3, "Library/Alpha": 3, "Library/Alpha Other Title": 3}
    minutes = {"Library/Alpha Title": 300, "Backup/Jane Doe - Alpha Title": 303, "Library/Beta Title (Fry)": 600, "Library/Beta Title (Jarvis)": 900, "Library/Alpha": 301, "Library/Alpha Other Title": 299}
    for folder, n in layout.items():
        for i in range(1, n + 1):
            p = tmp_path / folder / f"{i:02d}.mp3"; p.parent.mkdir(parents=True, exist_ok=True); p.write_bytes(f"{folder}{i}".encode())
    ids = fg.scan_files(s, str(tmp_path))
    for path, fid in ids.items():
        rel = path[len(str(tmp_path)) + 1:].rsplit("/", 1)[0]; s.get(fg.PartFile, fid).duration_s = minutes[rel] * 60 / 3
    s.flush(); folders = fg.group_by_folder(s, str(tmp_path), ids); top, _ = st.infer(s, folders); return top


def test_copies_of_one_recording_are_linked_into_one_listen_but_other_narrations_and_other_titles_are_not(s, copies):
    build.build_listens(s, copies)
    r = build.link_copies(s)
    assert r == {"listens_with_copies": 1, "copies_merged": 1}
    live = {l.label: l for l in s.scalars(select(Listen).where(Listen.merged_into_id.is_(None)))}
    assert sorted(live) == ["Alpha", "Alpha Other Title", "Beta Title (Fry)", "Beta Title (Jarvis)", "Jane Doe - Alpha Title"] or sorted(live) == ["Alpha", "Alpha Other Title", "Alpha Title", "Beta Title (Fry)", "Beta Title (Jarvis)"]
    survivor = next(l for n, l in live.items() if "Alpha Title" in n)
    parts = s.scalars(select(ListenPart).where(ListenPart.listen_id == survivor.id, ListenPart.role == "input", ListenPart.unlinked_at.is_(None)).order_by(ListenPart.ordinal)).all()
    assert [p.ordinal for p in parts] == [1, 2] and len({p.filegroup_id for p in parts}) == 2             # two copies, ranked
    ev = s.scalars(select(ListenEvent).where(ListenEvent.listen_id == survivor.id, ListenEvent.action == "merge")).one()
    assert ev.actor_kind == "rule" and "copies of one recording" in ev.reason and "300" in ev.reason


def test_api_shows_the_number_of_copies(s, copies):
    build.build_listens(s, copies); build.link_copies(s)
    app = FastAPI(); app.include_router(api.listens_router); app.dependency_overrides[api.get_session] = lambda: s; c = TestClient(app)
    body = c.get("/api/listens").json(); rows = {r["label"]: r for r in body["rows"]}
    assert body["total"] == len(body["rows"]) == len(rows) == 5 and sum(body["kinds"].values()) == 5      # one row per live listen, however many copies it has
    assert c.get("/api/listens?limit=2&offset=4").json()["rows"].__len__() == 1       # and paging counts listens, not input parts
    assert max(r["copies"] for r in rows.values()) == 2 and sum(r["copies"] for r in rows.values()) == 6 and all(r["files"] == 3 for r in rows.values())      # the first copy's facts, never doubled
