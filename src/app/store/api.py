"""Read-only API over the listory schema's filegroups (the Files page). Mounted by the server only when omarchy-db is installed and the database answers; otherwise /api/filegroups says 503 and the rest of the app is unaffected."""
from __future__ import annotations

import os
from functools import lru_cache

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.store.models import PartFile, PartFileLocation, PartFilegroup, PartFilegroupMember, Run

router = APIRouter(prefix='/api/filegroups')


@lru_cache(maxsize=1)
def _engine():
    from omarchy_db import connect
    return connect('listory')


def get_session():
    try: eng = _engine()
    except Exception as e: raise HTTPException(503, f'the listory database is not available ({e})')
    with Session(eng) as s: yield s


def _group(g: PartFilegroup) -> dict:
    return dict(id=g.id, kind=g.kind, label=g.label, path=g.path, file_count=g.file_count, total_bytes=g.total_bytes, group_count=g.group_count)


def latest_root(s: Session) -> PartFilegroup | None:
    return s.scalars(select(PartFilegroup).join(Run, Run.id == PartFilegroup.run_id).where(PartFilegroup.kind == 'root', Run.outcome == 'done').order_by(Run.started_at.desc(), PartFilegroup.id.desc())).first()


@router.get('')
def roots(s: Session = Depends(get_session)):
    """The newest grouping's root group (or null before the first scan)."""
    g = latest_root(s)
    return dict(root=_group(g) if g else None, groupings=s.scalar(select(func.count()).select_from(Run).where(Run.kind == 'group_folders', Run.outcome == 'done')))


@router.get('/search')
def search(q: str, limit: int = 50, s: Session = Depends(get_session)):
    """Case-insensitive 'contains' search (every word must match) over the newest grouping: groups by name and folder path, files by path. Each file hit names the group that holds it, so the page can jump there."""
    words = q.split()
    if not words or sum(map(len, words)) < 2: raise HTTPException(422, 'type at least two characters')
    root = latest_root(s)
    if root is None: return dict(groups=[], files=[], more=dict(groups=0, files=0))
    limit = max(1, min(limit, 200))
    gtext = func.concat(PartFilegroup.label, ' ', func.coalesce(PartFilegroup.path, ''))
    gfilter = [PartFilegroup.run_id == root.run_id, PartFilegroup.kind != 'root', *[gtext.icontains(w, autoescape=True) for w in words]]
    groups = list(s.scalars(select(PartFilegroup).where(*gfilter).order_by(func.length(PartFilegroup.path), PartFilegroup.label).limit(limit)))
    gtotal = s.scalar(select(func.count()).select_from(PartFilegroup).where(*gfilter))
    ffilter = [PartFilegroup.run_id == root.run_id, PartFileLocation.gone_at.is_(None), *[PartFileLocation.path.icontains(w, autoescape=True) for w in words]]
    base = select(PartFileLocation.file_id, PartFileLocation.path, PartFilegroup.id, PartFilegroup.label).select_from(PartFilegroupMember).join(PartFilegroup, PartFilegroup.id == PartFilegroupMember.group_id).join(PartFileLocation, PartFileLocation.file_id == PartFilegroupMember.file_id).where(*ffilter, func.regexp_replace(PartFileLocation.path, '/[^/]*$', '') == PartFilegroup.path)
    rows = s.execute(base.order_by(PartFileLocation.path).limit(limit)).all()
    ftotal = s.scalar(select(func.count()).select_from(base.subquery()))
    return dict(groups=[_group(g) for g in groups],
                files=[dict(id=fid, name=os.path.basename(path), path=path, group_id=gid, group_label=gl) for fid, path, gid, gl in rows],
                more=dict(groups=max(0, gtotal - len(groups)), files=max(0, ftotal - len(rows))))


@router.get('/{group_id}')
def group(group_id: int, s: Session = Depends(get_session)):
    g = s.get(PartFilegroup, group_id)
    if g is None: raise HTTPException(404, 'no such group')
    members = list(s.scalars(select(PartFilegroupMember).where(PartFilegroupMember.group_id == g.id).order_by(PartFilegroupMember.ordinal)))
    kids = {c.id: c for c in s.scalars(select(PartFilegroup).where(PartFilegroup.id.in_([m.child_group_id for m in members if m.child_group_id])))}
    files = {f.id: f for f in s.scalars(select(PartFile).where(PartFile.id.in_([m.file_id for m in members if m.file_id])))}
    paths: dict[int, str] = {}
    for fid, path in s.execute(select(PartFileLocation.file_id, PartFileLocation.path).where(PartFileLocation.file_id.in_(files), PartFileLocation.gone_at.is_(None)).order_by(PartFileLocation.id.desc())):
        if fid not in paths or os.path.dirname(path) == g.path: paths[fid] = path        # the same content can live at several paths: prefer the one in this group's folder
    ancestors, cur, seen = [], g, {g.id}
    while True:
        parent = s.scalars(select(PartFilegroup).join(PartFilegroupMember, PartFilegroupMember.group_id == PartFilegroup.id).where(PartFilegroupMember.child_group_id == cur.id, PartFilegroup.run_id == g.run_id)).first()
        if parent is None or parent.id in seen: break
        ancestors.insert(0, dict(id=parent.id, label=parent.label, kind=parent.kind)); seen.add(parent.id); cur = parent
    return dict(group=_group(g), ancestors=ancestors,
                children=[dict(_group(kids[m.child_group_id]), ordinal=m.ordinal) for m in members if m.child_group_id],
                files=[dict(id=m.file_id, ordinal=m.ordinal, name=os.path.basename(paths.get(m.file_id, '')) or f'file {m.file_id}', path=paths.get(m.file_id), size=files[m.file_id].size, duration_s=files[m.file_id].duration_s, codec=files[m.file_id].codec, tags=_tags(files[m.file_id]), probe_error=files[m.file_id].probe_error) for m in members if m.file_id])


def _tags(f: PartFile) -> dict:
    """The embedded tags (container first, then the audio stream's), lower-cased keys."""
    p = f.probe or {}; out = {}
    for src in [*(st.get('tags') or {} for st in p.get('streams', [])), (p.get('format') or {}).get('tags') or {}]: out.update({k.lower(): v for k, v in src.items()})
    return out


@router.get('/files/{file_id}')
def file(file_id: int, s: Session = Depends(get_session)):
    """Everything known about one file: hash, all places it has been seen, and the complete ffprobe output."""
    f = s.get(PartFile, file_id)
    if f is None: raise HTTPException(404, 'no such file')
    locs = s.execute(select(PartFileLocation.path, PartFileLocation.gone_at).where(PartFileLocation.file_id == f.id).order_by(PartFileLocation.id)).all()
    return dict(id=f.id, sha256=f.sha256.hex(), size=f.size, duration_s=f.duration_s, codec=f.codec, channels=f.channels, sample_rate=f.sample_rate, bit_rate=f.bit_rate, probe=f.probe, probe_error=f.probe_error, locations=[dict(path=p, gone=g is not None) for p, g in locs])
