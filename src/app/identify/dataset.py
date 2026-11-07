"""Training data: approved catalogue runs → (local item, approved work) pairs, expanded into candidate rows with features.

Manual approvals are the most trusted (weight 3); automatic approvals were produced by the previous rule engine (weight 1). Splits are by work group so a book and its duplicates never straddle train and test. Read-only on the catalogue."""
from contextlib import closing
import hashlib
import json
import re
import sqlite3
from pathlib import Path

from app.identify import candidates as cand, features as ft, textkeys as tk

WEIGHT = dict(manual=3., automatic=1.)
STAGES = ('identity', 'metadata', 'metadata_approval', 'metadata_revocation', 'match_review_decision')


def work_key(url):
    found = re.search(r'/works/(OL\d+W)', url or '')
    return '/works/' + found[1] if found else ''


def labelled_runs(catalogue_db):
    """One entry per approved, un-revoked run of a book: the local item, the approved title/authors and its Open Library work key when recorded."""
    with closing(sqlite3.connect(Path(catalogue_db).resolve().as_uri() + '?mode=ro', uri=True)) as c:
        events = {}
        for run_id, stage, status, data, event_id in c.execute(f"SELECT run_id, stage, status, data, id FROM ingest_events WHERE status='done' AND stage IN ({','.join('?' * len(STAGES))}) ORDER BY id", STAGES):
            events.setdefault(run_id, []).append((stage, event_id, data))
    out = []
    for run_id, items in events.items():
        approvals = [e for e in items if e[0] == 'metadata_approval']
        if not approvals: continue
        approval = approvals[-1]
        if any(e[0] == 'metadata_revocation' and e[1] > approval[1] for e in items): continue
        identity = next((json.loads(e[2]) for e in reversed(items) if e[0] == 'identity'), None)
        metadata = next((json.loads(e[2]) for e in reversed(items) if e[0] == 'metadata' and e[1] < approval[1]), None)
        if not identity or not metadata or identity.get('kind') != 'book': continue
        work = metadata.get('work') or {}; resolved = work.get('resolved') or metadata.get('resolved') or {}
        records = work.get('records') or metadata.get('records') or {}
        if not resolved.get('title') or not resolved.get('authors'): continue
        book = identity.get('book') or {}
        manual = not json.loads(approval[2]).get('automatic') or any(e[0] == 'match_review_decision' and json.loads(e[2]).get('action') == 'approve' for e in items)
        out.append(dict(run_id=run_id, item=dict(title=book.get('title', ''), author=book.get('author', ''), narrator=book.get('narrator', ''), series=book.get('series', ''), asin=book.get('asin', ''), year=book.get('year')),
                        target=dict(title=resolved['title'], authors=resolved['authors'], work=work_key((records.get('openlibrary') or {}).get('url'))), origin='manual' if manual else 'automatic'))
    return out


def positive_groups(connection, target, described):
    """Group keys of candidates that are the approved work: the recorded OL work (through redirects), else same title and a shared author."""
    if target['work']:
        mapping = cand.canonical(connection, [target['work']] + [d['key'] for d in described])
        wanted = mapping[target['work']]
        return {ft.group_key(d) for d in described if mapping.get(d['key'], d['key']) == wanted or d['key'] == target['work']}
    want = tk.title_key(target['title']); people = {k for a in target['authors'] for k in tk.person_variants(a)}
    return {ft.group_key(d) for d in described if (tk.title_key(d['title']) == want or any(n == want for (n, _), _c in d['titles'].items())) and people & {tk.person_key(a['name']) for a in d['authors']}}


def split_of(group_id):
    bucket = int(hashlib.sha256(group_id.encode()).hexdigest()[:8], 16) % 5
    return 'test' if bucket == 0 else 'calibration' if bucket == 1 else 'train'


def build(connection, runs, progress=None):
    """Rows: dict(run_id, features, label, weight, origin, split, group). Also reports runs whose approved work never appeared as a candidate (candidate recall misses)."""
    rows = []; misses = []
    for i, run in enumerate(runs):
        item = run['item']; hits = cand.find(connection, item['title'], item['author'], run['item'].get('isbns', ()))
        mapping = cand.canonical(connection, list(hits)); described = []
        for key in dict.fromkeys(mapping.values()):
            d = cand.describe(connection, key)
            if d: d['via'] = set().union(*(hits[k] for k in hits if mapping[k] == key)); described.append(d)
        positives = positive_groups(connection, run['target'], described)
        if not positives: misses.append(run); continue
        pairs = ft.add_context([(d, ft.item_features(item, d, d['via'])) for d in described])
        group_id = hashlib.sha256(json.dumps([tk.title_key(run['target']['title']), sorted(tk.person_key(a) for a in run['target']['authors'])]).encode()).hexdigest()
        for d, f in pairs:
            rows.append(dict(run_id=run['run_id'], work=d['key'], features=f, label=int(ft.group_key(d) in positives), weight=WEIGHT[run['origin']], origin=run['origin'], split=split_of(group_id), group=group_id))
        if progress and i % 50 == 0: progress(i, len(runs))
    return rows, misses
