"""Enrichment sources and aggregation: local Open Library & Audible stores, sidecar, Hardcover, Audnexus, Google Books, Apple Books, and summary building. No network: `net.get` / `net.request` are replaced."""

import json
import sqlite3
import zlib
from datetime import UTC, datetime

from app.covers import net
from app.enrichment import run, sources
from app.store.models import EnrichmentResult, EnrichmentRun


def reply(status, body=b"", error=""):
    return net.Reply(status, body, error, limited=status == 429)


def test_clean_text_handles_html_and_ol_dicts():
    assert sources.clean_text("<p>First para.</p><p>Second<br/>line &amp; more.</p>") == (
        "First para.\n\nSecond\nline & more."
    )
    assert sources.clean_text({"type": "/type/text", "value": "  From <b>OL</b>.  "}) == "From OL."
    assert sources.clean_text(None) == ""
    assert sources.clean_text("   ") == ""


def test_openlibrary_local_reads_ratings_and_description(tmp_path, monkeypatch):
    ol_dir = tmp_path / "openlibrary"
    ol_dir.mkdir()
    ident_db = ol_dir / "identify.sqlite"
    c = sqlite3.connect(ident_db)
    c.execute("CREATE TABLE works (key TEXT PRIMARY KEY, ratings INTEGER, rating_total REAL)")
    c.execute("CREATE TABLE redirects (old_key TEXT PRIMARY KEY, new_key TEXT)")
    c.execute("INSERT INTO works VALUES ('/works/OL1W', 4, 17.0)")
    c.commit()
    c.close()

    class FakeDetail:
        def get(self, key):
            return {"description": {"value": "A classic novel."}} if key == "/works/OL1W" else None

        def close(self):
            pass

    from app.identify import candidates as ident
    from app.identify import oldetail

    monkeypatch.setattr(ident, "path", lambda: ident_db)
    monkeypatch.setattr(ident, "connect", lambda: _row_conn(ident_db))
    monkeypatch.setattr(ident, "canonical", lambda conn, keys: {k: k for k in keys})
    monkeypatch.setattr(oldetail, "path", lambda: ol_dir / "detail.sqlite")
    (ol_dir / "detail.sqlite").write_bytes(b"placeholder")
    monkeypatch.setattr(oldetail, "connect", lambda: FakeDetail())

    ctx = sources.Context(ol_works=["/works/OL1W"])
    items, status = sources.openlibrary_local(ctx)
    assert status["status"] == "found"
    assert len(items) == 1
    assert items[0].rating == 4.25
    assert items[0].ratings_count == 4
    assert items[0].synopsis == "A classic novel."
    assert ctx.local_ol_rating and ctx.local_ol_synopsis


def _row_conn(db):
    c = sqlite3.connect(db)
    c.row_factory = sqlite3.Row
    return c


def test_audible_local_reads_archive_and_marks_satisfied(tmp_path):
    arch = tmp_path / "audible.sqlite"
    c = sqlite3.connect(arch)
    c.execute("CREATE TABLE product (asin TEXT, region TEXT, raw BLOB)")
    payload = {
        "product": {
            "asin": "B001",
            "publisher_summary": "<p>Great audiobook.</p>",
            "rating": {"overall_distribution": {"average_rating": 4.6, "num_ratings": 200}},
            "customer_reviews": [
                {
                    "author_display_name": "Alice",
                    "title": "Loved it",
                    "body": "Fantastic narration!",
                    "ratings": {"overall_rating": 5},
                    "submission_date": "2025-05-01T00:00:00Z",
                    "helpful_votes": 12,
                }
            ],
        }
    }
    c.execute("INSERT INTO product VALUES (?, ?, ?)", ("B001", "uk", zlib.compress(json.dumps(payload).encode())))
    c.commit()
    c.close()

    ctx = sources.Context(asins=["B001", "B002"])
    items, status = sources.audible_local(ctx, archive=str(arch))
    assert status["status"] == "found"
    assert len(items) == 1
    assert items[0].rating == 4.6
    assert items[0].ratings_count == 200
    assert items[0].synopsis == "Great audiobook."
    assert items[0].reviews[0]["user"] == "Alice"
    assert "B001" in ctx.local_audible
    assert "B002" not in ctx.local_audible


def test_sidecar_reads_metadata_json(tmp_path):
    meta = {
        "asin": "B009",
        "publisher_summary": "<p>Sidecar synopsis.</p>",
        "rating": {"overall_distribution": {"display_average_rating": 4.2, "num_ratings": 50}},
    }
    (tmp_path / "book.metadata.json").write_text(json.dumps(meta))
    items, status = sources.sidecar(sources.Context(folder=str(tmp_path)))
    assert status["status"] == "found"
    assert items[0].synopsis == "Sidecar synopsis."
    assert items[0].rating == 4.2


def test_hardcover_fetches_by_asin_and_falls_back_to_search(monkeypatch):
    monkeypatch.setenv("APP_HARDCOVER_KEY", "")
    monkeypatch.setattr("app.providers.hardcover.key", lambda: "")
    _, skipped = sources.hardcover(sources.Context(title="Book"))
    assert skipped["status"] == "skipped"

    monkeypatch.setattr("app.providers.hardcover.key", lambda: "tok")

    def fake_request(url, *, method="GET", headers=None, body=None, timeout=30):
        q = json.loads(body.decode())["query"]
        if "editions(where: {asin:" in q:
            return reply(200, json.dumps({"data": {"editions": []}}).encode())
        if "search(" in q:
            return reply(
                200,
                json.dumps(
                    {
                        "data": {
                            "search": {
                                "results": {
                                    "hits": [
                                        {
                                            "document": {
                                                "id": 77,
                                                "title": "Dune",
                                                "author_names": ["Frank Herbert"],
                                            }
                                        }
                                    ]
                                }
                            }
                        }
                    }
                ).encode(),
            )
        return reply(
            200,
            json.dumps(
                {
                    "data": {
                        "books": [
                            {
                                "id": 77,
                                "title": "Dune",
                                "slug": "dune",
                                "description": "Desert planet.",
                                "rating": 4.4,
                                "ratings_count": 9000,
                                "reviews_count": 400,
                                "cached_tags": {
                                    "Genre": [{"tag": "Sci-Fi", "count": 10}],
                                    "Mood": [{"tag": "Epic", "count": 5}],
                                    "Content Warning": [{"tag": "Violence", "count": 2, "spoiler_ratio": 0.1}],
                                },
                            }
                        ],
                        "user_books": [
                            {
                                "rating": 5,
                                "review_raw": "Masterpiece.",
                                "review_has_spoilers": False,
                                "likes_count": 25,
                                "reviewed_at": "2025-01-02T00:00:00Z",
                                "user": {"username": "paul"},
                            }
                        ],
                    }
                }
            ).encode(),
        )

    monkeypatch.setattr(net, "request", fake_request)
    items, status = sources.hardcover(sources.Context(title="Dune", author="Frank Herbert", asins=["B001"]))
    assert status["status"] == "found"
    assert items[0].rating == 4.4
    assert items[0].synopsis == "Desert planet."
    assert items[0].reviews[0]["user"] == "paul"
    assert items[0].extra["genres"] == ["Sci-Fi"]


def test_online_sources_audnexus_openlibrary_google_apple(monkeypatch):
    def fake_get(url, *, timeout=30):
        if "api.audnex.us" in url:
            return reply(
                200,
                json.dumps({"summary": "Audnexus blurb.", "genres": [{"type": "genre", "name": "Fiction"}]}).encode(),
            )
        if "openlibrary.org/works/OL1W.json" in url:
            return reply(200, json.dumps({"description": "OL blurb."}).encode())
        if "openlibrary.org/works/OL1W/ratings.json" in url:
            return reply(200, json.dumps({"summary": {"average": 4.1, "count": 12}}).encode())
        if "googleapis.com" in url:
            return reply(
                200,
                json.dumps(
                    {
                        "items": [
                            {
                                "volumeInfo": {
                                    "description": "Google blurb.",
                                    "averageRating": 4.0,
                                    "ratingsCount": 5,
                                    "infoLink": "https://books.google.com/1",
                                }
                            }
                        ]
                    }
                ).encode(),
            )
        if "itunes.apple.com" in url:
            return reply(
                200,
                json.dumps(
                    {
                        "results": [
                            {
                                "collectionId": 99,
                                "collectionName": "Dune (Unabridged)",
                                "description": "Apple blurb.",
                                "collectionViewUrl": "https://books.apple.com/99",
                            }
                        ]
                    }
                ).encode(),
            )
        return reply(404)

    monkeypatch.setattr(net, "get", fake_get)
    ctx = sources.Context(
        title="Dune", author="Frank Herbert", asins=["B001"], isbns=["9780441172719"], ol_works=["/works/OL1W"]
    )

    an, st_an = sources.audnexus(ctx)
    assert st_an["status"] == "found" and an[0].synopsis == "Audnexus blurb."

    ol, st_ol = sources.openlibrary_online(ctx)
    assert st_ol["status"] == "found" and ol[0].rating == 4.1 and ol[0].synopsis == "OL blurb."

    gb, st_gb = sources.googlebooks(ctx)
    assert st_gb["status"] == "found" and gb[0].synopsis == "Google blurb."

    ab, st_ab = sources.applebooks(ctx)
    assert st_ab["status"] == "found" and ab[0].synopsis == "Apple blurb."


def test_summarize_prioritizes_and_merges_results():
    assert run.summarize(None, [], job_state="queued")["status"] == "queued"

    r = EnrichmentRun(listen_id=1, sources=[{"source": "hardcover", "status": "found", "detail": "", "count": 1}])
    r.finished_at = datetime.now(UTC).replace(tzinfo=None)
    res_hc = EnrichmentResult(
        run_id=1,
        source="hardcover",
        url="https://hardcover.app/books/dune",
        rating=4.4,
        ratings_count=100,
        reviews_count=10,
        synopsis="Hardcover synopsis.",
        reviews=[{"user": "u1", "rating": 5, "text": "Great", "likes": 5}],
        extra={"genres": ["Sci-Fi"]},
    )
    res_ol = EnrichmentResult(
        run_id=1,
        source="openlibrary_local",
        url="https://openlibrary.org/works/OL1W",
        rating=4.0,
        ratings_count=20,
        synopsis="OL synopsis.",
    )
    summary = run.summarize(r, [res_ol, res_hc])
    assert summary["status"] == "done"
    assert summary["synopsis"]["source"] == "hardcover"
    assert len(summary["synopses"]) == 2
    assert [x["label"] for x in summary["ratings"]] == ["Hardcover", "Open Library"]
    assert summary["reviews"][0]["user"] == "u1"
    assert summary["tags"]["genres"] == ["Sci-Fi"]
