"""The match queue (`/api/matches`): every book listen whose work the model has proposed and no person has decided yet, easiest first (highest probability), with everything known about the listen and each candidate work so one look is enough; and the decisions (approve / not this / none of these), which become labels for the next fit. Every `AUTO_RETRAIN` decisions the model is refitted and re-applied in the background, so confirming easy matches lets it accept more on its own."""
from __future__ import annotations

import os

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.linkage import apply, service
from app.store.api import _engine, get_session
from app.store.models import Listen, ListenChoice, ListenEvent, ListenPart, PartRecord

router = APIRouter(prefix='/api/matches')
AUTO_RETRAIN = 25
TAG_FIELDS = ('album', 'title', 'artist', 'album_artist', 'composer', 'narratedby', 'date', 'genre', 'publisher')


def _listen_info(s: Session, ids: list[int]) -> dict[int, dict]:
    """What each listen says about itself: chosen fields, the tag summary and folder name records, and its copies (runtime, files, folder)."""
    out: dict[int, dict] = {i: dict(chosen={}, tags={}, folder={}, copies=[]) for i in ids}
    for lid, field, value in s.execute(select(ListenChoice.listen_id, ListenChoice.field, ListenChoice.value).where(ListenChoice.listen_id.in_(ids), ListenChoice.unchosen_at.is_(None))): out[lid]['chosen'][field] = value
    for lid, source, payload in s.execute(select(ListenPart.listen_id, PartRecord.source, PartRecord.payload).join(PartRecord, PartRecord.id == ListenPart.record_id).where(ListenPart.listen_id.in_(ids), ListenPart.role == 'record', ListenPart.unlinked_at.is_(None))):
        if source == 'embedded_tags': out[lid]['tags'] = dict(files=payload.get('files'), tagged=payload.get('tagged'), **{f: payload[f]['value'] for f in TAG_FIELDS if isinstance(payload.get(f), dict)})
        elif source == 'folder_name': out[lid]['folder'] = payload
    for p in s.scalars(select(ListenPart).where(ListenPart.listen_id.in_(ids), ListenPart.role == 'input', ListenPart.unlinked_at.is_(None)).order_by(ListenPart.ordinal)):
        d = p.detail or {}; out[p.listen_id]['copies'].append(dict(minutes=d.get('minutes'), files=d.get('files'), bytes=d.get('bytes'), folder=os.path.basename(d.get('path') or ''), path=d.get('path'), group_id=p.filegroup_id))
    return out


def _candidate(p: ListenPart) -> dict:
    d = p.detail or {}
    return dict(part_id=p.id, ordinal=p.ordinal, state=p.state, auto=p.state == 'accepted', p=p.score or 0., work_id=p.work_id, key=d.get('candidate'), title=d.get('title'), authors=d.get('authors') or [], year=d.get('year'), editions=d.get('editions'),
                ol=d.get('ol') or [], isbns=d.get('isbns') or [], recordings=d.get('recordings') or [], recording=d.get('recording'), alias=d.get('alias'), reading=d.get('reading'), evidence=d.get('vector') or {}, p_none=d.get('p_none'), second=d.get('second'))


def queue_items(s: Session) -> list[dict]:
    """Every undecided listen with model candidates, highest probability first (ties by listen id)."""
    parts = list(s.scalars(select(ListenPart).join(Listen, Listen.id == ListenPart.listen_id).where(ListenPart.role.in_(('match', 'candidate')), ListenPart.unlinked_at.is_(None), ListenPart.state != 'rejected', Listen.merged_into_id.is_(None), Listen.status != apply.NOT_FOUND)))
    decided = apply.person_decisions(s, parts)
    by: dict[int, list[ListenPart]] = {}
    for p in parts: by.setdefault(p.listen_id, []).append(p)
    items = []
    for lid, ps in by.items():
        if any(p.state == 'accepted' and (p.id in decided or p.linked_by == 'user') for p in ps): continue
        cands = sorted((_candidate(p) for p in ps), key=lambda c: -c['p'])
        items.append(dict(listen_id=lid, p=cands[0]['p'], candidates=cands))
    return sorted(items, key=lambda it: (-it['p'], it['listen_id']))


def summary(s: Session, items: list[dict]) -> dict:
    statuses = dict(s.execute(select(Listen.status, func.count()).where(Listen.merged_into_id.is_(None), Listen.kind == 'book').group_by(Listen.status)).all())
    decisions = s.scalar(select(func.count()).select_from(ListenEvent).where(ListenEvent.actor_kind == 'user', ListenEvent.action.in_(('accept', 'reject')))) or 0
    since = decisions - (service.STATE.get('decisions_at_train') or 0) if service.STATE.get('decisions_at_train') is not None else None
    params = service.load_params()
    return dict(waiting=len(items), auto_unconfirmed=sum(1 for it in items if it['candidates'][0]['auto']), confirmed=len(_confirmed(s)), not_found=statuses.get(apply.NOT_FOUND, 0), statuses=statuses, decisions=decisions, since_train=since,
                model=params.version() if params else None, training=dict(running=service.STATE['running'], finished=service.STATE['finished'], error=service.STATE['error'], counts=service.STATE['counts']), auto_retrain=AUTO_RETRAIN)


def _confirmed(s: Session) -> set[int]:
    return {lid for (lid,) in s.execute(select(ListenPart.listen_id).join(ListenEvent, ListenEvent.listen_part_id == ListenPart.id).where(ListenPart.role.in_(('match', 'candidate')), ListenPart.state == 'accepted', ListenPart.unlinked_at.is_(None), ListenEvent.actor_kind == 'user', ListenEvent.action == 'accept'))}


@router.get('')
def queue(after: str = '', limit: int = 20, s: Session = Depends(get_session)):
    """A page of the queue after the cursor `p:listen_id` (the last item held), so answers given meanwhile cannot skip or repeat cards."""
    limit = max(1, min(limit, 100)); items = queue_items(s)
    if after:
        try: ap, alid = after.split(':'); ap, alid = float(ap), int(alid)
        except ValueError: raise HTTPException(400, 'after must be p:listen_id')
        items_after = [it for it in items if (-it['p'], it['listen_id']) > (-ap, alid)]
    else: items_after = items
    page = items_after[:limit]; info = _listen_info(s, [it['listen_id'] for it in page])
    labels = dict(s.execute(select(Listen.id, Listen.label).where(Listen.id.in_([it['listen_id'] for it in page]))).all())
    return dict(summary=summary(s, items), items=[dict(it, label=labels.get(it['listen_id'], ''), cursor=f"{it['p']}:{it['listen_id']}", **info[it['listen_id']]) for it in page])


class Decision(BaseModel):
    listen_id: int
    action: str                 # approve | reject | none
    part_id: int | None = None


@router.post('/decide')
def decide(d: Decision, s: Session = Depends(get_session)):
    listen = s.get(Listen, d.listen_id)
    if listen is None or listen.merged_into_id is not None: raise HTTPException(404, 'no such listen')
    part = s.get(ListenPart, d.part_id) if d.part_id else None
    if part is not None and part.unlinked_at is not None and d.action == 'approve': raise HTTPException(409, 'that candidate was replaced by a newer identification; reload')
    try: apply.decide(s, listen, part, d.action)
    except ValueError as e: raise HTTPException(400, str(e))
    s.commit()
    decisions = s.scalar(select(func.count()).select_from(ListenEvent).where(ListenEvent.actor_kind == 'user', ListenEvent.action.in_(('accept', 'reject')))) or 0
    last = service.STATE.get('decisions_at_train')
    if last is None: service.STATE['decisions_at_train'] = last = decisions - 1
    retraining = False
    if decisions - last >= AUTO_RETRAIN and not service.STATE['running']:
        service.STATE['decisions_at_train'] = decisions; retraining = service.start(lambda: Session(_engine()))
    return dict(ok=True, status=listen.status, retraining=retraining)


@router.post('/retrain')
def retrain(s: Session = Depends(get_session)):
    """Refit the model with every answer so far and decide the undecided listens again (in the background; the summary's `training` says when it is done)."""
    service.STATE['decisions_at_train'] = s.scalar(select(func.count()).select_from(ListenEvent).where(ListenEvent.actor_kind == 'user', ListenEvent.action.in_(('accept', 'reject')))) or 0
    return dict(started=service.start(lambda: Session(_engine())))
