"""Persistent derived training evidence. Feature additions backfill only missing columns."""

import hashlib
import json
import sqlite3
from contextvars import ContextVar
from pathlib import Path

from app import db, read_models, settings
from app.ingest import match_audit as audit

DIRTY = ContextVar("training_dirty_runs", default=None)
INPUT_STAGES = {"inventory", "identity", "providers"}


def default_path():
    return Path(settings.data_dir()) / "training" / "features.sqlite"


def fingerprint(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True, ensure_ascii=False).encode()).hexdigest()


def input_key(run):
    events = {
        e["stage"]: e.get("id", i)
        for i, e in enumerate(run["events"])
        if e["status"] == "done" and e["stage"] in INPUT_STAGES
    }
    return fingerprint(dict(run_id=run["id"], events=events, candidate_schema=1))


def label_key(run):
    decisions = [
        e.get("id", i)
        for i, e in enumerate(run["events"])
        if e["status"] == "done" and e["stage"] in ("metadata_approval", "metadata_revocation", "match_review_decision")
    ]
    return fingerprint(dict(run_id=run["id"], decisions=decisions, label_schema=1))


def backfill(rows, cohorts=None):
    """Preserve existing feature values; extra evidence never reruns title resolution."""
    if cohorts is None:
        cohorts = {}
        for row in rows:
            cohorts.setdefault(row["run_id"], []).append(row["candidate"])
    changed = False
    for row in rows:
        existing = row["features"]
        candidate = row["candidate"]
        cohort = cohorts.get(row["run_id"], [])
        if candidate not in cohort:
            cohort = cohort + [candidate]
        missing = audit.EXTRA_FEATURES - existing.keys()
        if not missing:
            continue
        book = row.get("local_book") or dict(title=row.get("title", ""), author=row.get("author", ""))
        added = audit.evidence_features(book, candidate, cohort, row.get("edition_evidence", []))
        existing.update({name: added[name] for name in missing})
        row["version"] = audit.VERSION
        changed = True
    return changed


class FeatureCache:
    def __init__(self, path=None):
        self.path = Path(path) if path else default_path()
        self.path.parent.mkdir(parents=True, exist_ok=True)
        c = self.connect()
        try:
            with c:
                c.execute("PRAGMA journal_mode=WAL")
                c.execute(
                    "CREATE TABLE IF NOT EXISTS entries(kind TEXT NOT NULL,key TEXT NOT NULL,data TEXT NOT NULL,PRIMARY KEY(kind,key))"
                )
        finally:
            c.close()

    def connect(self):
        return sqlite3.connect(self.path, timeout=30)

    def get(self, kind, key):
        c = self.connect()
        try:
            row = c.execute("SELECT data FROM entries WHERE kind=? AND key=?", (kind, key)).fetchone()
        finally:
            c.close()
        return json.loads(row[0]) if row else None

    def save(self, kind, key, value):
        c = self.connect()
        try:
            with c:
                c.execute(
                    "INSERT OR REPLACE INTO entries VALUES(?,?,?)",
                    (kind, key, json.dumps(value, ensure_ascii=False, allow_nan=False)),
                )
        finally:
            c.close()

    def candidates(self, run):
        key = input_key(run)
        rows = self.get("candidates", key)
        if rows is None:
            rows = audit.candidate_rows(run)
            book = next(
                (
                    e["data"].get("book", {})
                    for e in reversed(run["events"])
                    if e["stage"] == "identity" and e["status"] == "done"
                ),
                {},
            )
            for row in rows:
                row.setdefault("local_book", book)
            self.save("candidates", key, rows)
        elif backfill(rows):
            self.save("candidates", key, rows)
        approval = next(
            (e["data"] for e in reversed(run["events"]) if e["stage"] == "metadata_approval" and e["status"] == "done"),
            None,
        )
        for row in rows:
            row["approval"] = approval
        return rows

    def trusted(self, run):
        from app.ingest import match_model

        key = label_key(run)
        rows = self.get("labels", key)
        if rows is None:
            rows = match_model.trusted_rows(run, candidate_loader=self.candidates)
            self.save("labels", key, rows)
        elif backfill(rows):
            self.save("labels", key, rows)
        return rows

    def negatives(self, rows, build, query_ids=None):
        # Keep existing pairs when new works arrive. Regenerate only a changed
        # query or a pair whose foreign consent/group membership is no longer valid.
        payload = lambda part: [{k: v for k, v in row.items() if k not in ("features", "version")} for row in part]
        legacy = self.get("negatives", fingerprint(payload(rows)))
        queries = {}
        positives = {}
        for row in rows:
            queries.setdefault(row["run_id"], []).append(row)
            if row["label"] == 1:
                positives[row["run_id"], row["candidate_key"]] = row
        result = list(rows)
        cohorts = {rid: [r["candidate"] for r in part] for rid, part in queries.items()}

        def foreign(negative):
            key = negative["candidate_key"].removeprefix("cross:")
            if negative.get("foreign_run_id"):
                return positives.get((negative["foreign_run_id"], key))
            return next(
                (
                    r
                    for r in positives.values()
                    if r["candidate_key"] == key and r["work_group"] == negative.get("foreign_group")
                ),
                None,
            )

        missing = []
        for rid, part in queries.items():
            if query_ids is not None and rid not in query_ids:
                continue
            key = fingerprint(payload(part))
            negatives = self.get("query_negatives", key)
            if negatives is None and legacy is not None:
                negatives = [r for r in legacy if r["run_id"] == rid]
            valid = negatives is not None
            for negative in negatives or []:
                origin = foreign(negative)
                if (
                    origin is None
                    or origin["split"] != part[0]["split"]
                    or origin["work_group"] == part[0]["work_group"]
                    or origin["candidate"] != negative["candidate"]
                    or origin.get("edition_evidence", []) != negative.get("edition_evidence", [])
                ):
                    valid = False
                    break
                negative["foreign_run_id"] = origin["run_id"]
            if not valid:
                missing.append(rid)
                continue
            backfill(negatives, cohorts)
            self.save("query_negatives", key, negatives)
            result.extend(negatives)
        if missing:
            generated = build(rows, query_ids=set(missing))
            for rid in missing:
                negatives = [
                    r for r in generated if r.get("label_origin") == "different_approved_work" and r["run_id"] == rid
                ]
                self.save("query_negatives", fingerprint(payload(queries[rid])), negatives)
                result.extend(negatives)
        return result

    def current_labels(self):
        c = db.connect()
        try:
            ids = [
                r[0]
                for r in c.execute(
                    "SELECT r.id FROM ingest_runs r WHERE NOT EXISTS (SELECT 1 FROM ingest_members mine JOIN ingest_members later ON later.book_id=mine.book_id JOIN ingest_runs newer ON newer.id=later.run_id WHERE mine.run_id=r.id AND newer.created>r.created)"
                )
            ]
            decisions = {rid: [] for rid in ids}
            for offset in range(0, len(ids), 200):
                batch = ids[offset : offset + 200]
                for rid, ident in c.execute(
                    f"SELECT run_id,id FROM ingest_events WHERE run_id IN ({','.join('?' for _ in batch)}) AND status='done' AND stage IN ('metadata_approval','metadata_revocation','match_review_decision') ORDER BY id",
                    batch,
                ):
                    decisions[rid].append(ident)
        finally:
            c.close()
        expected = {
            rid: fingerprint(dict(run_id=rid, decisions=values, label_schema=1)) for rid, values in decisions.items()
        }
        c = self.connect()
        try:
            values = c.execute(
                "SELECT heads.key,heads.data,labels.data FROM entries heads JOIN entries labels ON labels.kind='labels' AND labels.key=json_extract(heads.data,'$.key') WHERE heads.kind='label_head'"
            ).fetchall()
        finally:
            c.close()
        return [
            row
            for rid, head, value in values
            if json.loads(head)["key"] == expected.get(rid)
            for row in json.loads(value)
        ]

    def seed(self, run, rows):
        """Record an already verified derived artifact under its immutable input key."""
        book = next(
            (
                e["data"].get("book", {})
                for e in reversed(run["events"])
                if e["stage"] == "identity" and e["status"] == "done"
            ),
            {},
        )
        clean = []
        for row in rows:
            row = {
                k: v
                for k, v in row.items()
                if k not in ("target", "member_ids", "label_origin", "split", "foreign_group", "foreign_run_id")
            }
            row.update(label=None, work_group=None, approval=None)
            row.setdefault("local_book", book)
            clean.append(row)
        self.save("candidates", input_key(run), clean)


def mark_dirty(run):
    revision = max((e.get("id", 0) for e in run["events"]), default=0)
    read_models.save("training_dirty", run["id"], dict(revision=revision))
    if (dirty := DIRTY.get()) is not None:
        dirty.add(run["id"])


def enqueue(jobs, runs):
    for rid in runs:
        state = read_models.get("training_dirty", rid)
        if state:
            jobs.submit("system", "training_features", retry=True, run_id=rid, revision=state["revision"])


def update(run_id, revision):
    from app.ingest import store

    run = store.get(run_id)
    cache = FeatureCache()
    # Keep the frozen consent inputs as well as current unapproved evidence.
    rows = cache.trusted(run)
    current = cache.candidates(run)
    key = label_key(run)
    previous = cache.get("label_head", run_id)
    cache.save("label_head", run_id, dict(key=key))
    if rows and previous != dict(key=key):
        from app.ingest import match_model

        pool = match_model.assign_groups(cache.current_labels())
        cache.negatives(pool, match_model.add_cross_work_negatives, query_ids={run_id})
    c = db.connect()
    try:
        with c:
            c.execute(
                "DELETE FROM read_models WHERE kind='training_dirty' AND owner=? AND json_extract(data,'$.revision')=?",
                (run_id, revision),
            )
    finally:
        c.close()
    return dict(training_candidates=len(rows), current_candidates=len(current))
