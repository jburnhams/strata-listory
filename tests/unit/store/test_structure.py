"""Folder meaning inference (books, discs, series) on small in-memory trees, plus one run over real groups in Postgres."""

import pytest

from app.store import filegroups as fg
from app.store import structure as st


def leaf(label, *names, subs=()):
    return st.Node(-2, label, f"/lib/{label}", [(i, n) for i, n in enumerate(names)], list(subs))


def tracks(n, fmt="Track {:02d}.mp3"):
    return [fmt.format(i) for i in range(1, n + 1)]


def kinds(n):
    return n.kind, [c.kind for c in n.subs]


def test_discs_make_a_book():
    n = st.classify(leaf("Certain", subs=[leaf("CD1", *tracks(5)), leaf("CD2", *tracks(5))]))
    assert kinds(n) == ("book", ["disc", "disc"]) and n.detail["because"] == "disc names"


def test_numbered_names_and_number_words_are_discs():
    assert kinds(
        st.classify(leaf("Goldfinch", subs=[leaf("Goldfinch 01-26", *tracks(2)), leaf("Goldfinch 02-26", *tracks(2))]))
    ) == ("book", ["disc", "disc"])
    assert kinds(st.classify(leaf("Girls", subs=[leaf("one", *tracks(3)), leaf("two", *tracks(3))]))) == (
        "book",
        ["disc", "disc"],
    )


def test_folders_of_different_titles_are_a_series():
    n = st.classify(
        leaf(
            "Sam Bourne",
            subs=[leaf("Sam Bourne - The Chosen One", *tracks(4)), leaf("Sam Bourne - Pantheon", *tracks(4))],
        )
    )
    assert kinds(n) == ("series", ["book", "book"])


def test_whole_book_files_are_each_a_book_and_numbered_ones_are_one():
    assert kinds(st.classify(leaf("Just William", "A.m4b", "B.m4b"))) == ("series", ["book", "book"])
    n = st.classify(leaf("Psycho", *[f"{i:02d} - American Psycho.m4b" for i in range(1, 6)]))
    assert kinds(n) == ("book", [])


def test_files_beside_folders_become_their_own_groups():
    n = st.classify(leaf("Author", "Whole.m4b", subs=[leaf("Book A", *tracks(5))]))
    assert kinds(n) == ("series", ["book", "book"]) and n.files == []


def test_mp3_set_beside_its_m4b_is_two_books_to_merge_later():
    n = st.classify(leaf("Book", "Book.m4b", *tracks(6)))
    assert kinds(n) == ("series", ["book", "book"]) and n.detail["reason"].startswith("several books or copies")


def test_unrelated_tracks_are_loose_and_numbered_run_is_a_book():
    assert st.classify(leaf("Mix", "Bohemian.mp3", "Imagine.mp3", "Yesterday.mp3", "Hey Jude.mp3")).kind == "loose"
    assert (
        st.classify(leaf("Push", *[f"TP - {i:03d} - Part {c}.mp3" for i, c in enumerate("abcdefgh", 1)])).kind == "book"
    )
    assert (
        st.classify(leaf("Hardtalk", *[f"Hardtalk 2012-01-{d:02d}.mp3" for d in range(1, 9)])).kind == "programme"
    )  # dates are not track numbers; one title, dated episodes


def test_numbered_episodes_of_one_programme_are_a_programme():
    assert (
        st.classify(leaf("Wilderness", *[f"BBC R4X - Wilderness Years, Episode {i}.mp3" for i in range(1, 9)])).detail[
            "radio"
        ]
        is True
    )
    assert (
        st.classify(
            leaf("Prostrate", *[f"Adrian Mole - 2019-06-{23 + i} - Prostrate - ep{i:02d}.mp3" for i in range(1, 11)])
        ).kind
        == "programme"
    )
    assert (
        st.classify(
            leaf("Mix", "Show Episode 1.mp3", "Other Episode 1.mp3", "Third Episode 7.mp3", "Fourth Episode 9.mp3")
        ).kind
        == "loose"
    )  # not a run


def test_an_audible_style_id_and_episode_words_do_not_stop_a_numbered_run():
    names = [f"Backfired: The Vaping Wars [B0D23Y5KXP] - {i:02d} - Episode {i}: Title.mp3" for i in range(1, 10)] + [
        "Backfired: The Vaping Wars [B0D23Y5KXP] - 10 - End Credits.mp3"
    ]
    n = st.classify(leaf("Leon Neyfakh 2024 Backfired [B0D23Y5KXP]", *names))
    assert n.kind == "book" and n.detail["reason"] == "numbered tracks 1..n"


def test_three_copies_of_one_book_in_one_folder_are_three_books():
    m4b = [f"{i:02d} - Psycho.m4b" for i in range(1, 7)]
    mp3_a = [f"{i:02d} {i:02d} - Psycho.mp3" for i in range(1, 7)]
    mp3_b = [f"APS{300 + i} {t}.mp3" for i, t in enumerate(["Harrys", "Pastels", "Tunnel", "Office", "Aspen", "Taxi"])]
    n = st.classify(leaf("Psycho", *m4b, *mp3_a, *mp3_b))
    assert kinds(n) == ("series", ["book", "book", "book"]) and [c.detail["files"] for c in n.subs] == [6, 6, 6]
    assert st.classify(leaf("Book", *tracks(8), "Intro.mp3", "Credits.mp3")).kind in (
        "book",
        "loose",
    )  # a stray intro and credits file do not split one set


def test_season_episode_names_make_a_programme_and_several_seasons_split():
    one = st.classify(
        leaf("Milton Jones S1", *[f"The Very World Of Milton Jones S1E{i} Title.mp3" for i in range(1, 5)])
    )
    assert one.kind == "programme" and one.detail["radio"] is True
    flat = st.classify(leaf("Great Lives", *[f"Great Lives S{s}E0{e} Name.mp3" for s in (31, 32) for e in (1, 2, 3)]))
    assert kinds(flat) == ("series", ["programme", "programme"])
    assert st.classify(leaf("Tender", "Episode 1.mp3", "Episode 2.mp3")).kind == "programme"


def test_files_that_say_they_are_parts_of_n_are_one_book():
    assert (
        st.classify(
            leaf(
                "Rumpole's Last Case",
                *[f"rumAB08_6{c} - The Last Case ({i}-3), McKern.mp3" for i, c in enumerate("abc", 1)],
            )
        ).kind
        == "book"
    )
    assert st.classify(leaf("Rocket", *[f"it_is_rocket_science.{i}_of_4.mp3" for i in range(1, 5)])).kind == "book"
    assert (
        st.classify(leaf("Not all there", "X (1-3).mp3", "X (2-3).mp3")).kind != "book"
    )  # claims 3 parts, has 2: not one book


def test_dated_broadcasts_are_a_programme_and_unrelated_tracks_stay_loose():
    n = st.classify(leaf("Guy Garvey", *[f"2016-07-{d:02d}-Guest {d}.mp3" for d in range(1, 8)]))
    assert (
        n.kind == "programme" and n.detail["reason"].startswith("dated broadcasts") and n.detail["radio"] is True
    )  # dated files in one folder are the episodes of that programme
    assert (
        st.classify(leaf("Mix", "Bohemian.mp3", "Imagine.mp3", "Yesterday.mp3", "Hey Jude.mp3")).detail["reason"]
        == "unrelated tracks"
    )


def test_a_numbered_radio_series_with_broadcast_dates_is_a_programme_not_a_book():
    names = [f"{i:02d} of 12 - Title {i} ({i}th January 2013) [WebRip (mp3)].mp3" for i in range(1, 13)]
    n = st.classify(leaf("Story", *names))
    assert n.kind == "programme" and n.detail["radio"] is True
    assert st.classify(leaf("Novel", *[f"{i:02d} of 12 - Chapter {i}.mp3" for i in range(1, 13)])).kind == "book"


def test_one_programme_under_mixed_numbering_is_pulled_out_of_a_drawer():
    names = [
        "Mark.Steels.in.Town.20100407.DABRadioRip.mp3",
        "Mark.Steels.in.Town.20100414.DABRadioRip.mp3",
        "Mark Steel's in Town - Part 1 of 4 - 18032009.mp3",
        "Mark Steel's in Town - Edinburgh Special.mp3",
        "Mark Steel Celebrates Leith.mp3",
        "Mark_Steel_-_Who_Do_I_Think_I_Am_-_original.mp3",
        "Zebra Interview.mp3",
    ]
    n = st.classify(leaf("Mark Steel", *names))
    assert (
        kinds(n) == ("series", ["programme", "programme", "programme", "programme"])
        and n.subs[0].detail["files"] == 4
        and n.subs[1].detail["reason"].startswith("stand-alone file")
        and n.subs[0].detail["title"] == "marksteelsintown"
    )
    assert (
        st.programme_title("Intro.mp3") == ""
        and st.programme_title("Mark Steels in Town - S03E01 - Berwick.mp3") == "marksteelsintown"
    )


def test_bbc_radio_and_air_dates_in_the_names_make_a_programme():
    names = [
        f"0{i} - BBC Radio 4 - Espedair Street - Part {w} {8 + 7 * (i - 1)} Jan 1998.mp3"
        for i, w in enumerate(["One", "Two", "Three", "Four"], 1)
    ]
    n = st.classify(leaf("Espedair", *names))
    assert n.kind == "programme" and n.detail["radio"] is True
    assert (
        st.classify(
            leaf("Novel", *[f"0{i} - Part {w}.mp3" for i, w in enumerate(["One", "Two", "Three", "Four"], 1)])
        ).kind
        != "programme"
    )


def test_series_dash_number_and_bbc_programme_ids_make_a_programme():
    s8 = st.classify(
        leaf(
            "Series 8",
            *[
                f"Mark_Steels_in_Town_Series_8_-_{i}._{t}.mp3"
                for i, t in enumerate(["Bedford", "Matlock", "Inverness", "Portishead"], 1)
            ],
        )
    )
    assert s8.kind == "programme"
    s7 = st.classify(
        leaf(
            "Series 7",
            *[
                f"Mark_Steels_in_Town_Series_7_-_{i}._{t}_b07tbfm{i}_original.mp3"
                for i, t in enumerate(["Stockport", "Colchester", "Hebden"], 1)
            ],
            "Mark_Steels_in_Town_Series_7_-_Gibraltar_-_Extended_Edition_p04bs6z5_original.mp3",
        )
    )
    assert s7.kind == "programme" and "radio broadcast" in s7.detail["reason"]


def test_a_few_stand_alone_files_are_each_a_programme_but_a_big_pile_stays_loose():
    n = st.classify(
        leaf("Ripley", "Complete Ripley - 1 - The talented Mr Ripley.mp3", "ripleys game.mp3", "Ripley Underground.mp3")
    )
    assert kinds(n) == ("series", ["programme", "programme", "programme"])
    assert (
        st.classify(leaf("Drawer", *[f"{t}.mp3" for t in ("Bohemian", "Imagine", "Yesterday", "Hey Jude")])).kind
        == "loose"
    )


def test_a_book_length_stand_alone_file_is_a_book_not_a_programme(monkeypatch):
    monkeypatch.setattr(st, "DURATIONS", {0: 313 * 60.0, 1: 184 * 60.0, 2: 57 * 60.0})
    n = st.classify(leaf("Tony Benn", "Free at Last The Diaries.mp3", "The Benn Diaries Vol 1.mp3", "Tapes.mp3"))
    assert [c.kind for c in n.subs] == ["book", "book", "programme"] and n.subs[0].detail[
        "minutes"
    ] == 313  # two are book length, the hour-long one is not


def test_part_n_with_a_title_makes_the_parts_of_one_book():
    n = st.classify(
        leaf(
            "01 - A Study in Scarlet",
            subs=[leaf("Part 1 - Being a Reprint", *tracks(7)), leaf("Part 2 - The Country of the Saints", *tracks(7))],
        )
    )
    assert kinds(n) == ("book", ["disc", "disc"])
    assert (
        st.classify(
            leaf("Flashman", subs=[leaf("Book 1 - Flashman", *tracks(7)), leaf("Book 2 - Royal Flash", *tracks(7))])
        ).kind
        == "series"
    )  # 'Book N' is a series of books


def test_the_track_number_can_be_second_or_after_of_or_a_disc_track_pair():
    flash = st.classify(
        leaf("MP4b", *[f"G.M. Fraser -12- Flashman on the March {i:02d} of 13.m4b" for i in range(1, 14)])
    )
    assert (flash.kind == "book" and "numbered" in flash.detail["reason"]) or "parts" in flash.detail["reason"]
    discs = st.classify(
        leaf(
            "NurtureShock",
            *[f"{d}-{t:02d} NurtureShock - [Disc {d}] ~ Track.m4a" for d in (1, 2, 3) for t in range(1, 6)],
        )
    )
    assert discs.kind == "book" and discs.subs == []  # one book, not 15 one-file books
    assert (
        st.classify(
            leaf("Albums", "Bohemian 1999.m4a", "Imagine 2001.m4a", "Yesterday 1965.m4a", "Hey Jude 1968.m4a")
        ).kind
        == "series"
    )  # years are not track numbers


def test_a_year_range_in_the_title_is_not_a_radio_date():
    assert (
        st.classify(
            leaf(
                "Lost Diaries",
                *[f"{i:02d} - The Lost Diaries of Adrian Mole, 1999-2001 - Sue Townsend.mp3" for i in range(1, 30)],
            )
        ).kind
        == "book"
    )


def test_a_mix_of_books_and_radio_is_a_collection():
    n = st.classify(
        leaf(
            "Odd",
            subs=[
                leaf("Mix", "Bohemian.mp3", "Imagine.mp3", "Yesterday.mp3", "Hey Jude.mp3"),
                leaf("Book", *tracks(5)),
            ],
        )
    )
    assert n.kind == "collection" and n.detail["kinds"] == ["book", "loose"]


@pytest.fixture
def s(db_session, db_backend):
    if db_backend != "postgres":
        pytest.skip("needs Postgres")
    return db_session


def test_infer_writes_a_new_grouping_with_the_same_files(s, tmp_path):
    for rel in (
        "A/Book One/CD1/01.mp3",
        "A/Book One/CD1/02.mp3",
        "A/Book One/CD2/01.mp3",
        "A/Book Two/01.mp3",
        "A/Book Two/02.mp3",
        "A/Book Two/03.mp3",
    ):
        p = tmp_path / rel
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_bytes(rel.encode())
    ids = fg.scan_files(s, str(tmp_path))
    folders = fg.group_by_folder(s, str(tmp_path), ids)
    top, totals = st.infer(s, folders)
    assert (
        top.run_id != folders.run_id
        and top.kind == "root"
        and top.file_count == 6
        and top.total_bytes == folders.total_bytes
    )
    assert totals == {"disc": 2, "book": 2, "series": 1} and top.detail["from_group"] == folders.id


def test_snapshot_and_diff_show_what_a_rule_change_moves():
    tree = lambda: leaf(
        "Author",
        "Whole.m4b",
        subs=[leaf("Book A", *tracks(5)), leaf("Mix", "Bohemian.mp3", "Imagine.mp3", "Yesterday.mp3", "Hey Jude.mp3")],
    )
    before = st.snapshot(st.classify(tree()))
    assert (
        before["/lib/Book A"][0] == "book"
        and before["/lib/Mix"][0] == "loose"
        and before["/lib/Author#Whole"][0] == "book"
        and before["/lib/Author"][0] == "collection"
    )
    assert st.diff(before, st.snapshot(st.classify(tree()))) == []
    after = dict(before)
    after["/lib/Mix"] = ["book", "chapter files of one work"]
    del after["/lib/Book A"]
    after["/lib/New"] = ["disc", "x"]
    assert sorted(st.diff(before, after)) == sorted(
        [
            "+ /lib/New: disc (x)",
            "- /lib/Book A: book (chapter files of one work)",
            "~ /lib/Mix: loose -> book (chapter files of one work)",
        ]
    )


def test_play_length_makes_a_programme_but_a_lone_short_work_among_books_stays_a_book(monkeypatch):
    monkeypatch.setattr(st, "DURATIONS", dict.fromkeys(range(100), 2700.0))  # 45 minutes per file
    play = st.classify(leaf("Rumpole and the Reign of Terror", *[f"Part {i}.mp3" for i in (1, 2)]))
    assert play.kind == "programme" and play.detail["minutes"] == 90 and "play length" in play.detail["reason"]
    books = [leaf(f"Book {i}", *tracks(8)) for i in range(5)]
    assert all(
        c.kind == "book"
        for c in st.classify(leaf("Shelf", subs=books + [leaf("Short story", "Part 1.mp3", "Part 2.mp3")])).subs
    )  # one short work among five books
    assert all(i in st.DURATIONS for i in range(2))


def test_a_full_length_file_beside_its_split_chapters_is_two_groups(monkeypatch):
    names = ["Invented Book 06.mp3"] + [f"Invented Book 06_{i}.mp3" for i in range(1, 15)]
    monkeypatch.setitem(st.DURATIONS, 0, 47520.0)  # the full file: 792 minutes
    for i in range(1, 15):
        monkeypatch.setitem(st.DURATIONS, i, 3394.3)  # fourteen parts adding up to the same length
    n = st.classify(leaf("Invented", *names))
    assert [c.kind for c in n.subs] == ["book", "book"] and sorted(len(c.files) for c in n.subs) == [1, 14]
    assert n.subs[0].detail["reason"] == "a whole-book file with its split chapters beside it"


def test_chapter_files_alone_stay_one_book_even_with_a_long_one(monkeypatch):
    names = tracks(6)
    for i, d in enumerate((3600, 3600, 3600, 3600, 3600, 36000)):
        monkeypatch.setitem(st.DURATIONS, i, float(d))
    assert (
        kinds(st.classify(leaf("Invented", *names)))[0] == "book"
        and len(st.classify(leaf("Invented", *names)).subs) == 0
    )


def test_a_long_file_whose_name_has_nothing_to_do_with_the_chapters_is_not_their_full_copy(monkeypatch):
    names = ["Unrelated Long Talk.mp3"] + [f"Invented Book {i:02d}.mp3" for i in range(1, 6)]
    monkeypatch.setitem(st.DURATIONS, 0, 18000.0)
    for i in range(1, 6):
        monkeypatch.setitem(st.DURATIONS, i, 3600.0)  # the lengths add up, the names do not
    assert st.full_beside_its_parts([(i, n) for i, n in enumerate(names)]) is None


def test_explicitly_numbered_parts_are_never_split_into_a_full_copy(monkeypatch):
    names = [f"Invented Novel Part {i}_of_4.mp3" for i in range(1, 5)]
    for i, hours in enumerate((1, 1, 1, 3)):  # the last part is as long as the other three together
        monkeypatch.setitem(st.DURATIONS, i, hours * 3600.0)
    assert st.full_beside_its_parts([(i, n) for i, n in enumerate(names)]) is None
    n = st.classify(leaf("Invented", *names))
    assert n.kind == "book" and not n.subs


def _long(monkeypatch, n, minutes=400):
    for i in range(n):
        monkeypatch.setitem(st.DURATIONS, i, minutes * 60.0)


def test_long_files_with_titles_of_their_own_are_separate_books(monkeypatch):
    names = [
        f"Pat Author - {t}.mp3" for t in ("The Invented Road", "Blood Orchard", "Child of Nowhere", "Cities of Glass")
    ]
    _long(monkeypatch, 4)
    n = st.classify(leaf("Pat Author", *names))
    assert [c.kind for c in n.subs] == ["book"] * 4 and all(len(c.files) == 1 for c in n.subs)
    coded = [
        f"Pat Author HB{i:02d} {t}.mp3" for i, t in enumerate(("The First Case", "The Second Case", "A Third Case"), 1)
    ]
    _long(monkeypatch, 3)
    assert (
        len(st.classify(leaf("Pat Author", *coded)).subs) == 3
    )  # a series code in front of each title does not make them chapters


def test_numbered_parts_and_chapters_of_long_files_stay_one_book(monkeypatch):
    for names in (
        ["Invented Book Part 1.mp3", "Invented Book Part 2.mp3", "Invented Book Part 3.mp3"],
        ["01 Invented Book.mp3", "02 Invented Book.mp3", "03 Invented Book.mp3"],
        ["Invented Book - 01.mp3", "Invented Book - 02.mp3"],
        ["Show - 1x01.mp3", "Show - 1x02.mp3", "Show - 1x03.mp3"],
    ):
        _long(monkeypatch, len(names), 420)
        assert st.titled_books([(i, n) for i, n in enumerate(names)]) == []


def test_short_files_with_different_titles_are_not_taken_for_books(monkeypatch):
    _long(monkeypatch, 3, 45)  # chapter titles can differ too, but chapters are short
    assert (
        st.titled_books([(i, f"Chapter title {t}.mp3") for i, t in enumerate(("The Storm", "The Calm", "The End"))])
        == []
    )
    assert st.titled_books([(0, "Only One Long File.mp3")]) == []


def test_named_files_gives_each_member_of_a_repeated_file_its_own_name():
    names = {("/lib/B", 1): ["B 01.mp3", "XY01 B.mp3"], ("/lib/B", 2): ["B 02.mp3", "XY02 B.mp3"]}
    assert st.named_files("/lib/B", [1, 2, 1, 2], names) == [
        (1, "B 01.mp3"),
        (2, "B 02.mp3"),
        (1, "XY01 B.mp3"),
        (2, "XY02 B.mp3"),
    ]
    assert st.named_files("/lib/B", [3], names) == [(3, "file 3")]  # no name known
    assert st.named_files("/lib/B", [1, 1, 1], names)[2] == (
        1,
        "XY01 B.mp3",
    )  # more members than names: the last name again


def test_the_same_book_twice_under_two_naming_schemes_is_two_groups(s, tmp_path):
    for i in range(1, 7):
        content = f"chapter {i}".encode()  # identical bytes, so one file with two names
        for rel in (
            f"Books/Invented Book 06/Pat Author -06- Invented Book {i:03d}.mp3",
            f"Books/Invented Book 06/PAT06{i:02d} Pat Author -06- Invented Book (Unb)(Reader).mp3",
        ):
            p = tmp_path / rel
            p.parent.mkdir(parents=True, exist_ok=True)
            p.write_bytes(content)
    ids = fg.scan_files(s, str(tmp_path))
    assert len(set(ids.values())) == 6  # dedupe by content: twelve paths, six files
    folders = fg.group_by_folder(s, str(tmp_path), ids)
    top, totals = st.infer(s, folders)
    assert totals.get("book") == 2  # two copies of the book, not one book of twelve chapters
