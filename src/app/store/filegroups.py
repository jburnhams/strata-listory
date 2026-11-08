"""Step 1 and 2 of the pipeline, rerunnable from nothing: scan a library folder into `part_file` (+ where each was seen), then group what was found by folder into `part_filegroup` trees.

`scan_files` only hashes files whose size or mtime changed since the last scan. `group_by_folder` is deterministic and makes a new grouping run each time (old groups are left alone: they are immutable). Later steps (disc / book inference) add groups on top of these, never edit them."""
from __future__ import annotations

import hashlib, json, os, re, secrets, shutil, subprocess
from collections.abc import Callable
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.store.models import PartFile, PartFileLocation, PartFilegroup, PartFilegroupMember, Run

AUDIO = {'.mp3', '.m4b', '.m4a', '.aac', '.ogg', '.opus', '.flac', '.wma'}
CHUNK = 8 * 1024 * 1024


def is_audio(name: str) -> bool: return os.path.splitext(name)[1].lower() in AUDIO and not name.startswith('.')


def natural_key(name: str) -> list:
    """'Track 2' sorts before 'Track 10'."""
    return [int(t) if t.isdigit() else t.lower() for t in re.split(r'(\d+)', name)]


def sha256_of(path: str) -> bytes:
    h = hashlib.sha256()
    with open(path, 'rb') as f:
        while block := f.read(CHUNK): h.update(block)
    return h.digest()


def probe_file(path: str) -> dict:
    """The complete ffprobe description of a file: container format with every tag, all streams, chapters."""
    if not shutil.which('ffprobe'): raise RuntimeError('ffprobe is not installed')
    r = subprocess.run(['ffprobe', '-v', 'error', '-print_format', 'json', '-show_format', '-show_streams', '-show_chapters', path], capture_output=True, text=True, timeout=120)
    if r.returncode: raise RuntimeError((r.stderr or 'ffprobe failed').strip().splitlines()[-1][:300])
    return json.loads(r.stdout)


def _num(v, cast=float):
    try: return cast(v)
    except (TypeError, ValueError): return None


def apply_probe(f: PartFile, info: dict) -> None:
    """Store the whole probe on the file and copy the queryable parts into columns."""
    audio = next((st for st in info.get('streams', []) if st.get('codec_type') == 'audio'), {})
    fmt = info.get('format', {})
    f.probe, f.probe_error = info, None
    f.duration_s, f.codec = _num(fmt.get('duration')) or _num(audio.get('duration')), audio.get('codec_name')
    f.channels, f.sample_rate, f.bit_rate = _num(audio.get('channels'), int), _num(audio.get('sample_rate'), int), _num(fmt.get('bit_rate'), int) or _num(audio.get('bit_rate'), int)


def probe_missing(s: Session, ids: dict[str, int], *, workers: int = 4) -> int:
    """ffprobe every file in `ids` that has not been probed (and did not already fail); returns how many were probed. Each distinct content is probed once, from the first path that has it."""
    todo: dict[int, str] = {}
    for p, i in ids.items(): todo.setdefault(i, p)
    need = {f.id: f for f in s.scalars(select(PartFile).where(PartFile.id.in_(todo), PartFile.probe.is_(None), PartFile.probe_error.is_(None)))}
    def run(i):
        try: return i, probe_file(todo[i]), None
        except Exception as e: return i, None, str(e)
    with ThreadPoolExecutor(max_workers=workers) as pool:
        for n, (i, info, err) in enumerate(pool.map(run, list(need)), 1):
            if info is not None: apply_probe(need[i], info)
            else: need[i].probe_error = err
            if n % 200 == 0: s.commit()
    s.flush(); return len(need)


def new_run(s: Session, kind: str, options: dict | None = None) -> Run:
    r = Run(id=secrets.token_hex(16), kind=kind, options=options); s.add(r); s.flush(); return r


def finish_run(s: Session, run: Run, outcome: str) -> None:
    run.outcome = outcome; run.finished_at = datetime.now(timezone.utc); s.flush()


def walk_audio(root: str) -> list[tuple[str, int, int]]:
    """(path, size, mtime_ns) of every audio file under root, in a stable order."""
    out = []
    for d, dirs, files in os.walk(root):
        dirs.sort(key=natural_key)
        for n in sorted(files, key=natural_key):
            if is_audio(n):
                p = os.path.join(d, n); st = os.stat(p); out.append((p, st.st_size, st.st_mtime_ns))
    return out


def scan_files(s: Session, root: str, *, workers: int = 4, progress: Callable[[int, int], None] | None = None) -> dict[str, int]:
    """Make sure every audio file under `root` has a PartFile and a current location. Returns {path: part_file_id}. Locations no longer on disk get `gone_at`."""
    found = walk_audio(root); now = datetime.now(timezone.utc)
    known = {l.path: l for l in s.scalars(select(PartFileLocation).where(PartFileLocation.path.startswith(root.rstrip(os.sep) + os.sep, autoescape=True)))}
    todo = [(p, z, m) for p, z, m in found if not ((l := known.get(p)) and l.size == z and l.mtime_ns == m and l.gone_at is None)]
    pending = {p for p, _, _ in todo}
    ids: dict[str, int] = {p: known[p].file_id for p, _, _ in found if p not in pending}
    done = 0
    with ThreadPoolExecutor(max_workers=workers) as pool:
        for (p, z, m), digest in zip(todo, pool.map(lambda t: sha256_of(t[0]), todo)):
            f = s.scalars(select(PartFile).where(PartFile.sha256 == digest)).first()
            if f is None: f = PartFile(sha256=digest, size=z); s.add(f); s.flush()
            loc = known.get(p)
            if loc is None: s.add(PartFileLocation(file_id=f.id, path=p, size=z, mtime_ns=m))
            else: loc.file_id, loc.size, loc.mtime_ns, loc.gone_at = f.id, z, m, None
            ids[p] = f.id; done += 1
            if done % 200 == 0: s.commit()
            if progress: progress(done, len(todo))
    seen = set(ids)
    for p, loc in known.items():
        if p in seen: loc.last_seen = now
        elif loc.gone_at is None: loc.gone_at = now
    s.flush(); return ids


def group_by_folder(s: Session, root: str, ids: dict[str, int], sizes: dict[str, int] | None = None, run: Run | None = None) -> PartFilegroup:
    """One group per folder that has audio anywhere below it: its members are its audio files then its sub-folders, both in natural name order (ordinals 1..n). The root folder's group has kind 'root'."""
    root = root.rstrip(os.sep) or os.sep
    run = run or new_run(s, 'group_folders', {'root': root})
    by_dir: dict[str, list[str]] = {}
    for p in ids: by_dir.setdefault(os.path.dirname(p), []).append(p)
    dirs = set()
    for d in by_dir:
        while True:
            dirs.add(d)
            if d == root or len(d) <= len(root): break
            d = os.path.dirname(d)
    children: dict[str, list[str]] = {}
    for d in dirs:
        if d != root: children.setdefault(os.path.dirname(d), []).append(d)
    byte_size = {i: z for (i, z) in s.execute(select(PartFile.id, PartFile.size).where(PartFile.id.in_(set(ids.values()))))}

    def build(d: str) -> PartFilegroup:
        subs = [build(c) for c in sorted(children.get(d, []), key=lambda c: natural_key(os.path.basename(c)))]
        files = sorted(by_dir.get(d, []), key=lambda p: natural_key(os.path.basename(p)))
        g = PartFilegroup(run_id=run.id, kind='root' if d == root else 'folder', label=os.path.basename(d) or d, path=d,
                          file_count=len(files) + sum(c.file_count for c in subs), total_bytes=sum(byte_size[ids[p]] for p in files) + sum(c.total_bytes for c in subs), group_count=len(subs) + sum(c.group_count for c in subs))
        s.add(g); s.flush(); n = 0
        for p in files: n += 1; s.add(PartFilegroupMember(group_id=g.id, ordinal=n, file_id=ids[p]))
        for c in subs: n += 1; s.add(PartFilegroupMember(group_id=g.id, ordinal=n, child_group_id=c.id))
        s.flush(); return g

    top = build(root); finish_run(s, run, 'done'); return top


def would_cycle(s: Session, group_id: int, child_group_id: int) -> bool:
    """True if making `child_group_id` a member of `group_id` would put a group inside itself (checked by whoever adds inferred groups)."""
    if group_id == child_group_id: return True
    seen, stack = set(), [child_group_id]
    while stack:
        g = stack.pop()
        if g == group_id: return True
        if g in seen: continue
        seen.add(g); stack += list(s.scalars(select(PartFilegroupMember.child_group_id).where(PartFilegroupMember.group_id == g, PartFilegroupMember.child_group_id.is_not(None))))
    return False
