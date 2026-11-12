"""The cover job: which listens need a search, what each is known to be, the search itself, and storing what it found."""

import hashlib
import os
from datetime import UTC, datetime

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app import settings
from app.covers import imaging, net, sources
from app.store import listens as L
from app.store.models import (
    CoverCandidate,
    CoverImage,
    CoverRun,
    Listen,
    ListenChoice,
    ListenEvent,
    ListenPart,
    PartFilegroupMember,
    PartFileLocation,
    Work,
    WorkIdentifier,
)

FIELD = "cover"


def folder() -> str:
    p = os.path.join(settings.data_dir(), "covers")
    os.makedirs(p, exist_ok=True)
    return p


def image_path(img: CoverImage) -> str:
    return os.path.join(folder(), img.sha256[:2], img.sha256 + img.ext)


def changed_at(s: Session, listen_id: int) -> datetime | None:
    """The newest change to the listen, not counting a cover choice (choosing a cover must not make the listen look changed)."""
    cover_choice = (ListenEvent.action.in_(("choose", "unchoose"))) & (
        (func.coalesce(ListenEvent.after["field"].astext, "") == FIELD)  # coalesce: a null is not "not equal" in SQL
        | (func.coalesce(ListenEvent.before["field"].astext, "") == FIELD)
    )
    return s.scalar(select(func.max(ListenEvent.at)).where(ListenEvent.listen_id == listen_id, ~cover_choice))


def accepted_work(s: Session, listen_id: int) -> tuple[Work, ListenPart] | None:
    p = s.scalars(
        select(ListenPart)
        .where(
            ListenPart.listen_id == listen_id,
            ListenPart.role.in_(("match", "candidate")),
            ListenPart.state == "accepted",
            ListenPart.unlinked_at.is_(None),
            ListenPart.work_id.is_not(None),
        )
        .order_by(ListenPart.ordinal)
    ).first()
    return (s.get(Work, p.work_id), p) if p and p.work_id else None


def pending(
    s: Session, *, listen_ids: list[int] | None = None, force: bool = False, limit: int | None = None
) -> list[int]:
    """Ids of book listens with an accepted or confirmed work and no chosen cover whose newest cover search is older than their newest change (or has none). `force` includes those already searched."""
    q = (
        select(Listen.id)
        .where(Listen.kind == "book", Listen.merged_into_id.is_(None), Listen.retired_at.is_(None))
        .order_by(Listen.id)
    )
    if listen_ids:
        q = q.where(Listen.id.in_(listen_ids))
    out = []
    for lid in s.scalars(q):
        if not accepted_work(s, lid):
            continue
        if s.scalar(
            select(ListenChoice.id).where(
                ListenChoice.listen_id == lid, ListenChoice.field == FIELD, ListenChoice.unchosen_at.is_(None)
            )
        ):
            continue
        last = s.scalars(
            select(CoverRun)
            .where(CoverRun.listen_id == lid, CoverRun.finished_at.is_not(None))
            .order_by(CoverRun.id.desc())
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


def context(s: Session, listen: Listen) -> sources.Context:
    """What the listen is known to be: the identifiers of its accepted work and the folder and files it was made from."""
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
        if inp.filegroup_id:
            fid = s.scalar(
                select(PartFilegroupMember.file_id)
                .where(PartFilegroupMember.group_id == inp.filegroup_id, PartFilegroupMember.file_id.is_not(None))
                .order_by(PartFilegroupMember.ordinal)
            )
            if fid:
                ctx.files = list(
                    s.scalars(
                        select(PartFileLocation.path).where(
                            PartFileLocation.file_id == fid, PartFileLocation.gone_at.is_(None)
                        )
                    )
                )
    return ctx


def store_image(s: Session, data: bytes) -> CoverImage | None:
    """Save a picture under its content hash (once) and return its row; None when it is not a usable picture (not JPEG/PNG/WebP, a placeholder, or too small)."""
    ext = imaging.kind(data)
    dims = imaging.size(data) if ext else None
    if not ext or len(data) < imaging.MIN_BYTES or not dims or min(dims) < imaging.MIN_SIDE:
        return None
    sha = hashlib.sha256(data).hexdigest()
    img = s.scalars(select(CoverImage).where(CoverImage.sha256 == sha)).first()
    if img:
        return img
    img = CoverImage(sha256=sha, ext=ext, width=dims[0], height=dims[1], size=len(data), dhash=imaging.dhash(data))
    path = image_path(img)
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "wb") as f:
        f.write(data)
    s.add(img)
    s.flush()
    return img


def search(s: Session, listen: Listen, *, run_sources=None) -> CoverRun:
    """Search every source for the listen, download what they offer and record the outcome (every source's status, and a candidate for each picture). `run_sources` replaces the source list (tests)."""
    ctx = context(s, listen)
    run = CoverRun(listen_id=listen.id, listen_changed_at=changed_at(s, listen.id), sources=[])
    s.add(run)
    s.flush()
    statuses, seen = [], set()
    for name, fn in sources.SOURCES if run_sources is None else run_sources:
        try:
            items, status = fn(ctx)
        except Exception as e:  # a source's bug must not stop the others
            items, status = [], dict(source=name, status="error", detail=f"{type(e).__name__}: {e}", count=0)
        kept, failed = 0, []
        for it in items:
            key = it.url or f"{it.source}:{it.ref}"
            if key in seen:
                continue
            seen.add(key)
            data = it.data
            if data is None and it.url:
                known = s.scalars(
                    select(CoverCandidate).where(CoverCandidate.url == it.url).order_by(CoverCandidate.id.desc())
                ).first()
                img = s.get(CoverImage, known.image_id) if known else None
                if img is None:
                    r = net.get(it.url)
                    if r.status == 404:
                        failed.append(f"{it.ref}: no picture there")
                        continue
                    if not r.ok:
                        failed.append(f"{it.ref}: {r.error or r.status}")
                        continue
                    data = r.body
            else:
                img = None
            if img is None:
                img = store_image(s, data or b"")
                if img is None:
                    failed.append(f"{it.ref}: not a usable picture")
                    continue
            s.add(
                CoverCandidate(run_id=run.id, image_id=img.id, source=it.source, ref=it.ref, label=it.label, url=it.url)
            )
            kept += 1
        if items and not kept:
            status = dict(status, status="none", detail="; ".join(failed[:4]) or status.get("detail", ""), count=0)
        elif failed:
            status = dict(status, count=kept, detail="; ".join([status.get("detail", "")] + failed[:3]).strip("; "))
        else:
            status = dict(status, count=kept) if status["status"] == "found" else status
        statuses.append(status)
    run.sources = statuses
    run.finished_at = datetime.now(UTC).replace(tzinfo=None)
    s.flush()
    return run


def distinct(s: Session, run_id: int) -> list[dict]:
    """The run's pictures with near-duplicates merged (same hash, or a few bits apart and the same shape): one entry per distinct cover, largest first, each with every source that offered it."""
    rows = s.execute(
        select(CoverCandidate, CoverImage)
        .join(CoverImage, CoverImage.id == CoverCandidate.image_id)
        .where(CoverCandidate.run_id == run_id)
        .order_by(CoverCandidate.id)
    ).all()
    groups: list[dict] = []
    for c, img in rows:
        for g in groups:
            if g["image"].id == img.id or (
                imaging.distance(g["image"].dhash, img.dhash) <= 4
                and abs(g["image"].width / g["image"].height - img.width / img.height) < 0.03
            ):
                g["sources"].append(dict(source=c.source, ref=c.ref, label=c.label, url=c.url, image_id=img.id))
                if img.width * img.height > g["image"].width * g["image"].height:
                    g["image"] = img  # the largest of the near-duplicates stands for the group
                break
        else:
            groups.append(
                dict(image=img, sources=[dict(source=c.source, ref=c.ref, label=c.label, url=c.url, image_id=img.id)])
            )
    groups.sort(key=lambda g: -(g["image"].width * g["image"].height))
    return groups


def choose(s: Session, listen: Listen, image: CoverImage | None, *, actor_ref: str = "web") -> None:
    """Save the preferred cover (the listen's `cover` choice), or clear it."""
    if image is None:
        old = s.scalars(
            select(ListenChoice).where(
                ListenChoice.listen_id == listen.id, ListenChoice.field == FIELD, ListenChoice.unchosen_at.is_(None)
            )
        ).first()
        if old:
            old.unchosen_at = datetime.now(UTC).replace(tzinfo=None)
            L._event(s, listen, "unchoose", "user", actor_ref=actor_ref, before={"field": FIELD, "value": old.value})
        return
    L.choose(
        s,
        listen,
        FIELD,
        dict(image_id=image.id, sha256=image.sha256, width=image.width, height=image.height),
        actor_kind="user",
        actor_ref=actor_ref,
        reason="cover chosen",
    )
