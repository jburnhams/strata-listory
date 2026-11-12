"""Scanning and folder grouping into the listory schema (Postgres)."""

import os

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError

from app.store import api
from app.store import filegroups as fg
from app.store import listens as L
from app.store.models import PartFile, PartFilegroup, PartFilegroupMember, PartFileLocation


@pytest.fixture
def s(db_session, db_backend):
    if db_backend != "postgres":
        pytest.skip("needs Postgres")
    return db_session


@pytest.fixture
def lib(tmp_path):
    def w(rel, data):
        p = tmp_path / rel
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_bytes(data)

    w("Author/Book One/CD1/Track 2.mp3", b"b")
    w("Author/Book One/CD1/Track 10.mp3", b"c")
    w("Author/Book One/CD2/Track 1.mp3", b"d")
    w("Author/Single.m4b", b"e")
    w("Author/cover.jpg", b"x")
    w("Radio/Show.mp3", b"b")  # same bytes as Track 2: one PartFile, two locations
    (tmp_path / "Empty/Nothing").mkdir(parents=True)
    return str(tmp_path)


def test_scan_hashes_once_and_dedupes_content(s, lib):
    ids = fg.scan_files(s, lib)
    assert len(ids) == 5 and s.query(PartFile).count() == 4 and s.query(PartFileLocation).count() == 5
    assert ids[os.path.join(lib, "Radio/Show.mp3")] == ids[os.path.join(lib, "Author/Book One/CD1/Track 2.mp3")]


def test_rescan_only_rehashes_changed_files_and_marks_gone(s, lib, monkeypatch):
    fg.scan_files(s, lib)
    calls = []
    real = fg.sha256_of
    monkeypatch.setattr(fg, "sha256_of", lambda p: calls.append(p) or real(p))
    os.remove(os.path.join(lib, "Author/Single.m4b"))
    p = os.path.join(lib, "Radio/Show.mp3")
    open(p, "wb").write(b"changed!")
    ids = fg.scan_files(s, lib)
    assert calls == [p] and len(ids) == 4
    gone = s.scalars(select(PartFileLocation.path).where(PartFileLocation.gone_at.is_not(None))).all()
    assert gone == [os.path.join(lib, "Author/Single.m4b")]


def test_group_by_folder_builds_tree_in_natural_order(s, lib):
    ids = fg.scan_files(s, lib)
    top = fg.group_by_folder(s, lib, ids)
    assert (top.kind, top.file_count, top.group_count) == (
        "root",
        5,
        5,
    )  # Author, Book One, CD1, CD2, Radio (Empty has no audio)
    members = s.scalars(
        select(PartFilegroupMember).where(PartFilegroupMember.group_id == top.id).order_by(PartFilegroupMember.ordinal)
    ).all()
    assert [s.get(PartFilegroup, m.child_group_id).label for m in members] == ["Author", "Radio"]
    cd1 = s.scalars(select(PartFilegroup).where(PartFilegroup.label == "CD1")).one()
    names = [
        os.path.basename(
            s.scalars(
                select(PartFileLocation.path).where(
                    PartFileLocation.file_id == m.file_id, PartFileLocation.path.startswith(cd1.path)
                )
            ).first()
        )
        for m in s.scalars(
            select(PartFilegroupMember)
            .where(PartFilegroupMember.group_id == cd1.id)
            .order_by(PartFilegroupMember.ordinal)
        )
    ]
    assert names == ["Track 2.mp3", "Track 10.mp3"] and cd1.total_bytes == 2
    assert (
        s.get(PartFilegroup, top.id).run_id
        and fg.would_cycle(s, cd1.id, top.id)
        and fg.would_cycle(s, cd1.id, cd1.id)
        and not fg.would_cycle(s, top.id, cd1.id)
    )


def test_regrouping_adds_a_new_grouping_and_leaves_the_old(s, lib):
    ids = fg.scan_files(s, lib)
    a = fg.group_by_folder(s, lib, ids)
    b = fg.group_by_folder(s, lib, ids)
    assert a.id != b.id and a.run_id != b.run_id and s.query(PartFilegroup).count() == 2 * 6


def test_member_needs_exactly_one_target_and_listen_can_link_a_group(s, lib):
    ids = fg.scan_files(s, lib)
    top = fg.group_by_folder(s, lib, ids)
    with pytest.raises(IntegrityError), s.begin_nested():
        s.add(PartFilegroupMember(group_id=top.id, ordinal=99))
        s.flush()
    l = L.create_listen(s)
    p = L.link(s, l, "input", filegroup_id=top.id)
    assert p.filegroup_id == top.id
    with pytest.raises(IntegrityError), s.begin_nested():
        L.link(s, l, "input", filegroup_id=top.id, file_id=next(iter(ids.values())))


def test_api_browse(s, lib, monkeypatch):
    app = FastAPI()
    app.include_router(api.router)
    app.dependency_overrides[api.get_session] = lambda: s
    c = TestClient(app)
    assert c.get("/api/filegroups").json()["root"] is None
    ids = fg.scan_files(s, lib)
    top = fg.group_by_folder(s, lib, ids)
    r = c.get("/api/filegroups").json()
    assert r["root"]["id"] == top.id and r["root"]["file_count"] == 5
    kids = c.get(f"/api/filegroups/{top.id}").json()["children"]
    assert [k["label"] for k in kids] == ["Author", "Radio"]
    cd1 = s.scalars(select(PartFilegroup).where(PartFilegroup.label == "CD1")).one()
    d = c.get(f"/api/filegroups/{cd1.id}").json()
    assert [a["label"] for a in d["ancestors"]] == [os.path.basename(lib), "Author", "Book One"] and [
        f["name"] for f in d["files"]
    ] == ["Track 2.mp3", "Track 10.mp3"]
    assert c.get("/api/filegroups/999999").status_code == 404


FFPROBE = {
    "format": {
        "duration": "61.5",
        "bit_rate": "64000",
        "tags": {"title": "Chapter 1", "artist": "A. Author", "comment": "unabridged"},
    },
    "streams": [{"codec_type": "audio", "codec_name": "mp3", "channels": 2, "sample_rate": "44100"}],
    "chapters": [{"id": 0, "tags": {"title": "One"}}],
}


def test_probe_keeps_the_whole_ffprobe_output_and_copies_columns(s, lib, monkeypatch):
    ids = fg.scan_files(s, lib)
    bad = os.path.join(lib, "Author/Single.m4b")
    monkeypatch.setattr(
        fg, "probe_file", lambda p: (_ for _ in ()).throw(RuntimeError("moov atom not found")) if p == bad else FFPROBE
    )
    assert fg.probe_missing(s, ids) == 4  # 5 paths, 4 distinct contents
    f = s.get(PartFile, ids[os.path.join(lib, "Author/Book One/CD1/Track 2.mp3")])
    assert f.probe == FFPROBE and (f.duration_s, f.codec, f.channels, f.sample_rate, f.bit_rate) == (
        61.5,
        "mp3",
        2,
        44100,
        64000,
    )
    assert f.probe["format"]["tags"]["comment"] == "unabridged" and f.probe["chapters"][0]["tags"]["title"] == "One"
    broken = s.get(PartFile, ids[bad])
    assert broken.probe is None and broken.probe_error == "moov atom not found"
    assert fg.probe_missing(s, ids) == 0  # neither the probed nor the failed ones are retried


def test_probe_file_reports_ffprobe_failures(tmp_path):
    import shutil

    if not shutil.which("ffprobe"):
        pytest.skip("needs ffprobe")
    p = tmp_path / "x.mp3"
    p.write_bytes(b"not audio")
    with pytest.raises(RuntimeError):
        fg.probe_file(str(p))


def test_api_file_detail_has_whole_probe(s, lib, monkeypatch):
    app = FastAPI()
    app.include_router(api.router)
    app.dependency_overrides[api.get_session] = lambda: s
    c = TestClient(app)
    ids = fg.scan_files(s, lib)
    monkeypatch.setattr(fg, "probe_file", lambda p: FFPROBE)
    fg.probe_missing(s, ids)
    top = fg.group_by_folder(s, lib, ids)
    cd1 = s.scalars(select(PartFilegroup).where(PartFilegroup.label == "CD1")).one()
    f = c.get(f"/api/filegroups/{cd1.id}").json()["files"][0]
    assert f["tags"]["artist"] == "A. Author" and f["codec"] == "mp3" and f["duration_s"] == 61.5
    d = c.get(f"/api/filegroups/files/{f['id']}").json()
    assert (
        d["probe"] == FFPROBE and len(d["sha256"]) == 64 and len(d["locations"]) == 2
    )  # Track 2 and Show.mp3 are the same content
    assert c.get("/api/filegroups/files/999999").status_code == 404


def test_api_search_finds_groups_and_files_and_names_the_group_to_jump_to(s, lib):
    app = FastAPI()
    app.include_router(api.router)
    app.dependency_overrides[api.get_session] = lambda: s
    c = TestClient(app)
    assert c.get("/api/filegroups/search?q=book").json() == {
        "groups": [],
        "files": [],
        "more": {"groups": 0, "files": 0},
    }  # nothing grouped yet
    ids = fg.scan_files(s, lib)
    top = fg.group_by_folder(s, lib, ids)
    r = c.get("/api/filegroups/search?q=BOOK one").json()
    assert sorted(g["label"] for g in r["groups"]) == [
        "Book One",
        "CD1",
        "CD2",
    ]  # every word matches the group's name or folder path, in any case
    r = c.get("/api/filegroups/search?q=track 10").json()
    assert [f["name"] for f in r["files"]] == ["Track 10.mp3"] and r["files"][0]["group_label"] == "CD1"
    cd1 = s.scalars(select(PartFilegroup).where(PartFilegroup.label == "CD1")).one()
    assert r["files"][0]["group_id"] == cd1.id
    assert c.get("/api/filegroups/search?q=Show").json()["files"][0]["group_label"] == "Radio"
    assert c.get("/api/filegroups/search?q=100%25").json()["files"] == []  # % is not a wildcard
    assert c.get("/api/filegroups/search?q=a").status_code == 422
    assert c.get("/api/filegroups/search?q=track&limit=1").json()["more"]["files"] == 2


def test_group_roots_puts_several_roots_under_one_top_group(s, tmp_path):
    for rel in ("one/A/x.mp3", "two/B/y.mp3", "two/B/z.mp3"):
        p = tmp_path / rel
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_bytes(rel.encode())
    one, two = str(tmp_path / "one"), str(tmp_path / "two")
    ids = {**fg.scan_files(s, one), **fg.scan_files(s, two)}
    top = fg.group_roots(s, [one, two], ids)
    assert (top.kind, top.path, top.file_count, top.group_count) == ("root", None, 3, 4)
    roots = s.scalars(
        select(PartFilegroup).where(PartFilegroup.run_id == top.run_id, PartFilegroup.path.in_([one, two]))
    ).all()
    assert sorted((g.kind, g.file_count) for g in roots) == [("folder", 1), ("folder", 2)]
    assert s.scalars(
        select(PartFilegroup).where(PartFilegroup.run_id == top.run_id, PartFilegroup.kind == "root")
    ).all() == [top]
    single = fg.group_roots(s, [one], {p: i for p, i in ids.items() if p.startswith(one)})
    assert (single.kind, single.path) == ("root", one)  # one root is unchanged from before
