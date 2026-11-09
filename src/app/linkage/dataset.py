"""Candidates and comparison vectors for every listen (the slow part, so it is cached as JSON), and the known answers used to train and measure the model.

Known answers come from (a) decisions made on listens in this catalogue (`match` parts linked by a user, accepted or rejected) and (b) the approvals of the earlier SQLite catalogue, matched to listens through their files' library-relative paths. Old automatic approvals were produced by the earlier rule engine, so they are weaker evidence than manual ones; `origin` keeps them apart in every measurement."""
from __future__ import annotations

import json
from pathlib import Path

from app import settings
from app.identify import candidates as olc, textkeys as tk
from app.linkage import candidates as C, compare as K
from app.linkage.model import Case
from app.linkage.query import Query, relpath

VERSION = 6          # bump when candidates or comparisons change: cached vectors of older versions are recomputed


def folder() -> Path: return Path(settings.data_dir()) / 'linkage'


def summary(cluster: C.Cluster) -> dict:
    """What review and apply need to know about a candidate work without the indexes."""
    return dict(key=cluster.key, title=cluster.title, authors=cluster.authors[:6], year=cluster.year, ol=[w['key'] for w in cluster.works], editions=sum(w['editions'] for w in cluster.works),
                asins=[p.asin for p in cluster.products][:40], recordings=[dict(asin=p.asin, title=p.title, narrators=p.narrators[:4], minutes=p.runtime, fmt=p.fmt, year=p.year) for p in sorted(cluster.products, key=lambda p: -p.ratings)[:12]],
                isbns=sorted({p.isbn for p in cluster.products if p.isbn})[:20],
                programmes=[dict(source=p.source, ref=p.ref, title=p.title, service=p.service or p.detail.get('service') or '', channel=p.detail.get('channel') or '', first_broadcast=p.first_broadcast or p.detail.get('first_broadcast') or '', type=p.detail.get('type') or '', episodes=p.detail.get('episodes'),
                             genres=(p.detail.get('genres') or [])[:3], synopsis=p.detail.get('synopsis') or '') for p in cluster.programmes][:6])


def vectors(oc, ac, q: Query, radio=C.radio_candidates) -> dict:
    """{'clusters': {key: summary}, 'vectors': {key: [vector per reading]}} for one listen."""
    found = C.for_query(oc, ac, q, radio)
    homonyms = {r.title: olc.homonyms(oc, r.title) if oc is not None else 0 for r in q.readings[:C.MAX_READINGS]}
    out = {'clusters': {}, 'vectors': {}}
    for c in found:
        vs = [K.compare(q, r, c, homonyms[r.title] if not c.programmes or c.works or c.products else len(c.programmes)) for r in q.readings[:C.MAX_READINGS]]
        vs = [v for v in vs if v['title'] is not None]
        if vs: out['clusters'][c.key] = summary(c); out['vectors'][c.key] = vs
    return out


def build(oc, ac, queries: list[Query], cache: dict | None = None, progress=None, indexes: str = '') -> dict:
    """{listen_id: vectors(...)}; listens already in `cache` with the same signature (their readings, the code `VERSION` and `indexes`, the snapshot identities of the indexes used) are reused, so a rebuilt index recomputes everything."""
    cache = cache or {}; out = {}
    for i, q in enumerate(queries):
        sig = signature(q, indexes); hit = cache.get(str(q.listen_id))
        out[str(q.listen_id)] = hit if hit and hit.get('sig') == sig else dict(vectors(oc, ac, q), sig=sig)
        if progress and i % 25 == 0: progress(i, len(queries))
    return out


def index_stamp(oc, ac) -> str:
    """Which indexes candidates came from: the Open Library build time and the Audible snapshot, or 'none' when one is missing."""
    from app.identify import audibleindex, olindex
    from app.identify import radioindex
    ri = radioindex.index_path(); dt = radioindex.detail_path()
    return f"ol:{olindex.status().get('built', '?') if oc is not None else 'none'}|audible:{audibleindex.snapshot(ac) if ac is not None else 'none'}|radio:{int(ri.stat().st_mtime) if ri.exists() else 'none'}:{int(dt.stat().st_mtime) if dt.exists() else 'none'}"


def signature(q: Query, indexes: str = '') -> str:
    return json.dumps([VERSION, indexes] + [[r.title, r.author] for r in q.readings[:C.MAX_READINGS]] + [q.narrators, q.asin, q.year, q.minutes, q.hints, q.band, q.channels])


def load_cache(name: str = 'vectors.json') -> dict:
    try: return json.loads((folder() / name).read_text(encoding='utf8'))
    except (OSError, ValueError): return {}


def save_cache(data: dict, name: str = 'vectors.json') -> None:
    folder().mkdir(parents=True, exist_ok=True); tmp = folder() / (name + '.tmp'); tmp.write_text(json.dumps(data), encoding='utf8'); tmp.replace(folder() / name)


def positive_key(clusters: dict[str, dict], target: dict, canonical=None) -> str | None:
    """The candidate cluster that is the known work: by Open Library work key (through redirects when `canonical` is given), else the same title key and a shared author."""
    if target.get('key') in clusters: return target['key']
    work = target.get('work')
    if work:
        work = canonical(work) if canonical else work
        for k, c in clusters.items():
            if work in c['ol']: return k
    want = {tk.title_key(target.get('title', '')), C.main_key(target.get('title', ''))} - {''}
    authors = tk.people(target.get('authors') or [])
    for k, c in clusters.items():
        if want & {tk.title_key(c['title']), C.main_key(c['title'])} and C.shares_author(authors, c['authors']): return k
    return None


def old_labels(runs: list[dict], files_by_listen: dict[int, set[str]], paths: dict[int, str]) -> dict[int, dict]:
    """Old SQLite approvals ({run: item.path, target, origin}) matched to listens: a run's path is the listen's folder or one of its files. Manual wins over automatic when several runs land on one listen."""
    by_file: dict[str, int] = {f: lid for lid, fs in files_by_listen.items() for f in fs}
    by_path: dict[str, int] = {relpath(p): lid for lid, p in paths.items() if p}
    out: dict[int, dict] = {}
    for r in runs:
        rp = relpath(r['item'].get('path', ''))
        lid = by_path.get(rp) or by_file.get(rp)
        if lid is None: continue
        if lid not in out or (r['origin'] == 'manual' and out[lid]['origin'] != 'manual'): out[lid] = dict(target=r['target'], origin=r['origin'])
    return out


def cases(data: dict, labels: dict[int, dict], canonical=None) -> tuple[list[Case], dict]:
    """Cases for the model, plus counts of how many labels found their work among the candidates (recall)."""
    out = []; stats = dict(labelled=0, found=0, missing=[])
    for lid, d in data.items():
        lab = labels.get(int(lid)); pos = None; neg: set[str] = set()
        if lab:
            stats['labelled'] += 1
            rejects = set(lab.get('rejects') or []) | ({lab['reject']} if lab.get('reject') else set())
            neg = {k for k in d['clusters'] if k in rejects or rejects & set(d['clusters'][k]['ol'])}
            if lab.get('none'): pos = 'none'
            elif lab.get('target'):
                pos = positive_key(d['clusters'], lab['target'], canonical)
                if pos in neg: pos = None                    # a person rejected what an older label said
                if pos: stats['found'] += 1
                else: stats['missing'].append(int(lid))
        out.append(Case(int(lid), d['vectors'], pos, neg, lab['origin'] if lab else ''))
    return out, stats


def catalogue_labels(s) -> dict[int, dict]:  # pragma: no cover - Postgres: tests/unit/store/test_linkage_store.py
    """Decisions people made on listens here (the match queue): an approved or confirmed work is the answer, rejected candidates are known wrong works, and "none of these" says the work is not among the candidates."""
    from sqlalchemy import select
    from app.store.models import Listen, ListenEvent, ListenPart
    out: dict[int, dict] = {}
    decided = select(ListenEvent.listen_part_id).where(ListenEvent.actor_kind == 'user', ListenEvent.action.in_(('accept', 'reject')))
    for p in s.scalars(select(ListenPart).where(ListenPart.role.in_(('match', 'candidate')), ListenPart.state.in_(('accepted', 'rejected')), (ListenPart.linked_by == 'user') | ListenPart.id.in_(decided))):
        d = p.detail or {}; lab = out.setdefault(p.listen_id, dict(origin='user', rejects=[]))
        if p.state == 'accepted' and p.unlinked_at is None: lab['target'] = dict(key=d.get('candidate'), work=(d.get('ol') or [None])[0], title=d.get('title', ''), authors=d.get('authors') or [])
        elif p.state == 'rejected' and d.get('candidate'): lab['rejects'].append(d['candidate'])
    for (lid,) in s.execute(select(Listen.id).where(Listen.status == 'not_found')): out.setdefault(lid, dict(origin='user', rejects=[]))['none'] = True
    return out


def all_labels(s) -> dict[int, dict]:  # pragma: no cover - Postgres: tests/unit/store/test_linkage_store.py
    """This catalogue's decisions, over the old catalogue's approvals (`old-labels.json`, made by `listens-identify-prepare --old-catalogue`)."""
    try: old = {int(k): v for k, v in json.loads((folder() / 'old-labels.json').read_text(encoding='utf8')).items()}
    except (OSError, ValueError): old = {}
    out = dict(old)
    for lid, lab in catalogue_labels(s).items():
        merged = {**old.get(lid, {}), **lab}
        if 'target' not in lab and lab.get('rejects') and 'target' in merged: merged['origin'] = old[lid]['origin']      # only rejections here: the old answer still stands unless rejected (checked in `cases`)
        out[lid] = merged
    return out
