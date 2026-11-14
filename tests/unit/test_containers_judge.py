"""The decision of `linkage.containers` on invented folders: pure, no database."""

from app.linkage import containers as C
from app.linkage import model as M


def params():
    return M.train(
        [
            M.Case(
                i,
                {
                    "ok": [dict(title="exact_rare", author="all", runtime="1pct", sources="both")],
                    "bad": [dict(title="weak", author="conflict", sources="openlibrary")],
                },
            )
            for i in range(30)
        ]
    )


def group(*minutes):
    return C.Group(
        "/x/Collection", [C.Piece(i, i, f"{i + 1:02d} Tale", f"/x/Collection/{i}", m) for i, m in enumerate(minutes)]
    )


def data(recording, title="exact_rare", author="all", recordings=None):
    work = dict(key="w", title="Collection", authors=["A"], recordings=recordings or [dict(minutes=recording)])
    return dict(clusters={"w": work}, vectors={"w": [dict(title=title, author=author, runtime="1pct", sources="both")]})


def test_lead_number():
    assert C.lead_number("07 - Tale") == 7 and C.lead_number("Tale 7") is None


def test_pieces_that_sum_to_a_recording_join():
    v = C.judge(group(240, 225, 540), data(1005), params())
    assert v.action == "join" and v.recording == 1005 and v.key == "w"


def test_a_close_sum_or_an_unsure_name_is_only_proposed():
    assert C.judge(group(240, 225, 540), data(1090), params()).action == "propose"  # 8% off
    assert C.judge(group(240, 225, 540), data(1005), params(), threshold=1.01).action == "propose"  # name not certain


def test_other_sums_pieces_as_long_as_the_work_and_nothing_to_match_are_skipped():
    assert C.judge(group(240, 225, 540), data(1500), params()).action == "skip"  # 33% off
    assert C.judge(group(900, 105), data(1005), params()).action == "skip"  # a piece is the whole work
    assert C.judge(group(240, 225, 540), data(0, recordings=[]), params()).action == "skip"  # no recording length
    assert C.judge(group(240, 225), dict(clusters={}, vectors={}), params()).action == "skip"


def test_a_recording_titled_unlike_its_cluster_is_the_work_when_it_names_the_folder():
    rec = dict(asin="B0COMPLETE1", title="The Complete Quill Tales", minutes=1005, year=2013)
    d = data(0, recordings=[rec])
    d["clusters"]["w"]["title"] = "Quill Tale One [3 stories]"  # the clustering filed the whole under one of its books
    v = C.judge(group(240, 225, 540), d, params(), titles=frozenset({"complete quill tales"}))
    assert v.action == "join" and v.key == "asin:B0COMPLETE1" and v.summary["title"] == "The Complete Quill Tales"
    assert v.summary["asins"] == ["B0COMPLETE1"] and v.detail["candidate"] == "asin:B0COMPLETE1"
    other = C.judge(group(240, 225, 540), d, params(), titles=frozenset({"something else"}))
    assert (
        other.action == "propose" and "not titled like the work" in other.reason
    )  # not the folder's name: a person looks
    far = C.judge(
        group(240, 225, 540),
        data(0, recordings=[dict(rec, minutes=1100)]),
        params(),
        titles=frozenset({"something else"}),
    )
    assert far.action == "skip"  # a different title and a sum that is only roughly right: nothing to say
