"""Review queue for uncertain identifications, ordered by impact, with decisions kept as training labels.

A *group* is a set of files that reduce to the same stripped title in the same folder (a book split into discs/chapters, or one file). Impact = files settled by one answer, weighted by how unsure the model is, plus a bonus when the suggested author has other unresolved groups (one confirm there teaches the model most). Confirming or rejecting stores a high-weight label; `labels.sqlite` is the only thing written."""
from contextlib import closing
import json
import math
from pathlib import Path
import sqlite3
import time

from app import settings
from app.identify import context, local, score as S, textkeys as tk

SCHEMA = '''CREATE TABLE IF NOT EXISTS labels(group_key TEXT PRIMARY KEY, decision TEXT NOT NULL CHECK(decision IN ('approve','reject')), work TEXT NOT NULL, title TEXT NOT NULL, author TEXT NOT NULL, files INTEGER NOT NULL, item TEXT NOT NULL, decided REAL NOT NULL)'''


def labels_path(): return Path(settings.data_dir()) / 'identify' / 'labels.sqlite'


def labels_db():
    path = labels_path(); path.parent.mkdir(parents=True, exist_ok=True)
    c = sqlite3.connect(path, timeout=30); c.execute(SCHEMA); return c      # a long timeout: the box is often IO-starved by the Audible fetch and backups, and a lock timeout loses a person's answer


def group_key(item):
    folder, title = context.sibling_key(item.get('path', ''), item.get('title', ''), strict=True)
    return f"{folder}|{title}|{tk.person_key(item.get('author', ''))}"


def decided():
    with closing(labels_db()) as c: return {r[0]: r[1] for r in c.execute('SELECT group_key, decision FROM labels')}


def decide(item, decision, work, files=1, title='', author=''):
    """Record a human decision for a group; `work` is the OpenLibrary work key (approved, or the suggestion that was rejected)."""
    if decision not in ('approve', 'reject'): raise ValueError('decision must be approve or reject')
    with closing(labels_db()) as c, c:
        c.execute('INSERT OR REPLACE INTO labels VALUES(?,?,?,?,?,?,?,?)', (group_key(item), decision, work, title, author, files, json.dumps(item), time.time()))


def label_runs():
    """Stored decisions as training runs (see `dataset.build`): approvals are manual positives, rejections negatives for the rejected work."""
    with closing(labels_db()) as c:
        rows = c.execute('SELECT group_key, decision, work, title, author, item FROM labels').fetchall()
    runs = []
    for key, decision, work, title, author, item in rows:
        target = dict(title=title, authors=tk.people(author) or [author], work=work if decision == 'approve' else '')
        if decision == 'reject': target['reject'] = work
        runs.append(dict(run_id='label:' + key, item=json.loads(item), target=target, origin='manual'))
    return runs


def groups(items):
    """{group_key: dict(item, files)} from annotated items; one representative per group (the first, so results are stable)."""
    out = {}
    for item in items:
        g = out.setdefault(group_key(item), dict(item=item, files=0)); g['files'] += max(item.get('members', 1), 1) if item.get('members', 1) > 1 else 1
    return out


def impact(files, score, author_groups=1):
    uncertainty = 1 - abs(2 * score - 1)
    return files * (.25 + uncertainty) * (1 + .25 * math.log1p(author_groups - 1))


def build(connection, predict, items, threshold=S.ACCEPT_THRESHOLD, margin=S.ACCEPT_MARGIN):
    """Score every undecided group; returns (queue sorted by impact, accepted groups). Items must already carry folder context."""
    done = decided(); scored = []
    for key, g in groups(items).items():
        if key in done: continue
        result = S.decide_item(connection, predict, g['item'], threshold, margin)
        scored.append(dict(group=key, files=g['files'], item=g['item'], **result))
    by_author = {}
    for s in scored:
        if s['decision'] == 'review' and s.get('authors'): by_author[tk.person_key(s['authors'][0])] = by_author.get(tk.person_key(s['authors'][0]), 0) + 1
    queue = [dict(s, impact=impact(s['files'], s['score'], by_author.get(tk.person_key(s['authors'][0]), 1) if s.get('authors') else 1)) for s in scored if s['decision'] == 'review' and s['work']]
    queue.sort(key=lambda s: -s['impact'])
    return queue, [s for s in scored if s['decision'] == 'accept']
