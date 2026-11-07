"""Folder context for a local item: how many sibling files reduce to the same title once track/disc numbers are removed, and how many files the run already groups.

Features only. Chapter/disc grouping stays where it is (`ingest/grouping`); this exposes what it did and did not manage to group, so the model can weigh "136 numbered files sharing one stripped title" without a hard-coded rule."""
from collections import Counter
from contextlib import closing
import json
from pathlib import Path
import re
import sqlite3

from app.identify import local, textkeys as tk


def sibling_key(path, title):
    return (str(Path(path).parent) if path else '', tk.title_key(local.strip_numbers(title) or title))


class Index:
    def __init__(self, rows):
        """rows: [(path, title)] of every book item in the catalogue."""
        self.same = Counter(sibling_key(p, t) for p, t in rows); self.folder = Counter(str(Path(p).parent) for p, _ in rows if p)

    def annotate(self, item, members=1):
        path = item.get('path', ''); key = sibling_key(path, item.get('title', '')); folder = self.folder.get(key[0], 0) or 1
        return dict(item, members=members, siblings=self.same.get(key, 1), folder_files=folder, numbered=int(bool(local.strip_numbers(item.get('title', ''))) and local.strip_numbers(item.get('title', '')) != item.get('title', '').strip()))


def load(catalogue_db):
    """Index over the latest ingest run of every book item in the catalogue (read-only)."""
    with closing(sqlite3.connect(Path(catalogue_db).resolve().as_uri() + '?mode=ro', uri=True)) as c:
        latest = {}
        for run_id, book_id in c.execute('SELECT id, book_id FROM ingest_runs ORDER BY created'): latest[book_id] = run_id
        rows = []
        for run_id in set(latest.values()):
            row = c.execute("SELECT data FROM ingest_events WHERE run_id=? AND stage='identity' AND status='done' ORDER BY id DESC LIMIT 1", (run_id,)).fetchone()
            if row and json.loads(row[0]).get('kind') == 'book':
                book = json.loads(row[0]).get('book') or {}; rows.append((book.get('path', ''), book.get('title', '')))
    return Index(rows)
