"""Durable shared queue. HTTP submits small records; only bounded workers execute them."""

import hashlib
import json
import os
import subprocess
import threading
import time
import uuid
from contextlib import nullcontext
from contextvars import ContextVar
from datetime import UTC, datetime, timedelta

from app import db, oslib, settings

CURRENT_JOB = ContextVar("current_job", default=None)
# A stop we asked for (service restart, deploy, SIGTERM) says nothing against the job itself, unlike a timeout, a crash or an application error, so these failures are replayed on the next start. Capped per chain and per age so a job that really kills the server cannot loop.
SHUTDOWN_ERRORS = (
    "job interrupted by server shutdown",
    "job worker exited without a completed result (exit -15)",
)  # exact messages: fail() also cancels queued continuations with 'previous job failed: <error>', which must not be replayed on its own
AUTO_RETRY_LIMIT = 2
AUTO_RETRY_WINDOW_HOURS = 24


def bind_work(connection, kind, owner):
    if ident := CURRENT_JOB.get():
        connection.execute("INSERT OR REPLACE INTO job_work VALUES (?,?,?)", (kind, str(owner), ident))


def now():
    return datetime.now(UTC).isoformat()


class JobQueue:
    def __init__(self, handler, workers=None, timeout_seconds=None):
        self.handler = handler
        self.workers = max(1, min(8, int(workers or os.environ.get("APP_WORKERS", "1"))))
        self.timeout_seconds = float(
            timeout_seconds if timeout_seconds is not None else os.environ.get("APP_JOB_TIMEOUT_SECONDS", "1800")
        )
        if not 0 < self.timeout_seconds <= 86400:
            raise ValueError("job timeout must be greater than zero and at most 86400 seconds")
        self.wake = threading.Event()
        self.stopping = threading.Event()
        self.threads = []
        self.configuration_lock = threading.RLock()

    def submit(self, domain, kind, retry=False, **options):
        with self.configuration_lock:
            return self._submit(domain, kind, retry, options)

    def _submit(self, domain, kind, retry, options, connection=None):
        payload = json.dumps(options, sort_keys=True, allow_nan=False)
        key = hashlib.sha256(json.dumps([domain, kind, payload]).encode()).hexdigest()
        c = connection if connection is not None else db.connect()
        try:
            with c if connection is None else nullcontext():
                previous = c.execute(
                    "SELECT j.id,j.state FROM jobs j WHERE j.dedupe=? AND (j.state IN ('queued','running') OR (j.state='failed' AND NOT EXISTS (SELECT 1 FROM job_work r WHERE r.kind='retry' AND r.owner=j.id) AND NOT EXISTS (SELECT 1 FROM jobs newer WHERE newer.dedupe=j.dedupe AND newer.created>j.created))) ORDER BY CASE j.state WHEN 'running' THEN 0 WHEN 'queued' THEN 1 ELSE 2 END,j.created DESC LIMIT 1",
                    (key,),
                ).fetchone()
                if previous and (not retry or previous["state"] != "failed"):
                    return previous[0]
                ident = uuid.uuid4().hex
                ids = set(options.get("ids") or [])
                if options.get("book_id"):
                    ids.add(options["book_id"])
                if options.get("run_id"):
                    ids.update(
                        r[0]
                        for r in c.execute("SELECT book_id FROM ingest_members WHERE run_id=?", (options["run_id"],))
                    )
                # Derived evidence reads immutable event snapshots; it must not block a human decision.
                owned = sorted(f"book:{bid}" for bid in ids)
                if options.get("listen_id"):  # listens live in the Postgres catalogue, a different id space from books
                    owned.append(f"listen:{options['listen_id']}")
                resources = json.dumps([] if domain == "system" and kind == "training_features" else owned)
                c.execute(
                    "INSERT OR IGNORE INTO jobs(id,domain,kind,options,dedupe,state,created,resources,timeout_seconds) VALUES (?,?,?,?,?,'queued',?,?,?)",
                    (ident, domain, kind, payload, key, now(), resources, self.timeout_seconds),
                )
                row = c.execute(
                    "SELECT id FROM jobs WHERE dedupe=? AND state IN ('queued','running')", (key,)
                ).fetchone()
                ident = row[0]
        finally:
            if connection is None:
                c.close()
        self.wake.set()
        return ident

    def get(self, ident):
        c = db.connect()
        try:
            row = c.execute(
                "SELECT id,domain,kind,state,created,started,finished,error,result,timeout_seconds FROM jobs WHERE id=?",
                (ident,),
            ).fetchone()
        finally:
            c.close()
        if row is None:
            raise ValueError("no such job")
        out = dict(row)
        out["result"] = json.loads(out["result"]) if out["result"] else None
        return out

    def retry(self, ident):
        with self.configuration_lock:
            return self._retry(ident)

    def _retry(self, ident):
        c = db.connect()
        try:
            with c:
                row = c.execute(
                    "SELECT domain,kind,options FROM jobs WHERE id=? AND state='failed'", (ident,)
                ).fetchone()
                if row is None:
                    raise ValueError("only failed jobs can be retried")
                options = json.loads(row["options"])
                kind = row["kind"]
                if options.pop("_recovered", False):
                    options.pop("resume", None)
                if row["domain"] == "ingest" and kind == "approve":
                    options["_recovered"] = True
                if row["domain"] == "ingest" and kind in ("run", "regroup"):
                    failed = [
                        r[0]
                        for r in c.execute(
                            "SELECT DISTINCT r.book_id FROM ingest_runs r JOIN job_work w ON w.kind='ingest' AND w.owner=r.id WHERE w.job_id=? AND r.state='failed'",
                            (ident,),
                        )
                    ]
                    selected = failed or (options.get("ids") or [])[:1]
                    if selected:
                        kind = "run"
                        options.update(ids=selected, limit=len(selected))
                        options.pop("resume", None)
                        options.pop("run_id", None)
                if row["domain"] == "process" and row["kind"] in ("run", "propose"):
                    owned = "state='failed' AND book_id IN (SELECT CAST(owner AS INTEGER) FROM job_work WHERE job_id=? AND kind='plan')"
                    if row["kind"] == "run":
                        c.execute(
                            f"UPDATE process_plan SET state='approved',error='',updated=? WHERE {owned}", (now(), ident)
                        )
                    else:
                        c.execute(
                            f"DELETE FROM read_models WHERE kind='plan' AND CAST(owner AS INTEGER) IN (SELECT book_id FROM process_plan WHERE {owned})",
                            (ident,),
                        )
                        c.execute(f"DELETE FROM process_plan WHERE {owned}", (ident,))
                retried = self._submit(row["domain"], kind, True, options, connection=c)
                c.execute("INSERT OR REPLACE INTO job_work VALUES ('retry',?,?)", (ident, retried))
                return retried
        finally:
            c.close()

    def pending(self):
        c = db.connect()
        try:
            return c.execute("SELECT 1 FROM jobs WHERE state IN ('queued','running') LIMIT 1").fetchone() is not None
        finally:
            c.close()

    def failures(self, limit=25, domain=None):
        c = db.connect()
        owner = "COALESCE(json_extract(j.options,'$.book_id'),json_extract(j.options,'$.ids[0]'),(SELECT book_id FROM ingest_runs WHERE id=COALESCE(json_extract(j.options,'$.run_id'),json_extract(j.options,'$.resume'))),(SELECT CAST(owner AS INTEGER) FROM job_work WHERE job_id=j.id AND kind='plan' LIMIT 1))"
        try:
            return [
                dict(r)
                for r in c.execute(
                    f"SELECT j.id,j.domain,j.kind,j.error,j.finished,j.options,b.id AS book_id,b.title FROM jobs j LEFT JOIN books b ON b.id={owner} WHERE j.state='failed' AND (? IS NULL OR j.domain=?) AND NOT EXISTS (SELECT 1 FROM job_work retry WHERE retry.kind='retry' AND retry.owner=j.id) AND NOT EXISTS (SELECT 1 FROM jobs newer WHERE newer.dedupe=j.dedupe AND newer.created>j.created) ORDER BY j.finished DESC LIMIT ?",
                    (domain, domain, limit),
                )
            ]
        finally:
            c.close()

    def tasks(self, domain=None):
        c = db.connect()
        try:
            return [
                dict(
                    id=r["id"],
                    kind=r["kind"],
                    options=json.loads(r["options"]),
                    state=r["state"],
                    started=r["started"] or "",
                )
                for r in c.execute(
                    "SELECT id,kind,options,state,started FROM jobs WHERE state IN ('queued','running')"
                    + (" AND domain=?" if domain else "")
                    + " ORDER BY CASE state WHEN 'running' THEN 0 ELSE 1 END,created,id",
                    (domain,) if domain else (),
                )
            ]
        finally:
            c.close()

    def status(self, domain):
        tasks = self.tasks(domain)
        c = db.connect()
        try:
            row = c.execute(
                "SELECT kind,state,result,error FROM jobs WHERE domain=? AND state IN ('done','failed') ORDER BY finished DESC LIMIT 1",
                (domain,),
            ).fetchone()
        finally:
            c.close()
        return dict(
            busy=next((t["kind"] for t in tasks if t["state"] == "running"), None),
            queued=sum(t["state"] == "queued" for t in tasks),
            last={row["kind"]: json.loads(row["result"]) if row["state"] == "done" else dict(error=row["error"])}
            if row
            else {},
        )

    def _claim(self):
        c = db.connect()
        try:
            with c:
                c.execute("BEGIN IMMEDIATE")
                # Reserve one bounded slot for explicit review decisions so long audio
                # jobs cannot hold up the review UI. Resource locks still span both lanes.
                review = "domain='ingest' AND kind='match_review'"
                active = c.execute(
                    f"SELECT count(*) FILTER (WHERE {review}),count(*) FILTER (WHERE NOT ({review})) FROM jobs WHERE state='running'"
                ).fetchone()
                row = c.execute(
                    """SELECT j.id,j.timeout_seconds FROM jobs j WHERE j.state='queued'
                    AND ((j.domain='ingest' AND j.kind='match_review' AND ?<1)
                        OR (NOT (j.domain='ingest' AND j.kind='match_review') AND ?<?))
                    AND NOT EXISTS (SELECT 1 FROM jobs active,json_each(active.resources) a,json_each(j.resources) b WHERE active.state='running' AND a.value=b.value)
                    ORDER BY CASE WHEN j.domain='ingest' AND j.kind='match_review' THEN 0 WHEN j.domain='system' AND j.kind='training_features' THEN 2 ELSE 1 END,j.created,j.id LIMIT 1""",
                    (active[0], active[1], self.workers),
                ).fetchone()
                if row is None:
                    return None
                c.execute(
                    "UPDATE jobs SET state='running',started=?,owner_pid=? WHERE id=?", (now(), os.getpid(), row["id"])
                )
        finally:
            c.close()
        return dict(row)

    def run_one(self, isolated=False):
        with self.configuration_lock:
            row = self._claim()
        if row is None:
            return False
        if isolated:
            self._supervise(row)
        else:
            self.execute_claimed(row["id"])
        return True

    def execute_claimed(self, ident):
        c = db.connect()
        try:
            row = c.execute("SELECT domain,kind,options FROM jobs WHERE id=? AND state='running'", (ident,)).fetchone()
        finally:
            c.close()
        if row is None:
            raise ValueError("job is not running")
        token = CURRENT_JOB.set(ident)
        result = None
        error = ""
        serialized = None
        try:
            result = self.handler(row["domain"], row["kind"], json.loads(row["options"]))
            if row["domain"] == "process" and isinstance(result, dict) and result.get("failed"):
                raise ValueError(f"processing failed for {result['failed']} book(s)")
            serialized = json.dumps(result, allow_nan=False)
        except Exception as exc:
            error = str(exc) or type(exc).__name__
        finally:
            CURRENT_JOB.reset(token)
        if error:
            self.fail(ident, error)
            return
        c = db.connect()
        try:
            with c:
                c.execute(
                    "UPDATE jobs SET state='done',finished=?,result=?,error='' WHERE id=? AND state='running'",
                    (now(), serialized, ident),
                )
        finally:
            c.close()

    def _supervise(self, row):
        process = None
        try:
            deadline = time.monotonic() + row["timeout_seconds"]
            process = oslib.start_job(row["id"], settings.data_dir())
            while process.poll() is None:
                remaining = deadline - time.monotonic()
                if self.stopping.is_set() or remaining <= 0:
                    oslib.kill_job(process)
                    self.fail(
                        row["id"],
                        "job interrupted by server shutdown"
                        if self.stopping.is_set()
                        else f"job exceeded its {row['timeout_seconds']:g} second time limit",
                    )
                    return
                try:
                    process.wait(timeout=min(0.25, remaining))
                except subprocess.TimeoutExpired:
                    pass
            if process.returncode or self.get(row["id"])["state"] == "running":
                oslib.kill_job(process)
                self.fail(row["id"], f"job worker exited without a completed result (exit {process.returncode})")
        except Exception as exc:
            if process is not None:
                oslib.kill_job(process)
            self.fail(row["id"], str(exc) or type(exc).__name__)
        finally:
            if process is not None and (pipe := getattr(process, "stdin", None)) is not None:
                pipe.close()

    def fail(self, ident, error):
        c = db.connect()
        try:
            with c:
                job = c.execute(
                    "SELECT domain,kind,options,started FROM jobs WHERE id=? AND state IN ('queued','running')",
                    (ident,),
                ).fetchone()
                if job is None:
                    return
                c.execute(
                    "UPDATE jobs SET state='failed',finished=?,error=?,result=NULL WHERE id=?", (now(), error, ident)
                )
                options = json.loads(job["options"])
                runs = {
                    r[0] for r in c.execute("SELECT owner FROM job_work WHERE job_id=? AND kind='ingest'", (ident,))
                }
                runs.update(
                    r[0]
                    for r in c.execute(
                        "SELECT json_extract(p.plan,'$.ingest.run_id') FROM process_plan p JOIN job_work w ON w.kind='plan' AND w.owner=CAST(p.book_id AS TEXT) WHERE w.job_id=?",
                        (ident,),
                    )
                    if r[0]
                )
                if job["domain"] == "process" and job["kind"] == "run":
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
                if (
                    job["domain"] == "ingest"
                    and options.get("run_id")
                    and job["kind"] in ("run", "resume", "continue", "restore", "regroup")
                ):
                    runs.add(options["run_id"])
                if job["domain"] == "ingest" and options.get("run_id"):
                    runs.update(
                        r[0]
                        for r in c.execute(
                            "SELECT DISTINCT r.id FROM ingest_runs r JOIN ingest_members m ON m.run_id=r.id WHERE m.book_id IN (SELECT book_id FROM ingest_members WHERE run_id=?) AND r.created>=? AND r.state IN ('queued','running','processing')",
                            (options["run_id"], job["started"] or ""),
                        )
                    )
                if job["domain"] == "ingest" and job["kind"] == "run" and options.get("ids") and not runs:
                    legacy = c.execute(
                        "SELECT r.id FROM ingest_runs r JOIN ingest_members m ON m.run_id=r.id WHERE m.book_id=? AND r.created>=? ORDER BY r.created DESC LIMIT 1",
                        (options["ids"][0], job["started"] or ""),
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
                        self._submit("ingest", "continue", False, dict(run_id=rid), connection=c)
                        continue
                    unfinished = run and (
                        run["state"] in ("queued", "running", "processing")
                        or (
                            job["kind"] == "regroup"
                            and rid == options.get("run_id")
                            and run["state"] == "metadata_review"
                        )
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
                            "UPDATE jobs SET state='failed',finished=?,error=? WHERE state='queued' AND domain='ingest' AND kind IN ('continue','restore') AND json_extract(options,'$.run_id')=?",
                            (now(), "previous job failed: " + error, rid),
                        )
                c.execute(
                    "UPDATE process_plan SET state='failed',error=?,updated=? WHERE state IN ('planning','running') AND book_id IN (SELECT CAST(owner AS INTEGER) FROM job_work WHERE job_id=? AND kind='plan')",
                    (error, now(), ident),
                )
                if job["domain"] == "system" and job["kind"] == "samples":
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
                if job["domain"] == "ingest" and job["kind"] == "run" and len(ids) > 1 and options.get("limit", 5) > 1:
                    members = {
                        bid
                        for rid in runs
                        for (bid,) in c.execute("SELECT book_id FROM ingest_members WHERE run_id=?", (rid,))
                    }
                    remaining = [bid for bid in ids[1:] if bid not in members]
                    if remaining:
                        remainder = dict(options, ids=remaining, limit=min(len(remaining), options.get("limit", 5) - 1))
                        remainder.pop("resume", None)
                        remainder.pop("_recovered", None)
                        # Publish the remaining work in the same transaction as
                        # failure: Settings must never observe an empty queue gap.
                        self._submit("ingest", "run", False, remainder, connection=c)
        finally:
            c.close()

    def recover_interrupted(self):
        """Re-queue jobs that failed only because the server was stopped; returns the new job ids."""
        c = db.connect()
        out = []
        try:
            cutoff = (datetime.now(UTC) - timedelta(hours=AUTO_RETRY_WINDOW_HOURS)).isoformat()
            marker = "j.error IN (" + ",".join("?" for _ in SHUTDOWN_ERRORS) + ")"
            candidates = [
                r[0]
                for r in c.execute(
                    f"SELECT j.id FROM jobs j WHERE j.state='failed' AND j.finished>=? AND ({marker}) AND NOT EXISTS (SELECT 1 FROM job_work r WHERE r.kind='retry' AND r.owner=j.id) AND NOT EXISTS (SELECT 1 FROM jobs newer WHERE newer.dedupe=j.dedupe AND newer.created>j.created) ORDER BY j.finished,j.id",
                    (cutoff, *SHUTDOWN_ERRORS),
                )
            ]

            def attempts(ident):
                n = 0
                while r := c.execute("SELECT owner FROM job_work WHERE kind='retry' AND job_id=?", (ident,)).fetchone():
                    ident = r[0]
                    n += 1
                return n

            candidates = [i for i in candidates if attempts(i) < AUTO_RETRY_LIMIT]
        finally:
            c.close()
        for ident in candidates:
            try:
                out.append(self.retry(ident))
            except ValueError:
                pass  # superseded meanwhile (for example the same work was submitted again)
        return out

    def start(self):
        if self.threads:
            return
        c = db.connect()
        try:
            with c:
                for row in c.execute(
                    "SELECT id,owner_pid,domain,kind,options,started FROM jobs WHERE state='running'"
                ).fetchall():
                    if not oslib.pid_alive(row["owner_pid"]):
                        # Fail outside this read connection; never replay a job
                        # that may have killed or stalled its previous owner.
                        self.fail(row["id"], "job interrupted: previous worker stopped")
        finally:
            c.close()
        self.recover_interrupted()
        for n in range(self.workers + 1):
            thread = threading.Thread(
                target=self._loop, kwargs=dict(isolated=True), name=f"job-worker-{n}", daemon=True
            )
            self.threads.append(thread)
            thread.start()

    def _loop(self, isolated=False):
        while not self.stopping.is_set():
            self.wake.clear()
            if not self.run_one(isolated=isolated):
                self.wake.wait(1)

    def stop(self):
        self.stopping.set()
        self.wake.set()
        for thread in self.threads:
            thread.join(timeout=5)
