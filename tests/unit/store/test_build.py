"""Building listens from the structure grouping, and the listens API (Postgres)."""

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy import func, select

from app.store import api, build
from app.store import filegroups as fg
from app.store import metadata as md
from app.store import structure as st
from app.store.models import Listen, ListenChoice, ListenEvent, ListenPart, PartFilegroup


@pytest.fixture
def s(db_session, db_backend):
    if db_backend != "postgres":
        pytest.skip("needs Postgres")
    return db_session


@pytest.fixture
def structure(s, tmp_path):
    for rel in (
        "A/Book One/CD1/01.mp3",
        "A/Book One/CD1/02.mp3",
        "A/Book One/CD2/01.mp3",
        "A/Book Two/01.mp3",
        "A/Book Two/02.mp3",
        "A/Book Two/03.mp3",
        "Radio/Show S01E01.mp3",
        "Radio/Show S01E02.mp3",
        "Radio/Show S01E03.mp3",
        "Mix/Bohemian.mp3",
        "Mix/Imagine.mp3",
        "Mix/Yesterday.mp3",
        "Mix/Hey Jude.mp3",
    ):
        p = tmp_path / rel
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_bytes(rel.encode())
    ids = fg.scan_files(s, str(tmp_path))
    folders = fg.group_by_folder(s, str(tmp_path), ids)
    top, _ = st.infer(s, folders)
    return top


def test_listens_are_made_for_books_and_programmes_only_with_events(s, structure):
    counts = build.build_listens(s, structure)
    assert counts == {"book": 2, "radio_series": 1, "loose_files_skipped": 4}
    labels = {l.label: (l.kind, l.status) for l in s.scalars(select(Listen))}
    assert labels == {
        "Book One": ("book", "grouped"),
        "Book Two": ("book", "grouped"),
        "Radio": ("radio_series", "grouped"),
    }
    book = s.scalars(select(Listen).where(Listen.label == "Book One")).one()
    part = s.scalars(select(ListenPart).where(ListenPart.listen_id == book.id)).one()
    group = s.get(PartFilegroup, part.filegroup_id)
    assert (
        group.kind == "book" and part.role == "input" and part.detail["files"] == 3 and part.linked_by == "rule"
    )  # one link to the book group, whose discs and tracks sit below it
    assert [
        e.action
        for e in s.scalars(select(ListenEvent).where(ListenEvent.listen_id == book.id).order_by(ListenEvent.id))
    ] == ["create", "link", "status_change"]
    covered = sum(p.detail["files"] for p in s.scalars(select(ListenPart)))
    assert covered + counts["loose_files_skipped"] == 13  # every file is in exactly one listen or left over


def test_rebuild_is_needed_to_run_again_and_starts_from_nothing(s, structure):
    build.build_listens(s, structure)
    with pytest.raises(ValueError):
        build.build_listens(s, structure)
    build.build_listens(s, structure, rebuild=True)
    assert (
        s.scalar(select(func.count()).select_from(Listen)) == 3
        and s.scalar(select(func.count()).select_from(ListenEvent)) == 9
    )


def test_api_lists_filters_and_shows_one_listen(s, structure):
    build.build_listens(s, structure)
    app = FastAPI()
    app.include_router(api.listens_router)
    app.dependency_overrides[api.get_session] = lambda: s
    c = TestClient(app)
    r = c.get("/api/listens").json()
    assert (
        r["total"] == 3
        and r["kinds"] == {"book": 2, "radio_series": 1}
        and [x["label"] for x in r["rows"]] == ["Book One", "Book Two", "Radio"]
    )
    assert [x["label"] for x in c.get("/api/listens?q=two").json()["rows"]] == ["Book Two"]
    assert [x["label"] for x in c.get("/api/listens?kind=radio_series").json()["rows"]] == ["Radio"]
    assert c.get("/api/listens?status=approved").json()["total"] == 0
    one = c.get(f"/api/listens/{r['rows'][0]['id']}").json()
    assert (
        one["listen"]["files"] == 3
        and one["listen"]["group_id"]
        and [e["action"] for e in one["events"]] == ["create", "link", "status_change"]
        and one["parts"][0]["role"] == "input"
    )
    assert c.get("/api/listens/999999").status_code == 404


@pytest.fixture
def copies(s, tmp_path):
    """Folders (names only): an exact second copy of one recording, a copy with an author in front of the name, two narrations of one title, the same title by another author, and shorter titles that merely start the same way."""
    minutes = {
        "Books/Alpha Title": 300,
        "Audible/Alpha Title": 303,
        "Audible/Jane Doe - Alpha Title": 302,
        "Spoken/John Roe - Alpha Title": 301,
        "Books/Beta Title (Fry)": 600,
        "Books/Beta Title (Jarvis)": 900,
        "Books/Alpha": 301,
        "Books/Alpha Other Title": 299,
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
    folders = fg.group_by_folder(s, str(tmp_path), ids)
    top, _ = st.infer(s, folders)
    return top


def test_copies_need_the_same_title_compatible_authors_and_the_same_runtime(s, copies):
    build.build_listens(s, copies)
    md.add_metadata(s)

    def live():
        author = lambda l: next(
            (
                c.value
                for c in s.scalars(
                    select(ListenChoice).where(
                        ListenChoice.listen_id == l.id,
                        ListenChoice.field == "author",
                        ListenChoice.unchosen_at.is_(None),
                    )
                )
            ),
            "",
        )
        return [(l.label, author(l), l) for l in s.scalars(select(Listen).where(Listen.merged_into_id.is_(None)))]

    assert len(live()) == 8
    r = build.link_copies(s)
    assert (
        r == {"listens_with_copies": 1, "copies_merged": 1}
    )  # the two "Alpha Title" folders with no author (300 and 303 minutes) are copies; Jane Doe's and John Roe's differ in author from each other, and with two known authors an unknown one cannot be placed
    now = live()
    assert len(now) == 7
    assert sorted(a for t, a, _ in now if t == "Alpha Title") == [
        "",
        "Jane Doe",
        "John Roe",
    ]  # one merged, one by Jane Doe, one by John Roe
    survivor = next(l for t, a, l in now if t == "Alpha Title" and a == "")
    parts = s.scalars(
        select(ListenPart)
        .where(ListenPart.listen_id == survivor.id, ListenPart.role == "input", ListenPart.unlinked_at.is_(None))
        .order_by(ListenPart.ordinal)
    ).all()
    assert [p.ordinal for p in parts] == [1, 2] and len({p.filegroup_id for p in parts}) == 2  # two copies, ranked
    ev = s.scalars(select(ListenEvent).where(ListenEvent.listen_id == survivor.id, ListenEvent.action == "merge")).one()
    assert ev.actor_kind == "rule" and "copies of one recording" in ev.reason and "300" in ev.reason


def test_a_copy_with_an_unknown_author_may_join_one_known_author(s, tmp_path):
    for folder, mins in (("Books/Jane Doe - Gamma", 200), ("Audible/Gamma", 202)):
        for i in range(1, 4):
            p = tmp_path / folder / f"{i:02d}.mp3"
            p.parent.mkdir(parents=True, exist_ok=True)
            p.write_bytes(f"{folder}{i}".encode())
    ids = fg.scan_files(s, str(tmp_path))
    for path, fid in ids.items():
        s.get(fg.PartFile, fid).duration_s = (200 if "Jane" in path else 202) * 60 / 3
    s.flush()
    top, _ = st.infer(s, fg.group_by_folder(s, str(tmp_path), ids))
    build.build_listens(s, top)
    md.add_metadata(s)
    assert build.link_copies(s) == {"listens_with_copies": 1, "copies_merged": 1}


def test_api_shows_the_number_of_copies(s, copies):
    build.build_listens(s, copies)
    md.add_metadata(s)
    build.link_copies(s)
    app = FastAPI()
    app.include_router(api.listens_router)
    app.dependency_overrides[api.get_session] = lambda: s
    c = TestClient(app)
    body = c.get("/api/listens").json()
    rows = body["rows"]
    assert (
        body["total"] == len(rows) == 7 and sum(body["kinds"].values()) == 7
    )  # one row per live listen, however many copies it has
    assert len(c.get("/api/listens?limit=2&offset=6").json()["rows"]) == 1  # and paging counts listens, not input parts
    assert (
        max(r["copies"] for r in rows) == 2
        and sum(r["copies"] for r in rows) == 8
        and all(r["files"] == 3 for r in rows)
    )  # the first copy's facts, never doubled


def test_files_numbered_differently_are_parts_not_copies(s, tmp_path):
    """Two whole-book files of one book ('Delta Title 01/02', same album tag, 284 and 281 minutes) are halves of it, not two copies; a plain second copy still merges."""
    names = {"Books/Delta Title 01.m4b": 284, "Books/Delta Title 02.m4b": 281, "Audible/Delta Title.m4b": 283}
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
    assert build.link_copies(s) == {
        "listens_with_copies": 1,
        "copies_merged": 1,
    }  # the unnumbered copy joins one part, but the two parts stay apart
    live = s.scalars(select(Listen).where(Listen.merged_into_id.is_(None))).all()
    assert len(live) == 2


def test_format_words_are_not_part_numbers():
    assert (
        build.numbering("Delta Title mp3") == ()
        and build.numbering("Delta Title m4b") == ()
        and build.numbering("Delta Title Unabridged mp3 files") == ()
    )
    assert build.numbering("Delta Title 01") == ("1",) and build.numbering("Delta Title 02") == ("2",)


def test_copies_in_format_named_folders_still_merge(s, tmp_path):
    names = {"Books/Delta Title mp3": 284, "Audible/Delta Title m4b": 283}
    for folder in names:
        for i in range(1, 4):
            p = tmp_path / folder / f"{i:02d}.mp3"
            p.parent.mkdir(parents=True, exist_ok=True)
            p.write_bytes(f"{folder}{i}".encode())
    ids = fg.scan_files(s, str(tmp_path))
    for path, fid in ids.items():
        f = s.get(fg.PartFile, fid)
        f.duration_s = names[path[len(str(tmp_path)) + 1 :].rsplit("/", 1)[0]] * 60 / 3
        f.probe = {
            "format": {"tags": {"album": "Delta Title", "artist": "Pat Author"}},
            "streams": [{"codec_type": "audio", "tags": {}}],
        }
    s.flush()
    top, _ = st.infer(s, fg.group_by_folder(s, str(tmp_path), ids))
    build.build_listens(s, top)
    md.add_metadata(s)
    assert build.link_copies(s) == {"listens_with_copies": 1, "copies_merged": 1}


def test_split_input_parts_repairs_a_wrong_merge(s, tmp_path):
    from app.store import repair

    names = {"Books/Delta Title 01.m4b": 284, "Books/Delta Title 02.m4b": 281}
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
    a, b = s.scalars(select(Listen).order_by(Listen.id)).all()
    from app.store import listens as Ls

    Ls.merge(s, a, b, actor_kind="rule", actor_ref="copies", reason="wrongly merged as copies")
    inputs = s.scalars(
        select(ListenPart).where(ListenPart.listen_id == a.id, ListenPart.role == "input").order_by(ListenPart.ordinal)
    ).all()
    assert len(inputs) == 2
    with pytest.raises(ValueError):
        repair.split_input_parts(s, a.id, [inputs[0].id, inputs[1].id], reason="x")  # not all of them
    new = repair.split_input_parts(s, a.id, [inputs[1].id], reason="parts, not copies")
    assert new.id not in (a.id, b.id) and new.status == "grouped" and new.label == "Delta Title"
    left = s.scalars(
        select(ListenPart).where(
            ListenPart.listen_id == a.id, ListenPart.role == "input", ListenPart.unlinked_at.is_(None)
        )
    ).all()
    assert [p.id for p in left] == [inputs[0].id]
    assert any(e.action == "split" for e in s.scalars(select(ListenEvent).where(ListenEvent.listen_id == a.id)))


def test_restructure_listen_replaces_one_double_group_with_copies_and_keeps_decisions(s, tmp_path, monkeypatch):
    from app.store import listens as Ls
    from app.store import repair

    names = {"Books/Invented Book 06.mp3": 600, **{f"Books/Invented Book 06_{i}.mp3": 100 for i in range(1, 7)}}
    for rel in names:
        p = tmp_path / rel
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_bytes(rel.encode())
    ids = fg.scan_files(s, str(tmp_path))
    for path, fid in ids.items():
        f = s.get(fg.PartFile, fid)
        f.duration_s = names[path[len(str(tmp_path)) + 1 :]] * 60
        f.probe = {
            "format": {"tags": {"album": "Invented Book 06", "artist": "Pat Author"}},
            "streams": [{"codec_type": "audio", "tags": {}}],
        }
    s.flush()
    st.DURATIONS.clear()
    with monkeypatch.context() as m:
        m.setattr(
            st, "full_beside_its_parts", lambda files, tolerance=0.03: None
        )  # the rules as they were when the listen was built
        top, _ = st.infer(s, fg.group_by_folder(s, str(tmp_path), ids))
        build.build_listens(s, top)
    md.add_metadata(s)
    listen = s.scalars(select(Listen)).one()
    inputs = s.scalars(
        select(ListenPart).where(
            ListenPart.listen_id == listen.id, ListenPart.role == "input", ListenPart.unlinked_at.is_(None)
        )
    ).all()
    assert len(inputs) == 1 and inputs[0].detail["minutes"] == 1200  # the double runtime
    Ls.choose(s, listen, "author", "Chosen Person", actor_kind="user")
    groups = repair.restructure_listen(s, listen.id)
    assert len(groups) == 2
    inputs = s.scalars(
        select(ListenPart)
        .where(ListenPart.listen_id == listen.id, ListenPart.role == "input", ListenPart.unlinked_at.is_(None))
        .order_by(ListenPart.ordinal)
    ).all()
    assert [(p.ordinal, p.detail["minutes"], p.detail["files"]) for p in inputs] == [
        (1, 600, 6),
        (2, 600, 1),
    ]  # two copies of 600 minutes, ranked by build.preference (the bigger set first)
    assert {
        c.field: c.value
        for c in s.scalars(
            select(ListenChoice).where(ListenChoice.listen_id == listen.id, ListenChoice.unchosen_at.is_(None))
        )
    }["author"] == "Chosen Person"  # a person's choice survives
    assert s.scalar(select(func.count()).select_from(Listen)) == 1
    assert any(e.action == "unlink" for e in s.scalars(select(ListenEvent).where(ListenEvent.listen_id == listen.id)))
    with pytest.raises(ValueError):
        repair.restructure_listen(s, listen.id)  # two inputs now: nothing to do
