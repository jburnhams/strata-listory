"""Write identification answers to listens, and link listens of one work.

For each listen the model's posterior over its candidate works decides one of:
* **accept** (best work at least `threshold`): the work is found or created (`work` with its Open Library work keys, ISBNs and Audible ASINs as identifiers, so a second listen of the same book finds the same row) and linked as a `match` listen-part in state `accepted`, linked by the model, with the probability, the runner-up, P(none), the comparison vector and the closest recording in `detail`; status `identified`.
* **review**: the top few works are linked as `candidate` parts (ordinal = rank) and the status is `review`; a person accepts or rejects them, and those decisions become labels for the next fit.
* **none** (very likely none of the candidates): no parts, status `review` with that reason.
A listen with a match a person linked or decided is left alone. Rerunning replaces only the model's own parts, and only when the answer changed, so the event log records real changes."""
from __future__ import annotations

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.identify import textkeys as tk
from app.store import listens as L
from app.store.filegroups import finish_run, new_run
from app.store.models import Listen, ListenEvent, ListenPart, Person, Work, WorkCredit, WorkIdentifier
from app.linkage import model as M

THRESHOLD = .95
REVIEW_CANDIDATES = 3
REVIEW_FLOOR = .02


def identifiers(summary: dict) -> list[tuple[str, str]]:
    """`work` (the cluster's key: its most-published Open Library work, or `asin:...` for an Audible-only work) is what identifies the row; the other Open Library works of the cluster (duplicate records), its ASINs and ISBNs are kept as further identifiers but never used to find the row, because neighbouring clusters can share a stray record."""
    ids = [('work', summary['key'])] + [('ol_work', k) for k in summary.get('ol') or []]
    ids += [('asin', a) for a in summary.get('asins') or []] + [('isbn', i) for i in summary.get('isbns') or []]
    return list(dict.fromkeys(ids))


def work_for(s: Session, summary: dict) -> Work:
    """The `work` row for a candidate cluster: the existing one with the same key, else a new one with its identifiers and authors. Identifiers learned later are added to the existing row."""
    w = s.scalars(select(Work).join(WorkIdentifier, WorkIdentifier.work_id == Work.id).where(WorkIdentifier.scheme == 'work', WorkIdentifier.value == summary['key'], Work.kind == 'written')).first()
    if w:
        have = {(i.scheme, i.value) for i in s.scalars(select(WorkIdentifier).where(WorkIdentifier.work_id == w.id))}
        for sc, v in identifiers(summary):
            if (sc, v) not in have: s.add(WorkIdentifier(work_id=w.id, scheme=sc, value=v))
        s.flush(); return w
    w = Work(kind='written', title=summary['title'], year=summary.get('year')); s.add(w); s.flush()
    for sc, v in identifiers(summary): s.add(WorkIdentifier(work_id=w.id, scheme=sc, value=v))
    for pos, name in enumerate(dict.fromkeys(summary.get('authors') or [])):
        key = tk.person_key(name)
        if not key or not tk.people(name): continue
        p = s.scalars(select(Person).where(Person.key == key)).first()
        if p is None: p = Person(name=name, key=key); s.add(p); s.flush()
        if not s.scalars(select(WorkCredit).where(WorkCredit.work_id == w.id, WorkCredit.person_id == p.id, WorkCredit.role == 'author')).first(): s.add(WorkCredit(work_id=w.id, person_id=p.id, role='author', position=pos)); s.flush()
    s.flush(); return w


def clear(s: Session) -> int:
    """Remove everything identification wrote (its listen-parts and their events, the works only it referenced) and put the listens back to `grouped`; a person's decisions are kept. Identification is derived data, like the listens themselves."""
    from sqlalchemy import delete, update
    keep = {lid for (lid,) in s.execute(select(ListenPart.listen_id).where(ListenPart.role.in_(('match', 'candidate')), ListenPart.linked_by == 'user'))}
    keep |= {lid for (lid,) in s.execute(select(ListenEvent.listen_id).join(ListenPart, ListenPart.id == ListenEvent.listen_part_id).where(ListenPart.role.in_(('match', 'candidate')), ListenEvent.actor_kind == 'user'))}
    parts = [p for p in s.scalars(select(ListenPart).where(ListenPart.role.in_(('match', 'candidate')))) if p.listen_id not in keep]
    ids = [p.id for p in parts]
    if ids:
        s.execute(delete(ListenEvent).where(ListenEvent.listen_part_id.in_(ids))); s.execute(delete(ListenPart).where(ListenPart.id.in_(ids)))
    touched = {p.listen_id for p in parts}
    s.execute(delete(ListenEvent).where(ListenEvent.action == 'status_change', ListenEvent.actor_kind.in_(('model', 'auto_policy')), ListenEvent.listen_id.in_(touched)))
    s.execute(update(Listen).where(Listen.id.in_(touched)).values(status='grouped'))
    used = select(ListenPart.work_id).where(ListenPart.work_id.is_not(None))
    orphans = [w for (w,) in s.execute(select(Work.id).where(Work.kind == 'written', Work.id.not_in(used)))]
    if orphans:
        s.execute(delete(WorkCredit).where(WorkCredit.work_id.in_(orphans))); s.execute(delete(WorkIdentifier).where(WorkIdentifier.work_id.in_(orphans))); s.execute(delete(Work).where(Work.id.in_(orphans)))
    s.flush(); return len(touched)


def _detail(key, p, post, none, used, clusters) -> dict:
    second = sorted(post.values(), reverse=True)[1] if len(post) > 1 else 0.
    v = used.get(key) or {}; c = clusters.get(key) or {}
    return dict(candidate=key, p=round(p, 4), second=round(second, 4), p_none=round(none, 4), vector={k: x for k, x in v.items() if not k.startswith('_')}, alias=v.get('_alias'), reading=v.get('_reading'), recording=v.get('_asin'), title=c.get('title'), authors=c.get('authors'), year=c.get('year'))


def apply(s: Session, data: dict, params: M.Params, *, threshold: float = THRESHOLD, ids=None) -> dict[str, int]:
    """Decide and write every listen in `data` ({listen_id: vectors}); returns counts per outcome."""
    run = new_run(s, 'identify', {'model': params.version(), 'threshold': threshold}); ref = f'{params.version()}@{threshold}'
    counts = dict(accept=0, review=0, none=0, kept=0, unchanged=0)
    for lid, d in data.items():
        if ids and int(lid) not in ids: continue
        listen = s.get(Listen, int(lid))
        if listen is None or listen.merged_into_id is not None: continue
        parts = list(s.scalars(select(ListenPart).where(ListenPart.listen_id == listen.id, ListenPart.role.in_(('match', 'candidate')), ListenPart.unlinked_at.is_(None))))
        decided = parts and s.scalars(select(ListenEvent.id).where(ListenEvent.listen_part_id.in_([p.id for p in parts]), ListenEvent.actor_kind == 'user')).first() is not None
        if decided or any(p.linked_by == 'user' or p.state == 'rejected' for p in parts): counts['kept'] += 1; continue      # a person has decided: never replaced
        post, none, used = M.posterior(params, d['vectors'])
        decision, key = M.decide(post, none, threshold)
        want = [(key, 'accepted')] if decision == 'accept' else [(k, 'candidate') for k, p in sorted(post.items(), key=lambda kv: -kv[1])[:REVIEW_CANDIDATES] if p >= REVIEW_FLOOR] if decision == 'review' else []
        have = [((p.detail or {}).get('candidate'), p.state) for p in sorted(parts, key=lambda p: p.ordinal or 0)]
        status = 'identified' if decision == 'accept' else 'review'
        if have == want and listen.status == status and all(abs((p.score or 0) - post.get((p.detail or {}).get('candidate'), 0)) < .01 for p in parts): counts['unchanged'] += 1; continue
        for p in parts: L.unlink(s, p, actor_kind='model', actor_ref=ref, run_id=run.id, reason='replaced by a new identification')
        for n, (k, state) in enumerate(want, 1):
            work = work_for(s, d['clusters'][k])
            part = L.link(s, listen, 'match' if state == 'accepted' else 'candidate', work_id=work.id, ordinal=n, state='candidate', score=round(post[k], 4), detail=_detail(k, post[k], post, none, used, d['clusters']), actor_kind='model', actor_ref=ref, run_id=run.id,
                          reason=f'P={post[k]:.3f} of being this work' + (f' (runner-up {sorted(post.values(), reverse=True)[1]:.3f})' if len(post) > 1 else ''))
            if state == 'accepted': L.set_state(s, part, 'accepted', actor_kind='auto_policy', actor_ref=ref, run_id=run.id, reason=f'P={post[k]:.3f} >= {threshold}', after=dict(score=round(post[k], 4), p_none=round(none, 4), model=params.version(), threshold=threshold))
        L.set_status(s, listen, status, actor_kind='auto_policy' if decision == 'accept' else 'model', actor_ref=ref, run_id=run.id,
                     reason='identified' if decision == 'accept' else 'probably none of the candidates' if decision == 'none' else 'not certain enough: review the candidates')
        counts[decision] += 1
    finish_run(s, run, 'done'); return counts


def same_work_groups(s: Session) -> dict[int, list[int]]:
    """{work id: [listen ids]} for works more than one live listen is accepted as: different recordings or copies of one book."""
    rows = s.execute(select(ListenPart.work_id, ListenPart.listen_id).join(Listen, Listen.id == ListenPart.listen_id).where(ListenPart.role == 'match', ListenPart.state == 'accepted', ListenPart.unlinked_at.is_(None), Listen.merged_into_id.is_(None)))
    out: dict[int, list[int]] = {}
    for w, lid in rows: out.setdefault(w, []).append(lid)
    return {w: sorted(set(ls)) for w, ls in out.items() if len(set(ls)) > 1}


def merge_copies(s: Session, *, tolerance: float = .02, actor_ref: str = '') -> list[tuple[int, int]]:
    """Merge listens that are copies of one recording, now that their work is known: both accepted as the same work, runtimes within `tolerance` (2%, half the tolerance used before identification) and narrators that do not disagree. Different recordings of one work (another narrator, an abridgement, a radio version) have different runtimes and stay separate listens linked to the same work. The preferred copy survives (`store.build.preference`); returns [(survivor, merged)]."""
    from app.store.build import preference
    from app.store.models import ListenChoice
    run = new_run(s, 'merge_copies', {'tolerance': tolerance}); out = []
    for work_id, ids in same_work_groups(s).items():
        info = {}
        for lid in ids:
            part = s.scalars(select(ListenPart).where(ListenPart.listen_id == lid, ListenPart.role == 'input', ListenPart.unlinked_at.is_(None)).order_by(ListenPart.ordinal)).first()
            narr = s.scalars(select(ListenChoice.value).where(ListenChoice.listen_id == lid, ListenChoice.field == 'narrator', ListenChoice.unchosen_at.is_(None))).first()
            if part and (part.detail or {}).get('minutes'): info[lid] = (part, float(part.detail['minutes']), tk.people(narr or ''))
        live = sorted(info, key=lambda lid: preference(info[lid][0], s.get(Listen, lid).label))
        while live:
            keep = live.pop(0); _, minutes, narr = info[keep]; rest = []
            for other in live:
                _, m2, n2 = info[other]
                if abs(m2 - minutes) / max(minutes, 1) <= tolerance and not (narr and n2 and not any(tk.person_key(a) == tk.person_key(b) for a in narr for b in n2)):
                    L.merge(s, s.get(Listen, keep), s.get(Listen, other), actor_kind='model', actor_ref=actor_ref, run_id=run.id, reason=f'a copy of the same recording: both identified as work {work_id}, runtimes {minutes:.0f} and {m2:.0f} min'); out.append((keep, other))
                else: rest.append(other)
            live = rest
    finish_run(s, run, 'done'); return out
