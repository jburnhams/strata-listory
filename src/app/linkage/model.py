"""A Fellegi-Sunter record-linkage model fitted by expectation-maximisation, with known answers clamped (semi-supervised).

For every comparison field and level the model holds m = P(level | the candidate is the listen's work) and u = P(level | it is another candidate). A candidate's likelihood ratio is the product of m/u over the fields it can compare (a field with no level contributes nothing). Each listen has at most one right work among its candidates, so the posterior is over *hypotheses*: candidate i is the work, or none of them is:

    P(i) = (p/n) L_i^t / ((1 - p) + (p/n) sum_j L_j^t)

with p the share of listens whose work is among the candidates (learned), n the number of candidates (a generic title spreads its prior over more of them) and t a temperature fitted on the labelled listens, because naive-Bayes products of correlated fields are overconfident. P(none) is the rest. Each candidate is scored with the reading of the listen (tags, folder name, swapped...) that gives it the highest ratio.

EM: the E-step computes these posteriors (labelled listens keep their known answer), the M-step re-estimates m and u as posterior-weighted level frequencies (with a small Dirichlet prior) and p as the mean posterior mass on the candidates. Pure Python: the data is a few thousand listens."""

from __future__ import annotations

import hashlib
import json
import math
from dataclasses import dataclass, field

from app.linkage.compare import FIELDS, LEVELS

ALPHA = 1.0  # Dirichlet pseudo-count per level
ITERATIONS = 40
TEMPERATURES = (0.3, 0.4, 0.5, 0.6, 0.7, 0.8, 0.9, 1.0)


@dataclass
class Case:
    """One listen: per candidate cluster key, the comparison vectors of each reading. `positive` is the known right cluster (or 'none' when known to be absent), `negatives` are known wrong ones."""

    listen_id: int
    candidates: dict[str, list[dict]]
    positive: str | None = None
    negatives: set[str] = field(default_factory=set)
    origin: str = ""  # where the label came from: manual | automatic | user


@dataclass
class Params:
    m: dict[str, dict[str, float]]
    u: dict[str, dict[str, float]]
    prior: float = 0.8
    temperature: float = 1.0

    def weight(self, vector: dict) -> float:
        """log likelihood ratio of one comparison vector (before temperature)."""
        return sum(math.log(self.m[f][lvl] / self.u[f][lvl]) for f in FIELDS if (lvl := vector.get(f)) is not None)

    def to_json(self) -> dict:
        return dict(m=self.m, u=self.u, prior=self.prior, temperature=self.temperature)

    @classmethod
    def from_json(cls, d: dict) -> Params:
        """Parameters saved before a field or level existed (the radio fields, the `radio` source) get the starting values for what they lack, so an older model.json still loads (and is refitted by the next training)."""
        base = initial()
        out = {}
        for key in ("m", "u"):
            got = {}
            for f in FIELDS:
                have = d[key].get(f, {})
                got[f] = (
                    dict(have)
                    if all(lvl in have for lvl in LEVELS[f]) and set(have) == set(LEVELS[f])
                    else _norm({lvl: have.get(lvl, getattr(base, key)[f][lvl]) for lvl in LEVELS[f]})
                )  # a complete field is kept exactly as saved (no float drift); only a field with a missing level is filled and renormalised
            out[key] = got
        return cls(out["m"], out["u"], d["prior"], d["temperature"])

    def version(self) -> str:
        return "fs-" + hashlib.sha256(json.dumps(self.to_json(), sort_keys=True).encode()).hexdigest()[:10]

    def explain(self) -> dict[str, dict[str, float]]:
        """log2(m/u) per field and level: how many bits of evidence each level is worth (positive for a match)."""
        return {f: {lvl: round(math.log2(self.m[f][lvl] / self.u[f][lvl]), 2) for lvl in LEVELS[f]} for f in FIELDS}


def initial() -> Params:
    """Start with m favouring the first (best) levels and u the later ones; EM moves them to what the data says."""
    m, u = {}, {}
    for f, levels in LEVELS.items():
        k = len(levels)
        m[f] = _norm({lvl: 2.0 ** (k - i) for i, lvl in enumerate(levels)})
        u[f] = _norm({lvl: 1.0 + i for i, lvl in enumerate(levels)})
    return Params(m, u)


def _norm(d: dict[str, float]) -> dict[str, float]:
    s = sum(d.values())
    return {k: v / s for k, v in d.items()}


def best(params: Params, vectors: list[dict]) -> tuple[float, dict]:
    """The reading with the highest log ratio for one candidate: (log ratio, its vector)."""
    return max(((params.weight(v), v) for v in vectors), key=lambda x: x[0])


def posterior(params: Params, candidates: dict[str, list[dict]]) -> tuple[dict[str, float], float, dict[str, dict]]:
    """({cluster key: P(it is the work)}, P(none of them), {key: the vector used})."""
    if not candidates:
        return {}, 1.0, {}
    n = len(candidates)
    scored = {k: best(params, vs) for k, vs in candidates.items()}
    logs = {k: params.temperature * w for k, (w, _) in scored.items()}
    top = max(logs.values())
    prior = min(max(params.prior, 1e-6), 1 - 1e-6)
    none = math.log(1 - prior) - top
    base = math.log(prior / n)
    terms = {k: math.exp(base + v - top) for k, v in logs.items()}
    z = math.exp(none) + sum(terms.values())
    return {k: t / z for k, t in terms.items()}, math.exp(none) / z, {k: v for k, (_, v) in scored.items()}


def _responsibilities(params: Params, case: Case, clamp: bool) -> tuple[dict[str, float], dict[str, dict]]:
    post, _none, used = posterior(params, case.candidates)
    if clamp and case.positive is not None:
        post = {k: float(k == case.positive) for k in case.candidates}
    elif clamp and case.negatives:
        post = {k: 0.0 if k in case.negatives else v for k, v in post.items()}
    return post, used


def fit(
    cases: list[Case], *, iterations: int = ITERATIONS, use_labels: bool = True, params: Params | None = None
) -> Params:
    """EM over all listens; labelled ones are clamped when `use_labels`. Temperature is fitted afterwards (`calibrate`)."""
    params = params or initial()
    for _ in range(iterations):
        mc = {f: dict.fromkeys(LEVELS[f], ALPHA) for f in FIELDS}
        uc = {f: dict.fromkeys(LEVELS[f], ALPHA) for f in FIELDS}
        mass = 0.0
        for case in cases:
            post, used = _responsibilities(params, case, use_labels)
            mass += sum(post.values())
            for k, v in used.items():
                r = post.get(k, 0.0)
                for f in FIELDS:
                    if (lvl := v.get(f)) is not None:
                        mc[f][lvl] += r
                        uc[f][lvl] += 1 - r
        params = Params(
            {f: _norm(c) for f, c in mc.items()},
            {f: _norm(c) for f, c in uc.items()},
            min(max(mass / max(len(cases), 1), 0.05), 0.99),
            params.temperature,
        )
    return params


def calibrate(params: Params, cases: list[Case]) -> Params:
    """Choose the temperature that maximises the likelihood of the known answers (right candidate, or none)."""
    labelled = [c for c in cases if c.positive is not None]
    if not labelled:
        return params

    def loss(t):
        p = Params(params.m, params.u, params.prior, t)
        total = 0.0
        for c in labelled:
            post, none, _ = posterior(p, c.candidates)
            total -= math.log(max(none if c.positive == "none" else post.get(c.positive, 0.0), 1e-9))
        return total

    t = min(TEMPERATURES, key=loss)
    return Params(params.m, params.u, params.prior, t)


def train(cases: list[Case]) -> Params:
    return calibrate(fit(cases), cases)


def decide(post: dict[str, float], none: float, threshold: float, margin: float = 0.0) -> tuple[str, str | None]:
    """('accept', key) when the best candidate's probability is at least `threshold` (and ahead of the runner-up by `margin`); ('none', None) when it is very likely none of them; else ('review', best key)."""
    if not post:
        return "none", None
    ranked = sorted(post.items(), key=lambda kv: -kv[1])
    k, p = ranked[0]
    second = ranked[1][1] if len(ranked) > 1 else 0.0
    if p >= threshold and p - second >= margin:
        return "accept", k
    if none >= threshold:
        return "none", None
    return "review", k


def folds(cases: list[Case], k: int = 5) -> list[list[Case]]:
    """Labelled listens split into k folds by a stable hash of the listen id."""
    out: list[list[Case]] = [[] for _ in range(k)]
    for c in cases:
        if c.positive is not None:
            out[int(hashlib.sha256(str(c.listen_id).encode()).hexdigest()[:8], 16) % k].append(c)
    return out


def cross_validate(cases: list[Case], k: int = 5) -> list[dict]:
    """Out-of-fold predictions for every labelled listen: the fold's labels are hidden while fitting (its listens still take part unlabelled)."""
    out = []
    for fold in folds(cases, k):
        if not fold:
            continue
        hidden = {id(c) for c in fold}
        train_cases = [c if id(c) not in hidden else Case(c.listen_id, c.candidates) for c in cases]
        params = train(train_cases)
        for c in fold:
            post, none, _ = posterior(params, c.candidates)
            top = max(post.items(), key=lambda kv: kv[1]) if post else (None, 0.0)
            second = sorted(post.values(), reverse=True)[1] if len(post) > 1 else 0.0
            out.append(
                dict(
                    listen_id=c.listen_id,
                    origin=c.origin,
                    positive=c.positive,
                    top=top[0],
                    p=top[1],
                    second=second,
                    none=none,
                    correct=(top[0] == c.positive) if c.positive != "none" else False,
                    p_true=none if c.positive == "none" else post.get(c.positive, 0.0),
                )
            )
    return out


def summarise(predictions: list[dict], thresholds=(0.5, 0.8, 0.9, 0.95, 0.98, 0.99)) -> dict:
    """Accuracy of the top candidate, accepted share and false positives at each threshold, and calibration (expected calibration error over 10 bins of the top probability)."""
    n = len(predictions)
    if not n:
        return dict(listens=0)
    out = dict(listens=n, top1=round(sum(p["correct"] for p in predictions) / n, 3), at={})
    for t in thresholds:
        acc = [p for p in predictions if p["p"] >= t]
        out["at"][str(t)] = dict(
            accepted=len(acc),
            share=round(len(acc) / n, 3),
            false_positives=sum(not p["correct"] for p in acc),
            manual_false_positives=sum(not p["correct"] for p in acc if p["origin"] == "manual"),
        )
    bins: dict[int, list] = {}
    for p in predictions:
        bins.setdefault(min(int(p["p"] * 10), 9), []).append(p)
    out["ece"] = round(
        sum(
            len(b) / n * abs(sum(x["p"] for x in b) / len(b) - sum(x["correct"] for x in b) / len(b))
            for b in bins.values()
        ),
        4,
    )
    out["reliability"] = {
        f"{i / 10:.1f}": dict(
            n=len(b),
            mean_p=round(sum(x["p"] for x in b) / len(b), 3),
            correct=round(sum(x["correct"] for x in b) / len(b), 3),
        )
        for i, b in sorted(bins.items())
    }
    return out
