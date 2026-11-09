"""Ingest HTTP contracts and background task dispatch."""

import pytest
from job_client import JobClient as TestClient

from app import catalogue
from app.ingest import pipeline, policy, store
from app.ingest.worker import Worker
from app.server.app import create_app


@pytest.fixture
def client(monkeypatch):
    monkeypatch.setattr(Worker, "inline", True)
    with TestClient(create_app()) as c:
        yield c


def test_ingest_dashboard_and_versioned_policy(client):
    r = client.get("/api/ingest").json()
    assert r["runs"] == [] and r["policy"]["auto_process"] is False
    p = r["policy"]
    p["gates"]["work.score"] = {"min": 0.95}
    assert client.put("/api/ingest/policy", json=p).json()["revision"] == 1
    assert client.put("/api/ingest/policy", json={"auto_process": True}).status_code == 400
    assert client.get("/api/ingest/runs/missing").status_code == 400
    assert client.get("/api/ingest?offset=50").json()["offset"] == 50
    assert client.get("/api/ingest?offset=-1").status_code == 422


def test_ingest_dispatch_evaluation_and_resume(client, monkeypatch):
    b = catalogue.add_book(title="T")
    rid = store.create(b["id"], {})
    store.link(rid, [b["id"]])
    called = []
    monkeypatch.setattr(pipeline, "run", lambda **kw: called.append(kw) or [store.get(rid)])
    assert client.post("/api/ingest/run", json=dict(ids=[b["id"]], online=False)).status_code == 200
    assert called[-1]["ids"] == [b["id"]] and not called[-1]["online"]
    assert client.post("/api/ingest/run", json=dict(ids=[])).status_code == 400
    assert client.post("/api/ingest/run", json=dict(ids=[999])).status_code == 404
    assert client.post("/api/ingest/run", json=dict(limit=0)).status_code == 422
    assert client.post(f"/api/ingest/runs/{rid}/resume").status_code == 200
    assert called[-1]["resume"] == rid
    assert client.get(f"/api/ingest/runs/{rid}").json()["id"] == rid
    assert not client.post(f"/api/ingest/runs/{rid}/evaluate").json()["eligible"]
    assert client.post(f"/api/ingest/runs/{rid}/enqueue").status_code == 400
    assert client.get("/api/ingest").json()["runs"][0]["id"] == rid


def test_explicit_library_selection_dispatches_every_id(client, monkeypatch):
    ids = list(range(1, 1682))
    called = []
    validated = []
    monkeypatch.setattr(catalogue, "get_book", lambda bid: validated.append(bid) or dict(id=bid))
    monkeypatch.setattr(pipeline, "run", lambda **kw: called.append(kw) or [])
    response = client.post("/api/ingest/run", json=dict(ids=ids, limit=len(ids), online=False))
    assert response.status_code == 200
    assert validated == ids
    assert called == [dict(ids=ids, limit=1681, online=False)]
    # A high limit alone must not expand an automatic-discovery batch.
    assert client.post("/api/ingest/run", json=dict(limit=201)).status_code == 422
    assert client.post("/api/ingest/run", json=dict(ids=[1], limit=201)).status_code == 422
    assert len(called) == 1


def test_stage_approval_dispatches_continuation_only_once(client, monkeypatch):
    book = catalogue.add_book(title="T")
    rid = store.create(book["id"], {})
    called = []
    approved = []

    def approve(run_id, phase):
        if phase not in ("metadata", "quality"):
            raise ValueError("invalid review stage")
        changed = phase not in approved
        if changed:
            approved.append(phase)
        return dict(changed=changed, phase=phase)

    monkeypatch.setattr(pipeline, "approve_review", approve)
    monkeypatch.setattr(pipeline, "continue_review", lambda run_id: called.append(run_id) or store.get(run_id))
    assert client.post(f"/api/ingest/runs/{rid}/approve/metadata").json()["queued"]
    assert not client.post(f"/api/ingest/runs/{rid}/approve/metadata").json()["queued"]
    assert client.post(f"/api/ingest/runs/{rid}/approve/quality").json()["queued"]
    client.drain()
    assert called == [rid, rid]
    assert client.post(f"/api/ingest/runs/{rid}/approve/bad").status_code == 400


def test_enabling_metadata_automation_queues_existing_reviews_without_enabling_quality(client, monkeypatch):
    book = catalogue.add_book(title="T")
    rid = store.create(book["id"], {})
    called = []
    monkeypatch.setattr(pipeline, "approve_passing_metadata", lambda: [rid])
    monkeypatch.setattr(pipeline, "continue_review", lambda run_id: called.append(run_id) or store.get(run_id))
    value = client.get("/api/ingest").json()["policy"]
    value["auto_approve_metadata"] = True
    result = client.put("/api/ingest/policy", json=value).json()
    assert result["auto_approve_metadata"] and not result["auto_approve_quality"]
    client.drain()
    assert not result["auto_approve"] and not result["auto_process"] and called == [rid]


def test_review_pages_and_combined_operations_endpoint(client):
    b = catalogue.add_book(title="Waiting")
    rid = store.create(b["id"], {})
    store.finish(rid, "metadata_review")
    value = client.get("/api/operations").json()
    assert not value["busy"] and value["rows"][0]["run_id"] == rid
    assert client.get("/api/operations?completed=true").status_code == 200
    assert client.get("/api/ingest?phase=metadata").json()["total"] == 1
    assert client.get("/api/ingest?phase=quality").json()["total"] == 0
    assert client.get("/api/ingest?phase=other").status_code == 422
    assert client.get("/api/ingest?phase=metadata&sort=oldest").status_code == 200
    assert client.get("/api/ingest?phase=metadata&sort=other").status_code == 422


def test_source_endpoint_uses_saved_sample_filenames_without_ingesting(client, tmp_path):
    folder = tmp_path / "Chapters"
    folder.mkdir()
    for n in range(5):
        (folder / f"{n + 1:03}.mp3").write_bytes(b"audio")
    book = catalogue.add_book(title="A book", path=str(folder))
    rid = store.create(book["id"], {})
    store.event(
        rid,
        "inventory",
        "done",
        data=dict(root=str(folder), files=[dict(path=str(folder / f"{n + 1:03}.mp3")) for n in range(5)]),
    )
    value = client.get(f"/api/books/{book['id']}/source").json()
    assert value == dict(folder=str(folder), files=["001.mp3", "002.mp3", "003.mp3"], count=5, available=True)
    assert client.get("/api/books/99999/source").status_code == 404
    assert store.run_count() == 1


def test_listening_preview_http_contract(client, monkeypatch, tmp_path):
    from app.ingest import previews

    rid = store.create(catalogue.add_book(title="Sample")["id"], {})
    sample = tmp_path / "sample.mp3"
    sample.write_bytes(b"mp3")
    monkeypatch.setattr(previews, "prepare", lambda rid: dict(samples=[dict(index=0, status="generating")]))
    monkeypatch.setattr(previews, "status", lambda rid: dict(samples=[dict(index=0, status="ready")]))
    monkeypatch.setattr(previews, "audio", lambda rid, index: sample)
    assert client.post(f"/api/ingest/runs/{rid}/samples").json()["samples"][0]["status"] == "generating"
    assert client.get(f"/api/ingest/runs/{rid}/samples").json()["samples"][0]["status"] == "ready"
    r = client.get(f"/api/ingest/runs/{rid}/samples/0")
    assert r.content == b"mp3" and r.headers["content-type"] == "audio/mpeg"
    assert client.get(f"/api/ingest/runs/{rid}/samples/word").status_code == 422


def test_audiobook_identification_is_a_separate_queue_and_approval(client, monkeypatch):
    assert client.get("/api/ingest?phase=audiobook").status_code == 200
    book = catalogue.add_book(title="T")
    rid = store.create(book["id"], {})
    calls = []
    monkeypatch.setattr(
        pipeline, "approve_review", lambda run_id, phase: calls.append(phase) or dict(changed=True, phase=phase)
    )
    monkeypatch.setattr(pipeline, "continue_review", lambda run_id: store.get(run_id))
    assert client.post(f"/api/ingest/runs/{rid}/approve/audiobook").json()["queued"]
    assert calls == ["audiobook"]


def test_startup_reconciles_enabled_identification_automation(monkeypatch):
    value = policy.current()
    value["auto_approve_audiobook"] = True
    policy.save(value)
    calls = []
    monkeypatch.setattr(Worker, "inline", True)
    monkeypatch.setattr(pipeline, "approve_passing_metadata", lambda: calls.append("identification") or [])
    with TestClient(create_app()) as client:
        assert client.get("/api/health").status_code == 200
    assert calls == ["identification"]


def test_library_workflow_endpoint_includes_reviews_and_queued_work(client, monkeypatch):
    from app.ingest import operations

    monkeypatch.setattr(
        operations, "library", lambda ingest, process: dict(books={"1": ["metadata"], "2": ["processing"]})
    )
    assert client.get("/api/library/workflow").json() == dict(books={"1": ["metadata"], "2": ["processing"]})


def test_startup_restores_only_current_queued_collection_children(monkeypatch):
    monkeypatch.setattr(Worker, "inline", True)
    calls = []
    book = catalogue.add_book(title="Queued child")
    old = store.create(book["id"], dict(collection_run="parent"))
    store.finish(old, "queued")
    latest = store.create(book["id"], dict(collection_run="parent"))
    store.finish(latest, "queued")
    monkeypatch.setattr(pipeline, "continue_review", lambda rid: calls.append(rid) or store.get(rid))
    with TestClient(create_app()) as client:
        assert client.get("/api/health").status_code == 200
    assert calls == [latest]


def test_startup_queues_regrouping_for_pending_mixed_collections(monkeypatch, tmp_path):
    monkeypatch.setattr(Worker, "inline", True)
    root = tmp_path / "collection"
    root.mkdir()
    book = catalogue.add_book(title="Collection", path=str(root))
    rid = store.create(book["id"], {})
    files = []
    called = []
    for title in ("Alpha Novel", "Beta Novel"):
        p = root / f"{title}.mp3"
        p.write_bytes(b"audio")
        files.append(dict(path=str(p), probe=dict(tags=dict(album=title, artist="Test Writer"))))
    store.event(rid, "inventory", "done", data=dict(root=str(root), files=files))
    store.finish(rid, "metadata_review")
    monkeypatch.setattr(pipeline, "regroup_collection", lambda run_id: called.append(run_id) or [])
    with TestClient(create_app()) as client:
        assert client.get("/api/health").status_code == 200
    assert called == [rid]


def test_recording_choice_endpoint_passes_the_saved_candidate_key(client, monkeypatch):
    book = catalogue.add_book(title="Choice")
    store.create(book["id"], {})
    calls = []
    monkeypatch.setattr(
        pipeline, "choose_recording", lambda run_id, key: calls.append((run_id, key)) or dict(changed=True, key=key)
    )
    rid = store.create(catalogue.add_book(title="Choice")["id"], {})
    assert client.put(f"/api/ingest/runs/{rid}/recording", json=dict(key="audible:B000000001")).json()["changed"]
    assert calls == [(rid, "audible:B000000001")]


def test_library_covers_returns_confirmed_local_ids(client, monkeypatch):
    from app import details

    monkeypatch.setattr(details, "available_cover_ids", lambda: [12, 34])
    assert client.get("/api/library/covers").json() == dict(ids=[12, 34])


def test_get_requests_use_saved_evidence_and_never_resolve_old_metadata(client, monkeypatch):
    from app import sources
    from app.ingest import metadata

    book = catalogue.add_book(title="Archived", author="Writer")
    rid = store.create(book["id"], {})
    store.event(
        rid,
        "inventory",
        "done",
        data=dict(root="/offline/book", members=[book], files=[], representations=[dict(id="audio")]),
    )
    store.event(rid, "identity", "done", data=dict(book=book, kind="book"))
    store.event(rid, "providers", "done", data=dict(providers={}))
    store.event(
        rid,
        "metadata",
        "done",
        data=dict(
            version=1,
            resolved=sources.resolve(dict(book, title="Saved title"), {}),
            work=dict(resolved=dict(title="Saved title", authors=["Writer"])),
        ),
    )
    store.finish(rid, "metadata_review")

    def forbidden(*a, **kw):
        raise AssertionError("GET must not re-identify audio")

    monkeypatch.setattr(metadata, "resolve", forbidden)
    monkeypatch.setattr(metadata, "refine_recording", forbidden)
    for url in ("/api/library/workflow", "/api/operations", "/api/ingest?phase=metadata", f"/api/ingest/runs/{rid}"):
        response = client.get(url)
        assert response.status_code == 200, response.text
    assert client.get("/api/ingest?phase=metadata").json()["runs"][0]["title"] == "Saved title"
    assert store.get(rid)["state"] == "metadata_review"


def test_processing_requests_only_persist_jobs_until_a_worker_claims_them(monkeypatch, tmp_path):
    from fastapi.testclient import TestClient as RawClient

    from app import details, matcher, scan
    from app.ingest import previews

    app = create_app()
    client = RawClient(app)
    calls = []
    book = catalogue.add_book(title="Queued source")
    rid = store.create(book["id"], {})

    def forbidden(*a, **kw):
        raise AssertionError("processing ran in the request")

    for module, name in (
        (pipeline, "approve_review"),
        (pipeline, "evaluate"),
        (pipeline, "choose_recording"),
        (pipeline, "rate_quality"),
        (pipeline, "enqueue"),
        (previews, "prepare"),
        (details, "describe"),
        (matcher, "match_pending"),
        (scan, "scan"),
    ):
        monkeypatch.setattr(module, name, forbidden)
    requests = [
        ("post", "/api/scan", dict(json=dict(folder=str(tmp_path)))),
        ("post", "/api/match", {}),
        ("post", f"/api/books/{book['id']}/inspect", {}),
        ("post", f"/api/ingest/runs/{rid}/samples", {}),
        ("post", f"/api/ingest/runs/{rid}/approve/metadata", {}),
        ("post", f"/api/ingest/runs/{rid}/evaluate", {}),
        ("put", f"/api/ingest/runs/{rid}/recording", dict(json=dict(key="saved"))),
        ("put", f"/api/ingest/runs/{rid}/quality", dict(json=dict(rating="good"))),
        ("post", f"/api/ingest/runs/{rid}/enqueue", {}),
    ]
    receipts = []
    for method, url, kw in requests:
        response = getattr(client, method)(url, **kw)
        assert response.status_code == 202, response.text
        receipts.append(response.json()["job_id"])
    for ident in receipts:
        assert client.get(f"/api/jobs/{ident}").json()["state"] == "queued"
    assert len(app.state.jobs.tasks()) == len(requests)
    monkeypatch.setattr(scan, "scan", lambda folder: calls.append(folder) or iter([]))
    assert app.state.jobs.run_one() and calls == [str(tmp_path)]
    assert client.get(f"/api/jobs/{receipts[0]}").json()["result"] == dict(found=0, added=0, existing=0, replaced=0)
    assert client.get("/api/jobs/missing").status_code == 404


def test_json_asset_reads_use_database_records_without_directory_traversal(client, monkeypatch):
    from app import details, mirror, read_models

    book = catalogue.add_book(title="Stored")
    read_models.save("cover", book["id"], dict(path="/cached/cover.jpg"))
    read_models.save("output", book["id"], dict(chapters=[]))
    catalogue.set_processed(book["id"], dict(status="done", finished="now", out_dir="/offline/output"))

    def forbidden(*a, **kw):
        raise AssertionError("read traversed artifact or source folders")

    monkeypatch.setattr(mirror, "dir_for", forbidden)
    monkeypatch.setattr(details, "audio_files", forbidden)
    assert client.get("/api/library/covers").json() == dict(ids=[book["id"]])
    assert client.get(f"/api/books/{book['id']}/output").json() == dict(chapters=[])
    assert client.get(f"/api/books/{book['id']}/details").status_code == 200


def test_match_review_http_reads_are_saved_and_decisions_enqueue(client, monkeypatch):
    from app.ingest import match_review

    calls = []
    monkeypatch.setattr(match_review, "page", lambda *args: dict(rows=[], total=0, offset=args[1], limit=10))
    monkeypatch.setattr(match_review, "detail", lambda rid: dict(id=rid))
    assert client.get("/api/match-reviews?mode=shortlist&offset=10&q=title").json()["offset"] == 10
    assert client.get("/api/match-reviews/saved").json() == dict(id="saved")
    assert client.get("/api/match-reviews?mode=bad").status_code == 422
    assert client.get("/api/match-reviews?offset=-1").status_code == 422
    monkeypatch.setattr(store, "run_book_id", lambda rid: 1)
    monkeypatch.setattr(match_review, "accept", lambda **kw: calls.append(kw) or dict(changed=False))
    assert client.post("/api/match-reviews/saved/accept", json=dict(basis="a" * 64)).json() == dict(changed=False)
    assert calls == [dict(run_id="saved", expected="a" * 64)]
    assert client.post("/api/match-reviews/saved/unapprove", json={}).status_code == 422
