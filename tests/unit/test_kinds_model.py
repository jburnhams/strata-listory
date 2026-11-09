"""Learning the kind weights (`app.store.kinds_model`): the hand-set weights are the prior and labels move them. Invented data."""

import json

import pytest

from app.store import kinds as K
from app.store import kinds_model as KM


def sigs(**w):
    return [K.Signal(n.replace("_", " "), x) for n, x in w.items()]


def test_with_no_labels_the_fit_is_the_hand_set_model():
    p = KM.fit([])
    assert p.bias == pytest.approx(K.PRIOR) and p.mult == {} and p.version().startswith("kinds-")
    f = K.Features(label="Greenhouse: Series 2", artist="BBC Radio 4", files=6, minutes=170)
    assert K.classify(f, p).p_broadcast == pytest.approx(K.classify(f).p_broadcast)


def test_labels_move_only_the_signals_they_show_and_few_labels_move_them_little():
    rows = [(sigs(channel=3.0, play_length=0.6), True, 1.0)]
    p = KM.fit(rows)
    assert (
        p.seen == {"channel": 1, "play length": 1}
        and 1.0 < p.mult["channel"] < 1.5
        and p.bias == pytest.approx(K.PRIOR, abs=0.6)
    )
    many = KM.fit(rows * 40)
    assert many.mult["channel"] > p.mult["channel"] and many.mult["channel"] <= KM.MULT_RANGE[1]


def test_a_signal_the_labels_call_misleading_is_scaled_down_but_never_flipped():
    rows = [([K.Signal("slot-length parts", 1.2)], False, 1.0)] * 60 + [(sigs(channel=3.0), True, 1.0)] * 5
    p = KM.fit(rows)
    assert 0 <= p.mult["slot-length parts"] < 0.6 and p.mult["channel"] >= 1.0


def test_learned_weights_change_the_verdict_and_say_which_signals_were_learned():
    f = K.Features(label="X", genre="Comedy", minutes=60, files=1)
    prior = K.classify(f)
    p = K.Params(
        bias=K.PRIOR, mult={"programme genre": 2.5, "play length": 1.0}, seen={"programme genre": 9, "play length": 0}
    )
    v = K.classify(f, p)
    assert v.p_broadcast > prior.p_broadcast
    by = {s.name: s for s in v.signals}
    assert (
        by["programme genre"].learned
        and by["programme genre"].prior == 0.8
        and by["programme genre"].weight == pytest.approx(2.0)
    )
    assert not by["play length"].learned  # never seen in a label: still the prior
    d = {s["name"]: s for s in v.as_dict()["signals"]}
    assert d["programme genre"]["learned"] is True and d["programme genre"]["prior"] == 0.8
    assert (
        "prior" not in {s["name"]: s for s in prior.as_dict()["signals"]}["programme genre"]
    )  # an untrained verdict carries no prior


def test_params_round_trip_and_version_follows_the_weights(tmp_path, monkeypatch):
    monkeypatch.setenv("APP_DATA", str(tmp_path))
    assert KM.load() is None
    p = KM.fit([(sigs(channel=3.0), True, 1.0)], {"user": 1})
    KM.save(p)
    q = KM.load()
    assert q.mult == p.mult and q.seen == p.seen and q.labels == {"user": 1} and q.version() == p.version()
    assert K.Params(mult={"a": 2.0}).version() != K.Params(mult={"a": 1.0}).version()
    KM.path().write_text('{"bias": "x"}')
    assert KM.load() is None
    KM.path().write_text(json.dumps([]))
    assert KM.load() is None


def test_one_sided_labels_leave_the_bias_alone_but_still_rescale_signals():
    books = [([K.Signal("slot-length parts", 1.2)], False, 0.5)] * 40
    p = KM.fit(books)
    assert p.bias == K.PRIOR and p.mult["slot-length parts"] < 1.0
    both = KM.fit(books + [([K.Signal("channel", 3.0)], True, 1.0)] * 6)
    assert both.bias != K.PRIOR
