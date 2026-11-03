"""Small source-location summaries without probing or decoding audio."""
from pathlib import Path
from app import details, fs, scan
from app.ingest import pipeline, store


def describe(book):
    path=book.get('path') or ''
    if not path:return dict(folder='',files=[],count=0,available=False)
    run=store.latest(book['id']);inv=pipeline.stage_data(run,'inventory') if run else None
    if inv:
        root=Path(inv['root']);folder=root.parent if root.suffix.lower() in scan.AUDIO else root
        paths=[Path(f['path']) for f in inv['files']]
    else:
        root=Path(path);folder=root.parent if root.suffix.lower() in scan.AUDIO else root
        if not fs.reachable(path):return dict(folder=str(folder),files=[],count=book.get('file_count'),available=False)
        try:paths=[Path(p) for p in details.audio_files(path)]
        except OSError:return dict(folder=str(folder),files=[],count=book.get('file_count'),available=False)
    names=[str(p.relative_to(folder)) for p in paths[:3]]
    result=dict(folder=str(folder),files=names,count=len(paths),available=True)
    notes=(inv.get('work_hint') or {}).get('grouping_notes',[]) if inv else []
    if notes:result['grouping']=notes
    return result
