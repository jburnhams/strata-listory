"""Filtering and ordering the match queue (`/api/matches`), kept apart from the database so it is plain unit-tested logic.

Each queue item carries a few facts about its listen (`kind`, `status`, `added` as epoch seconds, `text` to search) beside the model's candidates. A person working the queue wants
to skip what the model already accepted, take the hardest cards first, or see what was just added, so the order is one of `SORTS` and the filters narrow by those facts. The page
holds a cursor of the last item it has (`key:listen_id`, the sort key itself), so answers given meanwhile cannot skip or repeat cards whatever the order."""
from __future__ import annotations

import re
from dataclasses import dataclass

SORTS = ('certain', 'uncertain', 'recent', 'oldest')      # most certain first (the default), least certain first, most recently added first, oldest first
BANDS = ('sure', 'likely', 'close', 'unsure')              # the certainty words the cards show (web MatchQueuePage `certainty`)


def band(p: float, second: float | None = None) -> str:
    """How sure the model is, as the word the card shows: the same thresholds as the web's `certainty`."""
    second = second or 0.
    if p >= 0.95: return 'sure'
    if p - second < 0.15 and second > 0.05: return 'close'
    if p >= 0.7: return 'likely'
    return 'unsure'


@dataclass(frozen=True)
class Filters:
    sort: str = 'certain'
    auto: str = ''          # '' any | skip: leave out matches the model accepted on its own | only: just those
    kind: str = ''          # a listen kind (book, radio_series, ...)
    status: str = ''        # a listen status
    certainty: str = ''     # one of BANDS
    audible: str = ''       # '' any | yes: the proposed work has an Audible recording | no: it has none
    check: str = ''         # '' any | review: the kind (book or not) is a low-confidence call | broadcast: classified as radio, play or programme
    q: str = ''             # every word must appear in the listen's title, author, narrator or folder, or the proposed work's title or authors

    def __post_init__(self):
        for name, ok in (('sort', SORTS), ('auto', ('', 'skip', 'only')), ('certainty', ('', *BANDS)), ('audible', ('', 'yes', 'no')), ('check', ('', 'review', 'broadcast'))):
            if getattr(self, name) not in ok: raise ValueError(f"{name} must be one of {', '.join(o for o in ok if o) or 'nothing'}")


def words(q: str) -> list[str]:
    return [w for w in re.split(r'\s+', q.lower().strip()) if w]


def matches(it: dict, f: Filters) -> bool:
    top = it['candidates'][0]
    if f.auto == 'skip' and top['auto']: return False
    if f.auto == 'only' and not top['auto']: return False
    if f.kind and it.get('kind') != f.kind: return False
    if f.status and it.get('status') != f.status: return False
    if f.check == 'review' and (it.get('kind_info') or {}).get('band') != 'review': return False
    if f.check == 'broadcast' and it.get('kind') in (None, 'book'): return False
    if f.certainty and band(top['p'], top.get('second')) != f.certainty: return False
    if f.audible and bool(top.get('recordings')) != (f.audible == 'yes'): return False
    if f.q:
        hay = ' '.join([it.get('text') or '', *(' '.join([c.get('title') or '', *c.get('authors', [])]) for c in it['candidates'])]).lower()
        if not all(w in hay for w in words(f.q)): return False
    return True


def key(it: dict, sort: str) -> float:
    """The number the order is by (ties go to the listen id; the newest listen first when sorting by recency, since a whole build is added at once)."""
    return {'certain': -it['p'], 'uncertain': it['p'], 'recent': -(it.get('added') or 0.), 'oldest': it.get('added') or 0.}[sort]


def order(it: dict, sort: str) -> tuple[float, int]:
    return key(it, sort), -it['listen_id'] if sort == 'recent' else it['listen_id']


def cursor(it: dict, sort: str) -> str:
    return f"{key(it, sort)!r}:{it['listen_id']}"


def parse_cursor(after: str, sort: str) -> tuple[float, int]:
    """The order of the item a cursor names; ValueError when it is not `key:listen_id`."""
    k, _, lid = after.rpartition(':')
    k, lid = float(k), int(lid)
    return k, -lid if sort == 'recent' else lid


def select(items: list[dict], f: Filters) -> list[dict]:
    """The items that pass the filters, in the chosen order."""
    return sorted((it for it in items if matches(it, f)), key=lambda it: order(it, f.sort))


def after(items: list[dict], cur: str, sort: str) -> list[dict]:
    """The items (already in `sort` order) that come after the cursor; ValueError for a cursor that is not `key:listen_id`."""
    start = parse_cursor(cur, sort)
    return [it for it in items if order(it, sort) > start]


def facets(items: list[dict]) -> dict:
    """How many items each filter value would show, over the whole queue, so the page offers only values that exist and says how many each holds."""
    out: dict[str, dict[str, int]] = dict(kind={}, status={}, certainty={}, auto={'skip': 0, 'only': 0}, audible={'yes': 0, 'no': 0}, check={'review': 0, 'broadcast': 0})
    for it in items:
        top = it['candidates'][0]
        out['check']['review'] += (it.get('kind_info') or {}).get('band') == 'review'; out['check']['broadcast'] += it.get('kind') not in (None, 'book')
        for name, value in (('kind', it.get('kind')), ('status', it.get('status')), ('certainty', band(top['p'], top.get('second'))), ('auto', 'only' if top['auto'] else 'skip'), ('audible', 'yes' if top.get('recordings') else 'no')):
            if value: out[name][value] = out[name].get(value, 0) + 1
    return out
