"""HTTP for the Covers page: the listens a cover search has run on with what it found (or why nothing), the pictures, the preferred cover, and a run of the job."""

from __future__ import annotations

import os
import shutil
import subprocess
import threading
import time

from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import FileResponse
from pydantic import BaseModel
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.covers import run as R
from app.store.api import _engine, get_session
from app.store.models import CoverCandidate, CoverImage, CoverRun, Listen, ListenChoice

router = APIRouter(prefix="/api/covers")
STATE: dict = dict(running=False, done=0, total=0, current="", finished=None, error="")
_lock = threading.Lock()


def _latest_runs(s: Session) -> dict[int, CoverRun]:
    newest = select(func.max(CoverRun.id)).where(CoverRun.finished_at.is_not(None)).group_by(CoverRun.listen_id)
    return {r.listen_id: r for r in s.scalars(select(CoverRun).where(CoverRun.id.in_(newest)))}


def _chosen(s: Session, ids: list[int]) -> dict[int, dict]:
    return {
        c.listen_id: c.value
        for c in s.scalars(
            select(ListenChoice).where(
                ListenChoice.listen_id.in_(ids), ListenChoice.field == R.FIELD, ListenChoice.unchosen_at.is_(None)
            )
        )
    }


def _card(s: Session, listen: Listen, run: CoverRun, chosen: dict | None) -> dict:
    covers = [  # best fit first: the page shows the first one already selected
        dict(
            image_id=x.group["image"].id,
            width=x.group["image"].width,
            height=x.group["image"].height,
            bytes=x.group["image"].size,
            sources=x.group["sources"],
            members=sorted({e["image_id"] for e in x.group["sources"]}),
            score=round(x.score, 2),
            share=round(x.share, 3),
            signals=[g.as_dict() for g in x.signals],
        )
        for x in R.ranked(s, listen, run.id)
    ]
    chosen_id = (chosen or {}).get("image_id")
    state = "chosen" if chosen_id else ("choose" if covers else "none")
    return dict(
        listen_id=listen.id,
        label=listen.label,
        kind=listen.kind,
        run_id=run.id,
        searched=run.finished_at.isoformat() if run.finished_at else None,
        state=state,
        chosen=chosen_id,
        covers=covers,
        sources=run.sources or [],
    )


@router.get("")
def covers(state: str = "", q: str = "", limit: int = 24, offset: int = 0, s: Session = Depends(get_session)):
    """Listens a cover search has run on, newest search first. `state`: choose (covers found, none chosen), chosen, none (nothing found); empty = all."""
    limit = max(1, min(limit, 100))
    runs = _latest_runs(s)
    listens = {l.id: l for l in s.scalars(select(Listen).where(Listen.id.in_(list(runs) or [0])))}
    chosen = _chosen(s, list(runs))
    has_cover = {
        lid: bool(s.scalar(select(func.count()).select_from(CoverCandidate).where(CoverCandidate.run_id == r.id)))
        for lid, r in runs.items()
    }

    def st(lid):
        return "chosen" if (chosen.get(lid) or {}).get("image_id") else ("choose" if has_cover[lid] else "none")

    counts = {k: sum(1 for lid in runs if st(lid) == k) for k in ("choose", "chosen", "none")}
    words = q.lower().split()
    ids = sorted(
        (
            lid
            for lid in runs
            if lid in listens
            and (not state or st(lid) == state)
            and all(w in listens[lid].label.lower() for w in words)
        ),
        key=lambda lid: (runs[lid].finished_at, lid),
        reverse=True,
    )
    page = ids[offset : offset + limit]
    return dict(
        total=len(ids),
        counts=counts,
        waiting=len(R.pending(s)),
        rows=[_card(s, listens[lid], runs[lid], chosen.get(lid)) for lid in page],
        job={k: v for k, v in STATE.items()},
    )


class Choice(BaseModel):
    image_id: int | None = None
    via: str = "pick"  # "accept": the suggested best fit as it was; "pick": any other choice (the ranking learns from both, accepting more weakly)


LEARN = threading.Lock()
_again = threading.Event()  # a choice or a cleared choice arrived while a fit was running: fit once more when it ends


def relearn() -> None:
    """Refit the ranking's weights on every cover chosen so far, in the background, one fit at a time: a request that arrives while one runs makes it fit once more when it ends, so the saved weights always catch up with the last choice (or the last cleared one). The ranking is computed when a page is read, so new weights apply at once."""
    _again.set()
    if not LEARN.acquire(blocking=False):
        return

    def work():
        try:
            while _again.is_set():
                _again.clear()
                try:
                    with Session(_engine()) as s:
                        R.learn(s)
                except Exception:  # the weights stay as they were; a person's choice is already saved
                    pass
        finally:
            LEARN.release()

    threading.Thread(target=work, daemon=True).start()


@router.post("/{listen_id}/choose")
def choose(listen_id: int, b: Choice, s: Session = Depends(get_session)):
    listen = s.get(Listen, listen_id)
    if listen is None:
        raise HTTPException(404, "no such listen")
    img = None
    if b.image_id is not None:
        img = s.get(CoverImage, b.image_id)
        if img is None:
            raise HTTPException(404, "no such picture")
    R.choose(s, listen, img, via="accept" if b.via == "accept" else "pick")
    s.commit()
    relearn()  # a cleared choice too: its influence must leave the weights
    return dict(ok=True, chosen=b.image_id)


@router.get("/image/{image_id}")
def image(image_id: int, w: int = 0, s: Session = Depends(get_session)):
    """The stored picture; `w` asks for a JPEG thumbnail that many pixels wide (made once and kept)."""
    img = s.get(CoverImage, image_id)
    if img is None or not os.path.exists(R.image_path(img)):
        raise HTTPException(404, "no such picture")
    path = R.image_path(img)
    if w and (ffmpeg := shutil.which("ffmpeg")):
        w = max(32, min(w, 1200))
        thumb = os.path.join(R.folder(), "thumbs", f"{img.sha256}-{w}.jpg")
        if not os.path.exists(thumb):
            os.makedirs(os.path.dirname(thumb), exist_ok=True)
            r = subprocess.run(
                [ffmpeg, "-v", "error", "-y", "-i", path, "-vf", f"scale={w}:-2", "-frames:v", "1", "-q:v", "4", thumb],
                capture_output=True,
                timeout=60,
            )
            if r.returncode:
                return FileResponse(path, headers={"Cache-Control": "public, max-age=31536000, immutable"})
        return FileResponse(
            thumb, media_type="image/jpeg", headers={"Cache-Control": "public, max-age=31536000, immutable"}
        )
    return FileResponse(path, headers={"Cache-Control": "public, max-age=31536000, immutable"})


class RunIn(BaseModel):
    listen_ids: list[int] | None = None
    force: bool = False
    limit: int | None = None


def run_job(listen_ids: list[int] | None, force: bool, limit: int | None) -> None:
    """Search every pending listen (or the given ones; `force` searches again), one commit each so the page shows results as they arrive."""
    try:
        with Session(_engine()) as s:
            todo = R.pending(s, listen_ids=listen_ids, force=force or bool(listen_ids), limit=limit)
            STATE.update(total=len(todo), done=0)
            for lid in todo:
                listen = s.get(Listen, lid)
                STATE.update(current=listen.label if listen else "")
                if listen:
                    R.search(s, listen)
                    s.commit()
                STATE["done"] += 1
    except Exception as e:  # shown on the page; the next run starts clean
        STATE["error"] = f"{type(e).__name__}: {e}"
    finally:
        STATE.update(running=False, current="", finished=time.time())
        _lock.release()


@router.post("/run", status_code=202)
def start(b: RunIn, s: Session = Depends(get_session)):
    if not _lock.acquire(blocking=False):
        raise HTTPException(409, "a cover search is already running")
    STATE.update(running=True, done=0, total=0, current="", error="", finished=None)
    threading.Thread(target=run_job, args=(b.listen_ids, b.force, b.limit), daemon=True).start()
    return dict(started=True)
