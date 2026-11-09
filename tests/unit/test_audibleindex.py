import json
import sqlite3
import zlib

from app.identify import audibleindex as ai


def archive(path):
    """The archive as scripts/fetch_audible_people.py writes it: product columns plus a credit table."""
    c = sqlite3.connect(path)
    c.execute(
        "CREATE TABLE product (asin TEXT NOT NULL, region TEXT NOT NULL, title TEXT, subtitle TEXT, authors TEXT, narrators TEXT, series TEXT, series_asin TEXT, series_position TEXT, runtime_min INTEGER, language TEXT, format_type TEXT, release_date TEXT, publisher TEXT, rating REAL, rating_count INTEGER, raw TEXT, fetched TEXT NOT NULL, isbn TEXT, PRIMARY KEY (asin, region))"
    )
    c.execute(
        "CREATE TABLE credit (asin TEXT NOT NULL, region TEXT NOT NULL, role TEXT NOT NULL, name TEXT NOT NULL, person_asin TEXT, PRIMARY KEY (asin, region, role, name))"
    )
    rows = [
        ("B000000001", "us", "The Gift of the Emperor", "", 64, "english", "unabridged", "9781407986197"),
        ("B000000002", "us", "Dune: Special Edition", "", 1260, "", "abridged", None),
        ("B000000001", "uk", "The Gift of the Emperor", "", 64, "english", "unabridged", "9781407986197"),
        ("B000000003", "uk", "Der Wüstenplanet", "", 1300, "german", "unabridged", None),
        ("B000000004", "de", "Elsewhere", "", 10, "english", "unabridged", None),
    ]
    for a, reg, t, sub, rt, lang, fmt, isbn in rows:
        c.execute(
            "INSERT INTO product (asin, region, title, subtitle, runtime_min, language, format_type, isbn, fetched, rating_count) VALUES (?,?,?,?,?,?,?,?,?,?)",
            (a, reg, t, sub, rt, lang, fmt, isbn, "2026-01-01", 3),
        )
    for a, reg, role, name, pa in [
        ("B000000001", "us", "Author", "E. W. Hornung", "A1"),
        ("B000000001", "us", "Narrator", "Gordon Griffin", None),
        ("B000000001", "uk", "Author", "E. W. Hornung", "A1"),
        ("B000000002", "us", "Author", "Frank Herbert", None),
        ("B000000002", "us", "Narrator", "Scott Brick", None),
        ("B000000003", "uk", "Author", "Frank Herbert", None),
    ]:
        c.execute("INSERT INTO credit VALUES (?,?,?,?,?)", (a, reg, role, name, pa))
    c.commit()
    c.close()


def built(tmp_path):
    archive(tmp_path / "a.sqlite")
    info = ai.build(tmp_path / "a.sqlite", tmp_path / "i.sqlite")
    assert info["books"] == 3  # both stores; German and other stores left out
    return ai.connect(tmp_path / "i.sqlite")


def test_asin_lookup_and_missing(tmp_path):
    c = built(tmp_path)
    assert ai.find(c, asin="b000000001")[0]["grade"] == "asin"
    assert (
        ai.find(c, asin="B999999999") == []
        and ai.find(c, title="") == []
        and ai.find(None, title="x", author="y") == []
    )


def test_title_alone_finds_candidates_with_unknown_author_and_conflicts_are_recorded(tmp_path):
    c = built(tmp_path)
    only = ai.find(c, title="Dune")
    assert (
        [r["asin"] for r in only] == ["B000000002"]
        and only[0]["evidence"]["author"] == "unknown"
        and only[0]["grade"] == "weak"
    )
    assert ai.find(c, title="Dune", author="Somebody Else")[0]["evidence"]["author"] == "conflict"
    assert ai.find(c, title="Nonexistent Book Title") == []
    assert ai.find(c, title="Gift Emperor")[0]["evidence"]["title"] == "fuzzy"


def test_author_and_title_is_weak_until_recording_agrees(tmp_path):
    c = built(tmp_path)
    assert [r["grade"] for r in ai.find(c, title="Gift of the Emperor", author="Hornung, E. W.")] == [
        "weak"
    ]  # article dropped, name reordered
    strong = ai.find(
        c, title="The Gift of the Emperor", author="E. W. Hornung", narrators=["Gordon Griffin"], runtime_min=64.5
    )[0]
    assert strong["grade"] == "strong" and strong["isbn"] == "9781407986197" and strong["snapshot"]
    conflict = ai.find(c, title="The Gift of the Emperor", author="E. W. Hornung", runtime_min=200)[0]
    assert conflict["grade"] == "weak" and conflict["evidence"]["runtime"] == "conflict"
    assert (
        ai.find(c, title="The Gift of the Emperor", author="E. W. Hornung", narrators=["Someone Else"])[0]["evidence"][
            "narrators"
        ]
        == "conflict"
    )


def test_main_title_match_is_not_strong(tmp_path):
    c = built(tmp_path)
    r = ai.find(c, title="Dune", author="Frank Herbert", narrators=["Scott Brick"])[0]
    assert r["evidence"]["title"] == "main" and r["grade"] == "weak" and r["abridged"] is True


def test_content_info_reads_the_content_type_keywords_and_publisher_by_asin(tmp_path):
    path = tmp_path / "a.sqlite"
    archive(path)
    c = sqlite3.connect(path)
    c.execute(
        "UPDATE product SET raw = ? WHERE asin = ? AND region = ?",
        (
            json.dumps(
                {
                    "product": {
                        "content_type": "Radio/TV Program",
                        "thesaurus_subject_keywords": ["dramatizations"],
                        "publisher_name": "BBC Audio",
                    }
                }
            ),
            "B000000001",
            "uk",
        ),
    )
    c.execute(
        "UPDATE product SET raw = ? WHERE asin = ? AND region = ?",
        (zlib.compress(json.dumps({"content_type": "Product"}).encode()), "B000000001", "us"),
    )
    c.execute("UPDATE product SET raw = ? WHERE asin = ?", ("not json", "B000000002"))
    c.commit()
    c.close()
    info = ai.content_info(["B000000001", "B000000002", "B0MISSING", "", None], archive=path)
    assert info == {
        "B000000001": dict(
            content_type="Radio/TV Program", keywords=["dramatizations"], publisher="BBC Audio", region="uk"
        )
    }  # the UK record wins, unreadable ones are skipped


def test_content_info_is_empty_without_an_archive_or_asins(tmp_path):
    assert (
        ai.content_info(["B000000001"], archive=tmp_path / "none.sqlite") == {}
        and ai.content_info([], archive=tmp_path / "none.sqlite") == {}
    )
    (tmp_path / "bad.sqlite").write_text("nope")
    assert ai.content_info(["B000000001"], archive=tmp_path / "bad.sqlite") == {}
