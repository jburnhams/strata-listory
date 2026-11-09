import sqlite3

from app.identify import audible, context
from app.identify import audibleindex as ai
from app.identify import features as ft


def index(tmp_path):
    c = sqlite3.connect(tmp_path / "a.sqlite")
    c.execute(
        "CREATE TABLE product (asin TEXT, region TEXT, title TEXT, subtitle TEXT, series TEXT, series_asin TEXT, series_position TEXT, runtime_min INTEGER, language TEXT, format_type TEXT, release_date TEXT, publisher TEXT, rating_count INTEGER, isbn TEXT)"
    )
    c.execute("CREATE TABLE credit (asin TEXT, region TEXT, role TEXT, name TEXT, person_asin TEXT)")
    c.execute(
        "INSERT INTO product (asin, region, title, series, runtime_min, language, format_type, isbn) VALUES ('B000000001', 'us', 'Census', 'S', 300, 'english', 'unabridged', '9780141039596')"
    )
    c.executemany(
        "INSERT INTO credit VALUES (?,?,?,?,?)",
        [("B000000001", "us", "Author", "Jesse Ball", None), ("B000000001", "us", "Narrator", "Some Reader", None)],
    )
    c.commit()
    c.close()
    ai.build(tmp_path / "a.sqlite", tmp_path / "i.sqlite")
    return ai.connect(tmp_path / "i.sqlite")


def test_lookup_features_and_reading(tmp_path):
    c = index(tmp_path)
    # a title alone still finds the recording, but carries no corroboration and steers nothing
    bare = audible.lookup(dict(title="Census", author="", narrator="", runtime_min=None), c)
    assert (
        bare["grade"] == "weak"
        and audible.reading(dict(title="Census"), bare) is None
        and audible.features(bare)["audible_author_unknown"] == 1
        and audible.features(bare)["audible_strong"] == 0
    )
    # one-word title, no author, but narrator and runtime agree: corroborated, so the audiobook's author drives the book search
    census = audible.lookup(dict(title="Census", author="", narrator="Some Reader", runtime_min=301), c)
    r = audible.reading(dict(title="Census", author=""), census)
    assert r["reading"] == "audible" and r["author"] == "Jesse Ball" and r["isbns"] == ["9780141039596"]
    # a conflicting author or runtime is never corroborated
    assert (
        audible.reading(
            dict(title="Census"),
            audible.lookup(dict(title="Census", author="Someone Else", narrator="Some Reader", runtime_min=301), c),
        )
        is None
    )
    assert (
        audible.reading(
            dict(title="Census"),
            audible.lookup(dict(title="Census", author="", narrator="Some Reader", runtime_min=900), c),
        )
        is None
    )
    hit = audible.lookup(
        dict(title="Census", author="Jesse Ball", narrator="Some Reader", runtime_min=301, language="english"), c
    )
    assert hit["grade"] == "strong"
    f = audible.features(hit, dict(language="English"))
    assert (
        f["audible_strong"] == 1
        and f["audible_author_match"] == 1
        and f["audible_narrator_match"] == 1
        and f["audible_runtime_match"] == 1
        and f["audible_has_isbn"] == 1
        and f["audible_language_match"] == 1
        and f["audible_series_known"] == 1
    )
    assert (
        set(audible.features(None)) == set(audible.FEATURES)
        and not any(audible.features(None).values())
        and not audible.corroborated(None)
    )


def test_no_index_means_no_hit_and_features_are_in_the_model(tmp_path, monkeypatch):
    audible.reset()
    monkeypatch.setattr(ai, "connect", lambda *a, **k: None)
    assert audible.lookup(dict(title="x", author="y")) is None
    audible.reset()
    assert "audible_found" in ft.feature_names() and "reading_audible" in ft.feature_names()


def test_context_facts():
    assert context.facts(
        dict(language="english"), dict(representations=[dict(duration=3600), dict(duration=7200)])
    ) == dict(runtime_min=120.0, language="english")
    assert context.facts(None, None) == dict(runtime_min=None, language="")
