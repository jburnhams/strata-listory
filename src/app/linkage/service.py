"""Train and re-apply in one step, for the CLI and the match queue's "learn from my answers": labels (old catalogue approvals plus every decision made on the queue) are clamped, the model is refitted on all prepared listens, saved, and every undecided listen is decided again, so confirming easy matches lets the next run accept more on its own."""
from __future__ import annotations

import json, threading, time

from app.linkage import apply, dataset, model as M

_lock = threading.Lock()
STATE: dict = dict(running=False, finished=None, error='', counts=None, report=None)


def load_params() -> M.Params | None:
    try: return M.Params.from_json(json.loads((dataset.folder() / 'model.json').read_text(encoding='utf8')))
    except (OSError, ValueError, KeyError): return None


def train(s, *, validate: bool = True) -> tuple[M.Params, dict]:
    """Fit on every prepared listen with the current labels; with `validate`, also cross-validate (5 more fits). Saves model.json and report.json."""
    data = dataset.load_cache()
    if not data: raise ValueError('nothing prepared: run ./app listens-identify-prepare first')
    labels = dataset.all_labels(s)
    try:
        from app.identify import candidates as olc
        oc = olc.connect(); canonical = lambda w: olc.canonical(oc, [w])[w]      # noqa: E731
    except FileNotFoundError: canonical = None
    cases, stats = dataset.cases(data, labels, canonical)
    params = M.train(cases); pred = M.cross_validate(cases) if validate else []
    report = dict(model=params.version(), prior=params.prior, temperature=params.temperature, trained=time.time(), labels=dict(labelled=stats['labelled'], found=stats['found'], missing=len(stats['missing']), user=sum(1 for v in labels.values() if v.get('origin') == 'user')),
                  evidence_bits=params.explain(), cross_validation=M.summarise(pred) if validate else None, manual=M.summarise([p for p in pred if p['origin'] in ('manual', 'user')]) if validate else None)
    dataset.folder().mkdir(parents=True, exist_ok=True)
    (dataset.folder() / 'model.json').write_text(json.dumps(params.to_json(), indent=1), encoding='utf8')
    (dataset.folder() / 'report.json').write_text(json.dumps(dict(report, predictions=pred), indent=1), encoding='utf8')
    return params, report


def retrain_and_apply(session_factory, *, threshold: float = apply.THRESHOLD, validate: bool = False) -> dict:
    """One locked run: train, then decide every undecided listen with the new model. Returns the apply counts."""
    if not _lock.acquire(blocking=False): raise RuntimeError('a retrain is already running')
    STATE.update(running=True, error='')
    try:
        with session_factory() as s:
            params, report = train(s, validate=validate)
            counts = apply.apply(s, dataset.load_cache(), params, threshold=threshold); s.commit()
            try:
                from app.store import kinds_db; counts['kinds_changed'] = kinds_db.learn_and_classify(s)['changed']; s.commit()      # Learning also refits the book / radio weights from the same answers
            except Exception: s.rollback()
        STATE.update(counts=counts, report={k: v for k, v in report.items() if k != 'evidence_bits'}); return counts
    except Exception as e:
        STATE.update(error=str(e)); raise
    finally:
        STATE.update(running=False, finished=time.time()); _lock.release()


def start(session_factory, **kw) -> bool:
    """Run `retrain_and_apply` in a background thread; False when one is already running."""
    if STATE['running']: return False
    def run():
        try: retrain_and_apply(session_factory, **kw)
        except Exception: pass                 # kept in STATE['error'] for the page
    threading.Thread(target=run, name='linkage-retrain', daemon=True).start(); return True
