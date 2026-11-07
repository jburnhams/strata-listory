"""Folder context for a local item: how many sibling files reduce to the same title once track/disc numbers are removed, and how many files the run already groups.

Features only. Chapter/disc grouping stays where it is (`ingest/grouping`); this exposes what it did and did not manage to group, so the model can weigh "136 numbered files sharing one stripped title" without a hard-coded rule."""
from collections import Counter
from contextlib import closing
import json
from pathlib import Path
import re
import sqlite3

from app.identify import local, textkeys as tk


def sibling_key(path, title, strict=False):
    """(folder, title without part numbers). `strict` keeps bare trailing numbers (volumes) so Book 1 and Book 2 stay different groups."""
    stripped = local.strip_parts(title) if strict else local.strip_numbers(title)
    return (str(Path(path).parent) if path else '', tk.title_key(stripped or title))


class Index:
    def __init__(self, rows):
        """rows: [(path, title)] of every book item in the catalogue."""
        self.same = Counter(sibling_key(p, t) for p, t in rows); self.folder = Counter(str(Path(p).parent) for p, _ in rows if p)

    def annotate(self, item, members=1):
        path = item.get('path', ''); key = sibling_key(path, item.get('title', '')); folder = self.folder.get(key[0], 0) or 1
        return dict(item, members=members, siblings=self.same.get(key, 1), folder_files=folder, numbered=int(bool(local.strip_numbers(item.get('title', ''))) and local.strip_numbers(item.get('title', '')) != item.get('title', '').strip()))


def current_runs(c):
    """Run ids that are the latest for at least one member book. A multi-file group re-ingested under a different primary book supersedes its old run, because every member's latest run is the new one."""
    latest = {}
    for book_id, run_id in c.execute('SELECT m.book_id, r.id FROM ingest_members m JOIN ingest_runs r ON r.id=m.run_id ORDER BY r.created, r.id'): latest[book_id] = run_id
    for run_id, book_id in c.execute('SELECT id, book_id FROM ingest_runs ORDER BY created, id'): latest.setdefault(book_id, run_id)       # runs recorded without membership rows
    return list(dict.fromkeys(latest.values()))


def load(catalogue_db):
    """Index over the latest ingest run of every book item in the catalogue (read-only)."""
    with closing(sqlite3.connect(Path(catalogue_db).resolve().as_uri() + '?mode=ro', uri=True)) as c:
        rows = []
        for run_id in current_runs(c):
            row = c.execute("SELECT data FROM ingest_events WHERE run_id=? AND stage='identity' AND status='done' ORDER BY id DESC LIMIT 1", (run_id,)).fetchone()
            if row and json.loads(row[0]).get('kind') == 'book':
                book = json.loads(row[0]).get('book') or {}; rows.append((book.get('path', ''), book.get('title', '')))
    return Index(rows)


def facts(identity, inventory):
    """Audiobook facts for the model from the ingest identity and inventory events: total runtime in minutes (the longest representation) and language."""
    seconds = max((r.get('duration') or 0 for r in (inventory or {}).get('representations') or []), default=0)
    return dict(runtime_min=round(seconds / 60, 2) if seconds else None, language=(identity or {}).get('language') or '')
