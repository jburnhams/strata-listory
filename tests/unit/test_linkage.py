"""Listen identification as probabilistic record linkage (`app.linkage`): readings, work clusters, comparison levels, the EM-fitted model and the labelled-data helpers. Every title and name here is invented."""

import random
import sqlite3

import pytest

from app.identify import audibleindex as ai
from app.linkage import candidates as C
from app.linkage import compare as K
from app.linkage import dataset as D
from app.linkage import model as M
from app.linkage import query as Q

# ------------------------------------------------------------------ readings


def test_clean_title_drops_recording_labels_only():
    assert Q.clean_title("The Glass Orchard (Unabridged)") == "The Glass Orchard"
    assert Q.clean_title("Ferryman of Lune_unabridged") == "Ferryman of Lune"
    assert Q.clean_title("Salt Roads 10th Anniversary Edition") == "Salt Roads"
    assert Q.clean_title("Volume 2 (MP3)") == "Volume 2"


def test_readings_cover_tags_folder_and_swapped_names_without_duplicates():
    tags = dict(
        tagged=10,
        album=dict(value="The Glass Orchard", share=1.0),
        album_artist=dict(value="Mara Quill", share=1.0),
        title=dict(value="Track 01", share=0.1),
    )
    folder = dict(
        title="Mara Quill",
        author="The Glass Orchard (Read by Tom Vale)",
        folder="The Glass Orchard (Read by Tom Vale) - Mara Quill",
    )
    rs = Q.make_readings(dict(title="The Glass Orchard", author="Mara Quill"), tags, folder)
    pairs = [(r.title, r.author, r.source) for r in rs]
    assert pairs[0] == ("The Glass Orchard", "Mara Quill", "chosen")
    assert ("The Glass Orchard", "Mara Quill", "tags") not in pairs  # same as the chosen reading
    assert any(src == "swapped" and t.startswith("The Glass Orchard") for t, _a, src in pairs)
    assert all(t != "Track 01" for t, _a, _s in pairs)


def test_generic_container_folder_is_not_a_title():
    rs = Q.make_readings({}, {}, dict(title="Books", folder="Books"))
    assert rs == []


def test_query_from_parts_reads_narrator_asin_runtime_and_bracket_hints():
    q = Q.from_parts(
        7,
        "book",
        "x",
        dict(title="Salt Roads (Ada Penn)", author="Mara Quill", year=2011, asin="B0ABCDEFGH"),
        dict(narratedby=dict(value="Ada Penn")),
        {},
        [dict(minutes=600, path="/srv/audio/Books/Salt Roads"), dict(minutes=590)],
    )
    assert (
        q.narrators == ["Ada Penn"]
        and q.asin == "B0ABCDEFGH"
        and q.year == 2011
        and q.minutes == [600.0, 590.0]
        and q.hints == ["Ada Penn"]
    )
    assert any(r.title == "Salt Roads" for r in q.readings)  # the bracket is also tried without
    assert Q.from_parts(1, "book", "", {}, {}, {}, []).path == ""


def test_relpath_ignores_the_mount_point():
    assert (
        Q.relpath("/home/someone/audio-local/Books/A/b.mp3")
        == Q.relpath("/home/someone/audio/Books/A/b.mp3")
        == "Books/A/b.mp3"
    )
    assert Q.relpath("/elsewhere/A/b.mp3") == "A/b.mp3"


# ------------------------------------------------------------------ clusters (Audible part from a small built index)


def audible(tmp_path):
    path = tmp_path / "a.sqlite"
    c = sqlite3.connect(path)
    c.execute(
        "CREATE TABLE product (asin TEXT, region TEXT, title TEXT, subtitle TEXT, authors TEXT, narrators TEXT, series TEXT, series_asin TEXT, series_position TEXT, runtime_min INTEGER, language TEXT, format_type TEXT, release_date TEXT, publisher TEXT, rating REAL, rating_count INTEGER, raw TEXT, fetched TEXT, isbn TEXT)"
    )
    c.execute("CREATE TABLE credit (asin TEXT, region TEXT, role TEXT, name TEXT, person_asin TEXT)")
    books = [
        ("B0AAAAAAA1", "The Glass Orchard", 600, "Mara Quill", "Tom Vale", "9780306406157"),
        ("B0AAAAAAA2", "The Glass Orchard", 420, "Mara Quill", "Ada Penn", None),
        ("B0AAAAAAA3", "The Glass Orchard", 300, "Other Person", "Tom Vale", None),
        ("B0AAAAAAA4", "Salt Roads", 500, "Mara Quill", "Tom Vale", None),
    ]
    for a, t, rt, au, na, isbn in books:
        c.execute(
            "INSERT INTO product (asin, region, title, subtitle, runtime_min, language, format_type, release_date, rating_count, isbn, fetched) VALUES (?,?,?,?,?,?,?,?,?,?,?)",
            (a, "uk", t, "", rt, "english", "unabridged", "2015-03-01", 10, isbn, "x"),
        )
        c.execute("INSERT INTO credit VALUES (?,?,?,?,?)", (a, "uk", "Author", au, None))
        c.execute("INSERT INTO credit VALUES (?,?,?,?,?)", (a, "uk", "Narrator", na, None))
    c.commit()
    c.close()
    ai.build(path, tmp_path / "i.sqlite")
    return ai.connect(tmp_path / "i.sqlite")


def work(key, title, authors, editions=5, year=1990):
    from app.identify import textkeys as tk

    return dict(
        key=key,
        title=title,
        subtitle="",
        year=year,
        editions=editions,
        audio_editions=0,
        ratings=0,
        rating_average=None,
        authors=[dict(key="/authors/" + a[:3], name=a) for a in authors],
        titles={(tk.title_key(title), 0): editions},
        series=[],
        isbns=[],
        subjects=[],
    )


def test_audible_candidates_and_clusters_join_recordings_of_one_work(tmp_path):
    ac = audible(tmp_path)
    q = Q.Query(1, "book", readings=[Q.Reading("The Glass Orchard", "Mara Quill")], minutes=[598.0])
    found = C.audible_candidates(ac, q)
    assert (
        set(found) == {"B0AAAAAAA1", "B0AAAAAAA2", "B0AAAAAAA3"}
        and found["B0AAAAAAA1"].via == {"title", "author"}
        and found["B0AAAAAAA3"].via == {"title"}
    )  # the author's other book is not similar enough
    works = {
        "/works/OL1W": work("/works/OL1W", "The Glass Orchard", ["Mara Quill"]),
        "/works/OL2W": work("/works/OL2W", "Glass Orchard", ["M. Quill"], editions=1),
    }
    cl = C.clusters(None, works, found)
    by = {tuple(sorted(w["key"] for w in c.works)) + tuple(sorted(p.asin for p in c.products)): c for c in cl}
    main = next(c for c in cl if c.works)
    assert {w["key"] for w in main.works} == {
        "/works/OL1W",
        "/works/OL2W",
    }  # duplicate catalogue works with a shared author (initial form)
    assert {p.asin for p in main.products} == {"B0AAAAAAA1", "B0AAAAAAA2"}  # both recordings, different narrators
    assert main.key == "/works/OL1W" and main.title == "The Glass Orchard" and main.year == 1990
    other = next(c for c in cl if [p.asin for p in c.products] == ["B0AAAAAAA3"])
    assert other.key == "asin:B0AAAAAAA3" and other.title == "The Glass Orchard" and other.authors == ["Other Person"]
    assert len(by) == len(cl) == 2
    assert C.audible_candidates(None, q) == {} and C.ol_candidates(None, q, found) == {}


def test_asin_and_full_text_routes(tmp_path):
    ac = audible(tmp_path)
    assert (
        "asin"
        in C.audible_candidates(ac, Q.Query(1, "book", readings=[Q.Reading("Nothing Like It")], asin="B0AAAAAAA4"))[
            "B0AAAAAAA4"
        ].via
    )
    words = C.audible_candidates(ac, Q.Query(1, "book", readings=[Q.Reading("Roads, Salt")]))
    assert list(words) == ["B0AAAAAAA4"] and words["B0AAAAAAA4"].via == {"words"}


# ------------------------------------------------------------------ comparison levels


def cluster(
    title="The Glass Orchard", authors=("Mara Quill",), runtime=600, narrators=("Tom Vale",), year=1990, editions=5
):
    products = (
        [
            C.Product(
                "B0AAAAAAA1",
                title,
                "",
                K.tk.title_key(title),
                K.tk.title_main(title),
                list(authors),
                list(narrators),
                runtime,
                "",
                2015,
                10,
                "",
                "",
                "unabridged",
            )
        ]
        if runtime
        else []
    )
    return C.Cluster([work("/works/OL1W", title, list(authors), editions, year)] if editions else [], products)


def test_title_levels_depend_on_how_common_the_title_is():
    c = cluster().title_keys()
    assert K.title_level(Q.Reading("The Glass Orchard"), c, 1)[0] == "exact_rare"
    assert K.title_level(Q.Reading("Glass Orchard"), c, 9)[0] == "exact_common"
    assert K.title_level(Q.Reading("the glass orchard"), c, 99)[0] == "exact_generic"
    assert K.title_level(Q.Reading("The Glass Orchard: A Novel"), c, 1)[0] == "main"
    assert K.title_level(Q.Reading("The Glass Orchards"), c, 1)[0] == "close"
    assert K.title_level(Q.Reading("Glass Orchard Winter"), c, 1)[0] == "partial"
    assert K.title_level(Q.Reading("Completely Different"), c, 1)[0] == "weak"
    assert K.title_level(Q.Reading(""), c, 1) == (None, "")


def test_author_narrator_runtime_year_and_numbers():
    c = cluster(authors=("Mara Quill", "Ben Ash"))
    assert K.author_level(Q.Reading("t", "Mara Quill"), c) == "all"
    assert K.author_level(Q.Reading("t", "Mara Quill, Zed Who"), c) == "some"
    assert K.author_level(Q.Reading("t", "M. Quill"), c) == "surname"
    assert K.author_level(Q.Reading("t", "Zed Who"), c) == "conflict"
    assert K.author_level(Q.Reading("t", ""), c) is None
    q = Q.Query(1, "book", narrators=["Tom Vale"], minutes=[606.0], year=2015)
    assert (
        K.narrator_level(q, c) == "match"
        and K.narrator_level(Q.Query(1, "book", narrators=["Ada Penn"]), c) == "conflict"
        and K.narrator_level(Q.Query(1, "book"), c) is None
    )
    assert K.narrator_level(Q.Query(1, "book", hints=["Tom Vale"]), c) == "match"
    assert (
        K.runtime_level(q, c) == ("1pct", "B0AAAAAAA1")
        and K.runtime_level(Q.Query(1, "book", minutes=[400.0]), c)[0] == "far"
        and K.runtime_level(Q.Query(1, "book"), c) == (None, "")
    )
    assert [K.runtime_level(Q.Query(1, "book", minutes=[m]), c)[0] for m in (620.0, 660.0, 760.0)] == [
        "4pct",
        "12pct",
        "30pct",
    ]
    assert [K.year_level(Q.Query(1, "book", year=y), c) for y in (1991, 1994, 2015, 1960, None)] == [
        "same",
        "close",
        "later",
        "earlier",
        None,
    ]
    assert (
        K.numbers_level(Q.Reading("Saga 2"), "saga 2") == "agree"
        and K.numbers_level(Q.Reading("Saga 2"), "saga 3") == "differ"
    )
    assert (
        K.numbers_level(Q.Reading("Saga 2"), "saga") == "local_only"
        and K.numbers_level(Q.Reading("Saga"), "saga 2") == "other_only"
        and K.numbers_level(Q.Reading("Saga 1984"), "saga") is None
    )


def test_compare_vector_and_popularity():
    q = Q.Query(1, "book", narrators=["Tom Vale"], minutes=[600.0], asin="B0AAAAAAA1")
    v = K.compare(q, Q.Reading("The Glass Orchard", "Mara Quill", "tags"), cluster(), 1)
    assert {k: v[k] for k in K.FIELDS} == dict(
        title="exact_rare",
        author="all",
        narrator="match",
        runtime="1pct",
        asin="match",
        year=None,
        numbers=None,
        sources="both",
        popularity="medium",
        channel=None,
        form=None,
    )  # the radio fields are empty for a book candidate without a kind verdict
    assert v["_asin"] == "B0AAAAAAA1" and v["_reading"] == "tags"
    assert K.compare(Q.Query(1, "book", asin="B0ZZZZZZZZ"), Q.Reading("x"), cluster(), 1)["asin"] == "other"
    assert (
        K.popularity_level(cluster(editions=30)) == "high"
        and K.popularity_level(cluster(editions=1, runtime=None)) == "low"
        and K.popularity_level(C.Cluster()) == "none"
    )
    assert K.compare(Q.Query(1, "book"), Q.Reading("x"), cluster(editions=0), 1)["sources"] == "audible"


# ------------------------------------------------------------------ the model

GOOD = dict(title="exact_rare", author="all", runtime="1pct", sources="both", popularity="medium")
BAD = [
    dict(title="exact_common", author="conflict", sources="openlibrary", popularity="low"),
    dict(title="weak", author="all", sources="audible", popularity="low"),
    dict(title="partial", author="conflict", runtime="far", sources="both", popularity="none"),
]


def synthetic(n=120, seed=0):
    """Listens whose right work agrees on title, author and runtime, among plausible wrong ones; a tenth have no right work at all."""
    rnd = random.Random(seed)
    out = []
    for i in range(n):
        cands = {f"w{i}-{j}": [dict(b)] for j, b in enumerate(rnd.sample(BAD, 2))}
        if i % 10:
            good = dict(GOOD)
            good["runtime"] = rnd.choice(["1pct", "1pct", "4pct", None])
            cands[f"w{i}-ok"] = [dict(good, author=None), good]  # two readings: the better one counts
        out.append(
            M.Case(
                i,
                cands,
                (f"w{i}-ok" if i % 10 else "none") if i < 40 else None,
                origin="manual" if i < 20 else "automatic",
            )
        )
    return out


def test_em_learns_that_agreement_is_evidence_and_posteriors_rank_the_right_work():
    cases = synthetic()
    params = M.train(cases)
    bits = params.explain()
    assert (
        bits["title"]["exact_rare"] > 0 > bits["title"]["weak"]
        and bits["author"]["all"] > 0 > bits["author"]["conflict"]
        and bits["runtime"]["1pct"] > bits["runtime"]["far"]
    )
    assert 0.7 < params.prior < 1 and params.temperature in M.TEMPERATURES
    post, none, used = M.posterior(params, cases[45].candidates)
    assert max(post, key=post.get) == "w45-ok" and post["w45-ok"] > 0.9 and used["w45-ok"]["author"] == "all"
    post, none, _ = M.posterior(params, cases[50].candidates)  # no right work among them
    assert none > max(post.values())
    assert M.posterior(params, {}) == ({}, 1.0, {})
    again = M.Params.from_json(params.to_json())
    assert again.version() == params.version() and again.version().startswith("fs-")


def test_decide_accept_review_none():
    assert M.decide({"a": 0.97, "b": 0.01}, 0.02, 0.95) == ("accept", "a")
    assert M.decide({"a": 0.97, "b": 0.01}, 0.02, 0.95, margin=0.99) == ("review", "a")
    assert M.decide({"a": 0.6, "b": 0.3}, 0.1, 0.95) == ("review", "a")
    assert M.decide({"a": 0.01}, 0.99, 0.95) == ("none", None) and M.decide({}, 1.0, 0.95) == ("none", None)


def test_labels_are_clamped_and_negatives_zeroed():
    params = M.initial()
    case = M.Case(1, {"a": [GOOD], "b": [BAD[0]]}, negatives={"a"})
    post, _ = M._responsibilities(params, case, True)
    assert post["a"] == 0.0
    post, _ = M._responsibilities(params, M.Case(1, {"a": [GOOD], "b": [BAD[0]]}, "b"), True)
    assert post == {"a": 0.0, "b": 1.0}
    assert M.calibrate(params, [case]) is params  # nothing labelled: unchanged


def test_cross_validation_hides_each_folds_labels_and_reports_calibration():
    cases = synthetic(200, seed=1)
    pred = M.cross_validate(cases, k=4)
    assert len(pred) == 40 and {p["origin"] for p in pred} == {"manual", "automatic"}
    s = M.summarise(pred)
    assert (
        s["listens"] == 40
        and s["top1"] >= 0.85
        and s["at"]["0.95"]["false_positives"] == 0
        and 0 <= s["ece"] < 0.3
        and s["reliability"]
    )
    assert M.summarise([]) == dict(listens=0)


# ------------------------------------------------------------------ labelled data


def test_positive_key_by_work_key_redirect_or_title_and_author():
    clusters = {
        "/works/OL1W": dict(ol=["/works/OL1W"], title="The Glass Orchard", authors=["Mara Quill"]),
        "asin:B0X": dict(ol=[], title="Salt Roads", authors=["Mara Quill"]),
    }
    assert D.positive_key(clusters, dict(work="/works/OL1W")) == "/works/OL1W"
    assert D.positive_key(clusters, dict(work="/works/OL9W"), canonical=lambda w: "/works/OL1W") == "/works/OL1W"
    assert D.positive_key(clusters, dict(title="Salt Roads", authors=["M. Quill"])) == "asin:B0X"
    assert D.positive_key(clusters, dict(key="asin:B0X")) == "asin:B0X"
    assert D.positive_key(clusters, dict(title="Salt Roads", authors=["Someone Else"])) is None


def test_old_labels_match_by_folder_or_file_and_manual_wins():
    runs = [
        dict(item=dict(path="/m/audio-local/Books/Glass"), target=dict(work="/works/OL1W"), origin="automatic"),
        dict(item=dict(path="/m/audio-local/Books/Glass/01.mp3"), target=dict(work="/works/OL2W"), origin="manual"),
        dict(item=dict(path="/m/audio-local/Books/Salt.m4b"), target=dict(work="/works/OL3W"), origin="automatic"),
        dict(item=dict(path="/m/audio-local/Books/Nowhere"), target={}, origin="manual"),
    ]
    labels = D.old_labels(
        runs, {1: {"Books/Glass/01.mp3"}, 2: {"Books/Salt.m4b"}}, {1: "/n/audio/Books/Glass", 2: "/n/audio/Books"}
    )
    assert labels == {
        1: dict(target=dict(work="/works/OL2W"), origin="manual"),
        2: dict(target=dict(work="/works/OL3W"), origin="automatic"),
    }


def test_cases_and_cached_build(tmp_path, monkeypatch):
    monkeypatch.setattr(D, "folder", lambda: tmp_path / "linkage")
    data = {
        "1": dict(
            clusters={"/works/OL1W": dict(ol=["/works/OL1W"], title="T", authors=[])}, vectors={"/works/OL1W": [GOOD]}
        ),
        "2": dict(clusters={"k": dict(ol=["/works/OL5W"], title="U", authors=[])}, vectors={"k": [GOOD]}),
        "3": dict(clusters={}, vectors={}),
    }
    cases, stats = D.cases(
        data,
        {
            1: dict(target=dict(work="/works/OL1W"), origin="manual"),
            2: dict(reject="/works/OL5W", origin="user"),
            3: dict(target=dict(work="/works/OL7W"), origin="automatic"),
        },
    )
    assert [(c.positive, c.negatives) for c in cases] == [
        ("/works/OL1W", set()),
        (None, {"k"}),
        (None, set()),
    ] and stats == dict(labelled=3, found=1, missing=[3])
    assert D.load_cache() == {}
    D.save_cache(data)
    assert D.load_cache() == data
    q = Q.Query(4, "book", readings=[Q.Reading("The Glass Orchard", "Mara Quill")])
    calls = []
    monkeypatch.setattr(D, "vectors", lambda oc, ac, q: calls.append(q.listen_id) or dict(clusters={}, vectors={}))
    first = D.build(None, None, [q], progress=lambda i, n: None)
    assert calls == [4]
    D.build(None, None, [q], cache=first)
    assert calls == [4]  # unchanged readings: reused


def test_vectors_and_summary_without_open_library(tmp_path):
    ac = audible(tmp_path)
    q = Q.Query(
        1,
        "book",
        readings=[Q.Reading("The Glass Orchard", "Mara Quill"), Q.Reading("", "")],
        minutes=[600.0],
        narrators=["Tom Vale"],
    )
    out = D.vectors(None, ac, q)
    key = next(k for k, c in out["clusters"].items() if "B0AAAAAAA1" in c["asins"])
    assert (
        out["vectors"][key][0]["runtime"] == "1pct"
        and out["vectors"][key][0]["narrator"] == "match"
        and out["clusters"][key]["recordings"][0]["asin"] in ("B0AAAAAAA1", "B0AAAAAAA2")
    )
    assert out["clusters"][key]["isbns"] == ["9780306406157"]


@pytest.mark.parametrize("name", ["a", "b"])
def test_shares_author_initials(name):
    assert (
        C.shares_author(["Mara Quill"], ["M. Quill"])
        and not C.shares_author(["Mara Quill"], ["Nina Quill"])
        and not C._same_person("", "x")
    )


def test_open_library_candidates_join_recordings_by_isbn_and_amazon_id(tmp_path):
    from identify_dumps import built

    from app.identify import candidates as olc

    (tmp_path / "ol").mkdir()
    oc = olc.connect(built(tmp_path / "ol"))
    path = tmp_path / "a.sqlite"
    c = sqlite3.connect(path)
    c.execute(
        "CREATE TABLE product (asin TEXT, region TEXT, title TEXT, subtitle TEXT, series TEXT, series_asin TEXT, series_position TEXT, runtime_min INTEGER, language TEXT, format_type TEXT, release_date TEXT, publisher TEXT, rating_count INTEGER, isbn TEXT)"
    )
    c.execute("CREATE TABLE credit (asin TEXT, region TEXT, role TEXT, name TEXT, person_asin TEXT)")
    for a, t, isbn in [
        ("B0ZZZZZZZ1", "There and Back Again", "9780261102217"),
        ("B002V0QK4C", "A Different Name", None),
    ]:  # neither title nor author would join these
        c.execute(
            "INSERT INTO product (asin, region, title, runtime_min, language, isbn) VALUES (?,?,?,?,?,?)",
            (a, "uk", t, 600, "english", isbn),
        )
        c.execute("INSERT INTO credit VALUES (?,?,?,?,?)", (a, "uk", "Narrator", "Ann Reader", None))
    c.commit()
    c.close()
    ai.build(path, tmp_path / "i.sqlite")
    ac = ai.connect(tmp_path / "i.sqlite")
    q = Q.Query(
        1,
        "book",
        readings=[
            Q.Reading("The Hobbit", "J.R.R. Tolkien"),
            Q.Reading("There and Back Again"),
            Q.Reading("A Different Name"),
        ],
        minutes=[606.0],
        asin="B002V0QK4C",
    )
    found = C.audible_candidates(ac, q)
    works = C.ol_candidates(oc, q, found)
    assert "/works/OL1W" in works and {"audible_isbn", "audible_asin", "title+author"} <= works["/works/OL1W"]["via"]
    hobbit = next(c for c in C.clusters(oc, works, found) if c.works and c.works[0]["key"] == "/works/OL1W")
    assert {p.asin for p in hobbit.products} == {"B0ZZZZZZZ1", "B002V0QK4C"}
    out = D.vectors(oc, ac, q)
    v = out["vectors"]["/works/OL1W"][0]
    assert (
        v["title"] == "exact_rare"
        and v["author"] == "all"
        and v["asin"] == "match"
        and v["runtime"] == "1pct"
        and v["sources"] == "both"
    )


def test_numbered_volumes_of_one_main_title_stay_separate_works():
    p = lambda asin, title: C.Product(
        asin, title, "", K.tk.title_key(title), C.main_key(title), ["Mara Quill"], [], 600, "", None, 1, "", "", ""
    )
    found = {
        "B1": p("B1", "Salt Roads: Volume 1"),
        "B2": p("B2", "Salt Roads: Volume 2"),
        "B3": p("B3", "Salt Roads: Volume 1 (Dramatised)"),
    }
    works = {
        "/works/OL1W": work("/works/OL1W", "Salt Roads: Volume 2", ["Mara Quill"]),
        "/works/OL2W": work("/works/OL2W", "Salt Roads", ["Mara Quill"]),
    }
    groups = sorted(
        sorted([w["key"] for w in c.works] + [x.asin for x in c.products]) for c in C.clusters(None, works, found)
    )
    assert (
        sorted(["B2", "/works/OL1W"]) in groups
        and any({"B1", "B3"} <= set(g) for g in groups)
        and not any({"B1", "B2"} <= set(g) for g in groups)
    )
    assert C.same_book({"a"}, [], {"a"}, [], "a 1", "a") and not C.same_book({"a"}, [], {"a"}, [], "a 1", "a 2")


def test_cache_signature_follows_the_indexes(monkeypatch, tmp_path):
    q = Q.Query(4, "book", readings=[Q.Reading("The Glass Orchard", "Mara Quill")])
    calls = []
    monkeypatch.setattr(D, "vectors", lambda oc, ac, q: calls.append(q.listen_id) or dict(clusters={}, vectors={}))
    first = D.build(None, None, [q], indexes="ol:1|audible:1")
    D.build(None, None, [q], cache=first, indexes="ol:1|audible:1")
    assert calls == [4]
    D.build(None, None, [q], cache=first, indexes="ol:1|audible:2")
    assert calls == [4, 4]  # a rebuilt index recomputes
    assert D.index_stamp(None, None) == "ol:none|audible:none|radio:none:none"
    ac = audible(tmp_path)
    assert D.index_stamp(None, ac).startswith("ol:none|audible:20")


def test_a_single_files_name_is_a_reading_even_when_the_title_came_from_the_container_folder():
    # a stand-alone file's name is the title itself; here the chosen title is the container folder and the tags say nothing
    file = Q.scan.parse_name("The Invented Handbook.m4b")
    rs = Q.make_readings(dict(title="Books"), {}, dict(title="Books", folder="Books"), file)
    assert ("The Invented Handbook", "file") in [(r.title, r.source) for r in rs]
    q = Q.from_parts(
        3,
        "book",
        "Books",
        dict(title="Books"),
        {},
        dict(title="Books", folder="Books"),
        [],
        filename="The Invented Handbook.m4b",
    )
    assert any(r.title == "The Invented Handbook" for r in q.readings)
    # tags that already say the same thing add no duplicate reading
    tags = dict(tagged=1, album=dict(value="The Invented Handbook", share=1.0))
    assert [r.title for r in Q.make_readings({}, tags, {}, file)].count("The Invented Handbook") == 1
    assert Q.make_readings({}, {}, {}, Q.scan.parse_name("Books.m4b")) == []  # a generic file name says nothing


def test_common_title_of_multi_file_names():
    ct = Q.common_title
    assert ct([f"Invented Book {i:02d}.mp3" for i in range(1, 9)]) == "Invented Book"
    assert ct([f"{i:02d} - The Invented Book - Part {i}.mp3" for i in range(1, 5)]) == "The Invented Book"
    assert ct([f"Invented Book Disc 1 Track {i}.mp3" for i in range(1, 5)]) == "Invented Book"
    assert ct([f"Chapter {i}.mp3" for i in range(1, 9)]) == ""  # no title in the names
    assert ct(["Track 01.mp3", "Track 02.mp3", "Track 03.mp3"]) == ""
    assert ct(["One Thing.mp3", "Another Thing.mp3", "Third Thing.mp3"]) == ""  # nothing shared
    assert ct(["Invented Book 1.mp3", "Invented Book 2.mp3"]) == ""  # too few files to trust
    rs = Q.make_readings({}, {}, {}, None, "Invented Book")
    assert [(r.title, r.source) for r in rs][0] == ("Invented Book", "prefix")


def test_tag_editor_placeholders_are_not_a_title_or_author():
    # files tagged with the literal field names 'Album' / 'Album Artist' (an empty tag written by an editor) say nothing
    rs = Q.make_readings(
        dict(title="Album", author="Album Artist"),
        dict(tagged=8, album=dict(value="Album", share=0.9), album_artist=dict(value="Album Artist", share=1.0)),
        dict(title="Pat Author The Invented Gate", folder="Pat Author The Invented Gate"),
    )
    assert all(r.title.lower() != "album" and r.author.lower() != "album artist" for r in rs) and rs[0].source in (
        "folder",
        "swapped",
        "split",
        "stripped",
    )


def test_peel_takes_off_a_leading_author_and_series_code_only():
    assert Q.peel("Pat Author RW01 The Invented Echo", ["Pat Author"]) == "The Invented Echo"
    assert Q.peel("RW01 The Invented Echo", []) == "The Invented Echo"
    assert Q.peel("HB04: Another Invented Road", []) == "Another Invented Road"
    assert Q.peel("The Invented Echo", ["Pat Author"]) == "The Invented Echo"
    assert Q.peel("Pat Author", ["Pat Author"]) == "Pat Author"  # never the whole title
    assert Q.peel("Pat Authority Rules", ["Pat Author"]) == "Pat Authority Rules"  # whole words only
    assert Q.peel("U2 by U2", []) == "U2 by U2"  # a code needs letters after it
    assert Q.peel("R2D2 Handbook", []) == "R2D2 Handbook"  # no separator after the code: a name, not a code


def test_a_title_read_with_the_author_and_a_series_code_also_gets_a_reading_without_them():
    choices = dict(title="Pat Author RW01 The Invented Echo", author="Pat Author")
    tags = dict(tagged=1, title=dict(value="Pat Author RW01 The Invented Echo", share=1.0))
    folder = dict(title="RW01 The Invented Echo", author="Pat Author", folder="Pat Author RW01 The Invented Echo")
    rs = Q.make_readings(choices, tags, folder)
    assert any(r.title == "The Invented Echo" and r.author == "Pat Author" for r in rs)
    assert [r.title for r in rs].count("The Invented Echo") == 1  # peeled once, however many readings it came from


def test_peel_leaves_a_programme_and_its_series_number_alone():
    assert Q.peel("Invented Show series 23 (2023)", ["Invented Show"]) == "Invented Show series 23 (2023)"
    assert Q.peel("Invented Show Part 2", ["Invented Show"]) == "Invented Show Part 2"
    assert Q.peel("Invented Show 12", ["Invented Show"]) == "Invented Show 12"


def test_peel_keeps_a_one_word_or_generic_remainder_out():
    assert Q.peel("Pat Author Upload", ["Pat Author"]) == "Pat Author Upload"  # one word left after an author prefix
    assert Q.peel("Pat Author the book", ["Pat Author"]) == "Pat Author the book"
    assert Q.peel("Pat Author RW01 Echo", ["Pat Author"]) == "Echo"  # a series code vouches for a one-word title
    assert Q.peel("Pat Author RW01 The Invented Echo", ["Pat Author"]) == "The Invented Echo"
