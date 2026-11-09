"""Operational glue for identification: train, score the library into a cached queue, page it, record decisions.

Nothing here approves a book or touches the catalogue; decisions go to `identify/labels.sqlite` and the next training run learns from them."""

import json
import sqlite3
from contextlib import closing
from pathlib import Path

from app import settings
from app.identify import candidates, context, dataset, queue
from app.identify import model as M


def folder():
    path = Path(settings.data_dir()) / "identify"
    path.mkdir(parents=True, exist_ok=True)
    return path


def model_file():
    return folder() / "model.ubj"


def load_predict():  # pragma: no cover - needs the optional model dependencies
    import numpy as np
    from xgboost import XGBClassifier

    if not model_file().is_file():
        raise FileNotFoundError("no trained identification model: run `./app identify-train`")
    m = XGBClassifier()
    m.load_model(str(model_file()))
    return lambda rows: m.predict_proba(np.array(rows))[:, 1]


def library_items(catalogue_db):
    """Latest ingest run per book: annotated book items (with folder context) of kind 'book'."""
    index = context.load(catalogue_db)
    items = []
    with closing(sqlite3.connect(Path(catalogue_db).resolve().as_uri() + "?mode=ro", uri=True)) as c:
        members = dict(c.execute("SELECT run_id, count(*) FROM ingest_members GROUP BY run_id").fetchall())
        for run_id in context.current_runs(c):
            row = c.execute(
                "SELECT data FROM ingest_events WHERE run_id=? AND stage='identity' AND status='done' ORDER BY id DESC LIMIT 1",
                (run_id,),
            ).fetchone()
            data = json.loads(row[0]) if row else {}
            if data.get("kind") != "book":
                continue
            b = data.get("book") or {}
            inventory = c.execute(
                "SELECT data FROM ingest_events WHERE run_id=? AND stage='inventory' AND status='done' ORDER BY id DESC LIMIT 1",
                (run_id,),
            ).fetchone()
            items.append(
                index.annotate(
                    dict(
                        title=b.get("title", ""),
                        author=b.get("author", ""),
                        narrator=b.get("narrator", ""),
                        series=b.get("series", ""),
                        asin=b.get("asin", ""),
                        year=b.get("year"),
                        path=b.get("path", ""),
                        run_id=run_id,
                        **context.facts(data, json.loads(inventory[0]) if inventory else None),
                    ),
                    members.get(run_id, 1),
                )
            )
    return items


def refresh(catalogue_db, predict=None):
    """Score the whole library and cache the queue (about a minute for a few thousand items)."""
    predict = predict or load_predict()
    with candidates.connect() as connection:
        q, accepted = queue.build(connection, predict, library_items(catalogue_db))
    summary = dict(
        queue=len(q),
        accepted=len(accepted),
        accepted_files=sum(a["files"] for a in accepted),
        queue_files=sum(s["files"] for s in q),
    )
    (folder() / "queue.json").write_text(json.dumps(dict(summary=summary, queue=q), default=str))
    return summary


def page(limit=10, after=None):
    """Undecided groups by impact from the cache, continuing after the group `after` (the review screen scrolls through all of them and sends the last group it holds, so answers given meanwhile cannot shift the next page); decisions made since the last refresh are skipped immediately."""
    try:
        cached = json.loads((folder() / "queue.json").read_text())
    except (OSError, ValueError):
        return dict(summary=None, items=[])
    done = queue.decided()
    entries = cached["queue"]
    start = next((i + 1 for i, s in enumerate(entries) if s["group"] == after), 0) if after else 0
    items = [s for s in entries if s["group"] not in done]
    return dict(
        summary=dict(cached["summary"], remaining=len(items), decided=len(done)),
        items=[s for s in entries[start:] if s["group"] not in done][: max(1, min(limit, 50))],
    )


def find(group):
    """One cached queue entry by group key, wherever it sits in the queue (None if unknown or already decided)."""
    try:
        cached = json.loads((folder() / "queue.json").read_text())
    except (OSError, ValueError):
        return None
    return None if group in queue.decided() else next((s for s in cached["queue"] if s["group"] == group), None)


def apply(item, decision, work=""):
    """Carry a person's decision on a queue entry into the ingest pipeline; returns dict(status, note). Never raises for a book that simply cannot be approved yet: the label is already saved, the note says why.

    approve: a run waiting for book identification is approved as the model's work (the ordinary `accept` path); a run whose book is already approved gets the human confirmation recorded. reject: only withdraws an approval that exists; a run still waiting needs nothing but the hard-negative label.
    A group whose files are still separate catalogue entries (no grouped run) is not approved: one answer would have to approve each fragment as its own book."""
    from app.identify import textkeys as tk
    from app.ingest import match_review, pipeline, store

    local = item.get("item") or {}
    run_id = local.get("run_id")
    if not run_id:
        return dict(status="skipped", note="no ingest run for these files yet")
    if local.get("members", 1) <= 1 and local.get("siblings", 1) > 1:
        return dict(
            status="skipped", note="the files are not grouped into one book yet, so there is no single book to approve"
        )
    try:
        run = store.get(run_id)
        approved = pipeline.raw_stage(run, "metadata_approval") and not pipeline.book_review_held(run)
        if decision == "reject":
            if not approved:
                return dict(status="labelled", note="nothing was approved, so nothing to withdraw")
            match_review.revoke(run_id, match_review.basis(run))
            return dict(status="withdrawn", note="the book approval was withdrawn")
        if approved:
            # Confirm only the book that is approved: a different approved work must be changed in Pipeline, never marked human-confirmed by an answer about another work.
            meta = pipeline.raw_stage(run, "metadata") or {}
            chosen = (meta.get("manual_selection") or {}).get("candidate") or {}
            resolved = (meta.get("work") or {}).get("resolved") or {}
            same = (
                chosen.get("id") == (work or item.get("work"))
                if chosen
                else (
                    tk.title_key(resolved.get("title") or "") == tk.title_key(item.get("title") or "")
                    and bool(resolved.get("title"))
                )
            )
            if not same:
                return dict(
                    status="skipped",
                    note=f"the book is already approved as {resolved.get('title') or 'a different work'}; change it in Pipeline if that is wrong",
                )
            return dict(
                status="confirmed"
                if match_review.confirm(run_id, match_review.basis(run)).get("changed")
                else "approved",
                note="the book was already approved",
            )
        if run["state"] not in ("metadata_review", "review", "eligible"):
            return dict(
                status="skipped",
                note=f"the run is {run['state'].replace('_', ' ')}, not waiting for book identification",
            )
        candidate = dict(
            source="openlibrary",
            id=work or item["work"],
            url="https://openlibrary.org" + (work or item["work"]),
            title=item.get("title") or "",
            authors=list(item.get("authors") or []),
            original_year=None,
        )
        result = match_review.accept_work(run_id, candidate, item.get("score"))
        return dict(
            status="approved",
            note="the book was approved and the pipeline continues",
            changed=bool(result.get("changed")),
        )
    except (ValueError, KeyError, OSError) as error:
        return dict(status="skipped", note=str(error))


def train(catalogue_db):  # pragma: no cover - needs the optional model dependencies
    """Retrain from approvals plus stored decisions (manual, highest weight), save the model and refresh the queue."""
    runs = dataset.labelled_runs(catalogue_db) + queue.label_runs()
    with candidates.connect() as connection:
        rows, misses = dataset.build(connection, runs)
    fitted, report, extra = M.fit(rows)
    fitted.save_model(str(model_file()))
    (folder() / "report.json").write_text(
        json.dumps(dict(report, runs=len(runs), candidate_misses=len(misses), **extra), indent=1)
    )
    return dict(report, runs=len(runs), candidate_misses=len(misses))
