"""Building listens from the structure grouping, and the listens API (Postgres)."""
import pytest

pytest.importorskip("omarchy_db")      # first: the standard CI test environment has neither omarchy-db nor SQLAlchemy
from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy import func, select

from app.store import api, build, filegroups as fg, metadata as md, structure as st
from app.store.models import Listen, ListenChoice, ListenEvent, ListenPart, PartFilegroup


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
    """Folders (names only): an exact second copy of one recording, a copy with an author in front of the name, two narrations of one title, the same title by another author, and shorter titles that merely start the same way."""
    minutes = {"Books/Alpha Title": 300, "Audible/Alpha Title": 303, "Audible/Jane Doe - Alpha Title": 302, "Spoken/John Roe - Alpha Title": 301, "Books/Beta Title (Fry)": 600, "Books/Beta Title (Jarvis)": 900, "Books/Alpha": 301, "Books/Alpha Other Title": 299}
    for folder in minutes:
        for i in range(1, 4):
            p = tmp_path / folder / f"{i:02d}.mp3"; p.parent.mkdir(parents=True, exist_ok=True); p.write_bytes(f"{folder}{i}".encode())
    ids = fg.scan_files(s, str(tmp_path))
    for path, fid in ids.items():
        rel = path[len(str(tmp_path)) + 1:].rsplit("/", 1)[0]; s.get(fg.PartFile, fid).duration_s = minutes[rel] * 60 / 3
    s.flush(); folders = fg.group_by_folder(s, str(tmp_path), ids); top, _ = st.infer(s, folders); return top


def test_copies_need_the_same_title_compatible_authors_and_the_same_runtime(s, copies):
    build.build_listens(s, copies); md.add_metadata(s)
    def live():
        author = lambda l: next((c.value for c in s.scalars(select(ListenChoice).where(ListenChoice.listen_id == l.id, ListenChoice.field == "author", ListenChoice.unchosen_at.is_(None)))), "")
        return [(l.label, author(l), l) for l in s.scalars(select(Listen).where(Listen.merged_into_id.is_(None)))]
    assert len(live()) == 8
    r = build.link_copies(s)
    assert r == {"listens_with_copies": 1, "copies_merged": 1}      # the two "Alpha Title" folders with no author (300 and 303 minutes) are copies; Jane Doe's and John Roe's differ in author from each other, and with two known authors an unknown one cannot be placed
    now = live(); assert len(now) == 7
    assert sorted(a for t, a, _ in now if t == "Alpha Title") == ["", "Jane Doe", "John Roe"]            # one merged, one by Jane Doe, one by John Roe
    survivor = next(l for t, a, l in now if t == "Alpha Title" and a == "")
    parts = s.scalars(select(ListenPart).where(ListenPart.listen_id == survivor.id, ListenPart.role == "input", ListenPart.unlinked_at.is_(None)).order_by(ListenPart.ordinal)).all()
    assert [p.ordinal for p in parts] == [1, 2] and len({p.filegroup_id for p in parts}) == 2             # two copies, ranked
    ev = s.scalars(select(ListenEvent).where(ListenEvent.listen_id == survivor.id, ListenEvent.action == "merge")).one()
    assert ev.actor_kind == "rule" and "copies of one recording" in ev.reason and "300" in ev.reason


def test_a_copy_with_an_unknown_author_may_join_one_known_author(s, tmp_path):
    for folder, mins in (("Books/Jane Doe - Gamma", 200), ("Audible/Gamma", 202)):
        for i in range(1, 4):
            p = tmp_path / folder / f"{i:02d}.mp3"; p.parent.mkdir(parents=True, exist_ok=True); p.write_bytes(f"{folder}{i}".encode())
    ids = fg.scan_files(s, str(tmp_path))
    for path, fid in ids.items(): s.get(fg.PartFile, fid).duration_s = (200 if "Jane" in path else 202) * 60 / 3
    s.flush(); top, _ = st.infer(s, fg.group_by_folder(s, str(tmp_path), ids)); build.build_listens(s, top); md.add_metadata(s)
    assert build.link_copies(s) == {"listens_with_copies": 1, "copies_merged": 1}


def test_api_shows_the_number_of_copies(s, copies):
    build.build_listens(s, copies); md.add_metadata(s); build.link_copies(s)
    app = FastAPI(); app.include_router(api.listens_router); app.dependency_overrides[api.get_session] = lambda: s; c = TestClient(app)
    body = c.get("/api/listens").json(); rows = body["rows"]
    assert body["total"] == len(rows) == 7 and sum(body["kinds"].values()) == 7                             # one row per live listen, however many copies it has
    assert len(c.get("/api/listens?limit=2&offset=6").json()["rows"]) == 1                                  # and paging counts listens, not input parts
    assert max(r["copies"] for r in rows) == 2 and sum(r["copies"] for r in rows) == 8 and all(r["files"] == 3 for r in rows)      # the first copy's facts, never doubled
