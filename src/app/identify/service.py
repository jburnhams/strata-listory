"""Operational glue for identification: train, score the library into a cached queue, page it, record decisions.

Nothing here approves a book or touches the catalogue; decisions go to `identify/labels.sqlite` and the next training run learns from them."""
from contextlib import closing
import json
from pathlib import Path
import sqlite3

from app import settings
from app.identify import candidates, context, dataset, model as M, queue, score


def folder():
    path = Path(settings.data_dir()) / 'identify'; path.mkdir(parents=True, exist_ok=True); return path


def model_file(): return folder() / 'model.ubj'


def load_predict():  # pragma: no cover - needs the optional model dependencies
    import numpy as np
    from xgboost import XGBClassifier
    if not model_file().is_file(): raise FileNotFoundError('no trained identification model: run `./app identify-train`')
    m = XGBClassifier(); m.load_model(str(model_file()))
    return lambda rows: m.predict_proba(np.array(rows))[:, 1]


def library_items(catalogue_db):
    """Latest ingest run per book: annotated book items (with folder context) of kind 'book'."""
    index = context.load(catalogue_db); items = []
    with closing(sqlite3.connect(Path(catalogue_db).resolve().as_uri() + '?mode=ro', uri=True)) as c:
        latest = {}
        for run_id, book_id in c.execute('SELECT id, book_id FROM ingest_runs ORDER BY created'): latest[book_id] = run_id
        members = dict(c.execute('SELECT run_id, count(*) FROM ingest_members GROUP BY run_id').fetchall())
        for run_id in dict.fromkeys(latest.values()):
            row = c.execute("SELECT data FROM ingest_events WHERE run_id=? AND stage='identity' AND status='done' ORDER BY id DESC LIMIT 1", (run_id,)).fetchone()
            data = json.loads(row[0]) if row else {}
            if data.get('kind') != 'book': continue
            b = data.get('book') or {}
            inventory = c.execute("SELECT data FROM ingest_events WHERE run_id=? AND stage='inventory' AND status='done' ORDER BY id DESC LIMIT 1", (run_id,)).fetchone()
            items.append(index.annotate(dict(title=b.get('title', ''), author=b.get('author', ''), narrator=b.get('narrator', ''), series=b.get('series', ''), asin=b.get('asin', ''), year=b.get('year'), path=b.get('path', ''), run_id=run_id, **context.facts(data, json.loads(inventory[0]) if inventory else None)), members.get(run_id, 1)))
    return items


def refresh(catalogue_db, predict=None):
    """Score the whole library and cache the queue (about a minute for a few thousand items)."""
    predict = predict or load_predict()
    with candidates.connect() as connection: q, accepted = queue.build(connection, predict, library_items(catalogue_db))
    summary = dict(queue=len(q), accepted=len(accepted), accepted_files=sum(a['files'] for a in accepted), queue_files=sum(s['files'] for s in q))
    (folder() / 'queue.json').write_text(json.dumps(dict(summary=summary, queue=q), default=str))
    return summary


def page(limit=10):
    """Next undecided groups by impact, from the cache; decisions made since the last refresh are skipped immediately."""
    try: cached = json.loads((folder() / 'queue.json').read_text())
    except (OSError, ValueError): return dict(summary=None, items=[])
    done = queue.decided(); items = [s for s in cached['queue'] if s['group'] not in done]
    return dict(summary=dict(cached['summary'], remaining=len(items), decided=len(done)), items=items[:max(1, min(limit, 50))])


def train(catalogue_db):  # pragma: no cover - needs the optional model dependencies
    """Retrain from approvals plus stored decisions (manual, highest weight), save the model and refresh the queue."""
    runs = dataset.labelled_runs(catalogue_db) + queue.label_runs()
    with candidates.connect() as connection: rows, misses = dataset.build(connection, runs)
    fitted, report, extra = M.fit(rows)
    fitted.save_model(str(model_file())); (folder() / 'report.json').write_text(json.dumps(dict(report, runs=len(runs), candidate_misses=len(misses), **extra), indent=1))
    return dict(report, runs=len(runs), candidate_misses=len(misses))
