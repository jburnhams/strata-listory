"""Learning the kind classifier's weights from labels, the way identification learns (`app.linkage.service`): the signals stay the features, the hand-set weights are the prior, and labelled listens move them.

A listen's evidence is the sum of its signals' hand-set weights x_s; the model is logistic, P(broadcast) = sigmoid(b + sum m_s * x_s), with one multiplier m_s per signal name (start 1) and a bias b (start at the prior). Fitting minimises the weighted log loss plus `PRIOR_STRENGTH / 2` times the squared distance of every multiplier and the bias from the hand-set values, so with no labels the model *is* the hand-set one, with a few labels only the signals those labels show move, and each further label moves them more. Multipliers stay within [0, 3]: learning rescales a signal, it never turns evidence for radio into evidence for a book.

Labels (`kinds_db.labels`): a person's choice of kind (weight 1) and, safely implied, a listen the model accepted as an Audible recording that is an ordinary audiobook by a non-BBC publisher (book) or a dramatisation filed as a radio programme (broadcast), at half weight. For those implicit labels the Audible signals are left out of the features, so a label is never learned from the very evidence that created it. Pure Python: a Newton step per signal, a few hundred rows, milliseconds."""
from __future__ import annotations

import json, math, time
from pathlib import Path

from app import settings
from app.store import kinds as K

PRIOR_STRENGTH = 4.0         # pseudo-labels of agreement with the hand-set weights per parameter
MULT_RANGE = (0.0, 3.0)
AUDIBLE_SIGNALS = ('audible content type', 'audible audiobook', 'audible keywords', 'audible disagrees', 'audible id')
STEPS = 60


def path() -> Path: return Path(settings.data_dir()) / 'kinds' / 'model.json'


def load() -> K.Params | None:
    try: return K.Params.from_json(json.loads(path().read_text(encoding='utf8')))
    except (OSError, ValueError, KeyError, TypeError): return None


def save(params: K.Params) -> None:
    path().parent.mkdir(parents=True, exist_ok=True); path().write_text(json.dumps(params.to_json(), indent=1), encoding='utf8')


def fit(rows: list[tuple[list[K.Signal], bool, float]], labels: dict[str, int] | None = None) -> K.Params:
    """`rows` are (signals with their hand-set weights, broadcast?, sample weight). Returns parameters regularised toward the hand-set ones."""
    names = sorted({s.name for sigs, _, _ in rows for s in sigs}); b0 = K.PRIOR
    x = [({n: sum(s.weight for s in sigs if s.name == n) for n in {s.name for s in sigs}}, 1. if y else 0., w) for sigs, y, w in rows]
    b = b0; m = {n: 1. for n in names}; seen = {n: sum(1 for sigs, _, _ in rows if any(s.name == n for s in sigs)) for n in names}
    for _ in range(STEPS):
        gb = PRIOR_STRENGTH * (b - b0); hb = PRIOR_STRENGTH; g = {n: PRIOR_STRENGTH * (m[n] - 1) for n in names}; h = {n: PRIOR_STRENGTH for n in names}
        for xs, y, w in x:
            z = b + sum(m[n] * v for n, v in xs.items()); p = 1 / (1 + math.exp(-max(-30, min(30, z)))); r = w * (p - y); q = w * p * (1 - p)
            gb += r; hb += q
            for n, v in xs.items(): g[n] += r * v; h[n] += q * v * v
        b -= gb / hb
        for n in names: m[n] = min(MULT_RANGE[1], max(MULT_RANGE[0], m[n] - g[n] / h[n]))
    return K.Params(bias=b, mult={n: round(v, 4) for n, v in m.items()}, seen=seen, labels=dict(labels or {}), trained=time.time())
