"""The durable shared queue: strata-python's `tasks.queue.Queue` and `Worker` (claims, heartbeats, job processes, cancel, timeouts) with this app's rules around them.

The library owns the table (`queue`, in catalogue.db next to the data it works on), claiming, running in a job process per worker thread and recording the outcome. What stays here is what the library has no word for:
- a job is `domain.kind` ("ingest.run"; kinds repeat across domains), its resources are the books and listens it touches (a book's jobs never overlap, folder scans never overlap), and `submit` returns the failed job again until a person retries or clears it;
- `retries=0`: a failure is shown on the Processing page and only an explicit retry runs it again (a retry is a new job, so what the failed one did to its runs and plans can be undone first);
- the cascade of a failure (`_on_fail`): the ingest runs and process plans the job owned are marked failed in the same transaction that fails it, so no screen ever sees a failed job with a run still 'running';
- a stop we asked for (service restart, deploy) is recorded as a failure with a known message and replayed on the next start (`recover_interrupted`), capped per chain and per age so a job that really kills the server cannot loop."""

from __future__ import annotations

import json
import os
import re
import sqlite3
import threading
import time
import uuid
from contextvars import ContextVar
from datetime import UTC, datetime, timedelta

from sqlalchemy import MetaData, create_engine, text, update
from sqlalchemy.engine import URL, Connection, Engine
from strata_python.tasks import progress as _progress
from strata_python.tasks.queue import Job, Queue, Worker, dedupe_key, jobs_table

from app import db

CURRENT_JOB = ContextVar("current_job", default=None)
TABLE = jobs_table(MetaData(), "queue")
# Every kind the server runs, by domain: the worker claims only these (the library routes by kind), and a test checks that every `submit` and `queued` call in the code names one.
KINDS = {
    "ingest": (
        "run",
        "resume",
        "continue",
        "restore",
        "regroup",
        "refresh",
        "auto_metadata",
        "approve",
        "evaluate",
        "recording",
        "quality",
        "enqueue",
        "reconcile",
    ),
    "process": ("propose", "approve", "reject", "repropose", "run"),
    "covers": ("search",),
    "enrichment": ("fetch",),
    "listens": ("export",),
    "system": (
        "bootstrap",
        "views",
        "samples",
        "mirror",
        "scan",
        "filegroups",
        "listens_build",
        "bbc_details",
        "identify",
        "match",
        "enrich",
        "enrich_book",
        "inspect",
    ),
}
# A stop we asked for says nothing against the job itself, unlike a timeout, a crash or an application error, so these failures are replayed on the next start. Capped per chain and per age so a job that really kills the server cannot loop.
SHUTDOWN = "Interrupted: job interrupted by server shutdown"  # exactly what `describe` stores for `Interrupted`
LEGACY_SHUTDOWN = (
    "job interrupted by server shutdown",
    "job worker exited without a completed result (exit -15)",
)  # the messages of the previous queue, kept so a migration replays them too
# A first scan of a big folder hashes every file (hundreds of GB), far past the default limit; a rerun resumes (hashed files are committed as it goes), but it should not need to.
LONG_JOBS = {
    "filegroups": 43200.0,
    "listens_build": 43200.0,
    "identify": 43200.0,
    "bbc_details": 43200.0,
    "export": 43200.0,
}
AUTO_RETRY_LIMIT = 2
AUTO_RETRY_WINDOW_HOURS = 24
# The library records a failure as "TypeName: message"; a person reads the message.
_TYPED = re.compile(r"^[A-Z][A-Za-z0-9_]*: ")


class Interrupted(RuntimeError):
    """The server was stopped while the job ran (see SHUTDOWN)."""


def plain(error):
    """A failure as the page shows it: the message without the exception's name."""
    return _TYPED.sub("", error, count=1) if error else ""


def iso(seconds):
    return datetime.fromtimestamp(seconds, UTC).isoformat() if seconds else None


def now():
    return datetime.now(UTC).isoformat()


def bind_work(connection, kind, owner):
    if ident := CURRENT_JOB.get():
        connection.execute("INSERT OR REPLACE INTO job_work VALUES (?,?,?)", (kind, str(owner), ident))


def report_progress(current, done=None, total=None):
    """What the running job is doing now (shown on the Processing page): the item or step, and how many of how many are done. A no-op outside a job. The library throttles it to one small write a second."""
    _progress.report_progress(done or 0, total, current)


def legacy_progress(raw):
    """The progress column as the pages read it: JSON {current, done, total}."""
    if not raw:
        return None
    p = json.loads(raw)

    def whole(n):
        return int(n) if isinstance(n, float) and n == int(n) else n

    return dict(current=p.get("text") or "", done=whole(p.get("done")), total=whole(p.get("total")))


def split_kind(kind):
    domain, _, name = kind.partition(".")
    return domain, name


def engine_for(path) -> Engine:
    # timeout: the same 30 s as db.connect(); the job processes open this URL too (it is what `Worker` hands them)
    return create_engine(URL.create("sqlite", database=path, query={"timeout": "30"}))


def write_lock(c: Connection):
    """Take SQLite's write lock before reading, as the library's claim does: a read that turns into a write after another process wrote fails with 'database is locked' instead of waiting."""
    c.execute(update(TABLE).where(text("0")).values(state=TABLE.c.state))


class _Queue(Queue):
    """The library's queue with this app's three differences: claims wait while the data folder is being switched, a worker that is stopping fails its job with a known message instead of putting it back (the runs it owns must be marked, see `_on_fail`), and the cascade runs in the failing transaction."""

    lock: threading.RLock

    def claim(self, kinds=None):
        with self.lock:
            return super().claim(kinds)

    def release(self, job):
        self.fail(job, Interrupted("job interrupted by server shutdown"))


def run_job(job):
    """The one handler every kind runs under, in a job process (the library imports it by this name)."""
    return default_queue().run_handler(job)


_default = None


def default_queue():
    """The queue of this process when no server owns one (a job process, the CLI)."""
    global _default
    if _default is None:
        _default = JobQueue(None)
    return _default


class JobQueue:
    def __init__(self, handler, workers=None, timeout_seconds=None):
        self.handler = handler
        self.workers = max(1, min(8, int(workers or os.environ.get("APP_WORKERS", "1"))))
        self.timeout_seconds = float(
            timeout_seconds if timeout_seconds is not None else os.environ.get("APP_JOB_TIMEOUT_SECONDS", "1800")
        )
        if not 0 < self.timeout_seconds <= 86400:
            raise ValueError("job timeout must be greater than zero and at most 86400 seconds")
        self.configuration_lock = threading.RLock()
        self.worker = None
        self._queue = None
        self._path = None

    # --- the queue of the current data folder -----------------------------------------------------------------------

    def queue(self) -> _Queue:
        """The library's queue on this data folder's database (made, with the schema migrated, on first use; made again when the folder is switched, which the server only allows with no job active)."""
        path = db.path()
        with self.configuration_lock:
            if self._queue is None or self._path != path:
                running = self.worker is not None
                if running:
                    self.stop()
                db.connect().close()  # the `queue` table belongs to the schema
                q = _Queue(engine_for(path), TABLE, retries=0, on_fail=self._on_fail)
                q.lock = self.configuration_lock
                self._queue, self._path = q, path
                if running:
                    self.start()
            return self._queue

    @property
    def handler_for_process(self):
        if self.handler is not None:
            return self.handler
        from app.server import tasks

        return lambda domain, kind, options: tasks.execute(self, domain, kind, options)

    def run_handler(self, job):
        domain, kind = split_kind(job.kind)
        token = CURRENT_JOB.set(str(job.id))
        try:
            result = self.handler_for_process(domain, kind, job.options)
            if domain == "process" and isinstance(result, dict) and result.get("failed"):
                raise ValueError(f"processing failed for {result['failed']} book(s)")
            return json.loads(json.dumps(result, allow_nan=False))
        finally:
            CURRENT_JOB.reset(token)

    # --- submitting --------------------------------------------------------------------------------------------------

    def submit(self, domain, kind, retry=False, **options):
        q = self.queue()
        with self.configuration_lock, q.engine.begin() as c:
            write_lock(c)
            return str(self._enqueue(c, domain, kind, retry, options))

    def _enqueue(self, c: Connection, domain, kind, retry, options):
        """Add the job (or return the one that already stands for this work) inside the caller's transaction."""
        full = f"{domain}.{kind}"
        key = dedupe_key(full, domain, options)
        previous = c.execute(
            text(
                "SELECT j.id,j.state FROM queue j WHERE j.dedupe=:key AND (j.state IN ('queued','running') OR (j.state='failed' AND NOT EXISTS (SELECT 1 FROM job_work r WHERE r.kind IN ('retry','dismissed') AND r.owner=CAST(j.id AS TEXT)) AND NOT EXISTS (SELECT 1 FROM queue newer WHERE newer.dedupe=j.dedupe AND newer.id>j.id))) ORDER BY CASE j.state WHEN 'running' THEN 0 WHEN 'queued' THEN 1 ELSE 2 END,j.id DESC LIMIT 1"
            ),
            dict(key=key),
        ).first()
        if previous and (not retry or previous.state != "failed"):
            return previous.id
        ids = set(options.get("ids") or [])
        if options.get("book_id"):
            ids.add(options["book_id"])
        if options.get("run_id"):
            ids.update(
                r[0]
                for r in c.execute(
                    text("SELECT book_id FROM ingest_members WHERE run_id=:r"), dict(r=options["run_id"])
                )
            )
        owned = sorted(f"book:{bid}" for bid in ids)
        if options.get("listen_id"):  # listens live in the Postgres catalogue, a different id space from books
            owned.append(f"listen:{options['listen_id']}")
        if (
            kind == "filegroups" and domain == "system"
        ):  # every folder scan publishes a grouping over all roots, so they must not overlap
            owned.append("filegroups")
        t = TABLE
        res = c.execute(
            t.insert().values(
                kind=full,
                domain=domain,
                options=json.dumps(options, sort_keys=True, allow_nan=False),
                dedupe=key,
                resources=json.dumps(owned),
                state="queued",
                priority=0,
                attempts=0,
                timeout_s=LONG_JOBS.get(kind, self.timeout_seconds),
                created_at=time.time(),
                cancel_requested=False,
            )
        )
        return int(res.inserted_primary_key[0])

    # --- reading ----------------------------------------------------------------------------------------------------

    def get(self, ident):
        job = self._job(ident)
        if job is None:
            raise ValueError("no such job")
        domain, kind = split_kind(job.kind)
        return dict(
            id=str(job.id),
            domain=domain,
            kind=kind,
            state=job.state,
            created=iso(job.created_at),
            started=iso(job.started_at),
            finished=iso(job.finished_at),
            error=plain(job.error) if job.state == "failed" else "",
            result=job.result if job.state == "done" else None,
            timeout_seconds=job.timeout_s,
        )

    def _job(self, ident) -> Job | None:
        try:
            number = int(ident)
        except (TypeError, ValueError):
            return None
        return self.queue().get(number)

    def pending(self):
        with self.queue().engine.connect() as c:
            return (
                c.execute(text("SELECT 1 FROM queue WHERE state IN ('queued','running') LIMIT 1")).first() is not None
            )

    def tasks(self, domain=None):
        sql = "SELECT id,kind,options,state,started_at,progress FROM queue WHERE state IN ('queued','running')"
        if domain:
            sql += " AND domain=:domain"
        sql += " ORDER BY CASE state WHEN 'running' THEN 0 ELSE 1 END,created_at,id"
        with self.queue().engine.connect() as c:
            rows = c.execute(text(sql), dict(domain=domain)).all()
        return [
            dict(
                id=str(r.id),
                kind=split_kind(r.kind)[1],
                options=json.loads(r.options),
                state=r.state,
                started=iso(r.started_at) or "",
                progress=legacy_progress(r.progress) if r.state == "running" else None,
            )
            for r in rows
        ]

    def failures(self, limit=25, domain=None):
        owner = "COALESCE(json_extract(j.options,'$.book_id'),json_extract(j.options,'$.ids[0]'),(SELECT book_id FROM ingest_runs WHERE id=COALESCE(json_extract(j.options,'$.run_id'),json_extract(j.options,'$.resume'))),(SELECT CAST(owner AS INTEGER) FROM job_work WHERE job_id=CAST(j.id AS TEXT) AND kind='plan' LIMIT 1))"
        with self.queue().engine.connect() as c:
            rows = c.execute(
                text(
                    f"SELECT j.id,j.domain,j.kind,j.error,j.finished_at,j.progress,j.options,b.id AS book_id,b.title FROM queue j LEFT JOIN books b ON b.id={owner} WHERE j.state='failed' AND (:domain IS NULL OR j.domain=:domain) AND {self._open_failure('j')} ORDER BY j.finished_at DESC,j.id DESC LIMIT :limit"
                ),
                dict(domain=domain, limit=limit),
            ).all()
        return [
            dict(
                id=str(r.id),
                domain=r.domain,
                kind=split_kind(r.kind)[1],
                error=plain(r.error),
                finished=iso(r.finished_at),
                progress=json.dumps(legacy_progress(r.progress)) if r.progress else None,
                options=r.options,
                book_id=r.book_id,
                title=r.title,
            )
            for r in rows
        ]

    @staticmethod
    def _open_failure(alias):
        """A failed job still waiting for a person: not retried, not cleared and not superseded by a newer job for the same work."""
        return f"NOT EXISTS (SELECT 1 FROM job_work retry WHERE retry.kind IN ('retry','dismissed') AND retry.owner=CAST({alias}.id AS TEXT)) AND NOT EXISTS (SELECT 1 FROM queue newer WHERE newer.dedupe={alias}.dedupe AND newer.id>{alias}.id)"

    def status(self, domain):
        tasks = self.tasks(domain)
        with self.queue().engine.connect() as c:
            row = c.execute(
                text(
                    "SELECT kind,state,result,error FROM queue WHERE domain=:d AND state IN ('done','failed') ORDER BY finished_at DESC,id DESC LIMIT 1"
                ),
                dict(d=domain),
            ).first()
        return dict(
            busy=next((t["kind"] for t in tasks if t["state"] == "running"), None),
            queued=sum(t["state"] == "queued" for t in tasks),
            last={
                split_kind(row.kind)[1]: json.loads(row.result) if row.state == "done" else dict(error=plain(row.error))
            }
            if row
            else {},
        )

    # --- a person's decisions on failed jobs ------------------------------------------------------------------------

    def dismiss(self, ident):
        """Clear a failed job from the Processing page (it stays in the queue table as history; a new job with the same work may then be submitted)."""
        job = self._job(ident)
        if job is None or job.state != "failed":
            raise ValueError("only failed jobs can be cleared")
        with self.queue().engine.begin() as c:
            c.execute(text("INSERT OR REPLACE INTO job_work VALUES ('dismissed',:i,:i)"), dict(i=str(job.id)))

    def retry(self, ident):
        with self.configuration_lock:
            return self._retry(ident)

    def retry_failed(self):
        """Re-queue every failed job that has not been retried, cleared or superseded; returns the new job ids."""
        with self.queue().engine.connect() as c:
            candidates = [
                str(r[0])
                for r in c.execute(
                    text(
                        f"SELECT j.id FROM queue j WHERE j.state='failed' AND {self._open_failure('j')} ORDER BY j.error LIKE '%previous job failed: %',j.finished_at,j.id"
                    )
                )
            ]
        out = []
        for ident in candidates:
            try:
                out.append(self.retry(ident))
            except ValueError:
                pass
        return list(dict.fromkeys(out))

    def _retry(self, ident):
        q = self.queue()
        with q.engine.begin() as c:
            write_lock(c)
            try:
                number = int(ident)
            except (TypeError, ValueError):
                raise ValueError("only failed jobs can be retried") from None
            row = c.execute(
                text(
                    "SELECT domain,kind,options FROM queue WHERE id=:i AND state='failed' AND NOT EXISTS (SELECT 1 FROM job_work WHERE kind IN ('retry','dismissed') AND owner=:o)"
                ),
                dict(i=number, o=str(number)),
            ).first()
            if row is None:
                raise ValueError("only failed jobs can be retried")
            domain, kind = split_kind(row.kind)
            options = json.loads(row.options)
            if options.pop("_recovered", False):
                options.pop("resume", None)
            if domain == "ingest" and kind == "approve":
                options["_recovered"] = True
            if domain == "ingest" and kind in ("run", "regroup"):
                failed = [
                    r[0]
                    for r in c.execute(
                        text(
                            "SELECT DISTINCT r.book_id FROM ingest_runs r JOIN job_work w ON w.kind='ingest' AND w.owner=r.id WHERE w.job_id=:o AND r.state='failed'"
                        ),
                        dict(o=str(number)),
                    )
                ]
                selected = failed or (options.get("ids") or [])[:1]
                if selected:
                    kind = "run"
                    options.update(ids=selected, limit=len(selected))
                    options.pop("resume", None)
                    options.pop("run_id", None)
            if domain == "process" and kind in ("run", "propose"):
                owned = "state='failed' AND book_id IN (SELECT CAST(owner AS INTEGER) FROM job_work WHERE job_id=:o AND kind='plan')"
                if kind == "run":
                    c.execute(
                        text(f"UPDATE process_plan SET state='approved',error='',updated=:now WHERE {owned}"),
                        dict(o=str(number), now=now()),
                    )
                else:
                    c.execute(
                        text(
                            f"DELETE FROM read_models WHERE kind='plan' AND CAST(owner AS INTEGER) IN (SELECT book_id FROM process_plan WHERE {owned})"
                        ),
                        dict(o=str(number)),
                    )
                    c.execute(text(f"DELETE FROM process_plan WHERE {owned}"), dict(o=str(number)))
            retried = self._enqueue(c, domain, kind, True, options)
            c.execute(
                text("INSERT OR REPLACE INTO job_work VALUES ('retry',:o,:n)"), dict(o=str(number), n=str(retried))
            )
            c.execute(
                text(
                    "INSERT OR REPLACE INTO job_work(kind,owner,job_id) SELECT 'retry',CAST(id AS TEXT),:n FROM queue WHERE state='failed' AND kind IN ('ingest.continue','ingest.restore') AND error LIKE '%previous job failed: %' AND json_extract(options,'$.run_id') IN (SELECT owner FROM job_work WHERE job_id=:o AND kind='ingest')"
                ),
                dict(n=str(retried), o=str(number)),
            )
            return str(retried)

    def recover_interrupted(self):
        """Re-queue jobs that failed only because the server was stopped; returns the new job ids."""
        cutoff = (datetime.now(UTC) - timedelta(hours=AUTO_RETRY_WINDOW_HOURS)).timestamp()
        with self.queue().engine.connect() as c:
            candidates = [
                str(r[0])
                for r in c.execute(
                    text(
                        f"SELECT j.id FROM queue j WHERE j.state='failed' AND j.finished_at>=:cutoff AND j.error=:marker AND {self._open_failure('j')} ORDER BY j.finished_at,j.id"
                    ),
                    dict(cutoff=cutoff, marker=SHUTDOWN),
                )
            ]

            def attempts(ident):
                n = 0
                while r := c.execute(
                    text("SELECT owner FROM job_work WHERE kind='retry' AND job_id=:i"), dict(i=ident)
                ).first():
                    ident = r[0]
                    n += 1
                return n

            candidates = [i for i in candidates if attempts(i) < AUTO_RETRY_LIMIT]
        out = []
        for ident in candidates:
            try:
                out.append(self.retry(ident))
            except ValueError:
                pass  # superseded meanwhile (for example the same work was submitted again)
        return out

    # --- the failure cascade ----------------------------------------------------------------------------------------

    def _on_fail(self, conn: Connection, job: Job, error: str):
        """Inside the transaction that fails `job` for good: mark what it owned. `conn` is the library's connection; the work below is plain SQL on the same SQLite connection."""
        raw = conn.connection.driver_connection
        previous = raw.row_factory
        raw.row_factory = sqlite3.Row
        try:
            self._cascade(conn, raw, job, plain(error))
        finally:
            raw.row_factory = previous

    def _cascade(self, conn, c, job, error):
        domain, kind = split_kind(job.kind)
        ident = str(job.id)
        options = job.options
        started = iso(job.started_at) or ""
        runs = {r[0] for r in c.execute("SELECT owner FROM job_work WHERE job_id=? AND kind='ingest'", (ident,))}
        runs.update(
            r[0]
            for r in c.execute(
                "SELECT json_extract(p.plan,'$.ingest.run_id') FROM process_plan p JOIN job_work w ON w.kind='plan' AND w.owner=CAST(p.book_id AS TEXT) WHERE w.job_id=?",
                (ident,),
            )
            if r[0]
        )
        if domain == "process" and kind == "run":
            legacy_plans = "state='running' AND NOT EXISTS (SELECT 1 FROM job_work w WHERE w.kind='plan' AND w.owner=CAST(process_plan.book_id AS TEXT))"
            runs.update(
                r[0]
                for r in c.execute(
                    f"SELECT json_extract(plan,'$.ingest.run_id') FROM process_plan WHERE {legacy_plans}"
                )
                if r[0]
            )
            c.execute(
                f"INSERT INTO job_work(kind,owner,job_id) SELECT 'plan',CAST(book_id AS TEXT),? FROM process_plan WHERE {legacy_plans}",
                (ident,),
            )
        if domain == "ingest" and options.get("run_id") and kind in ("run", "resume", "continue", "restore", "regroup"):
            runs.add(options["run_id"])
        if domain == "ingest" and options.get("run_id"):
            runs.update(
                r[0]
                for r in c.execute(
                    "SELECT DISTINCT r.id FROM ingest_runs r JOIN ingest_members m ON m.run_id=r.id WHERE m.book_id IN (SELECT book_id FROM ingest_members WHERE run_id=?) AND r.created>=? AND r.state IN ('queued','running','processing')",
                    (options["run_id"], started),
                )
            )
        if domain == "ingest" and kind == "run" and options.get("ids") and not runs:
            legacy = c.execute(
                "SELECT r.id FROM ingest_runs r JOIN ingest_members m ON m.run_id=r.id WHERE m.book_id=? AND r.created>=? ORDER BY r.created DESC LIMIT 1",
                (options["ids"][0], started),
            ).fetchone()
            if legacy:
                runs.add(legacy[0])
        for rid in runs:
            run = c.execute("SELECT state,options FROM ingest_runs WHERE id=?", (rid,)).fetchone()
            if (
                run
                and run["state"] == "queued"
                and json.loads(run["options"]).get("collection_run")
                and rid not in (options.get("run_id"), options.get("resume"))
            ):
                # These child works were discovered but never attempted.
                # Preserve them independently instead of retrying the parent.
                self._enqueue(conn, "ingest", "continue", False, dict(run_id=rid))
                continue
            unfinished = run and (
                run["state"] in ("queued", "running", "processing")
                or (kind == "regroup" and rid == options.get("run_id") and run["state"] == "metadata_review")
            )
            if not unfinished:
                continue
            changed = c.execute("UPDATE ingest_runs SET state='failed' WHERE id=?", (rid,)).rowcount
            if changed:
                c.execute("INSERT OR REPLACE INTO job_work VALUES ('ingest',?,?)", (rid, ident))
                c.execute(
                    "INSERT INTO ingest_events(run_id,stage,status,created,elapsed,metrics,data,error) VALUES (?,'job','failed',?,0,'{}','{}',?)",
                    (rid, now(), error),
                )
                c.execute(
                    "UPDATE queue SET state='failed',finished_at=?,error=? WHERE state='queued' AND kind IN ('ingest.continue','ingest.restore') AND json_extract(options,'$.run_id')=?",
                    (time.time(), "previous job failed: " + error, rid),
                )
        c.execute(
            "UPDATE process_plan SET state='failed',error=?,updated=? WHERE state IN ('planning','running') AND book_id IN (SELECT CAST(owner AS INTEGER) FROM job_work WHERE job_id=? AND kind='plan')",
            (error, now(), ident),
        )
        if domain == "system" and kind == "samples":
            saved = c.execute(
                "SELECT data FROM read_models WHERE kind='previews' AND owner=?", (options.get("run_id", ""),)
            ).fetchone()
            if saved:
                preview = json.loads(saved[0])
                for sample in preview.get("samples", []):
                    if sample.get("status") == "generating":
                        sample.update(status="failed", error=error)
                c.execute(
                    "UPDATE read_models SET data=?,updated=? WHERE kind='previews' AND owner=?",
                    (json.dumps(preview), now(), options.get("run_id", "")),
                )
        ids = options.get("ids") or []
        if domain == "ingest" and kind == "run" and len(ids) > 1 and options.get("limit", 5) > 1:
            members = {
                bid for rid in runs for (bid,) in c.execute("SELECT book_id FROM ingest_members WHERE run_id=?", (rid,))
            }
            remaining = [bid for bid in ids[1:] if bid not in members]
            if remaining:
                remainder = dict(options, ids=remaining, limit=min(len(remaining), options.get("limit", 5) - 1))
                remainder.pop("resume", None)
                remainder.pop("_recovered", None)
                # Publish the remaining work in the same transaction as
                # failure: Settings must never observe an empty queue gap.
                self._enqueue(conn, "ingest", "run", False, remainder)

    def fail(self, ident, error):
        """Fail a queued or running job now, as a supervisor that lost it would (the failure cascade included): what the tests and a person's emergency stop use."""
        q = self.queue()
        with q.engine.begin() as c:
            write_lock(c)
            c.execute(
                update(TABLE)
                .where(TABLE.c.id == int(ident), TABLE.c.state == "queued")
                .values(state="running", started_at=time.time(), claim_token=uuid.uuid4().hex)
            )
        job = q.get(int(ident))
        if job is not None and job.state == "running":
            q.fail(job, Interrupted(error) if error == "job interrupted by server shutdown" else RuntimeError(error))

    # --- running ----------------------------------------------------------------------------------------------------

    def run_one(self, isolated=False):
        """Claim and run one job on this thread, now (tests and the CLI; the server's workers run jobs in job processes)."""
        q = self.queue()
        job = q.claim()
        if job is None:
            return False
        q.run(job, self.run_handler)
        return True

    def start(self):
        if self.worker is not None:
            return
        q = self.queue()
        self.worker = Worker(
            q,
            {f"{domain}.{kind}": run_job for domain, kinds in KINDS.items() for kind in kinds},
            concurrency=self.workers,
            poll_s=0.5,
            lower_priority=True,
        ).start()
        self.recover_interrupted()

    def stop(self):
        if self.worker is not None:
            self.worker.stop(timeout=10)
            self.worker = None
