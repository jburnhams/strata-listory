"""Enrichment sources, runner, and HTTP API: local Open Library & Audible stores, sidecar, Hardcover, Audible API, Audnexus, Google Books, Apple Books, job orchestration, and FastAPI routes. No network: `net.get` / `net.request` are replaced."""

import json
import sqlite3
import threading
import zlib
from datetime import UTC, datetime
from types import SimpleNamespace

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.covers import net
from app.enrichment import api, run, sources
from app.store.api import get_session
from app.store.models import EnrichmentResult, EnrichmentRun, Listen, Work


def reply(status, body=b"", error=""):
    return net.Reply(status, body, error, limited=status == 429)


def _row_conn(db):
    c = sqlite3.connect(db)
    c.row_factory = sqlite3.Row
    return c


def test_clean_text_handles_html_and_ol_dicts():
    assert sources.clean_text("<p>First para.</p><p>Second<br/>line &amp; more.</p>") == (
        "First para.\n\nSecond\nline & more."
    )
    assert sources.clean_text({"type": "/type/text", "value": "  From <b>OL</b>.  "}) == "From OL."
    assert sources.clean_text(None) == ""
    assert sources.clean_text("   ") == ""


def test_record_statuses():
    assert sources.record("x", [], [], skipped="nope")["status"] == "skipped"
    assert sources.record("x", [sources.Result("x", "a", synopsis="hi")], [])["status"] == "found"
    assert sources.record("x", [], ["rate limited: 429"])["status"] == "limited"
    assert sources.record("x", [], ["error: boom"])["status"] == "error"
    assert sources.record("x", [], ["not found"])["status"] == "none"


def test_openlibrary_local_reads_ratings_and_description_and_handles_missing(tmp_path, monkeypatch):
    assert sources.openlibrary_local(sources.Context())[1]["status"] == "skipped"

    ol_dir = tmp_path / "openlibrary"
    ol_dir.mkdir()
    ident_db = ol_dir / "identify.sqlite"
    c = sqlite3.connect(ident_db)
    c.execute("CREATE TABLE works (key TEXT PRIMARY KEY, ratings INTEGER, rating_total REAL)")
    c.execute("INSERT INTO works VALUES ('/works/OL1W', 4, 17.0)")
    c.execute("INSERT INTO works VALUES ('/works/OL2W', 0, 0.0)")
    c.commit()
    c.close()

    class FakeDetail:
        def get(self, key):
            return {"description": {"value": "A classic novel."}} if key == "/works/OL1W" else None

        def close(self):
            pass

    from app.identify import candidates as ident
    from app.identify import oldetail

    monkeypatch.setattr(ident, "path", lambda: ol_dir / "missing.sqlite")
    monkeypatch.setattr(oldetail, "path", lambda: ol_dir / "missing-detail.sqlite")
    assert sources.openlibrary_local(sources.Context(ol_works=["/works/OL1W"]))[1]["status"] == "skipped"

    monkeypatch.setattr(ident, "path", lambda: ident_db)
    monkeypatch.setattr(ident, "connect", lambda: _row_conn(ident_db))
    monkeypatch.setattr(ident, "canonical", lambda conn, keys: {k: k for k in keys})
    monkeypatch.setattr(oldetail, "path", lambda: ol_dir / "detail.sqlite")
    (ol_dir / "detail.sqlite").write_bytes(b"placeholder")
    monkeypatch.setattr(oldetail, "connect", lambda: FakeDetail())

    ctx = sources.Context(ol_works=["/works/OL1W", "/works/OL1W"])
    items, status = sources.openlibrary_local(ctx)
    assert status["status"] == "found"
    assert len(items) == 1
    assert items[0].rating == 4.25
    assert items[0].ratings_count == 4
    assert items[0].synopsis == "A classic novel."
    assert ctx.local_ol_rating and ctx.local_ol_synopsis

    _, none_st = sources.openlibrary_local(sources.Context(ol_works=["/works/OL2W"]))
    assert none_st["status"] == "none"

    def boom():
        raise sqlite3.OperationalError("corrupt")

    monkeypatch.setattr(ident, "connect", boom)
    _, err_st = sources.openlibrary_local(sources.Context(ol_works=["/works/OL1W"]))
    assert err_st["status"] == "error"


def test_audible_local_reads_archive_and_handles_missing(tmp_path):
    assert sources.audible_local(sources.Context())[1]["status"] == "skipped"
    assert (
        sources.audible_local(sources.Context(asins=["B001"]), archive=str(tmp_path / "none.sqlite"))[1]["status"]
        == "skipped"
    )

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
                },
                {"body": "   "},
            ],
        }
    }
    c.execute("INSERT INTO product VALUES (?, ?, ?)", ("B001", "uk", zlib.compress(json.dumps(payload).encode())))
    c.execute("INSERT INTO product VALUES (?, ?, ?)", ("B003", "uk", b"not-zlib"))
    c.commit()
    c.close()

    ctx = sources.Context(asins=["B001", "B002", "B003"])
    items, status = sources.audible_local(ctx, archive=str(arch))
    assert status["status"] == "found"
    assert len(items) == 1
    assert items[0].rating == 4.6
    assert items[0].ratings_count == 200
    assert items[0].synopsis == "Great audiobook."
    assert items[0].reviews[0]["user"] == "Alice"
    assert "B001" in ctx.local_audible
    assert "B002" not in ctx.local_audible


def test_sidecar_reads_metadata_json_and_handles_missing(tmp_path):
    assert sources.sidecar(sources.Context())[1]["status"] == "skipped"
    assert sources.sidecar(sources.Context(folder=str(tmp_path / "missing")))[1]["status"] == "skipped"
    assert sources.sidecar(sources.Context(folder=str(tmp_path)))[1]["status"] == "none"

    (tmp_path / "empty.metadata.json").write_text("{}")
    assert sources.sidecar(sources.Context(folder=str(tmp_path)))[1]["status"] == "none"
    (tmp_path / "empty.metadata.json").unlink()

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


def test_hardcover_asin_search_and_error_branches(monkeypatch):
    monkeypatch.setattr("app.providers.hardcover.key", lambda: "")
    assert sources.hardcover(sources.Context(title="Book"))[1]["status"] == "skipped"

    monkeypatch.setattr("app.providers.hardcover.key", lambda: "tok")
    assert sources.hardcover(sources.Context())[1]["status"] == "skipped"

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

    # Rate limit and GraphQL error branches
    monkeypatch.setattr(net, "request", lambda *a, **k: reply(429, error="slow down"))
    assert sources.hardcover(sources.Context(asins=["B001"]))[1]["status"] == "limited"

    monkeypatch.setattr(
        net, "request", lambda *a, **k: reply(200, json.dumps({"errors": [{"message": "bad"}]}).encode())
    )
    assert sources.hardcover(sources.Context(title="Dune"))[1]["status"] == "error"

    monkeypatch.setattr(net, "request", lambda *a, **k: reply(200, b"not json"))
    assert sources.hardcover(sources.Context(title="Dune"))[1]["status"] == "error"


def test_audible_online_and_skip_when_local_complete(monkeypatch):
    assert sources.audible_online(sources.Context())[1]["status"] == "skipped"
    assert sources.audible_online(sources.Context(asins=["B001"], local_audible={"B001"}))[1]["status"] == "skipped"

    def fake_get(url, *, timeout=30):
        if "B001" in url:
            return reply(404)
        if "B002" in url:
            return reply(200, b"bad json")
        return reply(
            200,
            json.dumps(
                {
                    "product": {
                        "asin": "B003",
                        "publisher_summary": "Online audible blurb.",
                        "rating": {"overall_distribution": {"average_rating": 4.5, "num_ratings": 80}},
                    }
                }
            ).encode(),
        )

    monkeypatch.setattr(net, "get", fake_get)
    items, status = sources.audible_online(sources.Context(asins=["B001", "B002", "B003"]))
    assert status["status"] == "found"
    assert items[0].ref == "B003"
    assert items[0].synopsis == "Online audible blurb."

    monkeypatch.setattr(net, "get", lambda *a, **k: reply(429, error="limit"))
    assert sources.audible_online(sources.Context(asins=["B004"]))[1]["status"] == "limited"


def test_online_sources_audnexus_openlibrary_google_apple(monkeypatch):
    assert sources.audnexus(sources.Context())[1]["status"] == "skipped"
    assert sources.openlibrary_online(sources.Context())[1]["status"] == "skipped"
    assert (
        sources.openlibrary_online(
            sources.Context(ol_works=["/works/OL1W"], local_ol_rating=True, local_ol_synopsis=True)
        )[1]["status"]
        == "skipped"
    )
    assert sources.googlebooks(sources.Context())[1]["status"] == "skipped"
    assert sources.applebooks(sources.Context())[1]["status"] == "skipped"

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

    # 429 & bad JSON branches
    monkeypatch.setattr(net, "get", lambda *a, **k: reply(429, error="limit"))
    assert sources.audnexus(ctx)[1]["status"] == "limited"
    assert sources.googlebooks(ctx)[1]["status"] == "limited"
    assert sources.applebooks(ctx)[1]["status"] == "limited"

    monkeypatch.setattr(net, "get", lambda *a, **k: reply(200, b"bad"))
    assert sources.audnexus(ctx)[1]["status"] == "error"
    assert sources.googlebooks(ctx)[1]["status"] == "error"
    assert sources.applebooks(ctx)[1]["status"] == "error"


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
        extra={"genres": ["Sci-Fi"], "moods": ["Epic"], "content_warnings": ["Sand"]},
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
    assert summary["tags"] == {"genres": ["Sci-Fi"], "moods": ["Epic"], "content_warnings": ["Sand"]}


def test_runner_fetch_sweep_enqueue_and_api(monkeypatch):
    class FakeSession:
        def __init__(self):
            self.added = []
            self.listen = Listen(kind="book", label="Dune")
            self.listen.id = 42
            self.work = Work(kind="work", title="Dune")
            self.work.id = 7
            self.part = SimpleNamespace(work_id=7, detail={"authors": ["Frank Herbert"]})
            self.identifiers = [
                SimpleNamespace(scheme="asin", value="B001"),
                SimpleNamespace(scheme="isbn", value="9780441172719"),
                SimpleNamespace(scheme="ol_work", value="/works/OL1W"),
            ]
            self.scalar_results = [None, None]
            self.scalars_results = [
                self.identifiers,
                [SimpleNamespace(detail={"path": "/books/Dune"})],
            ]

        def get(self, model, key):
            if model is Listen:
                return self.listen if key == 42 else None
            return None

        def scalar(self, q):
            return self.scalar_results.pop(0) if self.scalar_results else None

        def scalars(self, q):
            class _Seq(list):
                def first(self):
                    return self[0] if self else None

            if self.scalars_results:
                return _Seq(self.scalars_results.pop(0))
            return _Seq()

        def add(self, obj):
            if isinstance(obj, EnrichmentRun):
                obj.id = 10
            self.added.append(obj)

        def flush(self):
            pass

        def commit(self):
            pass

        def __enter__(self):
            return self

        def __exit__(self, *a):
            pass

    fake_s = FakeSession()
    monkeypatch.setattr("app.enrichment.run.accepted_work", lambda s, lid: (fake_s.work, fake_s.part))

    def good_src(ctx):
        return [sources.Result("hardcover", "77", rating=4.5, synopsis="Good")], dict(
            source="hardcover", status="found", detail="", count=1
        )

    def bad_src(ctx):
        raise RuntimeError("boom")

    created_run = run.fetch(fake_s, fake_s.listen, run_sources=[("hardcover", good_src), ("broken", bad_src)])
    assert created_run.finished_at is not None
    assert created_run.sources[0]["status"] == "found"
    assert created_run.sources[1]["status"] == "error"
    assert any(isinstance(x, EnrichmentResult) and x.rating == 4.5 for x in fake_s.added)

    # Test sweep, enqueue, sweep_forever
    class FakeJobs:
        def __init__(self):
            self.submitted = []

        def submit(self, domain, kind, **kw):
            self.submitted.append((domain, kind, kw))

        def tasks(self, domain):
            return [{"state": "queued", "options": {"listen_id": 42}}]

    jobs = FakeJobs()
    orig_unfetched = run.unfetched
    orig_pending = run.pending
    monkeypatch.setattr(run, "unfetched", lambda s: [42])
    assert run.sweep(jobs, fake_s) == 1
    assert jobs.submitted[-1] == ("enrichment", "fetch", {"retry": True, "listen_id": 42})

    monkeypatch.setattr(run, "pending", lambda s, **kw: [42])
    assert run.enqueue(jobs, fake_s) == [42]

    stop = threading.Event()

    def stop_soon(j, s):
        stop.set()
        return 0

    monkeypatch.setattr("app.store.api._engine", lambda: None)
    monkeypatch.setattr("app.enrichment.run.Session", lambda eng: fake_s)
    monkeypatch.setattr(run, "sweep", stop_soon)
    run.sweep_forever(jobs, stop, 0.01)

    # Test summary_for_listens empty + non-empty (both without and with runs)
    assert run.summary_for_listens(fake_s, []) == {}
    sums = run.summary_for_listens(fake_s, [42], jobs=jobs)
    assert sums[42]["status"] == "queued"

    r_done = EnrichmentRun(listen_id=42, sources=[{"source": "hardcover", "status": "found", "detail": "", "count": 1}])
    r_done.id = 99
    r_done.finished_at = datetime.now(UTC).replace(tzinfo=None)
    res_done = EnrichmentResult(run_id=99, source="hardcover", rating=4.3, synopsis="Done blurb.")
    fake_s.scalars_results = [[r_done], [res_done]]
    sums_done = run.summary_for_listens(fake_s, [42])
    assert sums_done[42]["status"] == "done" and sums_done[42]["synopsis"]["text"] == "Done blurb."

    # Test is_library_book, changed_at, unfetched, pending, fetch_job
    monkeypatch.setattr(run, "pending", orig_pending)
    monkeypatch.setattr(run, "unfetched", orig_unfetched)
    fake_s.scalar_results = [42, None]
    assert run.is_library_book(fake_s, 42) is True
    assert run.changed_at(fake_s, 42) is None
    fake_s.scalars_results = [[42]]
    assert run.unfetched(fake_s) == [42]

    t_old = datetime(2025, 1, 1)
    t_new = datetime(2026, 1, 1)
    r_old = EnrichmentRun(listen_id=42, listen_changed_at=t_old, sources=[])
    r_old.id = 5
    r_old.finished_at = t_old
    fake_s.scalars_results = [[42], [r_old]]
    fake_s.scalar_results = [t_new]
    assert run.pending(fake_s, listen_ids=[42]) == [42]

    fake_s.scalars_results = [[42], [r_old]]
    fake_s.scalar_results = [t_old]
    assert run.pending(fake_s, listen_ids=[42]) == []

    monkeypatch.setattr(run, "fetch", lambda s, l: created_run)
    job_res = run.fetch_job(42)
    assert job_res["listen_id"] == 42 and job_res["run_id"] == 10

    # Test FastAPI endpoints
    app = FastAPI()
    app.include_router(api.router)
    app.dependency_overrides[get_session] = lambda: fake_s
    client = TestClient(app)

    assert client.get("/api/enrichment/999").status_code == 404
    assert client.post("/api/enrichment/999/fetch").status_code == 404
    assert client.post("/api/enrichment/42/fetch").status_code == 503
    assert client.post("/api/enrichment/fetch").status_code == 503

    app.state.jobs = jobs
    r_get = client.get("/api/enrichment/42")
    assert r_get.status_code == 200 and r_get.json()["status"] == "queued"
    r_one = client.post("/api/enrichment/42/fetch")
    assert r_one.status_code == 200 and r_one.json()["queued"] is True
    fake_s.scalars_results = [[42], []]
    r_all = client.post("/api/enrichment/fetch")
    assert r_all.status_code == 200 and r_all.json()["queued"] == 1


def test_hardcover_asin_hit_and_empty_online_responses(monkeypatch):
    monkeypatch.setattr("app.providers.hardcover.key", lambda: "tok")

    def fake_req(url, *, method="GET", headers=None, body=None, timeout=30):
        q = json.loads(body.decode())["query"]
        if "editions(where: {asin:" in q:
            return reply(200, json.dumps({"data": {"editions": [{"book": {"id": 88}}]}}).encode())
        return reply(
            200, json.dumps({"data": {"books": [{"id": 88, "title": "X", "rating": 4.0}], "user_books": []}}).encode()
        )

    monkeypatch.setattr(net, "request", fake_req)
    items, st = sources.hardcover(sources.Context(asins=["B001"]))
    assert st["status"] == "found" and items[0].ref == "88"

    # ISBN fallback when ASIN is absent
    def fake_isbn_req(url, *, method="GET", headers=None, body=None, timeout=30):
        q = json.loads(body.decode())["query"]
        if "isbn_13:" in q:
            return reply(200, json.dumps({"data": {"editions": [{"book": {"id": 89}}]}}).encode())
        return reply(
            200, json.dumps({"data": {"books": [{"id": 89, "title": "Y", "rating": 4.2}], "user_books": []}}).encode()
        )

    monkeypatch.setattr(net, "request", fake_isbn_req)
    items_isbn, st_isbn = sources.hardcover(sources.Context(isbns=["9780441172719"]))
    assert st_isbn["status"] == "found" and items_isbn[0].ref == "89"

    monkeypatch.setattr(
        net,
        "request",
        lambda *a, **k: reply(200, json.dumps({"data": {"editions": [{"book": {"id": 88}}], "books": []}}).encode()),
    )
    assert sources.hardcover(sources.Context(asins=["B001"]))[1]["status"] == "none"

    monkeypatch.setattr(
        net,
        "request",
        lambda *a, **k: reply(
            200,
            json.dumps({"data": {"editions": [{"book": {"id": 88}}], "books": [{"id": 88, "title": "X"}]}}).encode(),
        ),
    )
    assert sources.hardcover(sources.Context(asins=["B001"]))[1]["status"] == "none"

    monkeypatch.setattr(
        net,
        "request",
        lambda *a, **k: reply(
            200, json.dumps({"data": {"editions": [], "search": {"results": {"hits": []}}}}).encode()
        ),
    )
    assert sources.hardcover(sources.Context(title="Unknown", asins=["B001"]))[1]["status"] == "none"

    monkeypatch.setattr(net, "get", lambda *a, **k: reply(200, json.dumps({"items": [], "results": []}).encode()))
    ctx = sources.Context(title="X", asins=["B001"], isbns=["9780441172719"], ol_works=["/works/OL1W"])
    assert sources.audnexus(ctx)[1]["status"] == "none"
    assert sources.googlebooks(ctx)[1]["status"] == "none"
    assert sources.applebooks(ctx)[1]["status"] == "none"
    assert sources.openlibrary_online(ctx)[1]["status"] == "none"

    monkeypatch.setattr(net, "get", lambda *a, **k: reply(200, b"not json"))
    assert sources.openlibrary_online(ctx)[1]["status"] == "error"

    monkeypatch.setattr(net, "get", lambda *a, **k: reply(500, error="down"))
    assert sources.openlibrary_online(ctx)[1]["status"] == "error"


def test_summarize_dedup_and_pending_limit_and_missing_listen(monkeypatch):
    r = EnrichmentRun(listen_id=1, sources=[])
    r.finished_at = datetime.now(UTC).replace(tzinfo=None)
    res1 = EnrichmentResult(
        run_id=1,
        source="hardcover",
        rating=4.5,
        synopsis="Same blurb.",
        reviews=[{"user": "a", "text": "   "}],
    )
    res2 = EnrichmentResult(
        run_id=1,
        source="audible_local",
        rating=4.5,
        synopsis="Same blurb.",
    )
    res3 = EnrichmentResult(
        run_id=1,
        source="audible",
        rating=4.6,
        synopsis="Different blurb.",
    )
    sm = run.summarize(r, [res1, res2, res3])
    assert len(sm["synopses"]) == 2
    assert len(sm["ratings"]) == 2
    assert sm["reviews"] == []

    class S:
        def get(self, m, k):
            return None

        def __enter__(self):
            return self

        def __exit__(self, *a):
            pass

    monkeypatch.setattr("app.store.api._engine", lambda: None)
    monkeypatch.setattr("app.enrichment.run.Session", lambda eng: S())
    with pytest.raises(ValueError, match="999"):
        run.fetch_job(999)


def test_cli_cmd_enrichment(monkeypatch):
    from types import SimpleNamespace

    from app import cli

    fake_listen = Listen(kind="book", label="Dune")
    fake_listen.id = 42
    fake_run = EnrichmentRun(
        listen_id=42, sources=[{"source": "hardcover", "status": "found", "detail": "", "count": 1}]
    )

    class FakeS:
        def get(self, m, k):
            return fake_listen

        def commit(self):
            pass

        def __enter__(self):
            return self

        def __exit__(self, *a):
            pass

    monkeypatch.setattr("strata_python.db.connect", lambda name: None)
    monkeypatch.setattr("sqlalchemy.orm.Session", lambda eng: FakeS())
    monkeypatch.setattr("app.enrichment.run.enqueue", lambda q, s, **kw: [42])
    monkeypatch.setattr("app.enrichment.run.pending", lambda s, **kw: [42])
    monkeypatch.setattr("app.enrichment.run.fetch", lambda s, l: fake_run)

    assert cli.cmd_enrichment(SimpleNamespace(ids=[], force=False, limit=None, queue=True)) == 0
    assert cli.cmd_enrichment(SimpleNamespace(ids=[42], force=False, limit=None, queue=False)) == 0


def test_tasks_execute_enrichment_and_net_request(monkeypatch):
    from app.server import tasks

    monkeypatch.setattr("app.enrichment.run.fetch_job", lambda lid: {"listen_id": lid, "run_id": 1})
    assert tasks._execute(None, "enrichment", "fetch", {"listen_id": 42}) == {"listen_id": 42, "run_id": 1}

    class FakeResp:
        status = 200

        def read(self, n):
            return b'{"ok":true}'

        def __enter__(self):
            return self

        def __exit__(self, *a):
            pass

    monkeypatch.setattr("urllib.request.urlopen", lambda req, timeout=30: FakeResp())
    r = net.request("https://example.com/api", method="POST", headers={"X-Test": "1"}, body=b"{}")
    assert r.ok and r.json() == {"ok": True}

    assert not net.request("http://example.com/api").ok
    monkeypatch.setattr("app.covers.net._cool_until", {"example.com": 1e18})
    assert net.request("https://example.com/api").limited
    monkeypatch.setattr("app.covers.net._cool_until", {})

    # Test pending limit=1
    class FakeS:
        def __init__(self):
            self.n = 0

        def scalars(self, q):
            class _Seq(list):
                def first(self):
                    return self[0] if self else None

            self.n += 1
            return _Seq([42, 43] if self.n == 1 else [])

        def scalar(self, q):
            return None

    assert run.pending(FakeS(), limit=1) == [42]


def test_enrichment_summary_generation(monkeypatch):
    listen = Listen(label="Dune", kind="book")
    listen.id = 42
    r = EnrichmentRun(listen_id=42, sources=[{"source": "hardcover", "status": "found", "detail": "", "count": 1}])
    r.id = 9
    res = EnrichmentResult(
        run_id=9,
        source="hardcover",
        synopsis="Desert planet politics.",
        reviews=[{"user": "a", "rating": 5, "text": "Great narration.", "spoilers": False, "likes": 3}],
        extra={"genres": ["Sci-Fi"], "moods": ["Epic"]},
    )

    class FakeSession:
        def __init__(self, items):
            self.items = items
            self.flushed = False

        def scalars(self, q):
            return self.items

        def flush(self):
            self.flushed = True

    monkeypatch.setattr("app.enrichment.run.accepted_work", lambda s, lid: None)
    s = FakeSession([res])
    called = []
    ok = run.summarise_run(s, r, listen, call_fn=lambda t, inp: called.append((t, inp)) or ("Crisp summary.", "haiku"))
    assert ok and s.flushed and r.summary == "Crisp summary." and r.summary_model == "haiku"
    assert called[0][0] == "Dune" and len(called[0][1]) == 3

    out = run.summarize(r, [res])
    assert out["summary"] == "Crisp summary." and out["summary_model"] == "haiku"

    # Service errors are swallowed without failing enrichment
    r2 = EnrichmentRun(listen_id=42, sources=[])
    r2.id = 10
    assert not run.summarise_run(s, r2, listen, call_fn=lambda t, inp: (_ for _ in ()).throw(RuntimeError("down")))
    assert r2.summary is None

    # call_strata_models_summarise reads job["result"]["text"] and ["model"]
    class FakeJobResp:
        def __init__(self, body: dict):
            self._body = json.dumps(body).encode("utf8")

        def read(self):
            return self._body

        def __enter__(self):
            return self

        def __exit__(self, *a):
            pass

    monkeypatch.setattr(
        "urllib.request.urlopen",
        lambda req, timeout=15: FakeJobResp({"id": 1, "status": "done", "result": {"text": "Ok.", "model": "haiku"}}),
    )
    assert run.call_strata_models_summarise("Dune", [{"source": "s", "text": "t"}]) == ("Ok.", "haiku")
