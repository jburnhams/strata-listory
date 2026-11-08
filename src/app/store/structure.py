"""Step 3: infer what the folders mean (a book, its discs, a series of books) and write that as a new grouping run on top of the folder grouping. Deterministic, with the evidence for every call stored on the group, so the rules can be tuned and the run repeated from scratch.

Classification of each folder, bottom-up (a folder is judged by what is directly in it and by how its sub-folders were judged):
* **disc**: a leaf folder whose siblings are numbered variants of one name ("CD 1", "Disc 02", "Goldfinch 15-26", "one", "two"); the parent is then the **book**.
* **book**: a folder whose sub-folders are all discs; chapter files of one work (`scan.one_work`, or numbered 1..n); or a single whole-book file (.m4b/.m4a/.aac). Files lying next to sub-folders, or several kinds of file in one folder (an mp3 set beside its m4b), are first split into clusters, each judged on its own and written as a group under the folder.
* **series**: a folder whose sub-folders are books (an author folder, "Jack Reacher", "Hitchhiker's Guide").
* **loose**: a leaf folder of unrelated tracks (episodes, a mix) that is not one work.
* **collection**: a container whose contents are a mix (books, series, loose tracks such as radio series); each part is already classified inside it.
* **folder**: anything the rules cannot call at all; `detail.reason` says why. These are what a person or a smarter step looks at next.
Every group of the new run holds the same files as the folder it came from; the folder grouping it was built from is untouched."""
from __future__ import annotations

import os, re
from dataclasses import dataclass, field

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.scan import RADIO, one_work
from app.store.filegroups import finish_run, new_run, natural_key
from app.store.models import PartFilegroup, PartFilegroupMember, PartFileLocation, Run

WHOLE_BOOK = {'.m4b', '.m4a', '.aac'}
DISC_WORD = re.compile(r'^(?:cd|disc|disk|part|volume|vol|book|tape|side)\s*[-_. ]*\d+\s*(?:of\s*\d+)?$', re.I)
YEAR_RANGE = re.compile(r'\b(?:19|20)\d\d\s*[-–]\s*(?:19|20)\d\d\b')
NUMBER_WORDS = r'one|two|three|four|five|six|seven|eight|nine|ten|eleven|twelve|first|second|third|fourth|fifth'


@dataclass
class Node:
    id: int; label: str; path: str | None
    files: list[tuple[int, str]]                  # (file_id, file name), listening order
    subs: list['Node'] = field(default_factory=list)
    kind: str = 'folder'; detail: dict = field(default_factory=dict)
    members: list = field(default_factory=list)   # filled for synthesised groups (wrapped single files)


def stem(label: str) -> str:
    """The name with its numbers removed ('Goldfinch 15-26' and 'Goldfinch 03-26' share a stem; so do 'CD1' and 'CD2')."""
    s = re.sub(rf'\b({NUMBER_WORDS})\b', ' ', label.lower()); s = re.sub(r'\d+', ' ', s)
    return re.sub(r'[\W_]+', ' ', s).strip()


def discish(labels: list[str]) -> str | None:
    """Why these sibling folder names look like the discs of one book, or None."""
    if len(labels) < 2 and not (labels and DISC_WORD.match(labels[0].strip())): return None
    if all(DISC_WORD.match(l.strip()) for l in labels): return 'disc names'
    if len(labels) >= 2 and all(re.fullmatch(rf'\s*(\d+|{NUMBER_WORDS})\s*', l, re.I) for l in labels): return 'numbers or number words'
    stems = {stem(l) for l in labels}
    if len(labels) >= 2 and len(stems) == 1 and any(re.search(r'\d', l) for l in labels): return 'same name, numbered'
    if len(labels) >= 2 and all(re.search(r'\b(cd|disc|disk)\s*\d+', l, re.I) for l in labels): return 'disc names inside longer names'
    return None


def numbered_run(names: list[str]) -> bool:
    """True when almost every name carries a track number and the numbers run 1..n with hardly a gap or repeat ('TP - 007 - Chapter 3', 'Part 12 of 26'). Dates and years do not count as track numbers."""
    if len(names) < 3: return False
    nums = []
    for x in names:
        x = re.sub(r'\b(?:19|20)\d\d\b', ' ', os.path.splitext(x)[0])
        if m := re.search(r'\d+', x): nums.append(int(m.group()))
    if len(nums) < 0.9 * len(names) or RADIO.search(' '.join(names)): return False
    return len(set(nums)) >= 0.9 * len(names) and max(nums) - min(nums) + 1 <= 1.1 * len(names) + 2


def cluster_files(files: list[tuple[int, str]]) -> list[tuple[str, list[tuple[int, str]], dict]]:
    """Split the audio files directly in one folder into (kind, files, evidence) clusters: every whole-book file (.m4b/.m4a/.aac) is a book of its own, the rest are the chapter files of one work or unrelated tracks."""
    out = []; rest = []; whole = [f for f in files if os.path.splitext(f[1])[1].lower() in WHOLE_BOOK]
    if len(whole) >= 3 and numbered_run([f[1] for f in whole]): out.append(('book', whole, {'reason': 'numbered m4b/m4a files are the chapters of one work', 'files': len(whole)})); whole = []
    out += [('book', [f], {'reason': 'whole-book file'}) for f in whole]
    rest = [f for f in files if os.path.splitext(f[1])[1].lower() not in WHOLE_BOOK]
    if rest:
        names = [YEAR_RANGE.sub(' ', f[1]) for f in rest]      # '1999-2001' in a title reads as a date to the radio test
        if len(rest) == 1: out.append(('book', rest, {'reason': 'one file'}))
        elif one_work(names): out.append(('book', rest, {'reason': 'chapter files of one work', 'files': len(rest)}))
        elif numbered_run(names): out.append(('book', rest, {'reason': 'numbered tracks 1..n', 'files': len(rest)}))
        else: out.append(('loose', rest, {'reason': 'unrelated tracks', 'files': len(rest)}))
    return out


def classify(n: Node) -> Node:
    for c in n.subs: classify(c)
    clusters = cluster_files(n.files)
    if clusters and (n.subs or len(clusters) > 1):
        for kind, fl, ev in clusters:                                  # files next to sub-folders, or several kinds of file in one folder: each cluster becomes a group of its own under this folder
            n.subs.append(Node(-1, os.path.splitext(fl[0][1])[0] if len(fl) == 1 else f'{n.label} (files)', n.path, fl, kind=kind, detail=ev))
        n.files = []
        if all(c.kind in ('book', 'series') for c in n.subs): n.kind, n.detail = 'series', {'reason': 'several books or copies of one book side by side' if not any(c.id != -1 for c in n.subs) else 'its folders and files are books', 'books': len(n.subs)}
        else: n.kind, n.detail = 'collection', {'reason': 'books mixed with something else', 'kinds': sorted({c.kind for c in n.subs})}
        return n
    if clusters: n.kind, n.detail = clusters[0][0], clusters[0][2]; return n
    why = discish([c.label for c in n.subs]) if all(not c.subs for c in n.subs) else None
    if why:
        for c in n.subs: c.kind, c.detail = 'disc', {'reason': why}
        n.kind, n.detail = 'book', {'reason': 'its folders are discs', 'discs': len(n.subs), 'because': why}
    elif all(c.kind in ('book', 'series') for c in n.subs): n.kind, n.detail = 'series', {'reason': 'its folders are books', 'books': len(n.subs)}
    else:
        kinds = sorted({c.kind for c in n.subs}); n.kind, n.detail = ('folder', {'reason': 'contains folders the rules could not call', 'kinds': kinds}) if 'folder' in kinds else ('collection', {'reason': 'sub-folders of different kinds', 'kinds': kinds})
    return n


def load_tree(s: Session, root_group: PartFilegroup) -> Node:
    groups = {g.id: g for g in s.scalars(select(PartFilegroup).where(PartFilegroup.run_id == root_group.run_id))}
    mem: dict[int, list[PartFilegroupMember]] = {}
    for m in s.scalars(select(PartFilegroupMember).where(PartFilegroupMember.group_id.in_(groups)).order_by(PartFilegroupMember.group_id, PartFilegroupMember.ordinal)): mem.setdefault(m.group_id, []).append(m)
    names: dict[tuple[str, int], str] = {}
    for fid, path in s.execute(select(PartFileLocation.file_id, PartFileLocation.path).where(PartFileLocation.gone_at.is_(None))): names[(os.path.dirname(path), fid)] = os.path.basename(path)

    def build(g: PartFilegroup) -> Node:
        n = Node(g.id, g.label, g.path, [(m.file_id, names.get((g.path, m.file_id), f'file {m.file_id}')) for m in mem.get(g.id, []) if m.file_id])
        n.subs = [build(groups[m.child_group_id]) for m in mem.get(g.id, []) if m.child_group_id]; return n
    return build(root_group)


def write(s: Session, n: Node, run: Run, totals: dict, is_root: bool = False) -> PartFilegroup:
    """Write the classified tree as new groups (counts recomputed)."""
    children = [write(s, c, run, totals) for c in n.subs]; files = [f for f, _ in n.files]
    from app.store.models import PartFile
    size = {i: z for i, z in s.execute(select(PartFile.id, PartFile.size).where(PartFile.id.in_(files)))} if files else {}
    g = PartFilegroup(run_id=run.id, kind='root' if is_root else n.kind, label=n.label, path=n.path, detail={**n.detail, 'from_group': n.id},
                      file_count=len(files) + sum(c.file_count for c in children), total_bytes=sum(size.values()) + sum(c.total_bytes for c in children), group_count=len(children) + sum(c.group_count for c in children))
    s.add(g); s.flush(); o = 0
    for fid in files: o += 1; s.add(PartFilegroupMember(group_id=g.id, ordinal=o, file_id=fid))
    for c in children: o += 1; s.add(PartFilegroupMember(group_id=g.id, ordinal=o, child_group_id=c.id))
    s.flush()
    if not is_root: totals[g.kind] = totals.get(g.kind, 0) + 1
    return g


def infer(s: Session, folder_root: PartFilegroup) -> tuple[PartFilegroup, dict]:
    """Build the structure grouping from a folder grouping's root. Returns the new root and counts per kind."""
    run = new_run(s, 'infer_structure', {'from_run': folder_root.run_id}); totals: dict = {}
    tree = classify(load_tree(s, folder_root)); top = write(s, tree, run, totals, is_root=True); finish_run(s, run, 'done'); return top, totals
