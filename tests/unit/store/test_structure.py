"""Folder meaning inference (books, discs, series) on small in-memory trees, plus one run over real groups in Postgres."""
import pytest

pytest.importorskip("omarchy_db")
from app.store import filegroups as fg, structure as st


def leaf(label, *names, subs=()):
    return st.Node(-2, label, f"/lib/{label}", [(i, n) for i, n in enumerate(names)], list(subs))


def tracks(n, fmt="Track {:02d}.mp3"): return [fmt.format(i) for i in range(1, n + 1)]


def kinds(n): return n.kind, [c.kind for c in n.subs]


def test_discs_make_a_book():
    n = st.classify(leaf("Certain", subs=[leaf("CD1", *tracks(5)), leaf("CD2", *tracks(5))]))
    assert kinds(n) == ("book", ["disc", "disc"]) and n.detail["because"] == "disc names"


def test_numbered_names_and_number_words_are_discs():
    assert kinds(st.classify(leaf("Goldfinch", subs=[leaf("Goldfinch 01-26", *tracks(2)), leaf("Goldfinch 02-26", *tracks(2))]))) == ("book", ["disc", "disc"])
    assert kinds(st.classify(leaf("Girls", subs=[leaf("one", *tracks(3)), leaf("two", *tracks(3))]))) == ("book", ["disc", "disc"])


def test_folders_of_different_titles_are_a_series():
    n = st.classify(leaf("Sam Bourne", subs=[leaf("Sam Bourne - The Chosen One", *tracks(4)), leaf("Sam Bourne - Pantheon", *tracks(4))]))
    assert kinds(n) == ("series", ["book", "book"])


def test_whole_book_files_are_each_a_book_and_numbered_ones_are_one():
    assert kinds(st.classify(leaf("Just William", "A.m4b", "B.m4b"))) == ("series", ["book", "book"])
    n = st.classify(leaf("Psycho", *[f"{i:02d} - American Psycho.m4b" for i in range(1, 6)])); assert kinds(n) == ("book", [])


def test_files_beside_folders_become_their_own_groups():
    n = st.classify(leaf("Author", "Whole.m4b", subs=[leaf("Book A", *tracks(5))]))
    assert kinds(n) == ("series", ["book", "book"]) and n.files == []


def test_mp3_set_beside_its_m4b_is_two_books_to_merge_later():
    n = st.classify(leaf("Book", "Book.m4b", *tracks(6)))
    assert kinds(n) == ("series", ["book", "book"]) and n.detail["reason"].startswith("several books or copies")


def test_unrelated_tracks_are_loose_and_numbered_run_is_a_book():
    assert st.classify(leaf("Mix", "Bohemian.mp3", "Imagine.mp3", "Yesterday.mp3")).kind == "loose"
    assert st.classify(leaf("Push", *[f"TP - {i:03d} - Part {c}.mp3" for i, c in enumerate("abcdefgh", 1)])).kind == "book"
    assert st.classify(leaf("Hardtalk", *[f"Hardtalk 2012-01-{d:02d}.mp3" for d in range(1, 9)])).kind == "loose"      # dates are not track numbers


def test_a_year_range_in_the_title_is_not_a_radio_date():
    assert st.classify(leaf('Lost Diaries', *[f'{i:02d} - The Lost Diaries of Adrian Mole, 1999-2001 - Sue Townsend.mp3' for i in range(1, 30)])).kind == 'book'


def test_unknown_mixture_stays_a_folder_with_the_reason():
    n = st.classify(leaf("Odd", subs=[leaf("Mix", "Bohemian.mp3", "Imagine.mp3", "Yesterday.mp3"), leaf("Book", *tracks(5))]))
    assert n.kind == "folder" and n.detail["kinds"] == ["book", "loose"]


@pytest.fixture
def s(db_session, db_backend):
    if db_backend != "postgres": pytest.skip("needs Postgres")
    return db_session


def test_infer_writes_a_new_grouping_with_the_same_files(s, tmp_path):
    for rel in ("A/Book One/CD1/01.mp3", "A/Book One/CD1/02.mp3", "A/Book One/CD2/01.mp3", "A/Book Two/01.mp3", "A/Book Two/02.mp3", "A/Book Two/03.mp3"):
        p = tmp_path / rel; p.parent.mkdir(parents=True, exist_ok=True); p.write_bytes(rel.encode())
    ids = fg.scan_files(s, str(tmp_path)); folders = fg.group_by_folder(s, str(tmp_path), ids)
    top, totals = st.infer(s, folders)
    assert top.run_id != folders.run_id and top.kind == "root" and top.file_count == 6 and top.total_bytes == folders.total_bytes
    assert totals == {"disc": 2, "book": 2, "series": 1} and top.detail["from_group"] == folders.id
