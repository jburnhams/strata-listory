"""The durable queue (app.jobs on strata_python.tasks.queue): deduplication, outcomes, resources, a person's retry and clear, the failure cascade and the replay of a stop we asked for. Running a job in a job process, heartbeats, timeouts and cancel are the library's; tests/integration/test_jobs.py runs one for real."""

import socket

import pytest

from app import db, jobs
from app.jobs import JobQueue


def sql(statement, *args):
    c = db.connect()
    try:
        with c:
            return c.execute(statement, args).fetchall()
    finally:
        c.close()


def test_jobs_persist_deduplicate_and_record_success_or_failure():
    calls = []

    def handle(domain, kind, options):
        calls.append((domain, kind, options))
        if kind == "bad":
            raise ValueError("source changed")
        return dict(done=True)

    q = JobQueue(handle)
    ident = q.submit("system", "scan", folder="/source")
    assert q.submit("system", "scan", folder="/source") == ident
    assert q.get(ident)["state"] == "queued" and calls == []
    assert q.tasks()[0]["options"] == dict(folder="/source")
    assert q.run_one() and q.get(ident)["result"] == dict(done=True)
    assert q.status("system")["last"] == {"scan": dict(done=True)}
    bad = q.submit("system", "bad")
    assert q.run_one()
    assert (
        q.get(bad)["state"] == "failed" and q.get(bad)["error"] == "source changed"
    )  # the message, not "ValueError: ..."
    assert q.status("system")["last"] == {"bad": dict(error="source changed")}
    assert not q.run_one() and not q.tasks()
    assert JobQueue(handle).get(ident)["state"] == "done"
    assert q.submit("system", "scan", folder="/source") != ident


def test_a_job_is_domain_dot_kind_and_the_worker_claims_every_kind_the_server_runs():
    from strata_python.tasks.queue import Worker

    q = JobQueue(None)
    ident = q.submit("ingest", "run", ids=[1])
    assert q.queue().get(int(ident)).kind == "ingest.run" and q.get(ident)["kind"] == "run"
    handlers = {f"{d}.{k}": jobs.run_job for d, kinds in jobs.KINDS.items() for k in kinds}
    Worker(q.queue(), handlers)  # raises unless a job process can import every handler by name
    assert len(handlers) == sum(len(k) for k in jobs.KINDS.values())


def test_every_job_the_code_submits_is_a_kind_the_worker_claims():
    import re
    from pathlib import Path

    named = set()
    for path in Path(jobs.__file__).parent.rglob("*.py"):
        text = path.read_text(encoding="utf8")
        named.update(re.findall(r'(?:jobs\.submit|queued)\(\s*"(\w+)",\s*"(\w+)"', text))
        named.update(("ingest", kind) for kind in re.findall(r'ingest_worker\.submit\("(\w+)"', text))
    assert len(named) > 20
    assert {(d, k) for d, k in named if k not in jobs.KINDS.get(d, ())} == set()


def test_jobs_for_the_same_book_do_not_overlap_across_domains():
    q = JobQueue(lambda *a: None, workers=2)
    first = q.submit("ingest", "run", ids=[15])
    second = q.submit("system", "inspect", book_id=15)
    third = q.submit("system", "inspect", book_id=16)
    held = q.queue().claim()
    assert held.id == int(first)
    assert q.run_one() and q.get(third)["state"] == "done"
    assert not q.run_one() and q.get(second)["state"] == "queued"


def test_folder_scans_of_the_files_page_never_run_at_the_same_time():
    q = JobQueue(lambda *args: dict(saved=True), workers=2)
    a = q.submit("system", "filegroups", folder="/a")
    b = q.submit("system", "filegroups", folder="/b")
    first = q.queue().claim()
    assert first.id == int(a)
    assert q.queue().claim() is None  # /b waits for /a: both publish a grouping over every root
    q.queue().finish(first)
    assert q.queue().claim().id == int(b)


def test_serialization_errors_are_visible():
    q = JobQueue(lambda *a: {"invalid": float("nan")})
    ident = q.submit("system", "invalid")
    q.run_one()
    assert q.get(ident)["state"] == "failed" and "JSON" in q.get(ident)["error"]


def test_an_overrunning_job_fails_with_its_limit():
    import time

    q = JobQueue(lambda *a: time.sleep(0.05))
    ident = q.submit("system", "inspect")
    sql("UPDATE queue SET timeout_s=0.01")
    q.run_one()
    assert q.get(ident)["state"] == "failed" and q.get(ident)["error"] == "took longer than 0.01 s"


def test_a_process_run_that_failed_some_books_is_a_failed_job():
    q = JobQueue(lambda *a: dict(failed=1))
    ident = q.submit("process", "run")
    q.run_one()
    assert q.get(ident)["state"] == "failed" and "processing failed for 1 book" in q.get(ident)["error"]


def test_failed_jobs_require_explicit_retry_and_keep_their_deadline():
    q = JobQueue(lambda *a: (_ for _ in ()).throw(ValueError("bad source")), timeout_seconds=12)
    ident = q.submit("system", "inspect", book_id=7)
    q.run_one()
    assert q.submit("system", "inspect", book_id=7) == ident and not q.pending()
    assert q.failures()[0]["id"] == ident
    retry = q.retry(ident)
    assert retry != ident and q.get(retry)["timeout_seconds"] == 12 and not q.failures()
    with pytest.raises(ValueError, match="only failed"):
        q.retry(retry)
    with pytest.raises(ValueError, match="only failed"):
        q.retry("missing")


@pytest.mark.parametrize("timeout", [0, -1, float("inf"), float("nan"), 86401])
def test_invalid_deadlines_are_rejected(timeout):
    with pytest.raises(ValueError, match="timeout"):
        JobQueue(None, timeout_seconds=timeout)


def test_environment_deadline_is_snapshotted_on_submission(monkeypatch):
    monkeypatch.setenv("APP_WORKERS", "1")
    monkeypatch.setenv("APP_JOB_TIMEOUT_SECONDS", "37")
    queue = JobQueue(None)
    ident = queue.submit("system", "inspect")
    monkeypatch.setenv("APP_JOB_TIMEOUT_SECONDS", "99")
    assert queue.workers == 1 and queue.get(ident)["timeout_seconds"] == 37
    assert JobQueue(None).timeout_seconds == 99


def test_folder_scans_get_a_long_time_limit_and_other_jobs_the_default():
    q = JobQueue(lambda *args: None, workers=1, timeout_seconds=60)
    scan = q.submit("system", "filegroups", folder="/a")
    other = q.submit("system", "match", limit=1)
    assert q.get(scan)["timeout_seconds"] == 43200 and q.get(other)["timeout_seconds"] == 60


def test_a_running_job_reports_what_it_is_doing_for_the_processing_page():
    seen = []

    def handle(domain, kind, options):
        jobs.report_progress("/a/book/01.mp3", 3, 10)
        seen.append(q.tasks("system")[0]["progress"])

    q = JobQueue(handle, workers=1)
    jobs.report_progress("ignored outside a job", 1, 2)  # no job: nothing happens
    q.submit("system", "filegroups", folder="/a")
    q.run_one()
    assert seen == [dict(current="/a/book/01.mp3", done=3, total=10)]


def test_a_failed_job_can_be_cleared_and_the_same_work_submitted_again():
    q = JobQueue(lambda *args: (_ for _ in ()).throw(ValueError("boom")), workers=1)
    ident = q.submit("system", "filegroups", folder="/a")
    q.run_one()
    assert [f["id"] for f in q.failures()] == [ident] and q.submit("system", "filegroups", folder="/a") == ident
    q.dismiss(ident)
    assert q.failures() == []
    assert q.submit("system", "filegroups", folder="/a") != ident
    with pytest.raises(ValueError):
        q.dismiss(ident + "x")


def test_retry_failed_requeues_all_failed_jobs_beyond_the_page_limit_and_skips_cleared():
    from app import catalogue
    from app.ingest import store

    book = catalogue.add_book(title="Parent")

    def handle(domain, kind, options):
        if domain == "ingest" and kind == "run":
            rid = store.create(book["id"], {})
            q.submit("ingest", "continue", run_id=rid)
        raise ValueError("boom")

    q = JobQueue(handle, workers=1)
    ids = [q.submit("covers", "search", listen_id=i) for i in range(30)]
    q.submit("ingest", "run", ids=[book["id"]])
    while q.run_one():
        pass
    q.dismiss(ids[0])
    assert len(q.failures()) == 25
    retried = q.retry_failed()
    assert len(retried) == 30 and q.failures() == []
    assert [t["options"]["listen_id"] for t in q.tasks("covers")] == list(range(1, 30))
    assert [(t["kind"], t["options"]) for t in q.tasks("ingest")] == [("run", dict(ids=[book["id"]], limit=1))]


@pytest.mark.parametrize("retry_fails", [False, True])
def test_successful_retry_unblocks_future_work_but_failed_retry_stays_stopped(retry_fails):
    queue = JobQueue(lambda *a: (_ for _ in ()).throw(ValueError("first failure")))
    original = queue.submit("process", "run")
    queue.run_one()
    retry = queue.retry(original)
    queue.handler = lambda *a: (_ for _ in ()).throw(ValueError("retry failure")) if retry_fails else dict(ok=True)
    queue.run_one()
    following = queue.submit("process", "run")
    if retry_fails:
        assert following == retry and not queue.pending()
        assert [j["id"] for j in queue.failures()] == [retry]
    else:
        assert following not in (original, retry) and queue.pending() and not queue.failures()
        assert queue.submit("process", "run") == following
        queue.run_one()
        assert queue.get(following)["state"] == "done"


def test_changed_retry_options_also_supersede_the_original_failure():
    queue = JobQueue(None)
    original = queue.submit("ingest", "run", ids=[1, 2], limit=2)
    queue.fail(original, "timeout")
    retry = queue.retry(original)
    queue.handler = lambda *a: dict(ok=True)
    while queue.run_one():
        pass
    assert queue.get(retry)["state"] == "done"
    assert not queue.failures() and not queue.pending()


# --- what a failure does to the work the job owned ------------------------------------------------------------


def test_failure_marks_only_owned_ingest_and_plan_work_and_cancels_automatic_continuation():
    from app import catalogue
    from app.ingest import store

    q = JobQueue(None)
    book = catalogue.add_book(title="Owned")
    other = catalogue.add_book(title="Other")
    unrelated = store.create(other["id"], {})
    saved = []

    def handle(*args):
        rid = store.create(book["id"], {})
        saved.append(rid)
        catalogue.set_plan(book["id"], "planning")
        q.submit("ingest", "continue", run_id=rid)
        raise ValueError("decode failed")

    q.handler = handle
    ident = q.submit("ingest", "run", ids=[book["id"]])
    q.run_one()
    assert q.get(ident)["state"] == "failed" and store.get(saved[0])["state"] == "failed"
    assert catalogue.get_plan(book["id"])["state"] == "failed" and store.get(unrelated)["state"] == "running"
    assert not q.pending() and not q.run_one()


def test_failed_legacy_regroup_marks_its_new_unbound_run_failed(monkeypatch):
    from app import catalogue
    from app.ingest import store

    book = catalogue.add_book(title="Legacy")
    original = store.create(book["id"], {})
    store.finish(original, "metadata_review")
    created = []
    monkeypatch.setattr("app.jobs.bind_work", lambda *a: None)

    def regroup(*args):
        created.append(store.create(book["id"], {}))
        raise ValueError("interrupted regroup")

    queue = JobQueue(regroup)
    ident = queue.submit("ingest", "regroup", run_id=original)
    queue.run_one()
    assert queue.get(ident)["state"] == "failed" and store.get(original)["state"] == "failed"
    assert store.get(created[0])["state"] == "failed" and not queue.pending()
    retry = queue.retry(ident)
    assert next(j for j in queue.tasks() if j["id"] == retry)["kind"] == "run"


@pytest.mark.parametrize("kind", ["run", "propose"])
def test_explicit_process_retry_resets_only_its_own_failed_reservations(kind):
    from app import catalogue

    book = catalogue.add_book(title="Failed")
    other = catalogue.add_book(title="Rejected")
    queue = JobQueue(None)
    ident = queue.submit("process", kind)
    token = jobs.CURRENT_JOB.set(ident)
    try:
        catalogue.set_plan(book["id"], "planning")
    finally:
        jobs.CURRENT_JOB.reset(token)
    catalogue.set_plan(other["id"], "rejected")
    queue.fail(ident, "interrupted")
    assert catalogue.get_plan(book["id"])["state"] == "failed"
    retried = queue.retry(ident)
    assert queue.get(retried)["state"] == "queued" and catalogue.get_plan(other["id"])["state"] == "rejected"
    assert (
        catalogue.get_plan(book["id"])["state"] == "approved"
        if kind == "run"
        else catalogue.get_plan(book["id"]) is None
    )


def test_preview_timeout_marks_unfinished_samples_failed_without_discarding_ready_assets():
    from app import read_models

    queue = JobQueue(None)
    ident = queue.submit("system", "samples", run_id="saved")
    read_models.save(
        "previews", "saved", dict(samples=[dict(status="ready", url="saved.mp3"), dict(status="generating")])
    )
    queue.fail(ident, "job timed out")
    assert read_models.get("previews", "saved")["samples"] == [
        dict(status="ready", url="saved.mp3"),
        dict(status="failed", error="job timed out"),
    ]


def test_explicit_batch_retry_does_not_duplicate_the_queued_remainder():
    queue = JobQueue(None)
    ident = queue.submit("ingest", "run", ids=[1, 2, 3], limit=3)
    queue.fail(ident, "timeout")
    retried = queue.retry(ident)
    jobs_ = queue.tasks("ingest")
    assert len(jobs_) == 2 and next(j for j in jobs_ if j["id"] == retried)["options"] == dict(ids=[1], limit=1)
    assert next(j for j in jobs_ if j["id"] != retried)["options"] == dict(ids=[2, 3], limit=2)
    assert not queue.failures()


def test_collection_failure_preserves_finished_reviews_and_queues_unattempted_children():
    from app import catalogue
    from app.ingest import store

    books = [catalogue.add_book(title=title) for title in ("Collection", "Finished", "Active", "Unattempted")]
    children = []

    def handle(*args):
        parent = store.create(books[0]["id"], {})
        store.finish(parent, "split")
        for book, state in zip(books[1:], ("quality_review", "running", "queued"), strict=True):
            rid = store.create(book["id"], dict(collection_run=parent))
            store.finish(rid, state)
            children.append(rid)
        raise ValueError("timeout")

    queue = JobQueue(handle)
    queue.submit("ingest", "run", ids=[books[0]["id"]], limit=1)
    queue.run_one()
    assert [store.get(rid)["state"] for rid in children] == ["quality_review", "failed", "queued"]
    assert [(j["kind"], j["options"]) for j in queue.tasks()] == [("continue", dict(run_id=children[2]))]
    retried = queue.retry(queue.failures()[0]["id"])
    assert next(j["options"] for j in queue.tasks() if j["id"] == retried) == dict(ids=[books[2]["id"]], limit=1)


def test_explicit_approval_retry_reuses_the_idempotent_continuation_path():
    queue = JobQueue(None)
    ident = queue.submit("ingest", "approve", run_id="saved", phase="metadata")
    queue.fail(ident, "interrupted")
    retried = queue.retry(ident)
    assert next(j["options"] for j in queue.tasks() if j["id"] == retried) == dict(
        run_id="saved", phase="metadata", _recovered=True
    )


def test_batch_failure_and_remaining_queue_are_one_database_transaction(monkeypatch):
    import sqlite3

    queue = JobQueue(None)
    ident = queue.submit("ingest", "run", ids=[1, 2], limit=2)
    enqueue = queue._enqueue
    observed = []

    def publish(*args, **kwargs):
        result = enqueue(*args, **kwargs)
        c = sqlite3.connect(db.path())
        try:  # another connection still sees the job as it was before the failing transaction
            observed.append(
                (
                    c.execute("SELECT state FROM queue WHERE id=?", (int(ident),)).fetchone()[0],
                    c.execute("SELECT count(*) FROM queue").fetchone()[0],
                )
            )
        finally:
            c.close()
        return result

    monkeypatch.setattr(queue, "_enqueue", publish)
    queue.fail(ident, "timeout")
    assert observed == [("running", 1)]
    assert queue.get(ident)["state"] == "failed" and len(queue.tasks()) == 1


# --- a worker that stops, or is lost ---------------------------------------------------------------------------


class FakeWorker:
    """What `start` makes: the library's worker recovers lost jobs when it starts and puts running ones back when it stops; no threads or job processes here."""

    made = []

    def __init__(self, queue, handlers, **options):
        self.queue, self.handlers, self.options, self.stopped = queue, handlers, options, False
        FakeWorker.made.append(self)

    def start(self):
        self.queue.recover()
        return self

    def stop(self, timeout=30.0):
        self.stopped = True


@pytest.fixture
def fake_worker(monkeypatch):
    FakeWorker.made = []
    monkeypatch.setattr("app.jobs.Worker", FakeWorker)
    return FakeWorker


def lost(ident, pid=123):
    """The job is running under a worker of this host that is gone."""
    sql(
        "UPDATE queue SET state='running',owner=?,owner_pid=?,started_at=0 WHERE id=?",
        f"{socket.gethostname()}:{pid}",
        pid,
        int(ident),
    )


def test_start_runs_the_workers_once_for_every_kind_at_the_configured_width(fake_worker):
    q = JobQueue(None, workers=3)
    q.start()
    q.start()
    assert len(fake_worker.made) == 1
    worker = fake_worker.made[0]
    assert worker.options["concurrency"] == 3 and worker.options["lower_priority"]
    assert set(worker.handlers) == {f"{d}.{k}" for d, kinds in jobs.KINDS.items() for k in kinds}
    q.stop()
    assert worker.stopped and q.worker is None
    q.stop()


def test_dead_owner_fails_without_retry_but_live_owner_is_preserved(fake_worker, monkeypatch):
    monkeypatch.setattr("strata_python.tasks.queue.oslib.pid_alive", lambda pid: pid == 12)
    q = JobQueue(lambda *a: None, workers=1)
    dead = q.submit("ingest", "run", ids=[1])
    live = q.submit("process", "run")
    lost(dead, 11)
    lost(live, 12)
    sql("UPDATE queue SET heartbeat_at=strftime('%s','now') WHERE id=?", int(live))
    q.start()
    assert q.get(dead)["state"] == "failed" and q.get(live)["state"] == "running"
    assert "stopped while running" in q.get(dead)["error"] and q.queue().get(int(dead)).attempts == 1
    q.stop()


def test_recovery_fails_interrupted_book_and_preserves_remaining_batch(fake_worker, monkeypatch):
    from app import catalogue
    from app.ingest import store

    monkeypatch.setattr("strata_python.tasks.queue.oslib.pid_alive", lambda pid: False)
    q = JobQueue(lambda *a: None)
    book = catalogue.add_book(title="First")
    rid = store.create(book["id"], {})
    ident = q.submit("ingest", "run", ids=[book["id"], 99], limit=2)
    lost(ident)
    q.start()
    options = q.tasks("ingest")[0]["options"]
    assert q.get(ident)["state"] == "failed" and store.get(rid)["state"] == "failed"
    assert options["ids"] == [99] and options["limit"] == 1 and "resume" not in options
    q.stop()


def test_a_job_whose_worker_is_stopping_fails_with_the_known_message_and_is_replayed_on_start(fake_worker):
    from app import catalogue
    from app.ingest import store

    q = JobQueue(None)
    book = catalogue.add_book(title="Stopped")
    ident = q.submit("ingest", "run", ids=[book["id"]], limit=1)
    job = q.queue().claim()
    rid = store.create(book["id"], {})  # made by the running job
    q.queue().release(job)  # what `Worker.stop` does to a job it killed
    assert q.queue().get(int(ident)).error == jobs.SHUTDOWN and store.get(rid)["state"] == "failed"
    assert q.get(ident)["error"] == "job interrupted by server shutdown"
    q.start()
    assert not q.failures() and [t["options"] for t in q.tasks("ingest")] == [dict(ids=[book["id"]], limit=1)]
    q.stop()


def test_start_requeues_jobs_interrupted_by_shutdown_but_not_timeouts_or_errors(fake_worker):
    q = JobQueue(lambda *a: None)
    stopped = q.submit("system", "scan", folder="/a")
    timeout = q.submit("system", "scan", folder="/c")
    broken = q.submit("system", "scan", folder="/d")
    for ident, error in (
        (stopped, "job interrupted by server shutdown"),
        (timeout, "job exceeded its 1800 second time limit"),
        (broken, "source changed"),
    ):
        q.fail(ident, error)
    cancelled = q.submit("system", "scan", folder="/e")
    sql(
        "UPDATE queue SET state='failed',finished_at=strftime('%s','now'),error='previous job failed: job interrupted by server shutdown' WHERE id=?",
        int(cancelled),
    )
    q.start()
    assert {t["options"]["folder"] for t in q.tasks()} == {"/a"}
    assert {f["id"] for f in q.failures()} == {timeout, broken, cancelled}
    q.stop()


def test_interrupted_jobs_are_retried_a_bounded_number_of_times_and_not_when_old():
    q = JobQueue(lambda *a: None)
    ident = q.submit("system", "scan", folder="/a")
    for attempt in range(4):
        q.fail(ident, "job interrupted by server shutdown")
        retried = q.recover_interrupted()
        if attempt < 2:
            assert len(retried) == 1
            ident = retried[0]
        else:
            assert retried == [] and q.get(ident)["state"] == "failed"
            break
    old = q.submit("system", "scan", folder="/old")
    q.fail(old, "job interrupted by server shutdown")
    sql("UPDATE queue SET finished_at=946684800 WHERE id=?", int(old))
    assert q.recover_interrupted() == [] and q.get(old)["state"] == "failed"


def test_switching_the_data_folder_gives_the_queue_the_new_database(tmp_path, monkeypatch, fake_worker):
    q = JobQueue(lambda *a: dict(ok=True))
    first = q.submit("system", "scan", folder="/a")
    q.start()
    monkeypatch.setenv("APP_DATA", str(tmp_path / "elsewhere"))
    (tmp_path / "elsewhere").mkdir()
    second = q.submit("system", "scan", folder="/b")
    assert [t["options"]["folder"] for t in q.tasks()] == ["/b"] and first == second  # a new database, numbering from 1
    assert len(fake_worker.made) == 2 and fake_worker.made[0].stopped and not fake_worker.made[1].stopped
    q.stop()


def test_error_messages_are_shown_without_the_exception_name():
    assert jobs.plain("ValueError: no folder grouping yet") == "no folder grouping yet"
    assert jobs.plain("Interrupted: job interrupted by server shutdown") == "job interrupted by server shutdown"
    assert jobs.plain("previous job failed: boom") == "previous job failed: boom"
    assert jobs.plain("ValueError") == "ValueError" and jobs.plain(None) == ""
