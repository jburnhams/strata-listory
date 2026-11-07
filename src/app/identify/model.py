"""Stage 3: score candidate works with a gradient-boosted model, and choose an accept rule with no observed false positives.

Run: python -m app.identify.model CATALOGUE_DB OUT_DIR   (needs requirements-model.txt; the app itself never imports xgboost at runtime unless a model file is loaded)

A query is *accepted* only if its best work group scores at least `threshold` and beats the runner-up group by `margin`; anything else goes to human review. Both are chosen on the calibration split to keep false positives at zero while accepting as many queries as possible, then reported on the held-out test split."""
import json
from pathlib import Path

from app.identify import features as ft

THRESHOLDS = (.5, .6, .7, .8, .9, .95, .98, .99, .995)
MARGINS = (0., .1, .2, .3, .5, .7)


def by_query(rows, scores):
    """{run_id: [(group_score, group_is_positive, row_origin), ...] sorted best-first}; a group scores as its best member."""
    groups = {}
    for row, score in zip(rows, scores):
        g = groups.setdefault(row['run_id'], {}).setdefault(row['cgroup'], [0., 0, row['origin']])
        g[0] = max(g[0], float(score)); g[1] = max(g[1], row['label'])
    return {run: sorted(((s, bool(p), o) for s, p, o in g.values()), reverse=True) for run, g in groups.items()}


def decide(ranked, threshold, margin):
    """'accept' (top group is the answer), or 'review'. Returns (decision, top_is_positive)."""
    if not ranked: return 'review', False
    top = ranked[0]; second = ranked[1][0] if len(ranked) > 1 else 0.
    return ('accept' if top[0] >= threshold and top[0] - second >= margin else 'review'), top[1]


def evaluate(queries, threshold, margin):
    accepted = wrong = manual_accepted = manual_wrong = 0
    for ranked in queries.values():
        decision, correct = decide(ranked, threshold, margin)
        if decision != 'accept': continue
        accepted += 1; wrong += int(not correct)
        if ranked[0][2] == 'manual': manual_accepted += 1; manual_wrong += int(not correct)
    return dict(queries=len(queries), accepted=accepted, false_positives=wrong, manual_accepted=manual_accepted, manual_false_positives=manual_wrong)


def choose(queries):
    """The (threshold, margin) with zero false positives that accepts the most queries; None if no setting is clean (abstain everywhere)."""
    best = None
    for t in THRESHOLDS:
        for m in MARGINS:
            r = evaluate(queries, t, m)
            if r['false_positives'] == 0 and r['accepted'] and (best is None or r['accepted'] > best[2]['accepted']): best = (t, m, r)
    return best


def matrix(rows, names=None):
    names = names or ft.feature_names()
    return [[float(r['features'].get(n, 0.)) for n in names] for r in rows], names


def split_rows(rows): return {s: [r for r in rows if r['split'] == s] for s in ('train', 'calibration', 'test')}


def fit(rows):  # pragma: no cover - needs the optional model dependencies
    import numpy as np
    from xgboost import XGBClassifier
    parts = split_rows(rows); X, names = matrix(parts['train'])
    model = XGBClassifier(max_depth=3, n_estimators=250, learning_rate=.08, subsample=.9, colsample_bytree=.9, min_child_weight=2, eval_metric='logloss', n_jobs=4, random_state=0)
    # manual approvals count 3x; positives are up-weighted because each query has many more negatives
    weights = [r['weight'] * (3. if r['label'] else 1.) for r in parts['train']]
    model.fit(np.array(X), np.array([r['label'] for r in parts['train']]), sample_weight=np.array(weights))
    report = {}
    for split, items in parts.items():
        if items: report[split] = dict(rows=len(items), queries=len({r['run_id'] for r in items}), positives=sum(r['label'] for r in items))
    scores = {s: list(model.predict_proba(np.array(matrix(items, names)[0]))[:, 1]) if items else [] for s, items in parts.items()}
    queries = {s: by_query(parts[s], scores[s]) for s in parts}
    chosen = choose(queries['calibration'])
    report['chosen'] = dict(threshold=chosen[0], margin=chosen[1], calibration=chosen[2]) if chosen else None
    if chosen: report['test'] = dict(report.get('test', {}), **evaluate(queries['test'], chosen[0], chosen[1]))
    report['curve'] = {split: [dict(threshold=t, margin=m, **evaluate(queries[split], t, m)) for t in (.8, .9, .95, .98) for m in (0., .3)] for split in ('calibration', 'test')}
    importance = sorted(zip(names, map(float, model.feature_importances_)), key=lambda x: -x[1])[:15]
    return model, report, dict(importance=importance)


def main(argv=None):  # pragma: no cover - research entry point
    import argparse
    from app.identify import candidates, dataset
    parser = argparse.ArgumentParser(); parser.add_argument('catalogue'); parser.add_argument('out'); args = parser.parse_args(argv)
    out = Path(args.out); out.mkdir(parents=True)
    runs = dataset.labelled_runs(args.catalogue)
    with candidates.connect() as connection:
        rows, misses = dataset.build(connection, runs, progress=lambda i, n: print(f'{i}/{n}', flush=True))
    (out / 'dataset.jsonl').write_text('\n'.join(json.dumps(r) for r in rows))
    model, report, extra = fit(rows)
    report.update(runs=len(runs), candidate_misses=len(misses), miss_examples=[dict(item=m['item'], target=m['target']) for m in misses[:25]], **extra)
    model.save_model(str(out / 'model.ubj')); (out / 'report.json').write_text(json.dumps(report, indent=1))
    print(json.dumps(report, indent=1))


if __name__ == '__main__':  # pragma: no cover
    main()
