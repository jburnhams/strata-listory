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
