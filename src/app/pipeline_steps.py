"""The steps after a folder scan, as incremental, restartable jobs for the Files and Matches pages (the CLI steps `filegroups-structure`, `listens-build`, `listens-identify-prepare` and `listens-identify` do the same work in one go and, for `listens-build`, only on an empty catalogue).

`build_listens`: read the folder structure, make listens only for the books and programmes that have none (existing listens, decisions and merges are never touched), read their metadata and classify them. Every batch is committed, so a stop loses at most a batch and a rerun carries on.
`identify`: find candidate works for the listens that have no candidate yet and decide them with the saved model, a batch at a time (vectors are cached per batch, decisions committed per batch)."""

from __future__ import annotations

import time
from collections.abc import Callable

from omarchy_db import connect
from sqlalchemy import select
from sqlalchemy.orm import Session

Report = Callable[..., None]  # report(current: str, done: int | None, total: int | None)
BATCH = 100


def _throttled(report: Report, every: float = 1.0) -> Callable[[int, int, str], None]:
    last = [0.0]

    def tick(done: int, total: int, current: str) -> None:
        if time.monotonic() - last[0] >= every or done == total:
            last[0] = time.monotonic()
            report(current, done, total)

    return tick


def build_listens(report: Report) -> dict:
    from app.store import build, kinds_db, metadata, structure
    from app.store.models import Listen, ListenKind, PartFilegroup, Run

    with Session(connect("listory")) as s:
        src = s.scalars(
            select(PartFilegroup)
            .join(Run, Run.id == PartFilegroup.run_id)
            .where(PartFilegroup.kind == "root", Run.kind == "group_folders", Run.outcome == "done")
            .order_by(Run.started_at.desc(), PartFilegroup.id.desc())
        ).first()
        if src is None:
            raise ValueError("no folder grouping yet: scan a folder on the Files page first")
        report("Reading the folder structure", 0, 0)
        top, totals = structure.infer(s, src)
        s.commit()
        report("Making listens for new books and programmes", 0, 0)
        counts = build.add_listens(s, top, progress=_throttled(report), commit_every=BATCH)
        report("Reading titles, authors and tags of the new listens", 0, 0)
        meta = metadata.add_metadata(s, progress=_throttled(report), commit_every=BATCH)
        s.commit()
        unclassified = list(
            s.scalars(
                select(Listen.id).where(
                    Listen.merged_into_id.is_(None),
                    ~select(ListenKind.listen_id).where(ListenKind.listen_id == Listen.id).exists(),
                )
            )
        )
        for i in range(0, len(unclassified), BATCH):
            report("Deciding book, radio or play for the new listens", i, len(unclassified))
            kinds_db.classify_listens(s, unclassified[i : i + BATCH])
            s.commit()
        return dict(
            made=counts["book"] + counts["radio_series"],
            books=counts["book"],
            radio_series=counts["radio_series"],
            already_had_listens=counts["existing"],
            loose_files_left=counts["loose_files_skipped"],
            metadata_read=meta["listens"],
            classified=len(unclassified),
        )


def unidentified(s: Session) -> list[int]:
    """Live listens with no candidate work yet (none proposed, accepted or rejected) that a person has not marked 'none of these'."""
    from app.linkage import apply
    from app.store.models import Listen, ListenPart

    has = (
        select(ListenPart.id)
        .where(
            ListenPart.listen_id == Listen.id,
            ListenPart.role.in_(("match", "candidate")),
            ListenPart.unlinked_at.is_(None),
        )
        .exists()
    )
    return list(
        s.scalars(
            select(Listen.id)
            .where(
                Listen.merged_into_id.is_(None),
                Listen.retired_at.is_(None),
                Listen.status != apply.NOT_FOUND,
                ~has,
            )
            .order_by(Listen.id)
        )
    )


def identify(report: Report) -> dict:
    from app.identify import audibleindex
    from app.identify import candidates as olc
    from app.linkage import apply, dataset, query, service

    params = service.load_params()
    if params is None:
        raise ValueError("no model yet: retrain it on the Matches page first")
    with Session(connect("listory")) as s:
        todo = unidentified(s)
        try:
            oc = olc.connect()
        except FileNotFoundError:
            oc = None
        ac = audibleindex.connect()
        stamp = dataset.index_stamp(oc, ac)
        cache = dataset.load_cache()
        counts = dict(accept=0, review=0, none=0, kept=0, unchanged=0)
        for i in range(0, len(todo), BATCH):
            ids = todo[i : i + BATCH]
            report("Finding candidate works and deciding", i, len(todo))
            queries = query.load(s, ids=ids)
            data = dataset.build(oc, ac, queries, cache, indexes=stamp)
            cache.update(data)
            dataset.save_cache(cache)  # restartable: finished batches are never worked out again
            for k, v in apply.apply(s, data, params, ids=set(ids)).items():
                counts[k] = counts.get(k, 0) + v
            s.commit()
        report("Done", len(todo), len(todo))
        return dict(listens=len(todo), **counts)
