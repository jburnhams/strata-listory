"""The local radio programme index (`app.identify.radioindex`): fetching with a fake client, building, and lookups. Titles are invented."""

import json

from app.identify import radioindex as R


class FakeHttp:
    """Answers `request(url, headers=...)` from a dict of url-substring -> body; records the calls."""

    def __init__(self, replies):
        self.replies, self.calls = replies, []

    def request(self, url, body=None, headers=None, **kw):
        self.calls.append(url)
        for k, v in self.replies.items():
            if k in url:
                return v
        return None


def bbc_page(titles, total, letter="h"):
    return dict(
        atoz=dict(
            slice="all",
            search=letter,
            page=1,
            limit=30,
            total=total,
            tleo_titles=[
                dict(
                    title=t,
                    programme=dict(
                        type="brand",
                        pid=pid,
                        title=t,
                        first_broadcast_date="2012-05-01T09:00:00Z",
                        ownership=dict(service=dict(type=typ, id=svc)),
                    ),
                )
                for t, pid, typ, svc in titles
            ],
        )
    )


def test_only_radio_services_are_kept_from_a_bbc_page():
    page = bbc_page(
        [("Harbour Lights", "b001", "radio", "bbc_radio_four"), ("Harbour Telly", "b002", "tv", "bbc_one")], 2
    )
    assert R.bbc_rows(page) == [("bbc_radio", "b001", "Harbour Lights", "bbc_radio_four", "2012-05-01")]
    assert R.bbc_rows({}) == []


def test_fetching_pages_through_every_letter_and_resumes_from_saved_pages(tmp_path):
    first = bbc_page([("Harbour Lights", "b001", "radio", "bbc_radio_four")], 31)
    second = bbc_page([("Quiet Rooms", "b003", "radio", "bbc_radio_three")], 31)
    http = FakeHttp({"/by/a/all.json?page=1": first, "/by/a/all.json?page=2": second})
    n = R.fetch_bbc(http, tmp_path, log=lambda *_: None)
    assert (
        n == 2 and (tmp_path / "bbc-a-1.json").exists() and (tmp_path / "bbc-a-2.json").exists()
    )  # 31 titles at 30 a page: two pages; letters with nothing answer 404
    again = FakeHttp({})
    assert R.fetch_bbc(again, tmp_path, log=lambda *_: None) == 0 and not any(
        "/by/a/" in c for c in again.calls
    )  # saved pages are reused
    R.fetch_bbc(again, tmp_path, refresh=True, log=lambda *_: None)
    assert any("/by/a/" in c for c in again.calls)


def wikidata(items):
    return dict(
        results=dict(
            bindings=[dict(x=dict(value=f"http://www.wikidata.org/entity/{q}"), label=dict(value=t)) for q, t in items]
        )
    )


def test_wikidata_is_fetched_once_per_class_and_resumes(tmp_path):
    http = FakeHttp(
        {"Q1555508": wikidata([("Q1", "Harbour Lights")]), "Q2635894": wikidata([("Q2", "The Locked Door")])}
    )
    assert R.fetch_wikidata(http, tmp_path, log=lambda *_: None) == 2
    assert "wdt%3AP31+wd%3AQ1555508" in http.calls[0] and "P279" not in http.calls[0]
    assert R.fetch_wikidata(FakeHttp({}), tmp_path, log=lambda *_: None) == 0
    assert (
        R.fetch_wikidata(FakeHttp({}), tmp_path, refresh=True, log=lambda *_: None) == 0
    )  # nothing answered: nothing written, nothing raised


def test_build_and_find_by_title_key(tmp_path):
    raw = tmp_path / "raw"
    raw.mkdir()
    (raw / "bbc-h-1.json").write_text(
        json.dumps(
            bbc_page(
                [("Harbour Lights", "b001", "radio", "bbc_radio_four"), ("Dune", "b009", "radio", "bbc_radio_four")], 2
            )
        )
    )
    (raw / "wikidata-Q2635894.json").write_text(
        json.dumps(wikidata([("Q2", "The Locked Door"), ("Q3", "Harbour Lights")]))
    )
    (raw / "notes.json").write_text("{}")
    out = tmp_path / "index.sqlite"
    assert R.build(raw, out) == {"bbc_radio": 2, "wikidata_drama": 2}
    hits = R.find(["harbour lights", "Nothing Here"], path=out)
    assert [(h["source"], h["ref"]) for h in hits] == [("bbc_radio", "b001"), ("wikidata_drama", "Q3")]
    assert (
        R.find(["The Locked Door"], path=out)[0]["title"] == "The Locked Door"
    )  # the article is dropped by the shared key
    assert R.find(["Dune"], path=out) == []  # too short a key to mean anything
    assert R.find([], path=out) == [] and R.find(["", None], path=out) == []


def test_without_an_index_every_lookup_is_empty(tmp_path, monkeypatch):
    assert R.find(["Harbour Lights"], path=tmp_path / "missing.sqlite") == []
    monkeypatch.setenv("APP_DATA", str(tmp_path))
    assert not R.available() and R.find(["Harbour Lights"]) == []


def test_a_broken_index_file_answers_nothing(tmp_path):
    bad = tmp_path / "index.sqlite"
    bad.write_text("not a database")
    assert R.find(["Harbour Lights"], path=bad) == []


def test_a_programme_titled_with_a_channel_name_is_never_a_hit(tmp_path):
    raw = tmp_path / "raw"
    raw.mkdir()
    names = [
        "Radio 4",
        "BBC Radio 4",
        "BBC Radio 4 Extra",
        "Radio Scotland",
        "BBC World Service",
        "BBC Radio 6 Music",
        "6 Music",
        "Radio 5 Live",
        "R4",
        "The BBC Asian Network",
    ]
    (raw / "bbc-r-1.json").write_text(
        json.dumps(
            bbc_page(
                [(n, f"b{i}", "radio", "bbc_radio_four") for i, n in enumerate(names)]
                + [
                    ("Radio Garden Hours", "b99", "radio", "bbc_radio_four"),
                    ("Harbour Lights", "b98", "radio", "bbc_radio_four"),
                ],
                12,
            )
        )
    )
    out = tmp_path / "index.sqlite"
    R.build(raw, out)
    assert R.find(names + ["Radio Garden Hours", "Harbour Lights"], path=out) and [
        h["title"] for h in R.find(names + ["Radio Garden Hours", "Harbour Lights"], path=out)
    ] == ["Harbour Lights", "Radio Garden Hours"]
    for n in names:
        assert R.CHANNEL_NAME.match(n), n
    assert not R.CHANNEL_NAME.match("Radio Garden Hours") and not R.CHANNEL_NAME.match("Drama on 3")
