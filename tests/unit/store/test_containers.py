"""A folder of numbered books that is really one collection (`app.linkage.containers`). Invented names and runtimes only."""

import pytest
from sqlalchemy import select

from app.linkage import apply, containers
from app.linkage import model as M
from app.store import build
from app.store import filegroups as fg
from app.store import listens as L
from app.store import metadata as md
from app.store import structure as st
from app.store.models import Listen, ListenPart

FOLDER = "Books/Quill Author; The Complete Quill Tales"
BOOKS = {"First Tale": 240, "Second Tale": 225, "Third Tale": 540}  # 1005 minutes together

COLLECTION = dict(
    key="asin:B0COLLECT01",
    title="The Complete Quill Tales",
    authors=["Quill Author"],
    year=None,
    ol=[],
    asins=["B0COLLECT01"],
    isbns=[],
    recordings=[
        dict(asin="B0COLLECT01", title="The Complete Quill Tales", narrators=[], minutes=1005, fmt="", year=None)
    ],
)


@pytest.fixture
def s(db_session, db_backend):
    if db_backend != "postgres":
        pytest.skip("needs Postgres")
    return db_session


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


def build_folder(s, tmp_path, books, folder=FOLDER, elsewhere=None):
    trees = {folder: books, **(elsewhere or {})}
    minutes = {}
    for f, bs in trees.items():
        for name in bs:
            p = tmp_path / f / name / f"{name}.m4b"
            p.parent.mkdir(parents=True, exist_ok=True)
            p.write_bytes(f"{f}{name}".encode())
            minutes[str(tmp_path / f / name)] = bs[name]
    ids = fg.scan_files(s, str(tmp_path))
    for path, fid in ids.items():
        s.get(fg.PartFile, fid).duration_s = minutes[path.rsplit("/", 1)[0]] * 60
    s.flush()
    top, _ = st.infer(s, fg.group_by_folder(s, str(tmp_path), ids))
    build.build_listens(s, top)
    md.add_metadata(s)
    return [s.get(Listen, lid) for lid in sorted({p.listen_id for p in s.scalars(select(ListenPart))})]


def fake_vectors(recording=1005):
    work = dict(COLLECTION, recordings=[dict(COLLECTION["recordings"][0], minutes=recording)])
    good = dict(title="exact_rare", author="all", runtime="1pct", sources="both", _alias="x", _reading="folder")
    return lambda oc, ac, q: dict(clusters={work["key"]: work}, vectors={work["key"]: [good]})


def live(s):
    return s.scalars(select(Listen).where(Listen.merged_into_id.is_(None))).all()


def test_numbered_books_that_add_up_to_the_collection_become_one_listen(s, tmp_path):
    build_folder(s, tmp_path, BOOKS)
    assert len(live(s)) == 3
    out = containers.merge_containers(s, None, None, params(), vectors=fake_vectors())
    assert [v.action for v in out] == ["join"]
    (survivor,) = live(s)
    inputs = s.scalars(
        select(ListenPart)
        .where(ListenPart.listen_id == survivor.id, ListenPart.role == "input", ListenPart.unlinked_at.is_(None))
        .order_by(ListenPart.ordinal)
    ).all()
    assert sum(p.detail["minutes"] for p in inputs) == 1005 and {p.detail["relation"] for p in inputs} == {"part"}
    match = s.scalars(select(ListenPart).where(ListenPart.listen_id == survivor.id, ListenPart.role == "match")).one()
    assert match.state == "accepted" and match.detail["container"] and survivor.status == "identified"
    assert containers.merge_containers(s, None, None, params(), vectors=fake_vectors()) == []  # nothing left to join


def test_an_approved_sub_book_match_is_superseded_by_the_collection(s, tmp_path):
    listens = build_folder(s, tmp_path, BOOKS)
    first = listens[0]
    part = L.link(
        s, first, "match", work_id=apply.work_for(s, COLLECTION).id, state="candidate", ordinal=1, actor_kind="user"
    )
    L.set_state(s, part, "accepted", actor_kind="user")
    containers.merge_containers(s, None, None, params(), vectors=fake_vectors())
    (survivor,) = live(s)
    matches = s.scalars(
        select(ListenPart).where(
            ListenPart.listen_id == survivor.id, ListenPart.role == "match", ListenPart.unlinked_at.is_(None)
        )
    ).all()
    assert len(matches) == 1 and matches[0].detail["container"]  # one answer for the whole, not one per piece


def test_separate_books_in_a_complete_named_folder_stay_apart(s, tmp_path):
    build_folder(s, tmp_path, BOOKS)
    out = containers.merge_containers(s, None, None, params(), vectors=fake_vectors(recording=1500))  # 1005 is 33% off
    assert out == [] and len(live(s)) == 3


def test_a_piece_as_long_as_the_work_is_a_copy_not_a_part(s, tmp_path):
    build_folder(s, tmp_path, {"First Tale": 900, "Second Tale": 105})
    assert containers.merge_containers(s, None, None, params(), vectors=fake_vectors()) == []
    assert len(live(s)) == 2


def test_close_but_not_exact_sums_are_only_proposed(s, tmp_path):
    build_folder(s, tmp_path, BOOKS)
    out = containers.merge_containers(s, None, None, params(), vectors=fake_vectors(recording=1090))  # 8% off
    assert [v.action for v in out] == ["propose"] and len(live(s)) == 3


def test_dry_run_writes_nothing(s, tmp_path):
    build_folder(s, tmp_path, BOOKS)
    out = containers.merge_containers(s, None, None, params(), vectors=fake_vectors(), dry_run=True)
    assert [v.action for v in out] == ["join"] and len(live(s)) == 3


def test_an_over_long_listen_is_flagged(s, tmp_path):
    (a, *_) = build_folder(s, tmp_path, {"First Tale": 700}, folder="Books/Single Tale")
    part = L.link(
        s,
        a,
        "match",
        work_id=apply.work_for(s, COLLECTION).id,
        state="candidate",
        ordinal=1,
        actor_kind="user",
        detail=dict(title="Short Tale", recordings=[dict(minutes=300)]),
    )
    L.set_state(s, part, "accepted", actor_kind="user")
    assert containers.overlong(s) == [(a.id, 700.0, 300.0, "Short Tale")]


def test_a_copy_of_a_piece_kept_elsewhere_stays_with_the_joined_listen(s, tmp_path):
    listens = build_folder(s, tmp_path, BOOKS, elsewhere={"Books/Spare": {"First Tale": 240}})
    spare = next(l for l in listens if "Spare" in (l.label or "") or (l.label == "First Tale" and l is listens[-1]))
    first = next(l for l in listens if l is not spare and l.label == "First Tale")
    L.merge(s, first, spare, actor_kind="rule", actor_ref="copies", reason="the same recording")
    containers.merge_containers(s, None, None, params(), vectors=fake_vectors())
    (survivor,) = [l for l in live(s)]
    inputs = s.scalars(
        select(ListenPart)
        .where(ListenPart.listen_id == survivor.id, ListenPart.role == "input", ListenPart.unlinked_at.is_(None))
        .order_by(ListenPart.ordinal)
    ).all()
    assert [p.detail.get("relation") for p in inputs] == ["part", "part", "part", None]  # the copy follows the pieces


def test_the_joined_listen_gets_the_collection_vectors_in_the_cache(s, tmp_path):
    from app.linkage import dataset

    build_folder(s, tmp_path, BOOKS)
    containers.merge_containers(s, None, None, params(), vectors=fake_vectors())
    (survivor,) = live(s)
    assert dataset.load_cache()[str(survivor.id)]["clusters"].keys() == {COLLECTION["key"]}


def test_a_joined_listen_is_as_long_as_all_its_parts_in_the_listens_list_detail_and_kind_features(s, tmp_path):
    from fastapi import FastAPI
    from fastapi.testclient import TestClient

    from app.store import api, kinds_db

    build_folder(s, tmp_path, BOOKS)
    containers.merge_containers(s, None, None, params(), vectors=fake_vectors())
    (survivor,) = live(s)
    app = FastAPI()
    app.include_router(api.listens_router)
    app.dependency_overrides[api.get_session] = lambda: s
    c = TestClient(app)
    row = next(r for r in c.get("/api/listens").json()["rows"] if r["id"] == survivor.id)
    assert (row["minutes"], row["files"], row["parts"]) == (1005, 3, 3)
    assert c.get(f"/api/listens/{survivor.id}").json()["listen"]["minutes"] == 1005
    assert kinds_db.gather(s, [survivor.id], audible=lambda a: {}, radio=lambda t: [])[survivor.id].minutes == 1005
