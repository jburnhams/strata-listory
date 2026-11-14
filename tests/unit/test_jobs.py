"""Shared durable queue: bounded claims, replay, deduplication and visible outcomes."""

import subprocess
import threading
from concurrent.futures import ThreadPoolExecutor
from types import SimpleNamespace

import pytest

from app import db
from app.jobs import JobQueue


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
    assert q.get(bad)["state"] == "failed" and q.get(bad)["error"] == "source changed"
    assert not q.run_one() and not q.tasks()
    assert JobQueue(handle).get(ident)["state"] == "done"
    assert q.submit("system", "scan", folder="/source") != ident


def test_shared_cap_bounds_different_kinds_even_with_concurrent_claimers():
    entered = [threading.Event(), threading.Event()]
    release = threading.Event()
    lock = threading.Lock()
    active = []

    def handle(domain, kind, options):
        with lock:
            active.append(kind)
            if len(active) <= 2:
                entered[len(active) - 1].set()
        assert release.wait(5)
        return kind

    q = JobQueue(handle, workers=2)
    for kind in ("scan", "quality", "samples"):
        q.submit(kind, kind)
    with ThreadPoolExecutor(max_workers=3) as pool:
        futures = [pool.submit(q.run_one) for _ in range(2)]
        try:
            assert all(event.wait(5) for event in entered)
            assert pool.submit(q.run_one).result(timeout=5) is False
            assert len([t for t in q.tasks() if t["state"] == "running"]) == 2
            assert len([t for t in q.tasks() if t["state"] == "queued"]) == 1
        finally:
            release.set()
        assert all(f.result(timeout=5) for f in futures)
    assert q.run_one() and not q.tasks()


def test_dead_owner_fails_without_retry_but_live_owner_is_preserved(monkeypatch):
    q = JobQueue(lambda *a: None, workers=1)
    dead = q.submit("ingest", "run", ids=[1])
    live = q.submit("process", "run")
    c = db.connect()
    with c:
        c.execute("UPDATE jobs SET state='running',owner_pid=11 WHERE id=?", (dead,))
        c.execute("UPDATE jobs SET state='running',owner_pid=12 WHERE id=?", (live,))
    c.close()
    monkeypatch.setattr("app.jobs.oslib.pid_alive", lambda pid: pid == 12)

    class Thread:
        def __init__(self, **kw):
            pass

        def start(self):
            pass

        def join(self, **kw):
            pass

    monkeypatch.setattr("app.jobs.threading.Thread", Thread)
    q.start()
    q.start()
    assert len(q.threads) == 2
    assert q.get(dead)["state"] == "failed" and q.get(live)["state"] == "running"
    q.stop()


def test_jobs_for_the_same_book_do_not_overlap_across_domains():
    q = JobQueue(lambda *a: None, workers=2)
    first = q.submit("ingest", "run", ids=[15])
    second = q.submit("system", "inspect", book_id=15)
    third = q.submit("system", "inspect", book_id=16)
    c = db.connect()
    with c:
        c.execute("UPDATE jobs SET state='running' WHERE id=?", (first,))
    c.close()
    assert q.run_one() and q.get(third)["state"] == "done"
    assert not q.run_one() and q.get(second)["state"] == "queued"


def test_worker_loop_exits_after_finishing_its_job_and_serialization_errors_are_visible():
    from app.jobs import JobQueue

    q = JobQueue(lambda *a: q.stopping.set() or {"invalid": float("nan")})
    ident = q.submit("system", "invalid")
    q._loop()
    assert q.get(ident)["state"] == "failed" and "JSON" in q.get(ident)["error"]


def test_recovery_fails_interrupted_book_and_preserves_remaining_batch(monkeypatch):
    from app import catalogue
    from app.ingest import store

    q = JobQueue(lambda *a: None)
    book = catalogue.add_book(title="First")
    rid = store.create(book["id"], {})
    ident = q.submit("ingest", "run", ids=[book["id"], 99], limit=2)
    c = db.connect()
    with c:
        c.execute("UPDATE jobs SET state='running',owner_pid=123,started='1900' WHERE id=?", (ident,))
    c.close()
    monkeypatch.setattr("app.jobs.oslib.pid_alive", lambda pid: False)

    class Thread:
        def __init__(self, **kw):
            pass

        def start(self):
            pass

        def join(self, **kw):
            pass

    monkeypatch.setattr("app.jobs.threading.Thread", Thread)
    q.start()
    options = q.tasks("ingest")[0]["options"]
    assert q.get(ident)["state"] == "failed" and store.get(rid)["state"] == "failed"
    assert options["ids"] == [99] and options["limit"] == 1 and "resume" not in options
    q.stop()


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


@pytest.mark.parametrize("case", ["timeout", "shutdown", "crash", "empty_exit", "spawn_error", "success"])
def test_supervisor_kills_overrunning_children_and_records_terminal_outcomes(monkeypatch, case):
    from app import settings

    q = JobQueue(lambda *a: dict(ok=True), timeout_seconds=1)
    ident = q.submit("system", "inspect")
    killed = []

    class Process:
        returncode = None

        def poll(self):
            return self.returncode

        def wait(self, timeout):
            if case == "success":
                q.execute_claimed(ident)
            self.returncode = 3 if case == "crash" else 0
            return self.returncode

    closed = []
    process = Process()
    process.stdin = SimpleNamespace(close=lambda: closed.append(True))

    def start(job_id, data_dir):
        assert job_id == ident and data_dir == settings.data_dir()
        if case == "spawn_error":
            raise OSError("cannot spawn")
        return process

    monkeypatch.setattr("app.jobs.oslib.start_job", start)
    monkeypatch.setattr("app.jobs.oslib.kill_job", lambda p: killed.append(p))
    clock = iter([0, 2 if case == "timeout" else 0])
    monkeypatch.setattr("app.jobs.time", SimpleNamespace(monotonic=lambda: next(clock)))
    if case == "shutdown":
        q.stopping.set()
    assert q.run_one(isolated=True)
    assert q.get(ident)["state"] == ("done" if case == "success" else "failed") and not q.pending()
    if case in ("timeout", "shutdown", "crash", "empty_exit"):
        assert killed == [process]
    assert closed == ([] if case == "spawn_error" else [True])
    if case == "timeout":
        assert "time limit" in q.get(ident)["error"]
    assert not q.run_one()


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


def test_supervisor_waits_without_buffering_and_records_processing_failure(monkeypatch):
    q = JobQueue(lambda *a: dict(failed=1))
    ident = q.submit("process", "run")

    class Process:
        returncode = None
        waits = 0

        def poll(self):
            return self.returncode

        def wait(self, timeout):
            self.waits += 1
            if self.waits == 1:
                raise subprocess.TimeoutExpired("job", timeout)
            q.execute_claimed(ident)
            self.returncode = 0

    process = Process()
    monkeypatch.setattr("app.jobs.oslib.start_job", lambda *a: process)
    q.run_one(isolated=True)
    assert process.waits == 2 and q.get(ident)["state"] == "failed"
    assert "processing failed" in q.get(ident)["error"]


@pytest.mark.parametrize("timeout", [0, -1, float("inf"), float("nan"), 86401])
def test_invalid_deadlines_are_rejected(timeout):
    with pytest.raises(ValueError, match="timeout"):
        JobQueue(None, timeout_seconds=timeout)


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
    from app.jobs import CURRENT_JOB

    book = catalogue.add_book(title="Failed")
    other = catalogue.add_book(title="Rejected")
    queue = JobQueue(None)
    ident = queue.submit("process", kind)
    token = CURRENT_JOB.set(ident)
    try:
        catalogue.set_plan(book["id"], "planning")
    finally:
        CURRENT_JOB.reset(token)
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
    jobs = queue.tasks("ingest")
    assert len(jobs) == 2 and next(j for j in jobs if j["id"] == retried)["options"] == dict(ids=[1], limit=1)
    assert next(j for j in jobs if j["id"] != retried)["options"] == dict(ids=[2, 3], limit=2)
    assert not queue.failures()


def test_collection_failure_preserves_finished_reviews_and_queues_unattempted_children():
    from app import catalogue
    from app.ingest import store

    books = [catalogue.add_book(title=title) for title in ("Collection", "Finished", "Active", "Unattempted")]
    children = []

    def handle(*args):
        parent = store.create(books[0]["id"], {})
        store.finish(parent, "split")
        for book, state in zip(books[1:], ("quality_review", "running", "queued")):
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
    submit = queue._submit
    observed = []

    def publish(*args, **kwargs):
        result = submit(*args, **kwargs)
        c = sqlite3.connect(db.path())
        try:
            observed.append(
                (
                    c.execute("SELECT state FROM jobs WHERE id=?", (ident,)).fetchone()[0],
                    c.execute("SELECT count(*) FROM jobs").fetchone()[0],
                )
            )
        finally:
            c.close()
        return result

    monkeypatch.setattr(queue, "_submit", publish)
    queue.fail(ident, "timeout")
    assert observed == [("queued", 1)]
    assert queue.get(ident)["state"] == "failed" and len(queue.tasks()) == 1


def test_environment_deadline_is_snapshotted_on_submission(monkeypatch):
    monkeypatch.setenv("APP_WORKERS", "1")
    monkeypatch.setenv("APP_JOB_TIMEOUT_SECONDS", "37")
    queue = JobQueue(None)
    ident = queue.submit("system", "inspect")
    monkeypatch.setenv("APP_JOB_TIMEOUT_SECONDS", "99")
    assert queue.workers == 1 and queue.get(ident)["timeout_seconds"] == 37
    assert JobQueue(None).timeout_seconds == 99


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
    assert queue.submit("ingest", "run", ids=[1, 2], limit=2) != original


def test_review_decision_bypasses_busy_bulk_worker_but_keeps_both_lanes_bounded():
    q = JobQueue(lambda *args: dict(saved=True), workers=1)
    bulk = q.submit("ingest", "run", ids=[1])
    next_bulk = q.submit("system", "scan")
    review = q.submit("ingest", "match_review", book_id=2, action="confirm")
    next_review = q.submit("ingest", "match_review", book_id=3, action="confirm")
    c = db.connect()
    with c:
        c.execute("UPDATE jobs SET state='running' WHERE id=?", (bulk,))
    c.close()
    claimed = q._claim()
    assert claimed["id"] == review
    assert q._claim() is None
    q.execute_claimed(review)
    assert q.run_one() and q.get(next_review)["state"] == "done"
    assert q.get(next_bulk)["state"] == "queued" and q.get(bulk)["state"] == "running"


def test_review_slot_does_not_bypass_same_book_resource_lock_or_block_unrelated_bulk_work():
    q = JobQueue(lambda *args: dict(saved=True), workers=1)
    bulk = q.submit("ingest", "run", ids=[1])
    blocked = q.submit("ingest", "match_review", book_id=1)
    review = q.submit("ingest", "match_review", book_id=2)
    c = db.connect()
    with c:
        c.execute("UPDATE jobs SET state='running' WHERE id=?", (bulk,))
    c.close()
    assert q._claim()["id"] == review
    assert q._claim() is None
    c = db.connect()
    with c:
        c.execute("UPDATE jobs SET state='done' WHERE id=?", (bulk,))
    c.close()
    other = q.submit("system", "scan", book_id=3)
    assert q.run_one() and q.get(other)["state"] == "done"
    assert q.get(blocked)["state"] == "queued"
    q.execute_claimed(review)
    assert q.run_one() and q.get(blocked)["state"] == "done"


def test_training_feature_cache_work_does_not_lock_out_same_book_review():
    q = JobQueue(lambda *args: dict(saved=True), workers=1)
    cache = q.submit("system", "training_features", book_id=15, run_id="saved", revision=1)
    c = db.connect()
    with c:
        c.execute("UPDATE jobs SET state='running' WHERE id=?", (cache,))
    c.close()
    review = q.submit("ingest", "match_review", book_id=15, action="confirm")
    assert q.run_one() and q.get(review)["state"] == "done"


def test_background_training_backfill_yields_to_requested_ingest_work():
    q = JobQueue(lambda *args: dict(saved=True), workers=1)
    cache = q.submit("system", "training_features", run_id="old", revision=1)
    requested = q.submit("ingest", "run", ids=[25])
    assert q.run_one() and q.get(requested)["state"] == "done"
    assert q.get(cache)["state"] == "queued"
    assert q.run_one() and q.get(cache)["state"] == "done"


def _no_threads(monkeypatch):
    class Thread:
        def __init__(self, **kw):
            pass

        def start(self):
            pass

        def join(self, **kw):
            pass

    monkeypatch.setattr("app.jobs.threading.Thread", Thread)


def test_start_requeues_jobs_interrupted_by_shutdown_but_not_timeouts_or_errors(monkeypatch):
    _no_threads(monkeypatch)
    q = JobQueue(lambda *a: None)
    stopped = q.submit("system", "scan", folder="/a")
    sigterm = q.submit("system", "scan", folder="/b")
    timeout = q.submit("system", "scan", folder="/c")
    broken = q.submit("system", "scan", folder="/d")
    for ident, error in (
        (stopped, "job interrupted by server shutdown"),
        (sigterm, "job worker exited without a completed result (exit -15)"),
        (timeout, "job exceeded its 1800 second time limit"),
        (broken, "source changed"),
    ):
        c = db.connect()
        with c:
            c.execute("UPDATE jobs SET state='running' WHERE id=?", (ident,))
        c.close()
        q.fail(ident, error)
    cancelled = q.submit("system", "scan", folder="/e")
    c = db.connect()
    with c:
        c.execute(
            "UPDATE jobs SET state='failed',finished=?,error='previous job failed: job interrupted by server shutdown' WHERE id=?",
            (__import__("app.jobs", fromlist=["now"]).now(), cancelled),
        )
    c.close()
    q.start()
    queued = {t["options"]["folder"] for t in q.tasks()}
    assert queued == {"/a", "/b"}
    assert {f["id"] for f in q.failures()} == {timeout, broken, cancelled}
    q.stop()


def test_interrupted_jobs_are_retried_a_bounded_number_of_times_and_not_when_old(monkeypatch):
    _no_threads(monkeypatch)
    q = JobQueue(lambda *a: None)
    ident = q.submit("system", "scan", folder="/a")
    for attempt in range(4):
        c = db.connect()
        with c:
            c.execute("UPDATE jobs SET state='running' WHERE id=?", (ident,))
        c.close()
        q.fail(ident, "job interrupted by server shutdown")
        retried = q.recover_interrupted()
        if attempt < 2:
            assert len(retried) == 1
            ident = retried[0]
        else:
            assert retried == [] and q.get(ident)["state"] == "failed"
            break
    old = q.submit("system", "scan", folder="/old")
    c = db.connect()
    with c:
        c.execute("UPDATE jobs SET state='running' WHERE id=?", (old,))
    c.close()
    q.fail(old, "job interrupted by server shutdown")
    c = db.connect()
    with c:
        c.execute("UPDATE jobs SET finished='2000-01-01T00:00:00+00:00' WHERE id=?", (old,))
    c.close()
    assert q.recover_interrupted() == [] and q.get(old)["state"] == "failed"


def test_folder_scans_of_the_files_page_never_run_at_the_same_time():
    q = JobQueue(lambda *args: dict(saved=True), workers=2)
    a = q.submit("system", "filegroups", folder="/a")
    b = q.submit("system", "filegroups", folder="/b")
    assert q._claim()["id"] == a
    c = db.connect()
    with c:
        c.execute("UPDATE jobs SET state='running' WHERE id=?", (a,))
    c.close()
    assert q._claim() is None  # /b waits for /a: both publish a grouping over every root
    q.execute_claimed(a)
    assert q._claim()["id"] == b


def test_a_running_job_reports_what_it_is_doing_for_the_processing_page():
    from app.jobs import CURRENT_JOB, report_progress

    q = JobQueue(lambda *args: dict(saved=True), workers=1)
    ident = q.submit("system", "filegroups", folder="/a")
    report_progress("ignored outside a job", 1, 2)  # no job: nothing happens
    assert q._claim()["id"] == ident
    token = CURRENT_JOB.set(ident)
    try:
        report_progress("/a/book/01.mp3", 3, 10)
    finally:
        CURRENT_JOB.reset(token)
    assert q.tasks("system")[0]["progress"] == dict(current="/a/book/01.mp3", done=3, total=10)


def test_folder_scans_get_a_long_time_limit_and_other_jobs_the_default():
    q = JobQueue(lambda *args: None, workers=1, timeout_seconds=60)
    scan = q.submit("system", "filegroups", folder="/a")
    other = q.submit("system", "match", limit=1)
    assert q.get(scan)["timeout_seconds"] == 43200 and q.get(other)["timeout_seconds"] == 60
