"""The best-fit ranking of a listen's covers (`covers.fit`): the hand-set signals, the order they give, and learning from a person's choices. Pure functions over made-up pictures."""

from types import SimpleNamespace

import pytest

from app.covers import fit


def pic(w, h, *sources, id=1):
    """A distinct cover: its largest picture and each (source, ref, label) that offered it."""
    return dict(
        image=SimpleNamespace(id=id, width=w, height=h),
        sources=[dict(source=s, ref=r, label=label, url=None, image_id=id) for s, r, label in sources],
    )


BOOK = fit.Target(
    kind="book", title="The Glass Orchard", minutes=600, recording="B0AAA", recordings={"B0AAA": 598.0, "B0BBB": 800.0}
)


def names(g, target=BOOK):
    return {s.name: s.weight for s in fit.signals(g, target)}


def test_the_audiobook_cover_of_the_same_recording_beats_a_print_cover():
    audio = pic(2400, 2400, ("audible", "B0AAA", "The Glass Orchard"), ("audnexus", "B0AAA", "The Glass Orchard"), id=1)
    book = pic(2000, 3000, ("openlibrary", "9780000000002", "ISBN 9780000000002"), id=2)
    ranked = fit.rank([book, audio], BOOK)
    assert [r.group["image"].id for r in ranked] == [1, 2]
    assert ranked[0].share > 0.9 and abs(sum(r.share for r in ranked) - 1) < 1e-9
    n = names(audio)
    assert n["audiobook cover"] == 2.0 and n["this recording"] == 1.5 and n["recording runtime"] == 1.0
    assert n["title"] == 0.5 and n["audiobook shape"] == 0.8 and n["agreement"] == pytest.approx(0.6)
    assert names(book)["print edition only"] == -0.5 and "audiobook cover" not in names(book)


def test_another_narration_with_another_length_and_a_foreign_title_ranks_lower():
    other = pic(2400, 2400, ("audible", "B0BBB", "A Different Book"))
    n = names(other)
    assert n["recording runtime"] == -0.7 and n["title"] == -0.8 and "this recording" not in n
    same = pic(2400, 2400, ("audible", "B0AAA", "The Glass Orchard: A Novel"))
    assert fit.rank([other, same], BOOK)[0].group is same


def test_resolution_counts_for_more_the_larger_the_picture_and_is_bounded():
    def res(side):
        return {s.name: s.weight for s in fit.signals(pic(side, side, ("audible", "B0AAA", "")), BOOK)}.get(
            "resolution"
        )

    assert res(100) == -2.0 and res(1000) is None  # 1000 px is the neutral size: no signal
    assert res(500) == pytest.approx(-0.8) and res(2000) == pytest.approx(0.8) and res(20000) == 1.6


def test_a_tie_goes_to_the_larger_picture():
    a = pic(1000, 600, ("embedded", "a.mp3", ""), id=1)
    b = pic(1000, 1000, ("embedded", "b.mp3", ""), id=2)
    radio = fit.Target(kind="radio")  # no shape signal for radio: the scores are equal
    ranked = fit.rank([a, b], radio)
    assert ranked[0].score == ranked[1].score and ranked[0].group is b


def test_radio_pictures_prefer_the_broadcaster_then_podcast_artwork_then_wikimedia():
    t = fit.Target(kind="radio", title="The Glass Hour")
    bbc = pic(1920, 1080, ("bbc", "b0aaaaaa", "BBC programme b0aaaaaa"), id=1)
    pod = pic(3000, 3000, ("podcast", "5", "The Glass Hour"), id=2)
    wiki = pic(2000, 1500, ("wikidata", "Q7", "Glass Hour.jpg"), id=3)
    own = pic(800, 800, ("embedded", "a.mp3", ""), id=4)
    order = [r.group["image"].id for r in fit.rank([wiki, own, pod, bbc], t)]
    assert order == [1, 2, 3, 4]
    n = names(bbc, t)
    assert n["broadcaster's picture"] == 2.5 and "audiobook shape" not in n and "audiobook shape" in names(pod, BOOK)
    assert names(pod, t)["podcast artwork"] == 1.0 and names(wiki, t)["reference image"] == 0.5
    assert names(own, t)["from the listen's files"] == 1.0


def test_a_title_that_neither_contains_nor_is_contained_conflicts_and_an_unknown_one_says_nothing():
    assert fit._title_relation("The Glass Orchard (Unabridged)", "Glass Orchard") == "match"
    assert fit._title_relation("Salt Roads", "Glass Orchard") == "conflict"
    assert fit._title_relation("", "Glass Orchard") == "" and fit._title_relation("x", "") == ""


def test_signals_without_a_runtime_or_recording_to_compare_are_left_out():
    n = names(pic(1000, 1000, ("audible", "B0ZZZ", "")), fit.Target(kind="book"))
    assert "recording runtime" not in n and "this recording" not in n and "title" not in n


def example(chosen, *groups, target=BOOK, weight=1.0):
    return [fit.signals(g, target) for g in groups], chosen, weight


def test_without_choices_the_weights_stand_and_the_ranking_is_the_hand_set_one():
    p = fit.fit_params([])
    assert p.mult == {} and p.labels == {}
    audio = pic(2400, 2400, ("audible", "B0AAA", ""))
    assert [s.weight for s in fit.rank([audio], BOOK, p)[0].signals] == [s.weight for s in fit.signals(audio, BOOK)]


def test_choices_move_the_weights_of_the_signals_they_show_and_only_those():
    audio = pic(600, 600, ("audible", "B0AAA", ""), id=1)  # the hand-set model prefers the audiobook cover...
    big = pic(3000, 4500, ("openlibrary", "isbn", ""), id=2)  # ...this person keeps choosing the large print cover
    chosen = [example(1, audio, big) for _ in range(8)]
    params = fit.fit_params(chosen, dict(chosen=8))
    assert params.mult["resolution"] > 1 and params.mult["audiobook cover"] < 1
    assert params.seen["resolution"] == 8 and "agreement" not in params.mult
    assert all(0 <= v <= 3 for v in params.mult.values())
    before, ranked = fit.rank([audio, big], BOOK), fit.rank([audio, big], BOOK, params)
    share = lambda rs: next(r.share for r in rs if r.group is big)
    assert share(ranked) > share(before)
    learned = {s.name: s for s in next(r for r in ranked if r.group is big).signals}
    assert learned["resolution"].learned and learned["resolution"].prior is not None
    assert learned["resolution"].as_dict()["prior"] == round(learned["resolution"].prior, 2)
    assert "prior" not in fit.signals(big, BOOK)[0].as_dict()


def test_accepting_the_suggestion_weighs_less_than_choosing_something_else():
    audio = pic(600, 600, ("audible", "B0AAA", ""), id=1)
    big = pic(3000, 4500, ("openlibrary", "isbn", ""), id=2)
    accepted = fit.fit_params([example(0, audio, big, weight=fit.ACCEPT_WEIGHT) for _ in range(4)])
    chosen = fit.fit_params([example(0, audio, big) for _ in range(4)])
    assert abs(chosen.mult["audiobook cover"] - 1) > abs(accepted.mult["audiobook cover"] - 1)


def test_the_learned_weights_are_saved_and_read_back(tmp_path, monkeypatch):
    monkeypatch.setattr(fit.settings, "data_dir", lambda: str(tmp_path))
    assert fit.load() is None
    p = fit.Params(mult={"resolution": 1.4}, seen={"resolution": 3}, labels={"chosen": 3}, trained=5.0)
    fit.save(p)
    assert fit.load() == p
    fit.path().write_text("{nope")
    assert fit.load() is None
