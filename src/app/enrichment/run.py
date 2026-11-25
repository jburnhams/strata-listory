"""The enrichment job: which library books need ratings/reviews/synopsis, what each is known to be, fetching from all sources, and summarizing the results."""

from __future__ import annotations

import logging
import urllib.error
import urllib.request
from collections import defaultdict
from datetime import UTC, datetime
from typing import Any

log = logging.getLogger(__name__)

from sqlalchemy import func, select
from sqlalchemy.orm import Session
from strata_python.clients import models as sm

from app.covers.run import accepted_work
from app.enrichment import sources
from app.store.models import (
    EnrichmentResult,
    EnrichmentRun,
    Listen,
    ListenChoice,
    ListenEvent,
    ListenPart,
    WorkIdentifier,
)

KINDS = ("book",)

SOURCE_LABEL = {
    "openlibrary_local": "Open Library",
    "openlibrary": "Open Library",
    "audible_local": "Audible",
    "audible": "Audible",
    "audnexus": "Audnexus",
    "hardcover": "Hardcover",
    "googlebooks": "Google Books",
    "applebooks": "Apple Books",
    "sidecar": "Audible sidecar",
}

SYNOPSIS_ORDER = (
    "hardcover",
    "audible_local",
    "audible",
    "audnexus",
    "sidecar",
    "openlibrary_local",
    "openlibrary",
    "googlebooks",
    "applebooks",
)


def _confirmed_clause():
    mp = ListenPart
    accepted_part = select(mp.id).where(
        mp.listen_id == Listen.id, mp.role.in_(("match", "candidate")), mp.state == "accepted", mp.unlinked_at.is_(None)
    )
    return accepted_part.where(
        select(ListenEvent.id)
        .where(
            ListenEvent.listen_part_id == mp.id,
            ListenEvent.actor_kind == "user",
            ListenEvent.action == "accept",
        )
        .exists()
    ).exists()


def _chosen_cover_clause():
    return (
        select(ListenChoice.id)
        .where(
            ListenChoice.listen_id == Listen.id,
            ListenChoice.field == "cover",
            ListenChoice.unchosen_at.is_(None),
            ListenChoice.value["image_id"].is_not(None),
        )
        .exists()
    )


def is_library_book(s: Session, listen_id: int) -> bool:
    """True when the listen is a live book on the Library screen (confirmed match + chosen cover)."""
    return bool(
        s.scalar(
            select(Listen.id).where(
                Listen.id == listen_id,
                Listen.kind.in_(KINDS),
                Listen.merged_into_id.is_(None),
                Listen.retired_at.is_(None),
                _confirmed_clause(),
                _chosen_cover_clause(),
            )
        )
    )


def changed_at(s: Session, listen_id: int) -> datetime | None:
    """The newest change to the listen, not counting cover or output choices."""
    choice = (ListenEvent.action.in_(("choose", "unchoose"))) & (
        (func.coalesce(ListenEvent.after["field"].astext, "").in_(("cover", "output")))
        | (func.coalesce(ListenEvent.before["field"].astext, "").in_(("cover", "output")))
    )
    return s.scalar(select(func.max(ListenEvent.at)).where(ListenEvent.listen_id == listen_id, ~choice))


def unfetched(s: Session) -> list[int]:
    """Ids of Library book listens with no enrichment run at all yet."""
    searched = select(EnrichmentRun.id).where(EnrichmentRun.listen_id == Listen.id).exists()
    return list(
        s.scalars(
            select(Listen.id)
            .where(
                Listen.kind.in_(KINDS),
                Listen.merged_into_id.is_(None),
                Listen.retired_at.is_(None),
                _confirmed_clause(),
                _chosen_cover_clause(),
                ~searched,
            )
            .order_by(Listen.id)
        )
    )


def pending(
    s: Session, *, listen_ids: list[int] | None = None, force: bool = False, limit: int | None = None
) -> list[int]:
    """Ids of Library book listens whose newest enrichment run is older than their newest change (or has none). `force` includes already fetched listens."""
    q = (
        select(Listen.id)
        .where(
            Listen.kind.in_(KINDS),
            Listen.merged_into_id.is_(None),
            Listen.retired_at.is_(None),
            _confirmed_clause(),
            _chosen_cover_clause(),
        )
        .order_by(Listen.id)
    )
    if listen_ids:
        q = q.where(Listen.id.in_(listen_ids))
    out: list[int] = []
    for lid in s.scalars(q):
        last = s.scalars(
            select(EnrichmentRun)
            .where(EnrichmentRun.listen_id == lid, EnrichmentRun.finished_at.is_not(None))
            .order_by(EnrichmentRun.id.desc())
        ).first()
        now_changed = changed_at(s, lid)
        if (
            last
            and not force
            and (now_changed is None or last.listen_changed_at is None or now_changed <= last.listen_changed_at)
        ):
            continue
        out.append(lid)
        if limit and len(out) >= limit:
            break
    return out


def sweep(jobs, s: Session) -> int:
    """Queue an enrichment fetch for every Library book listen without one yet."""
    ids = unfetched(s)
    for lid in ids:
        jobs.submit("enrichment", "fetch", retry=True, listen_id=lid)
    return len(ids)


def sweep_forever(jobs, stop, every: float) -> None:
    """Sweep periodically until `stop` is set so any newly confirmed/covered Library book is enriched automatically."""
    from app.store.api import _engine

    while not stop.is_set():
        try:
            with Session(_engine()) as s:
                sweep(jobs, s)
        except Exception:
            pass
        stop.wait(every)


def enqueue(
    jobs, s: Session, *, listen_ids: list[int] | None = None, force: bool = False, limit: int | None = None
) -> list[int]:
    """Queue one `enrichment`/`fetch` job per pending Library book listen."""
    todo = pending(s, listen_ids=listen_ids, force=force or bool(listen_ids), limit=limit)
    for lid in todo:
        jobs.submit("enrichment", "fetch", retry=True, listen_id=lid)
    return todo


def context(s: Session, listen: Listen) -> sources.Context:
    """What the listen is known to be: identifiers of its accepted work and the folder it came from."""
    ctx = sources.Context(title=listen.label)
    found = accepted_work(s, listen.id)
    if found:
        work, part = found
        ctx.title = work.title or listen.label
        ids: dict[str, list[str]] = {}
        for i in s.scalars(select(WorkIdentifier).where(WorkIdentifier.work_id == work.id).order_by(WorkIdentifier.id)):
            ids.setdefault(i.scheme, []).append(i.value)
        ctx.asins = ids.get("asin", [])
        ctx.isbns = ids.get("isbn", [])
        ctx.ol_works = ids.get("ol_work", []) + [k for k in ids.get("work", []) if "/works/" in k]
        ctx.author = ", ".join((part.detail or {}).get("authors") or [])
    inp = s.scalars(
        select(ListenPart)
        .where(ListenPart.listen_id == listen.id, ListenPart.role == "input", ListenPart.unlinked_at.is_(None))
        .order_by(ListenPart.ordinal)
    ).first()
    if inp:
        ctx.folder = (inp.detail or {}).get("path") or ""
    return ctx


def fetch(s: Session, listen: Listen, *, run_sources=None) -> EnrichmentRun:
    """Query all sources for one listen and record the run and every source's result."""
    ctx = context(s, listen)
    run = EnrichmentRun(listen_id=listen.id, listen_changed_at=changed_at(s, listen.id), sources=[])
    s.add(run)
    s.flush()
    statuses: list[dict[str, Any]] = []
    for name, fn in sources.SOURCES if run_sources is None else run_sources:
        try:
            items, status = fn(ctx)
        except Exception as e:  # a source's bug must not stop the others
            items, status = [], dict(source=name, status="error", detail=f"{type(e).__name__}: {e}", count=0)
        for it in items:
            s.add(
                EnrichmentResult(
                    run_id=run.id,
                    source=it.source,
                    ref=it.ref,
                    url=it.url,
                    rating=it.rating,
                    ratings_count=it.ratings_count,
                    reviews_count=it.reviews_count,
                    synopsis=it.synopsis,
                    reviews=it.reviews,
                    extra=it.extra,
                )
            )
        statuses.append(status)
    run.sources = statuses
    run.finished_at = datetime.now(UTC).replace(tzinfo=None)
    s.flush()
    return run


def build_summary_inputs(s: Session, listen: Listen, summary_data: dict[str, Any]) -> tuple[str, list[dict[str, str]]]:
    """Build `(title, inputs)` for `strata-models` `summarise` from a listen's enrichment data."""
    found = accepted_work(s, listen.id)
    title = listen.label
    if found:
        work, part = found
        authors = ", ".join((part.detail or {}).get("authors") or [])
        title = f"{work.title or listen.label} by {authors}" if authors else (work.title or listen.label)

    inputs: list[dict[str, str]] = []
    for syn in summary_data.get("synopses") or []:
        text = (syn.get("text") or "").strip()
        if text:
            inputs.append(dict(source=f"{syn['label']} synopsis", text=text[:2500]))
    for rev in (summary_data.get("reviews") or [])[:12]:
        text = (rev.get("text") or "").strip()
        if not text:
            continue
        rating = f" ({rev['rating']}/5)" if rev.get("rating") is not None else ""
        spoiler = " [spoilers]" if rev.get("spoilers") else ""
        inputs.append(dict(source=f"{rev['label']} review{rating}{spoiler}", text=text[:1500]))
    tags = summary_data.get("tags") or {}
    tag_parts = [f"{k}: {', '.join(v)}" for k, v in (("genres", tags.get("genres")), ("moods", tags.get("moods"))) if v]
    if tag_parts:
        inputs.append(dict(source="tags", text="; ".join(tag_parts)))
    return title, inputs


def call_strata_models_summarise(
    title: str,
    inputs: list[dict[str, str]],
    *,
    timeout: float = 60.0,
) -> tuple[str, str] | None:
    """Call `strata-models` role `summarise` with `summarise-v3-xml` and return `(text, model)` or `None`.

    Through strata-python's client, not hand-made HTTP: strata-models takes a normal priority job only on its bulk
    endpoint now (a single `POST /api/jobs` is high priority only), and the client routes it there. Its errors
    (`Unavailable`, `JobFailed`: RuntimeErrors; TimeoutError) are the ones `summarise_run` already swallows."""
    out = sm.run(
        "summarise",
        dict(
            preset="summarise-v3-xml",
            effort="low",
            max_tokens=1500,
            context=dict(
                title=title,
                purpose="helping an audiobook listener decide whether to listen",
                audience="an audiobook listener browsing their library",
                target_size="one short paragraph (3-4 sentences)",
                inputs=inputs,
            ),
        ),
        "strata-listory",
        "normal",
        timeout=timeout,
    )
    text = str(out.get("text") or "").strip()
    if not text:
        return None
    return text, str(out.get("model") or "")


def summarise_run(
    s: Session,
    run: EnrichmentRun,
    listen: Listen,
    *,
    call_fn=None,
) -> bool:
    """Generate and store `run.summary` for an EnrichmentRun if it has synopses/reviews; swallows service errors."""
    results = list(
        s.scalars(select(EnrichmentResult).where(EnrichmentResult.run_id == run.id).order_by(EnrichmentResult.id))
    )
    if not results:
        return False
    summary_data = summarize(run, results)
    if not summary_data["synopses"] and not summary_data["reviews"]:
        return False
    title, inputs = build_summary_inputs(s, listen, summary_data)
    if not inputs:
        return False
    try:
        res = (call_fn or call_strata_models_summarise)(title, inputs)
    except (OSError, urllib.error.URLError, ValueError, RuntimeError, TimeoutError) as e:
        log.warning("enrichment summarise skipped for listen %s: %s", listen.id, e)
        return False
    if not res:
        return False
    run.summary, run.summary_model = res
    s.flush()
    return True


def fetch_job(listen_id: int) -> dict[str, Any]:
    """What a queued `enrichment`/`fetch` job does: fetch one listen on its own session, summarise it, and commit."""
    from app.store.api import _engine

    with Session(_engine()) as s:
        listen = s.get(Listen, listen_id)
        if listen is None:
            raise ValueError(f"no listen {listen_id}")
        run = fetch(s, listen)
        summarised = summarise_run(s, run, listen)
        s.commit()
        return dict(
            listen_id=listen_id,
            label=listen.label,
            run_id=run.id,
            summarised=summarised,
            sources={x["source"]: x["status"] for x in run.sources},
        )


def summarize(
    run: EnrichmentRun | None, results: list[EnrichmentResult], *, job_state: str | None = None
) -> dict[str, Any]:
    """Turn one EnrichmentRun + its results into the API summary for Library cards and ListenPage."""
    if run is None:
        return dict(
            status=job_state or "none",
            fetched_at=None,
            summary=None,
            summary_model=None,
            synopsis=None,
            synopses=[],
            ratings=[],
            reviews=[],
            tags=dict(genres=[], moods=[], content_warnings=[]),
            sources=[],
        )
    by_source: dict[str, list[EnrichmentResult]] = defaultdict(list)
    for r in results:
        by_source[r.source].append(r)

    synopses: list[dict[str, Any]] = []
    seen_syn: set[str] = set()
    seen_syn_label: set[str] = set()
    for src in SYNOPSIS_ORDER:
        label = SOURCE_LABEL.get(src, src)
        if label in seen_syn_label:
            continue
        for r in by_source.get(src, []):
            text = (r.synopsis or "").strip()
            if not text:
                continue
            key = " ".join(text.lower().split())[:200]
            if key in seen_syn:
                continue
            seen_syn.add(key)
            seen_syn_label.add(label)
            synopses.append(dict(source=src, label=label, text=text, url=r.url))
            break

    ratings: list[dict[str, Any]] = []
    seen_rating_label: set[str] = set()
    for src in ("hardcover", "audible_local", "audible", "openlibrary_local", "openlibrary", "googlebooks", "sidecar"):
        for r in by_source.get(src, []):
            if r.rating is None:
                continue
            label = SOURCE_LABEL.get(src, src)
            if label in seen_rating_label:
                continue
            seen_rating_label.add(label)
            ratings.append(
                dict(
                    source=src,
                    label=label,
                    rating=round(float(r.rating), 2),
                    count=r.ratings_count,
                    reviews_count=r.reviews_count,
                    url=r.url,
                )
            )

    reviews: list[dict[str, Any]] = []
    seen_rev: set[str] = set()
    for r in results:
        label = SOURCE_LABEL.get(r.source, r.source)
        for rev in r.reviews or []:
            text = (rev.get("text") or "").strip()
            if not text:
                continue
            key = " ".join(text.lower().split())[:200]
            if key in seen_rev:
                continue
            seen_rev.add(key)
            reviews.append(
                dict(
                    source=r.source,
                    label=label,
                    user=rev.get("user") or "Reader",
                    rating=rev.get("rating"),
                    title=rev.get("title") or "",
                    text=rev["text"],
                    spoilers=bool(rev.get("spoilers")),
                    likes=int(rev.get("likes") or 0),
                    date=rev.get("date") or "",
                    url=r.url,
                )
            )
    reviews.sort(key=lambda x: (-x["likes"], x["source"]))

    genres: list[str] = []
    moods: list[str] = []
    warnings: list[str] = []
    for r in results:
        ex = r.extra or {}
        for g in ex.get("genres") or []:
            if g not in genres:
                genres.append(g)
        for m in ex.get("moods") or []:
            if m not in moods:
                moods.append(m)
        for w in ex.get("content_warnings") or []:
            if w not in warnings:
                warnings.append(w)

    return dict(
        status=job_state or ("done" if run.finished_at else "running"),
        fetched_at=run.finished_at.isoformat() if run.finished_at else None,
        summary=run.summary,
        summary_model=run.summary_model,
        synopsis=synopses[0] if synopses else None,
        synopses=synopses,
        ratings=ratings,
        reviews=reviews,
        tags=dict(genres=genres[:10], moods=moods[:8], content_warnings=warnings[:8]),
        sources=run.sources or [],
    )


def summary_for_listens(s: Session, listen_ids: list[int], *, jobs=None) -> dict[int, dict[str, Any]]:
    """Batch-load enrichment summaries for a page of listens."""
    if not listen_ids:
        return {}
    newest = (
        select(func.max(EnrichmentRun.id))
        .where(EnrichmentRun.listen_id.in_(listen_ids), EnrichmentRun.finished_at.is_not(None))
        .group_by(EnrichmentRun.listen_id)
    )
    runs = {r.listen_id: r for r in s.scalars(select(EnrichmentRun).where(EnrichmentRun.id.in_(newest)))}
    run_ids = [r.id for r in runs.values()]
    results_by_run: dict[int, list[EnrichmentResult]] = defaultdict(list)
    if run_ids:
        for res in s.scalars(
            select(EnrichmentResult).where(EnrichmentResult.run_id.in_(run_ids)).order_by(EnrichmentResult.id)
        ):
            results_by_run[res.run_id].append(res)
    job_states: dict[int, str] = {}
    if jobs is not None:
        for t in jobs.tasks("enrichment"):
            lid = (t.get("options") or {}).get("listen_id")
            if lid in listen_ids:
                job_states[lid] = t["state"]
    return {
        lid: summarize(
            runs.get(lid),
            results_by_run.get(runs[lid].id, []) if lid in runs else [],
            job_state=job_states.get(lid),
        )
        for lid in listen_ids
    }
