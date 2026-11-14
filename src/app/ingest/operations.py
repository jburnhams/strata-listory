"""Current work across both workers, including books a batch has not reached yet."""

from datetime import UTC, datetime

from app import catalogue, db
from app.ingest import pipeline as P
from app.ingest import policy, store


def job_title(kind, options):
    """What a system job is called on the Processing page."""
    return {
        "auto_metadata": "Metadata approvals",
        "bootstrap": "Index saved library data",
        "scan": "Scan library",
        "match": "Match books",
        "enrich": "Refresh community data",
        "samples": "Prepare listening samples",
        "reconcile": "Restore interrupted work",
        "filegroups": f"Scan audiobook folder {options.get('folder', '')}",
        "listens_build": "Build listens from new folders",
        "identify": "Identify new listens",
    }.get(kind)


def dismiss_run(run_id):
    """Clear a failed analysis from the Processing page. The run itself is untouched (the book keeps its history); a new run of the book that fails again shows up again."""
    store.get(run_id)  # raises for an unknown run
    c = db.connect()
    try:
        with c:
            c.execute("INSERT OR REPLACE INTO job_work VALUES ('dismissed-run',?,?)", (run_id, run_id))
    finally:
        c.close()


def dismissed_runs():
    c = db.connect()
    try:
        return {r[0] for r in c.execute("SELECT owner FROM job_work WHERE kind='dismissed-run'")}
    finally:
        c.close()


def view(ingest_tasks, process_tasks, completed=False):
    ingest_tasks = [task for task in ingest_tasks if task["kind"] != "training_features"]
    snapshot = store.workflow_snapshot(activity=True)
    latest = snapshot["latest"]
    runs = snapshot["runs"]
    value = policy.current()
    rows = []
    pending = set()
    books = {b["id"]: b for b in catalogue.list_books()}
    plans = {r["book_id"] for r in snapshot["plans"]}
    for n, task in enumerate(ingest_tasks):
        options = task["options"]
        ids = options.get("ids") or ([options["book_id"]] if options.get("book_id") else [])
        if not ids and options.get("run_id"):
            ids = [store.run_book_id(options["run_id"])]
        for bid in ids:
            if bid not in books or bid in pending:
                continue
            run = runs.get(latest.get(bid))
            if task["state"] == "running" and run and (run["state"] == "running" or run["created"] >= task["started"]):
                continue
            pending.add(bid)
            rows.append(
                dict(
                    id=f"queued-{n}-{bid}",
                    book_id=bid,
                    run_id=None,
                    title=books[bid]["title"],
                    stage="Ingest analysis",
                    state="queued",
                    phase=None,
                    error="",
                )
            )
        if not ids:
            rows.append(
                dict(
                    id=f"ingest-task-{n}",
                    book_id=None,
                    run_id=None,
                    title=job_title(task["kind"], options) or "Discover ingest candidates",
                    stage="Ingest",
                    state=task["state"],
                    phase=None,
                    error="",
                    job_id=task.get("id"),
                    progress=task.get("progress"),
                )
            )
    hidden = dismissed_runs()
    for run in sorted(runs.values(), key=lambda r: r["created"], reverse=True):
        if run["id"] in hidden and run["state"] in ("failed", "processing_failed"):
            continue
        bid = run.get("primary_id") or run["book_id"]
        if latest.get(bid) != run["id"] or any(latest.get(member) == run["id"] for member in pending):
            continue
        if bid in plans and run["state"] in (
            "processed",
            "processing",
            "processing_failed",
            "eligible",
            "review",
            "split_existing",
        ):
            continue
        phase = "processing" if run["state"] in ("split", "split_existing", "queued") else P.header_phase(run, value)
        state = (
            "running"
            if run["state"] == "running"
            else "failed"
            if run["state"] in ("failed", "processing_failed")
            else "queued"
            if run["state"] == "queued"
            else "completed"
            if run["state"] in ("processed", "split", "split_existing")
            else "waiting"
        )
        if state == "completed" and not completed:
            continue
        last = run["activity"][-1] if run["activity"] else {}
        error = run["error"] if state == "failed" else ""
        title = run.get("title") or books.get(bid, {}).get("title") or f"Book {bid}"
        rows.append(
            dict(
                id=run["id"],
                run_id=run["id"],
                book_id=bid,
                title=title,
                stage=(
                    f"Split into {run.get('children') or 0} source groups"
                    if run["state"] == "split"
                    else "Existing proposal/output retained"
                )
                if run["state"] in ("split", "split_existing")
                else {
                    "providers": "Book catalogue search",
                    "metadata": "Book identification",
                    "recording_providers": "Recording catalogue search",
                    "recording": "Audiobook identification",
                }.get(last.get("stage"), last.get("stage", "inventory"))
                if state in ("running", "failed") or run["state"] in ("split", "split_existing")
                else (
                    "Book identification approval"
                    if phase == "metadata"
                    else "Audiobook identification approval"
                    if phase == "audiobook"
                    else f"{phase.capitalize()} approval"
                ),
                state=state,
                phase=phase,
                error=error,
                when=(run["activity"][-1]["created"] if run["activity"] else None) or run["created"],
            )
        )
    for n, task in enumerate(process_tasks):
        rows.append(
            dict(
                id=f"process-task-{n}",
                book_id=None,
                run_id=None,
                title="Build processing proposals" if task["kind"] == "propose" else "Process approved books",
                stage="Processing",
                state=task["state"],
                phase=None,
                error="",
            )
        )
    labels = {
        "inventory": "Source inventory",
        "identity": "Recover local identity",
        "providers": "Book catalogue search",
        "metadata": "Book identification",
        "recording_providers": "Recording catalogue search",
        "recording": "Audiobook identification",
        "alignment": "Compare audio versions",
        "quality": "Measure audio quality",
        "selection": "Choose source audio",
        "planning": "Build processing proposal",
        "validation": "Validate source files",
    }
    for row in rows:
        run = runs.get(row.get("run_id"))
        if not run:
            continue
        events = run["activity"]
        latest = events[-1] if events else {}
        stage = latest.get("stage", "inventory")
        start = dict(created=run.get("step_started"))
        row["current_step"] = (
            (latest.get("data") or {}).get("message")
            if latest.get("status") == "progress"
            else labels.get(stage, stage.replace("_", " "))
        )
        row["step_elapsed"] = (
            max(0, (datetime.now(UTC) - datetime.fromisoformat(start["created"])).total_seconds())
            if row["state"] == "running" and start.get("created")
            else latest.get("elapsed", 0)
        )
        row["logs"] = [
            dict(
                created=e["created"],
                message=e["data"].get("message")
                or f"{labels.get(e['stage'], e['stage'].replace('_', ' '))}: {e['status']}"
                + (f" — {e['error']}" if e["error"] else ""),
                status=e["status"],
            )
            for e in events[-12:]
        ]
    order = {"running": 0, "queued": 1, "failed": 2, "waiting": 3, "completed": 4}
    rows.sort(
        key=lambda r: r.get("when") or "", reverse=True
    )  # newest first inside each state (rows with no time go last)
    rows.sort(key=lambda r: order[r["state"]])
    return dict(rows=rows, busy=bool(ingest_tasks or process_tasks))


def library(ingest_tasks, process_tasks):
    """Current workflow membership for Library, including every linked group member."""
    snapshot = store.workflow_snapshot()
    latest = snapshot["latest"]
    runs = snapshot["runs"]
    value = policy.current()
    groups = {}
    stages = {}
    for bid, rid in latest.items():
        groups.setdefault(rid, []).append(bid)

    def add(bid, stage):
        for member in groups.get(latest.get(bid), [bid]):
            stages.setdefault(member, set()).add(stage)

    for rid, run in runs.items():
        state = run["state"]
        stage = (
            "finished"
            if state == "processed"
            else "processing"
            if state in ("running", "queued", "failed", "processing", "processing_failed", "split", "split_existing")
            else P.header_phase(run, value)
        )
        for bid in groups[rid]:
            stages.setdefault(bid, set()).add(stage)
    for row in snapshot["plans"]:
        add(
            row["book_id"],
            "proposed"
            if row["state"] in ("proposed", "rejected")
            else "finished"
            if row["state"] == "done"
            else "processing",
        )
    for row in snapshot["processed"]:
        add(row["book_id"], "finished")
    for task in ingest_tasks:
        options = task["options"]
        ids = options.get("ids") or ([options["book_id"]] if options.get("book_id") else [])
        if not ids and options.get("run_id"):
            ids = [store.run_book_id(options["run_id"])]
        for bid in ids:
            run = runs.get(latest.get(bid))
            if task["state"] == "running" and run and (run["state"] == "running" or run["created"] >= task["started"]):
                continue
            add(bid, "processing")
    for task in process_tasks:
        for bid in task["options"].get("ids") or []:
            add(bid, "processing")
    return dict(books={str(bid): sorted(values) for bid, values in stages.items()})
