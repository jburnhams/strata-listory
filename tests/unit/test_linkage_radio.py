"""Radio programmes as a third candidate source (`radioindex`, `linkage.candidates`, `compare`, `model`). Every title, name and id is invented."""

import json

from app.identify import radioindex as R
from app.linkage import candidates as C
from app.linkage import compare as K
from app.linkage import dataset
from app.linkage import model as M
from app.linkage.query import Query, Reading, channels_in


def q(title="Harbour Lights", band="broadcast", **kw):
    return Query(1, "book", title, [Reading(title)], band=band, **kw)


def hit(title="Harbour Lights", source="bbc_radio", ref="b001", service="bbc_radio_four", first="2012-05-01"):
    return dict(source=source, ref=ref, title=title, service=service, first_broadcast=first)


class Http:
    def __init__(self, replies):
        self.replies, self.calls = replies, []

    def request(self, url, body=None, headers=None, **kw):
        self.calls.append(url)
        return self.replies.get(url.rsplit("/", 1)[-1].removesuffix(".json"))


BBC_BODY = {
    "programme": {
        "type": "series",
        "ownership": {"service": {"id": "bbc_radio_four", "title": "BBC Radio 4"}},
        "first_broadcast_date": "2012-05-01T09:00:00+01:00",
        "aggregated_episode_count": 12,
        "categories": [{"type": "genre", "title": "Comedy"}, {"type": "format", "title": "Sitcom"}, {"type": "genre"}],
        "short_synopsis": "A harbour, a lamp and a rota.",
    }
}


# --- detail cache


def test_bbc_detail_is_summarised_fetched_once_cached_and_read_offline(tmp_path):
    assert R.summarise_bbc(BBC_BODY) == dict(
        type="series",
        service="bbc_radio_four",
        channel="BBC Radio 4",
        first_broadcast="2012-05-01",
        episodes=12,
        genres=["Comedy"],
        formats=["Sitcom"],
        synopsis="A harbour, a lamp and a rota.",
    )
    assert R.summarise_bbc({}) == dict(
        type="", service="", channel="", first_broadcast="", episodes=None, genres=[], formats=[], synopsis=""
    )
    db = tmp_path / "detail.sqlite"
    http = Http({"b001": BBC_BODY})
    log = []
    assert (
        R.fetch_details(http, ["b001", "b001", "b404"], path=db, log=log.append) == 1 and len(http.calls) == 2
    )  # an unknown pid is skipped
    assert R.fetch_details(Http({}), ["b001"], path=db) == 0  # cached: no request
    assert R.fetch_details(Http({"b001": BBC_BODY}), ["b001"], refresh=True, path=db) == 1
    assert R.details(["b001", "b999"], path=db)["b001"]["episodes"] == 12 and "b999" not in R.details(
        ["b001", "b999"], path=db
    )
    assert R.details(["b001"], path=tmp_path / "none.sqlite") == {} and R.details([], path=db) == {}


def test_a_damaged_detail_cache_answers_nothing(tmp_path):
    bad = tmp_path / "detail.sqlite"
    bad.write_text("not a database")
    assert R.details(["b001"], path=bad) == {}


# --- candidates


def test_a_broadcast_listen_gets_one_radio_cluster_per_title_with_the_cached_detail():
    hits = [hit(), hit(source="wikidata_drama", ref="Q9", service="", first=""), hit("The Lamp Keeper", ref="b002")]
    found = C.radio_candidates(
        q(),
        find=lambda titles: [h for h in hits if h["title"] == "Harbour Lights"],
        details=lambda refs: {"b001": dict(type="series", service="bbc_radio_four")},
    )
    assert (
        len(found) == 1
        and [p.ref for p in found[0].programmes] == ["b001", "Q9"]
        and found[0].programmes[0].detail["type"] == "series"
    )
    c = found[0]
    assert (
        c.key == "radio:harbour-lights"
        and c.title == "Harbour Lights"
        and c.year == 2012
        and c.title_keys() == {"harbour lights": 2}
        and c.via == {"radio_title"}
        and c.authors == []
    )


def test_a_book_never_collects_radio_candidates_and_the_index_is_optional():
    assert C.radio_candidates(q(band="book"), find=lambda t: [hit()]) == []
    assert C.radio_candidates(q(band=""), find=lambda t: [], details=lambda r: {}) == []
    found = C.radio_candidates(q(band="review"), find=lambda t: [hit()], details=lambda r: {})
    assert len(found) == 1  # a close call and an unclassified listen are both worth proposing


def test_at_most_a_handful_of_radio_clusters_exact_titles_first():
    hits = [hit(f"Show {n}", ref=f"b{n}") for n in range(10)] + [hit("Harbour Lights", ref="b50")]
    found = C.radio_candidates(q(), find=lambda t: hits, details=lambda r: {})
    assert len(found) == C.MAX_RADIO and found[0].title == "Harbour Lights"


def test_clusters_of_radio_and_books_are_kept_apart():
    out = C.for_query(
        None,
        None,
        q(),
        radio=lambda query: [
            C.Cluster(programmes=[C.Programme("bbc_radio", "b001", "Harbour Lights", "harbour lights")])
        ],
    )
    assert [c.key for c in out] == ["radio:harbour-lights"] and C.for_query(None, None, q(), radio=None) == []


# --- channels, comparison and the model


def test_channels_are_found_in_tags_and_names_and_the_specific_one_wins():
    assert channels_in("BBC Radio 4", "x") == ["bbc_radio_four"] and channels_in("Radio 4 Extra - Show") == [
        "bbc_radio_four_extra"
    ]
    assert (
        channels_in("BBC R3 Drama", "World Service") == ["bbc_radio_three", "bbc_world_service"]
        and channels_in("Plain Novel", "") == []
    )
    assert channels_in("BBC 6 Music", "Radio 5 Live") == ["bbc_radio_five_live", "bbc_6music"]


def cluster(service="bbc_radio_four"):
    return C.Cluster(
        programmes=[C.Programme("bbc_radio", "b001", "Harbour Lights", "harbour lights", service, "2012-05-01")]
    )


def test_the_channel_level_compares_the_listens_channel_with_the_programmes_service():
    assert K.channel_level(q(channels=["bbc_radio_four"]), cluster()) == "match"
    assert (
        K.channel_level(q(channels=["bbc_radio_four_extra"]), cluster()) == "match"
    )  # the same family: Radio 4 and its Extra
    assert K.channel_level(q(channels=["bbc_radio_three"]), cluster()) == "conflict"
    assert (
        K.channel_level(q(), cluster()) is None
        and K.channel_level(q(channels=["bbc_radio_four"]), cluster("")) is None
        and K.channel_level(q(channels=["bbc_radio_four"]), C.Cluster()) is None
    )


def test_the_form_level_is_the_kind_classifiers_prior_for_each_kind_of_candidate():
    book = C.Cluster(works=[dict(key="/works/OL1W", title="Harbour Lights", titles={}, authors=[], editions=1)])
    assert [K.form_level(q(band=b), cluster()) for b in ("broadcast", "review", "book", "")] == [
        "agree",
        "neutral",
        "conflict",
        None,
    ]
    assert [K.form_level(q(band=b), book) for b in ("broadcast", "review", "book", "")] == [
        "conflict",
        "neutral",
        "agree",
        None,
    ]


def test_comparing_a_radio_cluster_fills_the_radio_fields_and_leaves_book_ones_empty():
    v = K.compare(q(channels=["bbc_radio_four"], year=2012), Reading("Harbour Lights"), cluster(), 1)
    assert (
        v["title"],
        v["sources"],
        v["channel"],
        v["form"],
        v["year"],
        v["author"],
        v["narrator"],
        v["runtime"],
        v["popularity"],
    ) == ("exact_rare", "radio", "match", "agree", "same", None, None, None, "none")


def test_the_summary_carries_the_programmes_for_the_card():
    c = cluster()
    c.programmes[0].detail = dict(
        type="series", channel="BBC Radio 4", episodes=12, genres=["Comedy", "Sitcom", "x", "y"], synopsis="s"
    )
    s = dataset.summary(c)
    assert s["key"] == "radio:harbour-lights" and s["programmes"] == [
        dict(
            source="bbc_radio",
            ref="b001",
            title="Harbour Lights",
            service="bbc_radio_four",
            channel="BBC Radio 4",
            first_broadcast="2012-05-01",
            type="series",
            episodes=12,
            genres=["Comedy", "Sitcom", "x"],
            synopsis="s",
        )
    ]


def test_vectors_include_a_radio_cluster_beside_book_candidates():
    out = dataset.vectors(None, None, q(channels=["bbc_radio_four"]), radio=lambda query: [cluster()])
    assert (
        list(out["clusters"]) == ["radio:harbour-lights"]
        and out["vectors"]["radio:harbour-lights"][0]["form"] == "agree"
    )
    assert dataset.signature(q(band="book")) != dataset.signature(q(band="broadcast")) and dataset.signature(
        q(channels=["a"])
    ) != dataset.signature(q())  # a changed verdict recomputes


def test_the_model_ranks_the_fitting_candidate_first_and_an_older_saved_model_still_loads():
    p = M.initial()
    radio = dict(title="exact_rare", sources="radio", channel="match", form="agree")
    book = dict(title="exact_rare", sources="openlibrary", form="conflict")
    post, none, _used = M.posterior(p, {"radio:harbour-lights": [radio], "/works/OL1W": [book]})
    assert post["radio:harbour-lights"] > post["/works/OL1W"]
    old = p.to_json()
    old["m"].pop("channel")
    old["m"].pop("form")
    old["u"].pop("channel")
    old["m"]["sources"].pop("radio")
    old["u"]["sources"].pop("radio")
    loaded = M.Params.from_json(json.loads(json.dumps(old)))
    assert (
        set(loaded.m["channel"]) == {"match", "conflict"}
        and abs(sum(loaded.m["sources"].values()) - 1) < 1e-9
        and "radio" in loaded.u["sources"]
    )
