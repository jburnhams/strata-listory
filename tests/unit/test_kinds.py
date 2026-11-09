"""Book or radio / play / programme (`app.store.kinds`). Every title, name and tag here is invented, modelled on the shapes the real library has."""

import pytest

from app.store import kinds as K


def F(**kw):
    return K.Features(**kw)


names = lambda v: {s.name for s in v.signals}


# --- the shapes that were missed: the author or channel is the only clue


def test_a_series_credited_to_a_channel_is_a_radio_series():
    v = K.classify(
        F(
            label="Ada Penn: Greenhouse: Series 1",
            author="BBC Radio",
            narrator="BBC Radio 4",
            artist="BBC Radio",
            genre="Comedy",
            folder="Ada Penn - Greenhouse Series 1-2",
            minutes=221,
            files=8,
            part_minutes=[28.1, 27.9, 28.2, 28.0, 27.8, 28.0, 28.1, 28.0],
        )
    )
    assert (v.kind, v.band) == ("radio_series", "broadcast") and v.p_broadcast > 0.95
    assert {"channel", "series number", "slot-length parts"} <= names(v)


def test_a_play_with_a_bracketed_broadcast_channel_and_year_is_not_a_book():
    v = K.classify(
        F(
            label="The Signal and The Visit (BBC R4 2008)",
            author="Hana Vell",
            folder="Hana Vell - The Signal and The Visit (BBC R4 2008)",
            minutes=44,
            files=1,
        )
    )
    assert v.band == "broadcast" and v.kind != "book" and "channel" in names(v)


def test_a_collection_of_radio_adaptations_is_broadcast_even_with_a_book_title():
    v = K.classify(
        F(
            label="Pip Marlow 10 book collection 1-6",
            author="BBC Radio Adaptation",
            narrator="BBC Radio Adaptation",
            artist="BBC Radio Adaptation",
            genre="Audiobook",
            minutes=350,
            files=10,
            part_minutes=[29, 30, 28.5, 29.5, 30, 28, 29, 29.5, 30, 28],
        )
    )
    assert v.band == "broadcast" and "radio wording" in names(v)


def test_the_channel_in_the_artist_tag_alone_with_a_radio_genre():
    v = K.classify(F(label="Drama Hour", artist="BBC Radio 4", genre="Radio Drama", minutes=88, files=2))
    assert v.band == "broadcast" and v.kind == "play"


@pytest.mark.parametrize(
    "text",
    [
        "BBC R4 Drama",
        "BBC Radio 4 Extra",
        "BBC World Service",
        "Radio 3",
        "BBC iPlayer",
        "bbc.radio.4",
        "Radio 4 Extra",
    ],
)
def test_channel_names_in_any_spelling(text):
    assert "channel" in names(K.classify(F(label="Some Show", comment=text)))


def test_a_bare_radio_author_counts_but_less_than_a_channel():
    v = K.classify(F(label="Some Show", author="BBC Radio"))
    assert "radio credit" in names(v) and "channel" not in names(v)


# --- a reading is a book


def test_an_unabridged_reading_narrated_by_a_broadcaster_stays_a_book():
    v = K.classify(
        F(
            label="The Glass Orchard (Unabridged)",
            author="Mara Quill",
            narrator="Ada Penn",
            publisher="BBC Audio",
            tag_publisher="BBC Audiobooks",
            genre="Audiobook",
            minutes=606,
            files=12,
            part_minutes=[50] * 12,
        )
    )
    assert (v.kind, v.band) == ("book", "book") and {"unabridged", "long"} <= names(v)


def test_bbc_as_a_publisher_or_author_alone_is_not_a_channel():
    v = K.classify(F(label="A Short History", author="BBC", publisher="BBC Audio", minutes=300, files=5))
    assert "channel" not in names(v) and v.kind == "book"


def test_a_radio_collection_of_readings_is_flagged_for_review_not_reclassified():
    v = K.classify(
        F(
            label="Tales for Tuesday: A BBC Radio Collection: Classic Readings from the Archive",
            author="Mara Quill",
            artist="Mara Quill - Read by Ada Penn",
            genre="Audiobook",
            minutes=797,
            files=1,
        )
    )
    assert v.kind == "book" and "reading" in names(v) and "radio wording" in names(v)


def test_a_matching_audible_audiobook_pulls_a_structure_guess_back_to_a_book():
    v = K.classify(
        F(
            structure_kind="programme",
            label="Six Nights",
            genre="Audiobook",
            asin="B0TESTTEST",
            minutes=300,
            files=10,
            part_minutes=[30] * 10,
            audible=[dict(asin="B0TESTTEST", content_type="Product", diff=0.01, p=0.99)],
        )
    )
    assert v.kind == "book" and "audible audiobook" in names(v)


def test_a_matching_audible_radio_programme_is_evidence_for_broadcast():
    v = K.classify(
        F(
            label="Quiet Rooms",
            minutes=88,
            files=1,
            genre="Drama",
            audible=[dict(asin="B0AAAAAAAA", content_type="Radio/TV Program", diff=0.005, p=0.9)],
        )
    )
    assert "audible content type" in names(v) and v.band == "broadcast" and v.kind == "play"


def test_audible_evidence_is_ignored_when_the_match_is_doubtful_or_the_runtime_differs():
    base = dict(label="Quiet Rooms", minutes=88, files=1)
    assert "audible content type" not in names(
        K.classify(F(**base, audible=[dict(content_type="Radio/TV Program", diff=0.005, p=0.2)]))
    )
    assert "audible content type" not in names(
        K.classify(F(**base, audible=[dict(content_type="Radio/TV Program", diff=0.4, p=0.9)]))
    )


def test_audible_listing_the_same_length_as_both_book_and_programme_decides_nothing():
    v = K.classify(
        F(
            label="Quiet Rooms",
            minutes=88,
            files=1,
            audible=[
                dict(content_type="Product", diff=0.005, p=0.9),
                dict(content_type="Radio/TV Program", diff=0.008, p=0.9),
            ],
        )
    )
    assert "audible disagrees" in names(v) and not names(v) & {"audible audiobook", "audible content type"}


@pytest.mark.parametrize(
    "ct, expected",
    [
        ("Radio/TV Program", "broadcast"),
        ("Product", "book"),
        ("Show", "show"),
        ("Performance", "performance"),
        ("Lecture", "book"),
        ("Something Else", ""),
    ],
)
def test_audible_content_types(ct, expected):
    assert K._content_class(ct) == expected


# --- structure, length and the catalogues


def test_the_structure_step_is_only_a_prior_that_other_evidence_can_overturn():
    solo = K.classify(F(structure_kind="programme", label="Numbered Parts", files=4, minutes=100))
    assert solo.band == "review" and solo.kind != "book"  # kept as the structure step made it, flagged
    long_read = K.classify(
        F(structure_kind="programme", label="Numbered Parts", genre="Audiobook", files=12, minutes=900, asin="B0X")
    )
    assert long_read.kind == "book"


def test_a_long_listen_is_a_book_unless_it_is_an_archive_of_broadcasts():
    assert "long" in names(K.classify(F(label="Huge Novel", minutes=900, files=30)))
    assert "long" not in names(K.classify(F(label="Years of Shows", artist="BBC Radio 4", minutes=900, files=30)))


def test_slot_length_parts_need_three_files_mostly_of_broadcast_length():
    assert "slot-length parts" in names(K.classify(F(label="X", files=4, minutes=116, part_minutes=[29, 29, 29, 29])))
    assert "slot-length parts" not in names(K.classify(F(label="X", files=2, minutes=58, part_minutes=[29, 29])))
    assert "slot-length parts" not in names(
        K.classify(F(label="X", files=4, minutes=200, part_minutes=[29, 51, 49, 71]))
    )


def test_a_programme_catalogue_hit_adds_moderate_evidence_and_more_when_it_is_a_drama_and_on_the_bbc():
    one = K.classify(F(label="Harbour Lights", radio_hits=[dict(source="wikidata_radio", title="Harbour Lights")]))
    both = K.classify(
        F(
            label="Harbour Lights",
            radio_hits=[
                dict(source="bbc_radio", title="Harbour Lights"),
                dict(source="wikidata_drama", title="Harbour Lights"),
            ],
        )
    )
    assert "programme catalogue" in names(one) and one.band == "review" and both.p_broadcast > one.p_broadcast


def test_episode_wording_and_broadcast_dates():
    assert "episode number" in names(K.classify(F(label="Show - Episode 3")))
    assert "broadcast date" in names(K.classify(F(label="Show 2012-01-03")))
    assert "episode number" not in names(
        K.classify(F(label="Money Mischief Episodes in Monetary History"))
    )  # prose, not numbering


def test_strand_names_and_dramatisation_wording():
    assert "strand" in names(K.classify(F(label="Afternoon Play - The Locked Door")))
    assert "dramatised" in names(K.classify(F(label="Quiet Town", comment="A full cast dramatisation by Ida Fenn")))
    assert "dramatised" not in names(K.classify(F(label="Quiet Town", narrator="Full cast")))
    assert "full cast" in names(K.classify(F(label="Quiet Town", narrator="Full cast")))


# --- the shape of the result


def test_a_book_with_no_evidence_is_a_confident_book_with_a_prior_only():
    v = K.classify(F(label="Plain Novel", author="Ida Fenn", minutes=420, files=14))
    assert v.kind == "book" and v.band == "book" and v.confidence == pytest.approx(1 - v.p_broadcast)


def test_a_weak_signal_alone_makes_a_close_call_that_stays_a_book():
    v = K.classify(F(label="One Voice", genre="Comedy", minutes=60, files=1))
    assert (
        v.band == "review"
        and v.kind == "book"
        and K._kind(F(label="One Voice", genre="Comedy", minutes=60, files=1), v.signals) == "radio_show"
    )


def test_the_kind_distinguishes_series_play_and_programme():
    series = K.classify(F(label="Greenhouse: Series 2", artist="BBC Radio 4", files=6, minutes=170))
    play = K.classify(F(label="The Locked Door", artist="BBC Radio 4", genre="Radio Drama", minutes=60, files=1))
    show = K.classify(F(label="Harbour Talk", artist="BBC World Service", minutes=60, files=1))
    assert (series.kind, play.kind, show.kind) == ("radio_series", "play", "radio_show")


def test_the_verdict_serialises_with_its_reasons():
    d = K.classify(F(label="Greenhouse: Series 2", artist="BBC Radio 4", files=6, minutes=170)).as_dict()
    assert (
        d["kind"] == "radio_series"
        and d["version"] == K.VERSION
        and {s["name"] for s in d["signals"]} >= {"channel", "series number"}
    )
    assert all(set(s) == {"name", "weight", "detail"} for s in d["signals"])


def test_label_of_names_the_kinds():
    assert [K.label_of(k) for k in ("book", "radio_series", "radio_show", "play", "other")] == [
        "book",
        "radio series",
        "radio programme",
        "play",
        "other",
    ]
