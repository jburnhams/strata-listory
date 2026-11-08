"""Identification written to the Postgres catalogue (`app.linkage.apply`, `query.load`, `files`, catalogue labels). Invented folder names only."""
import pytest

pytest.importorskip("omarchy_db")
from sqlalchemy import func, select

from app.linkage import apply, dataset as D, files, model as M, query as Q
from app.store import build, filegroups as fg, listens as L, metadata as md, structure as st
from app.store.models import Listen, ListenEvent, ListenPart, Work, WorkIdentifier


@pytest.fixture
def s(db_session, db_backend):
    if db_backend != "postgres": pytest.skip("needs Postgres")
    return db_session


@pytest.fixture
def listens(s, tmp_path):
    """Two copies of one recording (same runtime), another narration of the same book, and an unrelated book; names say title and author."""
    minutes = {"Books/Mara Quill - Glass Orchard": 600, "Audible/Mara Quill - Glass Orchard": 606, "Books/Mara Quill - Glass Orchard (Ada Penn)": 450, "Books/Ben Ash - Salt Roads": 300}
    for folder in minutes:
        for i in range(1, 4):
            p = tmp_path / folder / f"{i:02d}.mp3"; p.parent.mkdir(parents=True, exist_ok=True); p.write_bytes(f"{folder}{i}".encode())
    ids = fg.scan_files(s, str(tmp_path))
    for path, fid in ids.items():
        rel = path[len(str(tmp_path)) + 1:].rsplit("/", 1)[0]; s.get(fg.PartFile, fid).duration_s = minutes[rel] * 60 / 3
    s.flush(); top, _ = st.infer(s, fg.group_by_folder(s, str(tmp_path), ids)); build.build_listens(s, top); md.add_metadata(s)
    return {p.detail["path"][len(str(tmp_path)) + 1:]: s.get(Listen, p.listen_id) for p in s.scalars(select(ListenPart).where(ListenPart.role == "input"))}      # keyed by folder


def params():
    return M.train([M.Case(i, {"ok": [dict(title="exact_rare", author="all", runtime="1pct", sources="both")], "bad": [dict(title="weak", author="conflict", sources="openlibrary")]}) for i in range(30)])


GLASS = dict(key="/works/OL1W", title="Glass Orchard", authors=["Mara Quill"], year=2001, ol=["/works/OL1W", "/works/OL7W"], asins=["B0AAAAAAA1"], isbns=["9780306406157"])
SALT = dict(key="asin:B0AAAAAAA9", title="Salt Roads", authors=["Ben Ash", "Anonymous"], year=None, ol=[], asins=["B0AAAAAAA9"], isbns=[])


def data_for(listens, sure=True):
    good = dict(title="exact_rare", author="all", runtime="1pct", sources="both", _alias="glass orchard", _asin="B0AAAAAAA1", _reading="folder")
    out = {}
    for label, l in listens.items():
        if "Salt" in label: out[str(l.id)] = dict(clusters={SALT["key"]: SALT, GLASS["key"]: GLASS}, vectors={SALT["key"]: [dict(good, _alias="salt roads")], GLASS["key"]: [dict(title="weak", author="conflict", sources="both")] if sure else [good]})      # unsure: two works fit equally well
        else: out[str(l.id)] = dict(clusters={GLASS["key"]: GLASS, SALT["key"]: SALT}, vectors={GLASS["key"]: [good], SALT["key"]: [dict(title="weak", author="conflict", sources="audible")]})
    return out


def test_load_reads_readings_runtime_and_files(s, listens):
    q = next(q for q in Q.load(s) if q.listen_id == listens["Books/Mara Quill - Glass Orchard (Ada Penn)"].id)
    assert q.minutes == [450.] and q.hints == ["Ada Penn"] and any(r.title == "Glass Orchard" and r.author == "Mara Quill" for r in q.readings)
    fs, paths = files.listen_files(s)
    assert sorted(len(v) for v in fs.values()) == [3, 3, 3, 3] and all(p for p in paths.values())
    assert len(Q.load(s, ids=[q.listen_id])) == 1


def test_apply_accepts_links_one_work_per_book_and_reruns_quietly(s, listens):
    p = params(); counts = apply.apply(s, data_for(listens), p)
    assert counts["accept"] == 4
    works = s.scalars(select(Work)).all(); assert sorted(w.title for w in works) == ["Glass Orchard", "Salt Roads"]
    glass = next(w for w in works if w.title == "Glass Orchard")
    assert {(i.scheme, i.value) for i in s.scalars(select(WorkIdentifier).where(WorkIdentifier.work_id == glass.id))} >= {("work", "/works/OL1W"), ("ol_work", "/works/OL7W"), ("asin", "B0AAAAAAA1"), ("isbn", "9780306406157")}
    match = s.scalars(select(ListenPart).where(ListenPart.role == "match")).first()
    assert match.state == "accepted" and match.linked_by == "model" and match.score > .95 and match.detail["recording"] == "B0AAAAAAA1" and match.detail["vector"]["runtime"] == "1pct"
    assert {l.status for l in s.scalars(select(Listen))} == {"identified"}
    assert [e.action for e in s.scalars(select(ListenEvent).where(ListenEvent.listen_part_id == match.id).order_by(ListenEvent.id))] == ["link", "accept"]
    assert len(apply.same_work_groups(s)[glass.id]) == 3          # copies and the other narration all link to one work
    assert apply.apply(s, data_for(listens), p)["unchanged"] == 4


def test_review_candidates_then_a_person_decides_and_is_kept(s, listens):
    p = params(); apply.apply(s, data_for(listens, sure=False), p)
    salt = next(l for k, l in listens.items() if "Salt" in k)
    cands = s.scalars(select(ListenPart).where(ListenPart.listen_id == salt.id, ListenPart.role == "candidate", ListenPart.unlinked_at.is_(None)).order_by(ListenPart.ordinal)).all()
    assert salt.status == "review" and cands and all(c.state == "candidate" for c in cands)
    L.set_state(s, cands[0], "accepted", actor_kind="user", actor_ref="tester")
    labels = D.catalogue_labels(s)
    assert labels[salt.id]["origin"] == "user" and labels[salt.id]["target"]["key"] == cands[0].detail["candidate"]
    assert apply.apply(s, data_for(listens), p)["kept"] == 1                                       # a person's decision is never replaced
    assert apply.clear(s) == 3 and s.scalar(select(func.count()).select_from(ListenPart).where(ListenPart.role == "match")) == 0
    assert s.scalars(select(ListenPart).where(ListenPart.listen_id == salt.id, ListenPart.role == "candidate")).first() is not None


def test_merge_copies_only_merges_the_same_recording(s, listens):
    apply.apply(s, data_for(listens), params())
    merged = apply.merge_copies(s)
    assert len(merged) == 1
    live = {l.id for l in s.scalars(select(Listen).where(Listen.merged_into_id.is_(None)))}
    assert len(live) == 3 and listens["Books/Mara Quill - Glass Orchard (Ada Penn)"].id in live                  # the other narration (450 min) stays its own listen
    assert apply.merge_copies(s) == []


def test_all_labels_read_the_old_catalogue_file(s, listens, tmp_path, monkeypatch):
    monkeypatch.setattr(D, "folder", lambda: tmp_path)
    assert D.all_labels(s) == {}
    (tmp_path / "old-labels.json").write_text('{"5": {"target": {"work": "/works/OL1W"}, "origin": "manual"}}')
    assert D.all_labels(s) == {5: {"target": {"work": "/works/OL1W"}, "origin": "manual"}}
