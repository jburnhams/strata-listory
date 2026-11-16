"""HTTP for listen enrichment: reading fetched ratings, reviews, and synopses, and queueing refreshes."""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Request
from sqlalchemy.orm import Session

from app.enrichment import run as R
from app.store.api import get_session
from app.store.models import Listen

router = APIRouter(prefix="/api/enrichment")


@router.post("/fetch")
def fetch_pending(request: Request, force: bool = False, limit: int = 0, s: Session = Depends(get_session)):
    """Queue enrichment jobs for Library books that have not been fetched yet (or all with `force=true`)."""
    jobs = getattr(request.app.state, "jobs", None)
    if jobs is None:
        raise HTTPException(503, "job queue is not running")
    queued = R.enqueue(jobs, s, force=force, limit=limit or None)
    return dict(queued=len(queued), listen_ids=queued)


@router.get("/{listen_id}")
def get_enrichment(listen_id: int, request: Request, s: Session = Depends(get_session)):
    """The latest enrichment summary for one listen."""
    listen = s.get(Listen, listen_id)
    if listen is None:
        raise HTTPException(404, "no such listen")
    jobs = getattr(request.app.state, "jobs", None)
    return R.summary_for_listens(s, [listen_id], jobs=jobs)[listen_id]


@router.post("/{listen_id}/fetch")
def fetch_one(listen_id: int, request: Request, s: Session = Depends(get_session)):
    """Queue an enrichment fetch for one listen."""
    listen = s.get(Listen, listen_id)
    if listen is None:
        raise HTTPException(404, "no such listen")
    jobs = getattr(request.app.state, "jobs", None)
    if jobs is None:
        raise HTTPException(503, "job queue is not running")
    jobs.submit("enrichment", "fetch", retry=True, listen_id=listen_id)
    return dict(ok=True, listen_id=listen_id, queued=True)
