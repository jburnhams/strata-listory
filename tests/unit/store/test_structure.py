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
    assert st.classify(leaf("Hardtalk", *[f"Hardtalk 2012-01-{d:02d}.mp3" for d in range(1, 9)])).kind == "programme"      # dates are not track numbers; one title, dated episodes


def test_numbered_episodes_of_one_programme_are_a_programme():
    assert st.classify(leaf('Wilderness', *[f'BBC R4X - Wilderness Years, Episode {i}.mp3' for i in range(1, 9)])).detail['radio'] is True
    assert st.classify(leaf('Prostrate', *[f'Adrian Mole - 2019-06-{23 + i} - Prostrate - ep{i:02d}.mp3' for i in range(1, 11)])).kind == 'programme'
    assert st.classify(leaf('Mix', 'Show Episode 1.mp3', 'Other Episode 1.mp3', 'Third Episode 7.mp3')).kind == 'loose'      # not a run


def test_an_audible_style_id_and_episode_words_do_not_stop_a_numbered_run():
    names = [f'Backfired: The Vaping Wars [B0D23Y5KXP] - {i:02d} - Episode {i}: Title.mp3' for i in range(1, 10)] + ['Backfired: The Vaping Wars [B0D23Y5KXP] - 10 - End Credits.mp3']
    n = st.classify(leaf('Leon Neyfakh 2024 Backfired [B0D23Y5KXP]', *names)); assert n.kind == 'book' and n.detail['reason'] == 'numbered tracks 1..n'


def test_three_copies_of_one_book_in_one_folder_are_three_books():
    m4b = [f'{i:02d} - Psycho.m4b' for i in range(1, 7)]
    mp3_a = [f'{i:02d} {i:02d} - Psycho.mp3' for i in range(1, 7)]
    mp3_b = [f'APS{300 + i} {t}.mp3' for i, t in enumerate(['Harrys', 'Pastels', 'Tunnel', 'Office', 'Aspen', 'Taxi'])]
    n = st.classify(leaf('Psycho', *m4b, *mp3_a, *mp3_b))
    assert kinds(n) == ('series', ['book', 'book', 'book']) and [c.detail['files'] for c in n.subs] == [6, 6, 6]
    assert st.classify(leaf('Book', *tracks(8), 'Intro.mp3', 'Credits.mp3')).kind in ('book', 'loose')      # a stray intro and credits file do not split one set


def test_season_episode_names_make_a_programme_and_several_seasons_split():
    one = st.classify(leaf('Milton Jones S1', *[f'The Very World Of Milton Jones S1E{i} Title.mp3' for i in range(1, 5)]))
    assert one.kind == 'programme' and one.detail['radio'] is True
    flat = st.classify(leaf('Great Lives', *[f'Great Lives S{s}E0{e} Name.mp3' for s in (31, 32) for e in (1, 2, 3)]))
    assert kinds(flat) == ('series', ['programme', 'programme'])
    assert st.classify(leaf('Tender', 'Episode 1.mp3', 'Episode 2.mp3')).kind == 'programme'


def test_files_that_say_they_are_parts_of_n_are_one_book():
    assert st.classify(leaf("Rumpole's Last Case", *[f"rumAB08_6{c} - The Last Case ({i}-3), McKern.mp3" for i, c in enumerate('abc', 1)])).kind == 'book'
    assert st.classify(leaf('Rocket', *[f'it_is_rocket_science.2011-03-0{i}.{i}_of_4.mp3' for i in range(1, 5)])).kind == 'book'
    assert st.classify(leaf('Not all there', 'X (1-3).mp3', 'X (2-3).mp3')).kind == 'loose'      # claims 3 parts, has 2


def test_dated_broadcasts_are_loose_with_that_reason_and_unrelated_tracks_say_so():
    n = st.classify(leaf('Interviews', *[f'2016-07-{d:02d}-Guest {d}.mp3' for d in range(1, 8)]))
    assert n.kind == 'loose' and n.detail['reason'].startswith('dated broadcasts')      # dated, but no programme title to tie them together
    assert st.classify(leaf('Mix', 'Bohemian.mp3', 'Imagine.mp3', 'Yesterday.mp3')).detail['reason'] == 'unrelated tracks'


def test_a_numbered_radio_series_with_broadcast_dates_is_a_programme_not_a_book():
    names = [f'{i:02d} of 12 - Title {i} ({i}th January 2013) [WebRip (mp3)].mp3' for i in range(1, 13)]
    n = st.classify(leaf('Story', *names)); assert n.kind == 'programme' and n.detail['radio'] is True
    assert st.classify(leaf('Novel', *[f'{i:02d} of 12 - Chapter {i}.mp3' for i in range(1, 13)])).kind == 'book'


def test_one_programme_under_mixed_numbering_is_pulled_out_of_a_drawer():
    names = ["Mark.Steels.in.Town.20100407.DABRadioRip.mp3", "Mark.Steels.in.Town.20100414.DABRadioRip.mp3", "Mark Steel's in Town - Part 1 of 4 - 18032009.mp3", "Mark Steel's in Town - Edinburgh Special.mp3",
             "Mark Steel Celebrates Leith.mp3", "Mark_Steel_-_Who_Do_I_Think_I_Am_-_original.mp3", "Zebra Interview.mp3"]
    n = st.classify(leaf('Mark Steel', *names))
    assert kinds(n) == ('collection', ['programme', 'loose']) and [c.detail['files'] for c in n.subs] == [4, 3] and n.subs[0].detail['title'] == 'marksteelsintown'
    assert st.programme_title('Intro.mp3') == '' and st.programme_title("Mark Steels in Town - S03E01 - Berwick.mp3") == 'marksteelsintown'


def test_bbc_radio_and_air_dates_in_the_names_make_a_programme():
    names = [f'0{i} - BBC Radio 4 - Espedair Street - Part {w} {8 + 7 * (i - 1)} Jan 1998.mp3' for i, w in enumerate(['One', 'Two', 'Three', 'Four'], 1)]
    n = st.classify(leaf('Espedair', *names)); assert n.kind == 'programme' and n.detail['radio'] is True
    assert st.classify(leaf('Novel', *[f'0{i} - Part {w}.mp3' for i, w in enumerate(['One', 'Two', 'Three', 'Four'], 1)])).kind != 'programme'


def test_a_year_range_in_the_title_is_not_a_radio_date():
    assert st.classify(leaf('Lost Diaries', *[f'{i:02d} - The Lost Diaries of Adrian Mole, 1999-2001 - Sue Townsend.mp3' for i in range(1, 30)])).kind == 'book'


def test_a_mix_of_books_and_radio_is_a_collection():
    n = st.classify(leaf("Odd", subs=[leaf("Mix", "Bohemian.mp3", "Imagine.mp3", "Yesterday.mp3"), leaf("Book", *tracks(5))]))
    assert n.kind == "collection" and n.detail["kinds"] == ["book", "loose"]


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


def test_snapshot_and_diff_show_what_a_rule_change_moves():
    tree = lambda: leaf("Author", "Whole.m4b", subs=[leaf("Book A", *tracks(5)), leaf("Mix", "Bohemian.mp3", "Imagine.mp3", "Yesterday.mp3")])
    before = st.snapshot(st.classify(tree()))
    assert before["/lib/Book A"][0] == "book" and before["/lib/Mix"][0] == "loose" and before["/lib/Author#Whole"][0] == "book" and before["/lib/Author"][0] == "collection"
    assert st.diff(before, st.snapshot(st.classify(tree()))) == []
    after = dict(before); after["/lib/Mix"] = ["book", "chapter files of one work"]; del after["/lib/Book A"]; after["/lib/New"] = ["disc", "x"]
    assert sorted(st.diff(before, after)) == sorted(["+ /lib/New: disc (x)", "- /lib/Book A: book (chapter files of one work)", "~ /lib/Mix: loose -> book (chapter files of one work)"])
