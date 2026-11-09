"""A single background ingest worker; persistent stages allow explicit restart/resume."""

from datetime import UTC, datetime

from app.ingest import pipeline
from app.process.queue import Worker as Base


class Worker(Base):
    domain = "ingest"

    def _do(self, kind, kw):
        self.busy = kind
        self.active = dict(kind=kind, options=kw, state="running", started=datetime.now(UTC).isoformat())
        remainder = None
        try:
            if kind == "reconcile":
                result = self._reconcile()
            elif kind == "run":
                ids = kw.get("ids") or []
                limit = kw.get("limit", 5)
                if not self.inline and len(ids) > 1 and limit > 1:
                    remainder = dict(kw, ids=ids[1:], limit=limit - 1)
                    remainder.pop("resume", None)
                    runs = pipeline.run(**dict(kw, ids=ids[:1], limit=1))
                    members = {
                        member["id"]
                        for run in runs
                        for member in (pipeline.stage_data(run, "inventory") or {}).get("members", [])
                    }
                    remainder["ids"] = [bid for bid in remainder["ids"] if bid not in members]
                    result = [run["id"] for run in runs]
                else:
                    result = [r["id"] for r in pipeline.run(**kw)]
            elif kind == "resume" or kind == "continue":
                result = [pipeline.continue_review(kw["run_id"])["id"]]
            elif kind == "restore":
                current = pipeline.store.get(kw["run_id"])
                result = (
                    [pipeline.continue_review(kw["run_id"])["id"]]
                    if current["state"] in ("queued", "running")
                    else [current["id"]]
                )
            elif kind == "regroup":
                result = pipeline.regroup_collection(kw["run_id"])
            elif kind == "refresh":
                refreshed = pipeline.refresh_metadata(**kw)
                result = [refreshed["id"]]
                if refreshed["state"] == "queued":
                    self.submit("continue", run_id=refreshed["id"])
            elif kind == "auto_metadata":
                result = pipeline.approve_passing_metadata()
                for rid in result:
                    self.submit("continue", run_id=rid)
            else:
                raise ValueError("unknown ingest task")
            self.last = {kind: result}
        except Exception as e:
            self.last = {kind: dict(error=str(e))}
        finally:
            self.busy = None
            self.active = None
            if remainder and remainder["ids"]:
                remainder["limit"] = min(remainder["limit"], len(remainder["ids"]))
                self.submit("run", **remainder)

    def status(self):
        return (
            self.jobs.status(self.domain)
            if self.jobs is not None
            else dict(busy=self.busy, queued=self.q.qsize(), last=self.last)
        )

    def _reconcile(self):
        """Keep bulk recovery off the HTTP thread and load at most one run's evidence."""
        from app import catalogue, db
        from app.ingest import grouping, policy, store

        try:
            snapshot = store.workflow_snapshot()
            current = policy.current()
            count = 0
            for header in snapshot["runs"].values():
                if snapshot["latest"].get(header["book_id"]) != header["id"]:
                    continue
                if header["state"] not in (
                    "queued",
                    "running",
                    "metadata_review",
                    "audiobook_review",
                    "quality_review",
                    "review",
                    "eligible",
                ):
                    continue
                run = store.get(header["id"])
                inv = pipeline.raw_stage(run, "inventory") or {}
                last = run["events"][-1] if run["events"] else {}
                continuation = (
                    run["state"] == "running"
                    and last.get("stage", "").endswith("_approval")
                    and last.get("status") == "done"
                )
                if run["state"] == "running" and inv.get("work_groups"):
                    self.submit("restore", run_id=run["id"])
                    count += 1
                elif not catalogue.get_plan(run["book_id"]) and not catalogue.get_processed(run["book_id"]):
                    if (
                        (run["state"] not in ("running", "queued") or continuation)
                        and inv.get("files")
                        and len(grouping.partition(inv["files"], inv["root"])) > 1
                    ):
                        self.submit("regroup", run_id=run["id"])
                        count += 1
                    elif continuation or run["state"] in ("queued", "running"):
                        self.submit("restore", run_id=run["id"])
                        count += 1
                del run, inv
            if current["auto_approve_metadata"] or current["auto_approve_audiobook"]:
                self.submit("auto_metadata")
            return count
        except db.CorruptDatabase:
            return 0
