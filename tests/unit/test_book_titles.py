"""Full, incomplete and author-prefixed titles retain subtitle ambiguity checks."""

import pytest

from app import book_titles as B


def c(title, subtitle="", authors=None):
    return dict(title=title, subtitle=subtitle, authors=authors or ["Writer"])


@pytest.mark.parametrize(
    "wanted,found,accepted",
    [
        ("A Very Stable Genius: Testing of America", c("A Very Stable Genius"), True),
        ("History: Ancient World", c("History", "Modern World"), False),
        ("History: Ancient World", c("History: Ancient World"), True),
        ("Sherlock Holmes: The Definitive Collection", c("Sherlock Holmes The Definitive Collection"), True),
        (
            "Sherlock Holmes: The Definitive Collection",
            c("Sherlock Holmes The Definitive Audio Collection Part Two"),
            False,
        ),
        ("History Volume 1: Ancient World", c("History Volume 2 Ancient World"), False),
        ("History", c("History", "Ancient World"), True),
        ("Chi Running", c("ChiRunning"), True),
        (
            "Chuck Klosterman IV: A Decade of Curious People",
            c("IV", "A Decade of Curious People", ["Chuck Klosterman"]),
            True,
        ),
        (
            "A Decade of Curious People",
            c("Chuck Klosterman IV", "A Decade of Curious People", ["Chuck Klosterman"]),
            True,
        ),
        (
            "Chuck Klosterman IV: A Decade of Curious People",
            c("Chuck Klosterman V", "A Decade of Curious People", ["Chuck Klosterman"]),
            False,
        ),
        ("Different Title: Ancient World", c("History", "Ancient World"), False),
    ],
)
def test_title_matching_handles_missing_subtitles_spacing_and_author_prefixes(wanted, found, accepted):
    assert (B.analyse(wanted, found, ", ".join(found["authors"]))["score"] >= 0.85) == accepted


def test_missing_subtitle_is_rejected_when_other_subtitles_disambiguate_books():
    candidates = [c("History"), c("History", "Ancient World"), c("History", "Modern World")]
    assert B.analyse("History", candidates[0], "Writer", candidates)["score"] == 0
    assert B.analyse("History: Lost World", candidates[0], "Writer", candidates)["score"] == 0
    assert B.analyse("History: Ancient World", candidates[1], "Writer", candidates)["score"] == 1
    assert B.analyse("History: Ancient World", candidates[2], "Writer", candidates)["score"] == 0


def test_recording_subtitle_supplies_punctuation_for_both_joined_work_titles():
    joined = c("Sherlock Holmes The Definitive Collection")
    recording = c("Sherlock Holmes: The Definitive Collection")
    assert B.analyse(joined["title"], joined, "Writer", [recording, joined])["score"] == 1


def test_subtitle_only_match_does_not_choose_between_different_volumes():
    candidates = [
        c("Chuck Klosterman IV", "Collected essays", ["Chuck Klosterman"]),
        c("Chuck Klosterman V", "Collected essays", ["Chuck Klosterman"]),
    ]
    assert B.analyse("Collected essays", candidates[0], "Chuck Klosterman", candidates)["score"] == 0


@pytest.mark.parametrize("wanted,found", [("Stories 1", "Stories"), ("Stories 2", "Stories 3"), ("1984", "1985")])
def test_fuzzy_title_matching_cannot_erase_a_volume_or_title_number(wanted, found):
    assert B.analyse(wanted, c(found), "Writer")["score"] == 0


def test_volume_label_spelling_does_not_change_the_numbered_work_identity():
    assert B.analyse("Stories 2", c("Stories Volume 2"), "Writer")["score"] == 1
    assert B.analyse("Stories Volume 2", c("Stories Vol. 3"), "Writer")["score"] == 0


def linked(subtitle, work="/works/OL1W", edition="/books/OL1M", **kwargs):
    return dict(c("History", subtitle), source="openlibrary", id=work, subtitle_edition=edition, **kwargs)


def test_attached_edition_subtitles_identify_one_original_work():
    english = linked("Ancient World")
    translated = linked("El mundo antiguo", edition="/books/OL2M")
    other_source = dict(c("History", "Ancient World"), source="hardcover", id="99")
    cohort = [english, translated, other_source]
    for record in cohort:
        assert B.analyse("History", record, "Writer", cohort)["score"] == 0.96
    match = B.analyse("History: Ancient World", translated, "Writer", cohort)
    assert match == dict(score=0.96, basis="linked editions of the same original work", reason="")
    assert B.analyse("History: Unknown Subtitle", translated, "Writer", cohort)["score"] == 0


@pytest.mark.parametrize(
    "field,value",
    [
        ("id", "/works/OL2W"),
        ("id", "1"),
        ("subtitle_edition", ""),
        ("subtitle_edition", "unverified"),
        ("source", "hardcover"),
        ("authors", ["Other Writer"]),
        ("title", "Another History"),
    ],
)
def test_subtitle_equivalence_requires_explicit_same_work_edition_links(field, value):
    english = linked("Ancient World")
    other = linked("Modern World", edition="/books/OL2M")
    other[field] = value
    cohort = [english, other]
    # Without a verifiable bridge, differing subtitles cannot stand in for each other.
    assert B.analyse("History: Ancient World", other, "Writer", cohort)["score"] == 0
    if field not in ("authors", "title"):
        assert B.analyse("History", english, "Writer", cohort)["score"] == 0


def test_linked_edition_variants_do_not_hide_another_distinct_work():
    cohort = [
        linked("Ancient World"),
        linked("El mundo antiguo", edition="/books/OL2M"),
        linked("Modern World", work="/works/OL2W", edition="/books/OL3M"),
    ]
    assert B.analyse("History", cohort[0], "Writer", cohort)["score"] == 0
    assert B.analyse("History: Modern World", cohort[0], "Writer", cohort)["score"] == 0
    assert B.analyse("History: Ancient World", cohort[1], "Writer", cohort)["score"] == 0.96


@pytest.mark.parametrize("subtitle", ["Ancient World: Volume 2", "Ancient World: Volume 1"])
def test_work_links_cannot_erase_explicit_or_missing_volume_numbers(subtitle):
    cohort = [linked("Ancient World"), linked(subtitle, edition="/books/OL2M")]
    assert B.analyse("History", cohort[0], "Writer", cohort)["score"] == 0
    assert B.analyse("History: Ancient World", cohort[1], "Writer", cohort)["score"] == 0
    assert B.analyse("History Volume 1", dict(cohort[0], title="History Volume 2"), "Writer", cohort)["score"] == 0


def test_same_work_bridges_merge_transitively_across_duplicate_catalogue_records():
    cohort = [
        linked("Ancient World"),
        linked("El mundo antiguo", edition="/books/OL2M"),
        linked("Ancient World", work="/works/OL2W", edition="/books/OL3M"),
        linked("Le monde antique", work="/works/OL2W", edition="/books/OL4M"),
    ]
    assert B.analyse("History", cohort[0], "Writer", cohort)["score"] == 0.96
    assert B.analyse("History: Le monde antique", cohort[1], "Writer", cohort)["score"] == 0.96
