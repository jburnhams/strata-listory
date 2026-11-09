"""Role labels explain contributors without dropping real coauthors."""

import pytest

from app import contributors as C


@pytest.mark.parametrize(
    "label,role",
    [
        ("foreword", "foreword"),
        ("Introduction", "introduction"),
        ("introductions", "introduction"),
        ("forewords", "foreword"),
        ("afterwords", "afterword"),
        ("afterword", "afterword"),
        ("translation", "translator"),
        ("editor", "editor"),
        ("illustrator", "illustrator"),
    ],
)
def test_explicit_contributor_suffixes_and_prefixes(label, role):
    for value in (f"Writer, Helper - {label}", f"Writer; Helper ({label})", f"Writer & {label} by Helper"):
        assert C.authors(value) == ["Writer"]
        assert C.auxiliary(value) == [dict(name="Helper", role=role)]


def test_unlabelled_coauthors_and_hyphenated_names_are_preserved():
    assert C.authors("Jean-Paul Sartre and Simone de Beauvoir") == ["Jean-Paul Sartre", "Simone de Beauvoir"]
    assert C.authors(["A Writer", "B Writer"]) == ["A Writer", "B Writer"]
    assert C.parse(None) == [] and C.parse(" , ; ") == []
    assert C.authors("A Writer - unknown role") == ["A Writer - unknown role"]
    roles = C.parse("Writer, Helper - foreword")
    assert C.authors(["Writer", "Helper"], roles) == ["Writer"]
    assert C.authors(["Writer", "Other"], roles) == ["Writer", "Other"]


def test_provider_lists_can_contain_combined_author_credits():
    assert C.authors(["Michael Diamond & Adam Horovitz"]) == ["Michael Diamond", "Adam Horovitz"]
    assert C.authors(["Michael Diamond and Adam Horovitz"]) == ["Michael Diamond", "Adam Horovitz"]
    assert C.authors(["Michael Diamond; Adam Horovitz"]) == ["Michael Diamond", "Adam Horovitz"]
    assert C.authors(["Herbert, Frank"]) == ["Herbert, Frank"]
    assert C.authors(["Writer & Helper - foreword"]) == ["Writer"]


def test_slash_joined_person_credits_keep_both_names_without_inventing_roles():
    assert C.parse("Muriel Spark/Pamela Garelick") == [
        dict(name="Muriel Spark", role="author"),
        dict(name="Pamela Garelick", role="author"),
    ]
    assert C.authors(["Peter Robison / Feodor Chin"]) == ["Peter Robison", "Feodor Chin"]
    assert C.authors("AC/DC") == ["AC/DC"]
    assert C.authors("http://DSPguru.doom9.net") == ["http://DSPguru.doom9.net"]
    assert C.authors("A/B Testing") == ["A/B Testing"]


@pytest.mark.parametrize("label", ["Narrated by:", "read by", "Narrator by", "Reader by"])
def test_explicit_reader_credit_cannot_supply_a_work_author(label):
    parsed = C.parse(label + " Alice Reader")
    assert C.authors(label + " Alice Reader") == []
    assert parsed[0]["name"] == "Alice Reader" and parsed[0]["role"] in ("narrator", "reader")


@pytest.mark.parametrize(
    "a,b,match",
    [
        ("Professor Dame Sue Black", "Sue Black", True),
        ("Dr. Susan Brown", "Sue Brown", True),
        ("Charles Klosterman", "Chuck Klosterman", True),
        ("Charles Klosterman", "Chuck Palahniuk", False),
        ("Susan Brown", "S. Brown", True),
        ("Susan Brown", "Suan Brown", True),
        ("Susan Browning", "Susan Browinng", True),
        ("Susan Browning", "Susan Browningg", True),
        ("Susan Brown", "Sue Black", False),
        ("Susan Brown", "Sarah Brown", False),
        ("Susan Brown", "Susan Browne", False),
        ("Susan Brown", "Suan Browning", False),
        ("Susan A Brown", "Susan Brown", False),
        ("", "", False),
        ("Brown", "Browne", False),
        ("Susan Brown", "Susan Bxxxxxx", False),
        ("Susan Browning", "Susan Browng", False),
    ],
)
def test_person_comparison_bounds_variants_without_changing_credits(a, b, match):
    assert C.same_person(a, b) == match
    assert C.same_person(b, a) == match
    assert C.authors([a]) == ([a] if a else [])


@pytest.mark.parametrize(
    "a,b,title,match",
    [
        ("Chester Klosterman", "Chuck Klosterman", True, True),
        ("Chester Klosterman", "Chuck Klosterman", False, False),
        ("Charles Klosterman", "Chuck Palahniuk", True, False),
        ("Thomas Klosterman", "Chuck Klosterman", True, False),
        ("Chester A Klosterman", "Chuck B Klosterman", True, False),
        ("Klosterman", "Chuck Klosterman", True, False),
        ("Anita Jones", "Alita Jones", True, True),
        ("Iain M. Banks", "Iain Banks", True, True),
        ("Charles Murray", "Charles A. Murray", True, True),
        ("Iain M. Banks", "Iain Banks", False, False),
        ("Iain M. Banks", "Iain N. Banks", True, False),
        ("Iain M. Banks", "Ian Banks", True, False),
        ("Charles A. Murray", "Charles Murry", True, False),
        ("Charles Alan Murray", "Charles Murray", True, False),
        ("C. A. Murray", "C. Murray", True, False),
        ("Charles 2 Murray", "Charles Murray", True, False),
        ("Charles A M", "Charles M", True, False),
    ],
)
def test_author_name_similarity_uses_strong_title_context(a, b, title, match):
    assert C.same_author(a, b, title) == match
    assert C.same_author(b, a, title) == match
