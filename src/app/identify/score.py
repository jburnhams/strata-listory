"""Inference: score every candidate for a local item and decide accept / review.

`decide_item` is the whole stage-3 decision for one item: the best duplicate-cluster's score and its margin over the runner-up, compared with the calibrated thresholds. Nothing here writes approvals."""
from app.identify import dataset, features as ft, model as M

ACCEPT_THRESHOLD = .98
ACCEPT_MARGIN = .3


def rank_clusters(described, scores):
    """[(score, [candidate indexes])] best first; a cluster scores as its best member."""
    ids = ft.cluster_ids(described); clusters = {}
    for i, (cid, s) in enumerate(zip(ids, scores)):
        c = clusters.setdefault(cid, [0., []]); c[0] = max(c[0], float(s)); c[1].append(i)
    return sorted(((s, members) for s, members in clusters.values()), reverse=True)


def decide_item(connection, predict, item, threshold=ACCEPT_THRESHOLD, margin=ACCEPT_MARGIN):
    """`predict(matrix) -> probabilities`. Returns dict(decision, score, margin, work, title, authors, candidates)."""
    described = dataset.candidates_for(connection, item)
    if not described: return dict(decision='review', reason='no candidates', score=0., margin=0., work=None, candidates=0)
    pairs = ft.add_context([(d, d['features']) for d in described])
    names = ft.feature_names()
    scores = predict([[float(f.get(n, 0.)) for n in names] for _, f in pairs])
    ranked = rank_clusters(described, scores); top, members = ranked[0]; second = ranked[1][0] if len(ranked) > 1 else 0.
    best = described[max(members, key=lambda i: scores[i])]
    accept = top >= threshold and top - second >= margin
    return dict(decision='accept' if accept else 'review', score=top, margin=top - second, work=best['key'], title=best['title'], authors=[a['name'] for a in best['authors']], editions=best['editions'], candidates=len(described))
