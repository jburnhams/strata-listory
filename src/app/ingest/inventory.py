"""Bounded tag-aware group discovery, immutable manifests and representations."""
import hashlib
import json
import math
from pathlib import Path
import re
from app import catalogue, details, fs, merge, scan, sources
from app.process import probe


def digest(path):
    h=hashlib.sha256()
    with Path(path).open('rb') as f:
        for b in iter(lambda:f.read(1024*1024),b''): h.update(b)
    return h.hexdigest()


def signature(book):
    return hashlib.sha256(json.dumps({k:book.get(k) for k in ('title','author','narrator','series','series_position','asin','year','locked','path','review')},sort_keys=True).encode()).hexdigest()


def track(tags):
    m=re.match(r'\s*(\d+)(?:/(\d+))?',str(tags.get('track') or tags.get('tracknumber') or ''))
    return (int(m[1]),int(m[2]) if m[2] else None) if m else (None,None)


def is_part(path,tags):
    a,b=track(tags)
    return bool(b and b>1 or re.search(r'(?:chapter|part|track|\s-)[ _-]*\d+\s*$',Path(path).stem,re.I))


def discover(book):
    path=Path(book['path']).resolve()
    if not fs.reachable(str(path)) or not path.exists(): raise ValueError('audio path unavailable')
    root=path
    if path.is_file():
        try:first=probe.probe_file(str(path))
        except (OSError,ValueError,probe.ToolError):first=dict(tags={})
        other_formats=any(p.is_file() and scan.is_audio(p.name) and p.suffix.lower()!=path.suffix.lower() for p in path.parent.iterdir())
        if is_part(path,first['tags']) or other_formats:
            siblings=[p for p in path.parent.iterdir() if p.is_file() and scan.is_audio(p.name)]
            def key(p):return scan._norm(re.sub(r'(?:chapter|part|track|disc|cd)[ _-]*\d+', '', p.stem, flags=re.I))
            base=key(path) or scan._norm(path.parent.name)
            related=lambda p:not key(p) or key(p).endswith(base) or base.endswith(key(p))
            if len(base)>=4 and all(related(p) for p in siblings):root=path.parent
    paths=[root] if root.is_file() else sorted((p for p in root.rglob('*') if p.is_file() and scan.is_audio(p.name) and not any(x.startswith(('.', '@')) for x in p.relative_to(root).parts)),key=lambda p:probe.natural_key(str(p)))
    if not paths: raise ValueError('no audio files')
    files=[];errors=[]
    for p in paths:
        try:
            before=p.stat();raw=details._ffprobe(str(p));info=probe.probe_file(str(p));sha=digest(p);after=p.stat()
            if not raw or not math.isfinite(info['duration']) or info['duration']<=0: raise ValueError('invalid duration or probe')
            if (before.st_size,before.st_mtime_ns)!=(after.st_size,after.st_mtime_ns): raise ValueError('input changed during inventory')
            files.append(dict(path=str(p),sha256=sha,size=before.st_size,mtime_ns=before.st_mtime_ns,probe=info,raw_probe=raw))
        except (OSError,ValueError,probe.ToolError) as e: errors.append(dict(path=str(p),error=str(e)))
    sidecars=[]
    names=set(root.glob('*.metadata.json')) if root.is_dir() else {root.with_suffix('.metadata.json')}
    for p in sorted(names):
        if p.exists():
            try: sidecars.append(dict(path=str(p),sha256=digest(p),raw=json.loads(p.read_text(encoding='utf8'))))
            except (OSError,ValueError) as e: errors.append(dict(path=str(p),error=str(e)))
    members=[b for b in catalogue.list_books() if b['path'] and (Path(b['path']).resolve()==root or Path(b['path']).resolve() in paths)]
    if book['id'] not in [b['id'] for b in members]: members.append(book)
    reps={}
    for f in files:
        p=Path(f['path']);info=f['probe'];t=info['tags'];part=is_part(p,t)
        key=str(p.parent)+'|'+p.suffix.lower()+'|'+merge.norm(t.get('album','')) if part else str(p)
        reps.setdefault(key,[]).append(f)
    representations=[]
    for key,items in sorted(reps.items()):
        items.sort(key=lambda f:probe.natural_key(f['path']));numbers=[track(f['probe']['tags']) for f in items];total=max((n[1] or 0 for n in numbers),default=0)
        nums=[n[0] for n in numbers];missing=len(set(range(1,total+1))-set(nums)) if total else None
        duplicate=len(nums)-len(set(nums)) if all(n is not None for n in nums) else 0
        representations.append(dict(id=hashlib.sha256(key.encode()).hexdigest()[:16],files=[f['path'] for f in items],duration=sum(f['probe']['duration'] for f in items),bytes=sum(f['size'] for f in items),missing_tracks=missing,duplicate_tracks=duplicate))
    group=hashlib.sha256(str(root).encode()).hexdigest()
    return dict(root=str(root),group=group,primary_id=min(b['id'] for b in members),members=members,files=files,sidecars=sidecars,representations=representations,errors=errors),{'inventory.files':len(files),'inventory.probe_errors':len(errors)}


def recover(book,inv):
    locked=set(book.get('locked') or []);votes={};authors={};narrators={};asins=set();languages=set();formats=set();evidence=[]
    def add(target,value):
        if value: target[value]=target.get(value,0)+1
    for f in inv['files']:
        t=f['probe']['tags'];title=t.get('album') or t.get('title') or ''
        if re.match(r'^(?:unabridged\s*\d|\d+$)',title,re.I): title=t.get('title') or ''
        title=re.sub(r'\s*\((?:un)?abridged\)\s*','',title,flags=re.I).strip()
        add(votes,title);who=t.get('album_artist') or t.get('artist') or ''
        if '/' in who and len(set(who.split('/')))==1: who=who.split('/')[0]
        add(authors,who);add(narrators,t.get('composer') or t.get('narrator') or '')
        if t.get('audible_asin'): asins.add(t['audible_asin'])
        if t.get('language'): languages.add(t['language'].lower())
        if re.search(r'\bunabridged\b',t.get('album',''),re.I): formats.add(False)
        elif re.search(r'\babridged\b',t.get('album',''),re.I): formats.add(True)
        evidence.append(dict(source='embedded',file=f['path'],tags=t))
    for side in inv['sidecars']:
        a=details.audible_summary(side['raw']);add(votes,a['title']);add(authors,', '.join(a['authors']));add(narrators,', '.join(a['narrators']))
        if a.get('asin'): asins.add(a['asin'])
        fmt=a.get('format')
        if fmt in ('abridged','unabridged'): formats.add(fmt=='abridged')
        evidence.append(dict(source='audible_file',data=a))
    out=dict(book)
    for field,target in [('title',votes),('author',authors),('narrator',narrators)]:
        if field not in locked and target: out[field]=max(target,key=target.get)
    if 'asin' not in locked and len(asins)==1: out['asin']=next(iter(asins))
    text=' '.join([out['title'],inv['root']]+[str(f['probe']['tags'].get(k,'')) for f in inv['files'] for k in ('genre','album','title')]).lower()
    kind='radio' if 'radio comedy' in text or re.search(r'\bs\d+\s*-?\s*e\d+',text) else 'episode' if re.search(r'\bep(?:isode)?\.?\s*\d+',text) else 'standup' if 'stand-up' in text or 'standup' in text else 'story' if re.search(r'\ba story from\b|\bshort story\b',text) else 'book'
    conflicts=int(len(asins)>1)+int(len(languages)>1)+int(len(formats)>1)
    # Multi-work/narrator folders must not turn into a single book by popularity vote.
    conflicts+=int(len({merge.norm(x) for x in authors})>1)+int(len({merge.norm(x) for x in votes})>1)
    return dict(book=out,kind=kind,language=next(iter(languages)) if len(languages)==1 else '',abridged=next(iter(formats)) if len(formats)==1 else None,evidence=evidence),{'identity.title_known':int(bool(out.get('title'))),'identity.author_known':int(bool(out.get('author'))),'identity.conflicts':conflicts}


def unchanged(manifest):
    for f in manifest:
        p=Path(f['path'])
        if not p.is_file() or p.stat().st_size!=f['size'] or digest(p)!=f['sha256']: return False
    return True


def snapshot_unchanged(inv):
    if not unchanged(inv['files']):return False
    for side in inv.get('sidecars',[]):
        if not Path(side['path']).is_file() or digest(side['path'])!=side['sha256']:return False
    root=Path(inv['root'])
    if root.is_dir():
        paths={str(p) for p in root.rglob('*') if p.is_file() and scan.is_audio(p.name) and not any(x.startswith(('.', '@')) for x in p.relative_to(root).parts)}
        if paths!={f['path'] for f in inv['files']}:return False
        if {str(p) for p in root.glob('*.metadata.json')}!={s['path'] for s in inv.get('sidecars',[])}:return False
    elif root.with_suffix('.metadata.json').exists() and str(root.with_suffix('.metadata.json')) not in {s['path'] for s in inv.get('sidecars',[])}:return False
    return True
