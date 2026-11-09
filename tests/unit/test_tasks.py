"""Queued action dispatch and bootstrap of saved artifacts, with synthetic sources."""

import json
from pathlib import Path
from types import SimpleNamespace

import pytest

from app import catalogue, mirror, read_models
from app.ingest import pipeline, store
from app.server import tasks


@pytest.mark.parametrize(
    "kind,options,target",
    [
        ("evaluate", {}, "evaluate"),
        ("recording", dict(key="saved"), "choose_recording"),
        ("quality", dict(rating="good"), "rate_quality"),
        ("enqueue", {}, "enqueue"),
    ],
)
def test_ingest_actions_publish_a_saved_view_after_worker_validation(monkeypatch, kind, options, target):
    calls = []
    monkeypatch.setattr(pipeline, target, lambda **kw: calls.append(kw) or dict(changed=True))
    monkeypatch.setattr(pipeline, "publish_run", lambda rid: calls.append(rid))
    assert tasks.execute(None, "ingest", kind, dict(run_id="saved", **options)) == dict(changed=True)
    assert calls == [dict(run_id="saved", **options), "saved"]


@pytest.mark.parametrize("state", ["metadata_review", "queued"])
def test_metadata_refresh_reuses_the_worker_and_publishes_the_new_run(monkeypatch, state):
    calls = []
    submitted = []
    jobs = SimpleNamespace(submit=lambda *a, **kw: submitted.append((a, kw)))
    monkeypatch.setattr(pipeline, "refresh_metadata", lambda **kw: calls.append(kw) or dict(id="fresh", state=state))
    monkeypatch.setattr(pipeline, "publish_run", lambda rid: calls.append(rid))
    monkeypatch.setattr(pipeline.store, "get", lambda rid: dict(state=state, events=[]))
    assert tasks.execute(jobs, "ingest", "refresh", dict(run_id="saved", book_only=True)) == ["fresh"]
    assert calls == [dict(run_id="saved", book_only=True), "fresh"]
    assert submitted == ([(("ingest", "continue"), dict(run_id="fresh"))] if state == "queued" else [])


def test_inspection_is_worker_owned_and_legacy_worker_errors_fail_jobs(monkeypatch):
    book = catalogue.add_book(title="Inspect")
    calls = []
    monkeypatch.setattr(tasks.details, "describe", lambda b: calls.append("probe") or dict(files=None))
    monkeypatch.setattr(tasks.details, "cover", lambda b: calls.append("cover"))
    assert tasks.execute(None, "system", "inspect", dict(book_id=book["id"])) == dict(files=None)
    assert calls == ["probe", "cover"]
    with pytest.raises(ValueError, match="unknown ingest task"):
        tasks.execute(None, "ingest", "bad", {})
    for domain in ("system", "process", "unknown"):
        with pytest.raises(ValueError, match="unknown job kind"):
            tasks.execute(None, domain, "bad", {})
    catalogue.remove_book(book["id"])
    assert tasks.execute(None, "system", "mirror", dict(book_id=book["id"])) is None


def test_scan_queues_inspection_only_for_uncached_books_and_never_probes_inline(tmp_path, monkeypatch):
    entries = [
        dict(
            path=str(tmp_path / f"{name}.mp3"),
            title=name,
            author="",
            narrator="",
            asin="",
            kind="book",
            file_count=1,
            series_position=None,
        )
        for name in ("New", "Covered", "No artwork")
    ]
    catalogue.add_scanned(entries[1:])
    existing = {b["title"]: b["id"] for b in catalogue.list_books()}
    read_models.save("cover", existing["Covered"], dict(path="saved.jpg"))
    read_models.save("details", existing["No artwork"], dict(files=None))
    monkeypatch.setattr(tasks.scan, "scan", lambda folder: iter(entries))
    monkeypatch.setattr(tasks.details, "describe", lambda *a: pytest.fail("scan must enqueue inspection"))
    monkeypatch.setattr(tasks.details, "cover", lambda *a: pytest.fail("scan must enqueue artwork"))
    calls = []
    jobs = SimpleNamespace(submit=lambda *a, **kw: calls.append((a, kw)))
    result = tasks.execute(jobs, "system", "scan", dict(folder=str(tmp_path)))
    new = next(b["id"] for b in catalogue.list_books() if b["title"] == "New")
    assert result == dict(found=3, added=1, existing=2, replaced=0)
    assert calls == [(("system", "inspect"), dict(book_id=new))]
    read_models.save("details", new, dict(files=None))
    calls.clear()
    assert tasks.execute(jobs, "system", "scan", dict(folder=str(tmp_path)))["existing"] == 3
    assert not calls


def test_bootstrap_indexes_legacy_assets_outputs_and_plans_without_audio_analysis(tmp_path, monkeypatch):
    books = [catalogue.add_book(title=name) for name in ("Saved", "Missing")]
    folder = Path(mirror.dir_for(books[0], probe_source=False))
    folder.mkdir(parents=True, exist_ok=True)
    (folder / "cover.png").write_bytes(b"cover")
    (folder / "metadata.json").write_text(json.dumps(dict(audible_file=dict(title="Saved Audible"))))
    out = tmp_path / "output"
    out.mkdir()
    metadata = dict(chapters=[dict(file="01.mp3")])
    (out / "metadata.json").write_text(json.dumps(metadata))
    for i, book in enumerate(books):
        catalogue.set_processed(
            book["id"], dict(status="done", finished="now", out_dir=str(out if i == 0 else tmp_path / "absent"))
        )
        catalogue.set_plan(book["id"], "proposed", plan=dict(valid=i == 0))
    rid = store.create(books[0]["id"], {})
    published = []
    monkeypatch.setattr(pipeline, "publish_run", lambda value: published.append(value))

    def summary(plan):
        if not plan["valid"]:
            raise KeyError("incomplete old plan")
        return dict(chapters=1)

    monkeypatch.setattr(tasks.pq, "summary", summary)
    monkeypatch.setattr(tasks.providers, "status", lambda: [dict(name="saved")])
    monkeypatch.setattr(tasks.details, "audio_files", lambda *a: pytest.fail("bootstrap must not inspect sources"))
    assert tasks.bootstrap() == dict(indexed=2)
    assert read_models.get("output", books[0]["id"]) == metadata
    assert read_models.get("audible", books[0]["id"]) == dict(title="Saved Audible")
    assert read_models.get("cover", books[0]["id"]) == dict(path=str(folder / "cover.png"))
    assert read_models.get("plan", books[0]["id"]) == dict(chapters=1)
    assert published == [rid]


def test_approval_continuation_is_queued_only_for_changed_consent(monkeypatch):
    submitted = []
    published = []
    queue = SimpleNamespace(submit=lambda *a, **kw: submitted.append((a, kw)))
    monkeypatch.setattr(pipeline, "publish_run", lambda rid: published.append(rid))
    monkeypatch.setattr(pipeline, "approve_review", lambda *a: dict(changed=False, phase="metadata"))
    assert not tasks.execute(queue, "ingest", "approve", dict(run_id="run", phase="metadata"))["queued"]
    assert not submitted and published == ["run"]
    monkeypatch.setattr(pipeline, "approve_review", lambda *a: dict(changed=True, phase="metadata"))
    assert tasks.execute(queue, "ingest", "approve", dict(run_id="run", phase="metadata"))["queued"]
    assert submitted == [(("ingest", "continue"), dict(run_id="run"))]


def test_policy_refresh_and_recovered_approval_keep_durable_consent(monkeypatch):
    book = catalogue.add_book(title="Recovered")
    rid = store.create(book["id"], {})
    store.finish(rid, "metadata_review")
    published = []
    submitted = []
    monkeypatch.setattr(pipeline, "publish_run", lambda value: published.append(value))
    assert tasks.execute(None, "system", "views", {}) == dict(updated=1)
    monkeypatch.setattr(pipeline, "approve_review", lambda *a: dict(changed=False, phase="metadata"))
    monkeypatch.setattr(pipeline, "approval_valid", lambda *a: True)
    queue = SimpleNamespace(submit=lambda *a, **kw: submitted.append((a, kw)))
    tasks.execute(queue, "ingest", "approve", dict(run_id=rid, phase="metadata", _recovered=True))
    assert store.get(rid)["state"] == "running" and submitted == [(("ingest", "continue"), dict(run_id=rid))]


@pytest.mark.parametrize("action", ["accept", "unapprove", "confirm", "reject"])
def test_match_review_actions_are_queued_and_only_acceptance_schedules_continuation(monkeypatch, action):
    from app.ingest import match_review

    calls = []
    jobs = SimpleNamespace(submit=lambda *args, **kwargs: calls.append((args, kwargs)))
    monkeypatch.setattr(match_review, "accept", lambda **kw: dict(changed=True, **kw))
    monkeypatch.setattr(match_review, "revoke", lambda **kw: dict(changed=True, **kw))
    monkeypatch.setattr(match_review, "confirm", lambda **kw: dict(changed=True, **kw))
    monkeypatch.setattr(match_review, "reject", lambda **kw: dict(changed=True, **kw))
    result = tasks.execute(jobs, "ingest", "match_review", dict(run_id="r", expected="basis", action=action))
    assert result["expected"] == "basis"
    assert calls == [(("ingest", "continue"), dict(run_id="r"))] if action == "accept" else not calls


def test_queued_continuation_cannot_undo_withdrawn_book_consent(monkeypatch):
    monkeypatch.setattr(store, "get", lambda rid: {})
    monkeypatch.setattr(pipeline, "book_review_held", lambda run: True)
    calls = []
    monkeypatch.setattr(pipeline, "publish_run", lambda rid: calls.append(rid))
    assert tasks.execute(None, "ingest", "continue", dict(run_id="held")) == dict(held_for_manual_book_review=True)
    assert calls == ["held"]
