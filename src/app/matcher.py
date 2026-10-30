"""Match scanned books against the metadata providers (see providers/) and record the best candidate as a *suggestion* for review. A suggestion never changes a book until it is confirmed (`catalogue.confirm`).

How a book is matched (`match_book`):
1. An ASIN in the folder name: each provider is asked for it in order; the first hit wins (score 1.0, matched_by 'asin').
2. Otherwise every provider is searched by title and author, in order. Each candidate is scored on title and author similarity; the same book found by several providers is merged (fields filled in from each, and a small bonus for agreement). The search stops early when a provider already gives a near-certain answer.
The suggestion keeps the providers that agreed (`sources`) and the next best alternatives for review."""
import difflib, re

from app import catalogue, providers
from app.providers.base import MatchError

MIN_SCORE = 0.6          # below this a candidate is not worth showing
GOOD_SCORE = 0.85        # the UI marks suggestions at or above this as likely right
EARLY_STOP = 0.95        # a candidate this good ends the search: asking more providers would not change the answer
AGREE_BONUS = 0.05       # per additional provider that found the same book
ALTERNATIVES = 3

__all__ = ['MatchError', 'MIN_SCORE', 'GOOD_SCORE', 'norm', 'title_score', 'author_score', 'score', 'match_book', 'match_pending', 'evaluate']


def norm(s: str) -> str:
    s = re.sub(r'\b(unabridged|abridged|the|a|an)\b', ' ', (s or '').lower())
    return re.sub(r'\s+', ' ', re.sub(r'[^a-z0-9 ]+', ' ', s.replace('&', ' and '))).strip()


def title_score(wanted: str, cand: dict) -> float:
    w = norm(wanted); best = 0.0
    for t in (cand['title'], f"{cand['title']} {cand['subtitle']}", cand['subtitle']):
        t = norm(t)
        if not t: continue
        r = difflib.SequenceMatcher(None, w, t).ratio()
        if len(t) >= 6 and (t in w or w in t) and min(len(t), len(w)) >= 0.5 * max(len(t), len(w)): r = max(r, 0.9)       # one side is the other plus a subtitle or volume
        best = max(best, r)
    return best


def author_score(wanted: str, cand: dict) -> float:
    """1 when the surname of every wanted author part appears among the candidate's authors, 0.5 when we do not know the author, else 0.3 for a similar name and 0."""
    w = norm(wanted)
    if not w: return 0.5
    have = ' '.join(norm(a) for a in cand['authors'])
    parts = [p for p in re.split(r'\band\b|;|,', w) if p.strip()] or [w]
    if all(p.split()[-1] in have.split() for p in parts if p.split()): return 1.0
    return 0.3 if difflib.SequenceMatcher(None, w, have).ratio() > 0.6 else 0.0


def score(book: dict, cand: dict) -> float:
    return round(0.7 * title_score(book['title'], cand) + 0.3 * author_score(book['author'], cand), 3)


def _key(c: dict) -> str:
    return c['asin'] or norm(c['title']) + '|' + (norm(c['authors'][0]).split() or [''])[-1] if c['authors'] else c['asin'] or norm(c['title'])


def _merge(group: list[tuple[float, dict]]) -> tuple[float, dict]:
    """One book found by one or more providers: take the best-scored record, fill its empty fields from the others, and reward agreement."""
    group = sorted(group, key=lambda x: -x[0]); best = dict(group[0][1])
    for _, other in group[1:]:
        for k, v in other.items():
            if v in ('', None, []) or best.get(k) not in ('', None, []): continue
            best[k] = v
    sources = list(dict.fromkeys(c['source'] for _, c in group))
    return min(1.0, round(group[0][0] + AGREE_BONUS * (len(sources) - 1), 3)), dict(best, sources=sources)


def match_book(book: dict, plist: list | None = None) -> tuple[dict, float | None]:
    """The best suggestion for a scanned book: (suggestion, score), or ({}, None) when nothing reaches MIN_SCORE. Raises MatchError when every provider failed."""
    plist = providers.enabled() if plist is None else plist
    if not plist: raise MatchError('no metadata provider is available: see `./app providers`')
    errors = []
    if book.get('asin'):
        for p in plist:
            try: c = p.by_asin(book['asin'])
            except MatchError as e: errors.append(str(e)); continue
            if c: return dict(c, matched_by='asin', sources=[p.name]), 1.0
    scored = []; asked = 0
    for p in plist:
        try: cands = p.search(book['title'], book['author'])
        except MatchError as e: errors.append(str(e)); continue
        asked += 1; scored += [(score(book, c), c) for c in cands]
        if scored and max(s for s, _ in scored) >= EARLY_STOP: break
    if not asked and errors: raise MatchError('; '.join(dict.fromkeys(errors)))
    groups: dict[str, list] = {}
    for s, c in scored: groups.setdefault(_key(c), []).append((s, c))
    merged = sorted((_merge(g) for g in groups.values()), key=lambda x: -x[0])
    if not merged or merged[0][0] < MIN_SCORE: return {}, None
    best_score, best = merged[0]
    alts = [dict(source=c['source'], title=c['title'], author=c['author'], asin=c['asin'], score=s) for s, c in merged[1:1 + ALTERNATIVES] if s >= MIN_SCORE]
    return dict(best, matched_by='asin' if book.get('asin') and best['asin'] == book['asin'] else 'title', alternatives=alts), best_score


def match_pending(limit: int = 25, plist: list | None = None) -> dict:
    """Try to match up to `limit` scanned books; returns {tried, suggested, none, remaining, providers}."""
    plist = providers.enabled() if plist is None else plist; tried = suggested = 0
    for b in catalogue.pending_match(limit):
        sug, sc = match_book(b, plist); catalogue.set_suggestion(b['id'], sug, sc); tried += 1; suggested += bool(sug)
    return dict(tried=tried, suggested=suggested, none=tried - suggested, remaining=len(catalogue.pending_match(100000)), providers=[p.name for p in plist])


def evaluate(books: list[dict], plist: list | None = None) -> dict:
    """Compare the providers on the same books without recording anything: for each provider alone and for all together, how many books got a candidate scoring at least GOOD_SCORE (and at least MIN_SCORE). A rough hit rate, since there is no ground truth."""
    plist = providers.enabled() if plist is None else plist; rows = {}
    for label, group in [(p.name, [p]) for p in plist] + ([('combined', plist)] if len(plist) > 1 else []):
        good = some = errors = 0
        for b in books:
            try: sug, sc = match_book(b, group)
            except MatchError: errors += 1; continue
            some += bool(sug); good += bool(sug) and sc >= GOOD_SCORE
        rows[label] = dict(books=len(books), good=good, some=some, errors=errors)
    return rows
