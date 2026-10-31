"""Append-only stage events and policy revisions in the catalogue database."""
from datetime import datetime, timezone
import json
import uuid
from app import db


def now(): return datetime.now(timezone.utc).isoformat()


def create(book_id, options):
    ident = uuid.uuid4().hex; c = db.connect()
    try:
        with c: c.execute('INSERT INTO ingest_runs VALUES (?,?,?,?,?,?)', (ident, book_id, now(), 'running', json.dumps(options), 1))
    finally: c.close()
    return ident


def event(run_id, stage, status, metrics=None, data=None, error='', elapsed=0):
    c = db.connect()
    try:
        with c: c.execute('INSERT INTO ingest_events(run_id,stage,status,created,elapsed,metrics,data,error) VALUES (?,?,?,?,?,?,?,?)', (run_id, stage, status, now(), elapsed, json.dumps(metrics or {}, allow_nan=False), json.dumps(data or {}, allow_nan=False), error))
    finally: c.close()


def finish(run_id, state):
    c = db.connect()
    try:
        with c: c.execute('UPDATE ingest_runs SET state=? WHERE id=?', (state, run_id))
    finally: c.close()


def get(run_id):
    c = db.connect()
    try:
        r = c.execute('SELECT * FROM ingest_runs WHERE id=?', (run_id,)).fetchone()
        if r is None: raise ValueError('no such ingest run')
        out = dict(r); out['options'] = json.loads(out['options']); out['events'] = []
        for e in c.execute('SELECT * FROM ingest_events WHERE run_id=? ORDER BY id', (run_id,)):
            e = dict(e); e['metrics'] = json.loads(e['metrics']); e['data'] = json.loads(e['data']); out['events'].append(e)
        return out
    finally: c.close()


def runs(limit=50):
    c = db.connect()
    try: ids = [r[0] for r in c.execute('SELECT id FROM ingest_runs ORDER BY created DESC LIMIT ?', (max(1, min(limit, 200)),))]
    finally: c.close()
    return [get(i) for i in ids]


def link(run_id, members):
    c = db.connect()
    try:
        with c:
            for bid in members: c.execute('INSERT OR IGNORE INTO ingest_members VALUES (?,?)', (run_id, bid))
    finally: c.close()


def latest(book_id):
    c = db.connect()
    try: r = c.execute('SELECT r.id FROM ingest_runs r JOIN ingest_members m ON m.run_id=r.id WHERE m.book_id=? ORDER BY r.created DESC LIMIT 1', (book_id,)).fetchone()
    finally: c.close()
    return get(r[0]) if r else None


def policy_save(value):
    c = db.connect()
    try:
        with c: cursor = c.execute('INSERT INTO ingest_policy(created,data) VALUES (?,?)', (now(), json.dumps(value, allow_nan=False))); return cursor.lastrowid
    finally: c.close()


def policy_latest():
    c = db.connect()
    try: r = c.execute('SELECT id,data FROM ingest_policy ORDER BY id DESC LIMIT 1').fetchone(); return dict(revision=r[0], **json.loads(r[1])) if r else None
    finally: c.close()
