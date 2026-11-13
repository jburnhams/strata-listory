"""Which of a listen's covers is the best fit? A scored ranking with its reasons, the way identification and the kind classifier work: every piece of evidence is a *signal* with a weight (hand-set from reading what the sources offer), the weights of a picture add to a score, and a softmax over the listen's pictures turns the scores into shares, so the page can show the best one already selected and say why.

What makes a cover fit, strongest first: it is the cover of the audiobook (Audible, Audnexus, Apple Books audiobooks) rather than of a print edition, and best of the very recording the listen is (the closest recording the identification found, with a runtime that agrees with the listen's) rather than another narration of the same book; the title it was filed under agrees with the book's; it is large (a 2400 px picture beats a 500 px one of the same cover); it is square, the shape audiobook covers have and print covers do not; several sources serve the same picture; and the listen's own files carry it. For radio, the programme's own picture from the BBC beats a podcast feed's artwork, which beats a Wikimedia image.

Learning (`fit_params`): the hand-set weights are the prior and a person's choices move them. A listen's score is sum m_s * x_s with one multiplier m_s per signal name (start 1); the model is a conditional logit, so the chosen picture of each listen competes with that listen's other pictures (P(chosen) = softmax of the scores), and fitting minimises the weighted log loss plus `PRIOR_STRENGTH / 2` times the squared distance of every multiplier from 1. With no choices the ranking *is* the hand-set one; with a few, only the signals those choices show move; multipliers stay within [0, 3], so learning rescales a signal and never turns evidence for into evidence against. Accepting the suggested picture is a weak label (it may only mean the person trusted the page), choosing another is a full one. Pure Python and pure functions: the database and the tests feed the same code."""

from __future__ import annotations

import json
import math
import time
from dataclasses import dataclass, field
from pathlib import Path

from app import settings
from app.identify import textkeys as tk

AUDIOBOOK = {"audible", "audnexus", "apple"}  # sources that show the audiobook's own cover
PRINT = {"openlibrary", "google"}  # sources that show a print edition's cover
OWN_FILES = {"folder", "embedded"}
LABELLED = {
    "audible",
    "audnexus",
    "apple",
    "podcast",
    "google",
}  # sources whose label is the title the picture was filed under
RUNTIME_AGREES, RUNTIME_DIFFERS = (
    0.04,
    0.15,
)  # relative difference of a recording's runtime from the listen's (the identification's own tolerance)
PRIOR_STRENGTH = 3.0  # pseudo-choices of agreement with the hand-set weights per signal
MULT_RANGE = (0.0, 3.0)
ACCEPT_WEIGHT = 0.5  # accepting the suggestion is half a label; choosing something else is a whole one
STEPS = 60


@dataclass
class Target:
    """What the listen is, as far as the picture's fit goes: its kind (`book` or `radio`), the title it was identified as, its runtime in minutes, the Audible recording the identification found closest and the runtime of each recording of the work."""

    kind: str = "book"
    title: str = ""
    minutes: float = 0.0
    recording: str = ""
    recordings: dict[str, float] = field(default_factory=dict)


@dataclass
class Signal:
    name: str
    weight: float  # the weight in use: the hand-set weight times what was learned
    detail: str = ""
    prior: float | None = None  # the hand-set weight, when learning has changed it
    learned: bool = False

    def as_dict(self) -> dict:
        d = dict(name=self.name, weight=round(self.weight, 2), detail=self.detail)
        if self.prior is not None:
            d.update(prior=round(self.prior, 2), learned=self.learned)
        return d


@dataclass
class Params:
    """What learning changes: one multiplier per signal name on the hand-set weights (an empty one is the hand-set model), how many labelled listens showed each signal and the label counts."""

    mult: dict[str, float] = field(default_factory=dict)
    seen: dict[str, int] = field(default_factory=dict)
    labels: dict[str, int] = field(default_factory=dict)
    trained: float = 0.0

    def to_json(self) -> dict:
        return dict(mult=self.mult, seen=self.seen, labels=self.labels, trained=self.trained)

    @classmethod
    def from_json(cls, d: dict) -> Params:
        return cls(
            mult={k: float(v) for k, v in d["mult"].items()},
            seen={k: int(v) for k, v in d.get("seen", {}).items()},
            labels=dict(d.get("labels", {})),
            trained=float(d.get("trained", 0)),
        )


@dataclass
class Scored:
    group: dict
    score: float
    share: float  # softmax over the listen's pictures: how likely this is the one a person picks
    signals: list[Signal]


def path() -> Path:
    return Path(settings.data_dir()) / "covers" / "fit-model.json"


def load() -> Params | None:
    try:
        return Params.from_json(json.loads(path().read_text(encoding="utf8")))
    except (OSError, ValueError, KeyError, TypeError):
        return None


def save(params: Params) -> None:
    path().parent.mkdir(parents=True, exist_ok=True)
    path().write_text(json.dumps(params.to_json(), indent=1), encoding="utf8")


def _title_relation(label: str, title: str) -> str:
    """`match` when the label's title is the book's (or one holds the other: a subtitle, a series name), `conflict` when both are known and share nothing, else ''."""
    a, b = tk.title_key(label), tk.title_key(title)
    if not a or not b:
        return ""
    return "match" if a == b or a in b or b in a else "conflict"


def signals(group: dict, target: Target) -> list[Signal]:
    """The hand-set evidence for one picture (a distinct cover with every source that offered it). Zero-weight signals are left out."""
    srcs = group["sources"]
    names = {s["source"] for s in srcs}
    img = group["image"]
    out: list[Signal] = []

    def add(name: str, weight: float, detail: str = "") -> None:
        if weight:
            out.append(Signal(name, weight, detail))

    if names & AUDIOBOOK:
        add("audiobook cover", 2.0, ", ".join(sorted(names & AUDIOBOOK)))
    elif names <= PRINT:
        add("print edition only", -0.5, ", ".join(sorted(names)))
    if "bbc" in names:
        add("broadcaster's picture", 2.5, "the BBC's own image of the programme")
    if "podcast" in names:
        add("podcast artwork", 1.0, "the programme's feed artwork on Apple Podcasts")
    if "wikidata" in names:
        add("reference image", 0.5, "the image Wikidata lists for the programme")
    if names & OWN_FILES:
        add("from the listen's files", 1.0, "carried by the folder or the audio")
    asins = {s["ref"]: s for s in srcs if s["source"] in ("audible", "audnexus")}
    if target.recording and target.recording in asins:
        add("this recording", 1.5, f"{target.recording} is the closest recording to the listen")
    if target.minutes and asins:
        diffs = [abs(target.recordings[a] - target.minutes) / target.minutes for a in asins if target.recordings.get(a)]
        if diffs and min(diffs) <= RUNTIME_AGREES:
            add("recording runtime", 1.0, f"within {min(diffs):.0%} of the listen's length")
        elif diffs and min(diffs) > RUNTIME_DIFFERS:
            add("recording runtime", -0.7, f"{min(diffs):.0%} away from the listen's length")
    rel = [_title_relation(s["label"], target.title) for s in srcs if s["source"] in LABELLED and s.get("label")]
    if "match" in rel:
        add("title", 0.5, "filed under the book's title")
    elif "conflict" in rel:
        add("title", -0.8, "filed under another title")
    side = max(img.width, img.height)
    add("resolution", max(-2.0, min(1.6, 0.8 * math.log2(side / 1000))), f"{img.width}×{img.height}")
    if target.kind == "book" and 0.9 <= img.width / img.height <= 1.1:
        add("audiobook shape", 0.8, "square, as audiobook covers are")
    if len(names) > 1:
        add("agreement", 0.6 * math.log2(len(names)), f"{len(names)} sources serve it")
    return out


def _use(sigs: list[Signal], params: Params | None) -> list[Signal]:
    if params is None or not params.mult:
        return sigs
    out = []
    for s in sigs:
        m = params.mult.get(s.name, 1.0)
        out.append(
            Signal(
                s.name,
                s.weight * m,
                s.detail,
                prior=s.weight,
                learned=params.seen.get(s.name, 0) > 0 and abs(m - 1) > 0.02,
            )
        )
    return out


def rank(groups: list[dict], target: Target, params: Params | None = None) -> list[Scored]:
    """The pictures best fit first, each with its score, share and the signals that made it. A tie goes to the larger picture."""
    scored = [(g, _use(signals(g, target), params)) for g in groups]
    totals = [sum(s.weight for s in sigs) for _, sigs in scored]
    top = max(totals, default=0.0)
    exps = [math.exp(t - top) for t in totals]
    z = sum(exps) or 1.0
    out = [Scored(g, t, e / z, sigs) for (g, sigs), t, e in zip(scored, totals, exps, strict=True)]
    out.sort(key=lambda x: (-x.score, -(x.group["image"].width * x.group["image"].height)))
    return out


Example = tuple[
    list[list[Signal]], int, float
]  # each picture's hand-set signals, the index of the chosen one, the sample weight


def _loss(xs, m, names) -> float:
    """The documented objective: the weighted log loss of the chosen pictures plus the pull toward the hand-set weights."""
    out = PRIOR_STRENGTH / 2 * sum((m[n] - 1) ** 2 for n in names)
    for pics, chosen, w in xs:
        z = [sum(m[n] * v for n, v in p.items()) for p in pics]
        top = max(z)
        out += w * (top + math.log(sum(math.exp(t - top) for t in z)) - z[chosen])
    return out


def fit_params(examples: list[Example], labels: dict[str, int] | None = None) -> Params:
    """Fit the multipliers on the examples (one per labelled listen with at least two pictures), regularised toward the hand-set weights. Every multiplier moves by its own Newton step (the curvature is the diagonal only, so signals that always occur together could overshoot as a set); the step is halved until the objective goes down, so it never oscillates, and stops when no step helps."""
    xs = [([{s.name: s.weight for s in sigs} for sigs in pics], chosen, w) for pics, chosen, w in examples]
    names = sorted({n for pics, _, _ in xs for p in pics for n in p})
    m = dict.fromkeys(names, 1.0)
    seen = {n: sum(1 for pics, _, _ in xs if any(n in p for p in pics)) for n in names}
    lo, hi = MULT_RANGE
    here = _loss(xs, m, names)
    for _ in range(STEPS):
        g = {n: PRIOR_STRENGTH * (m[n] - 1) for n in names}
        h = dict.fromkeys(names, PRIOR_STRENGTH)
        for pics, chosen, w in xs:
            z = [sum(m[n] * v for n, v in p.items()) for p in pics]
            top = max(z)
            e = [math.exp(t - top) for t in z]
            tot = sum(e)
            pr = [x / tot for x in e]
            for n in {n for p in pics for n in p}:
                mean = sum(pj * p.get(n, 0.0) for pj, p in zip(pr, pics, strict=True))
                var = sum(pj * p.get(n, 0.0) ** 2 for pj, p in zip(pr, pics, strict=True)) - mean * mean
                g[n] += w * (mean - pics[chosen].get(n, 0.0))
                h[n] += w * var
        step = {n: g[n] / h[n] for n in names}
        for damp in (1.0, 0.5, 0.25, 0.125, 0.0625, 0.03125):
            trial = {n: min(hi, max(lo, m[n] - damp * step[n])) for n in names}
            after = _loss(xs, trial, names)
            if after < here - 1e-12:
                m, here = trial, after
                break
        else:
            break  # no step lowers the objective: converged
    return Params(
        mult={n: round(v, 4) for n, v in m.items()}, seen=seen, labels=dict(labels or {}), trained=time.time()
    )
