"""Write identification answers to listens, and link listens of one work.

For each listen the model's posterior over its candidate works decides one of:
* **accept** (best work at least `threshold`): the work is found or created (`work` with its Open Library work keys, ISBNs and Audible ASINs as identifiers, so a second listen of the same book finds the same row) and linked as a `match` listen-part in state `accepted`, linked by the model, with the probability, the runner-up, P(none), the comparison vector and the closest recording in `detail`; status `identified`.
* **review**: the top few works are linked as `candidate` parts (ordinal = rank) and the status is `review`; a person accepts or rejects them, and those decisions become labels for the next fit.
* **none** (very likely none of the candidates): no parts, status `review` with that reason.
A listen with a match a person linked or decided is left alone. Rerunning replaces only the model's own parts, and only when the answer changed, so the event log records real changes. A rejection stays linked with its ordinal, so the replacement parts are numbered after it."""

from __future__ import annotations

import os
import re

from sqlalchemy import select, text
from sqlalchemy.orm import Session

from app.identify import textkeys as tk
from app.linkage import model as M
from app.store import listens as L
from app.store.filegroups import finish_run, new_run
from app.store.models import Listen, ListenEvent, ListenPart, PartFilegroup, Person, Work, WorkCredit, WorkIdentifier

THRESHOLD = 0.95
REVIEW_CANDIDATES = 3
REVIEW_FLOOR = 0.02


def identifiers(summary: dict) -> list[tuple[str, str]]:
    """`work` (the cluster's key: its most-published Open Library work, or `asin:...` for an Audible-only work) is what identifies the row; the other Open Library works of the cluster (duplicate records), its ASINs and ISBNs are kept as further identifiers but never used to find the row, because neighbouring clusters can share a stray record."""
    ids = [("work", summary["key"])] + [("ol_work", k) for k in summary.get("ol") or []]
    ids += [("asin", a) for a in summary.get("asins") or []] + [("isbn", i) for i in summary.get("isbns") or []]
    ids += [
        ("bbc_pid" if p["source"] == "bbc_radio" else "wikidata", p["ref"]) for p in summary.get("programmes") or []
    ]  # a radio programme: its BBC pid and Wikidata item
    return list(dict.fromkeys(ids))


def work_for(s: Session, summary: dict) -> Work:
    """The `work` row for a candidate cluster: the existing one with the same key, else a new one with its identifiers and authors. Identifiers learned later are added to the existing row."""
    kind = "radio_programme" if summary["key"].startswith("radio:") else "written"
    w = s.scalars(
        select(Work)
        .join(WorkIdentifier, WorkIdentifier.work_id == Work.id)
        .where(WorkIdentifier.scheme == "work", WorkIdentifier.value == summary["key"], Work.kind == kind)
    ).first()
    if w:
        have = {(i.scheme, i.value) for i in s.scalars(select(WorkIdentifier).where(WorkIdentifier.work_id == w.id))}
        for sc, v in identifiers(summary):
            if (sc, v) not in have:
                s.add(WorkIdentifier(work_id=w.id, scheme=sc, value=v))
        s.flush()
        return w
    w = Work(kind=kind, title=summary["title"], year=summary.get("year"))
    s.add(w)
    s.flush()
    for sc, v in identifiers(summary):
        s.add(WorkIdentifier(work_id=w.id, scheme=sc, value=v))
    for pos, name in enumerate(dict.fromkeys(summary.get("authors") or [])):
        key = tk.person_key(name)
        if not key or not tk.people(name):
            continue
        p = s.scalars(select(Person).where(Person.key == key)).first()
        if p is None:
            p = Person(name=name, key=key)
            s.add(p)
            s.flush()
        if not s.scalars(
            select(WorkCredit).where(
                WorkCredit.work_id == w.id, WorkCredit.person_id == p.id, WorkCredit.role == "author"
            )
        ).first():
            s.add(WorkCredit(work_id=w.id, person_id=p.id, role="author", position=pos))
            s.flush()
    s.flush()
    return w


def clear(s: Session, ids: set[int] | None = None) -> int:
    """Remove everything identification wrote (its listen-parts and their events, the works only it referenced) and put the listens back to `grouped`; a person's decisions are kept. Identification is derived data, like the listens themselves."""
    from sqlalchemy import delete, update

    keep = {
        lid
        for (lid,) in s.execute(
            select(ListenPart.listen_id).where(
                ListenPart.role.in_(("match", "candidate")), ListenPart.linked_by == "user"
            )
        )
    }
    keep |= {
        lid
        for (lid,) in s.execute(
            select(ListenEvent.listen_id)
            .join(ListenPart, ListenPart.id == ListenEvent.listen_part_id)
            .where(ListenPart.role.in_(("match", "candidate")), ListenEvent.actor_kind == "user")
        )
    }
    parts = [
        p
        for p in s.scalars(select(ListenPart).where(ListenPart.role.in_(("match", "candidate"))))
        if p.listen_id not in keep and (ids is None or p.listen_id in ids)
    ]  # `ids`: only these listens
    ids = [p.id for p in parts]
    if ids:
        s.execute(delete(ListenEvent).where(ListenEvent.listen_part_id.in_(ids)))
        s.execute(delete(ListenPart).where(ListenPart.id.in_(ids)))
    touched = {p.listen_id for p in parts}
    s.execute(
        delete(ListenEvent).where(
            ListenEvent.action == "status_change",
            ListenEvent.actor_kind.in_(("model", "auto_policy")),
            ListenEvent.listen_id.in_(touched),
        )
    )
    s.execute(update(Listen).where(Listen.id.in_(touched)).values(status="grouped"))
    used = select(ListenPart.work_id).where(ListenPart.work_id.is_not(None))
    orphans = [
        w
        for (w,) in s.execute(
            select(Work.id).where(Work.kind.in_(("written", "radio_programme")), Work.id.not_in(used))
        )
    ]
    if orphans:
        s.execute(delete(WorkCredit).where(WorkCredit.work_id.in_(orphans)))
        s.execute(delete(WorkIdentifier).where(WorkIdentifier.work_id.in_(orphans)))
        s.execute(delete(Work).where(Work.id.in_(orphans)))
    s.flush()
    return len(touched)


def _detail(key, p, post, none, used, clusters) -> dict:
    second = sorted(post.values(), reverse=True)[1] if len(post) > 1 else 0.0
    v = used.get(key) or {}
    c = clusters.get(key) or {}
    return dict(
        candidate=key,
        p=round(p, 4),
        second=round(second, 4),
        p_none=round(none, 4),
        vector={k: x for k, x in v.items() if not k.startswith("_")},
        alias=v.get("_alias"),
        reading=v.get("_reading"),
        recording=v.get("_asin"),
        title=c.get("title"),
        authors=c.get("authors"),
        year=c.get("year"),
        editions=c.get("editions"),
        ol=(c.get("ol") or [])[:5],
        isbns=(c.get("isbns") or [])[:5],
        recordings=(c.get("recordings") or [])[:6],
        programmes=(c.get("programmes") or [])[:4],
    )  # what the review card shows, so it needs no index


def apply(s: Session, data: dict, params: M.Params, *, threshold: float = THRESHOLD, ids=None) -> dict[str, int]:
    """Decide and write every listen in `data` ({listen_id: vectors}); returns counts per outcome."""
    _one_run_at_a_time(s)
    run = new_run(s, "identify", {"model": params.version(), "threshold": threshold})
    ref = f"{params.version()}@{threshold}"
    counts = dict(accept=0, review=0, none=0, kept=0, unchanged=0)
    for lid, d in data.items():
        if ids and int(lid) not in ids:
            continue
        listen = s.get(Listen, int(lid))
        if listen is None or listen.merged_into_id is not None:
            continue
        if listen.status == NOT_FOUND:
            counts["kept"] += 1
            continue  # a person said it is none of the candidates
        everything = list(
            s.scalars(
                select(ListenPart).where(
                    ListenPart.listen_id == listen.id,
                    ListenPart.role.in_(("match", "candidate")),
                    ListenPart.unlinked_at.is_(None),
                )
            )
        )
        by_user = person_decisions(s, everything)
        if any(p.state == "accepted" for p in everything if p.id in by_user or p.linked_by == "user"):
            counts["kept"] += 1
            continue  # a person approved a work: never replaced
        rejected = {(p.detail or {}).get("candidate") for p in everything if p.state == "rejected"}
        parts = [
            p for p in everything if p.state != "rejected"
        ]  # the model's own current answer; rejections stay linked as the record of the decision
        d = dict(
            d, vectors={k: v for k, v in d["vectors"].items() if k not in rejected}
        )  # a rejected work is out of the running; the rest are re-decided
        post, none, used = M.posterior(params, d["vectors"])
        decision, key = M.decide(post, none, threshold)
        want = (
            [(key, "accepted")]
            if decision == "accept"
            else [
                (k, "candidate")
                for k, p in sorted(post.items(), key=lambda kv: -kv[1])[:REVIEW_CANDIDATES]
                if p >= REVIEW_FLOOR
            ]
            if decision == "review"
            else []
        )
        have = [((p.detail or {}).get("candidate"), p.state) for p in sorted(parts, key=lambda p: p.ordinal or 0)]
        status = "identified" if decision == "accept" else "review"
        if (
            have == want
            and listen.status == status
            and all(abs((p.score or 0) - post.get((p.detail or {}).get("candidate"), 0)) < 0.01 for p in parts)
        ):
            for p in (
                parts
            ):  # same answer: only the evidence shown with it is refreshed (no event; the decision did not change)
                fresh = _detail(p.detail["candidate"], post[p.detail["candidate"]], post, none, used, d["clusters"])
                if fresh != p.detail:
                    p.detail = fresh
            counts["unchanged"] += 1
            continue
        for p in parts:
            L.unlink(s, p, actor_kind="model", actor_ref=ref, run_id=run.id, reason="replaced by a new identification")
        held = {}  # rejections stay linked with their ordinals and the active ordinal is unique per role, so new parts are numbered after them (still in rank order)
        for p in everything:
            if p.state == "rejected" and p.ordinal is not None:
                held[p.role] = max(held.get(p.role, 0), p.ordinal)
        for k, state in want:
            role = "match" if state == "accepted" else "candidate"
            held[role] = n = held.get(role, 0) + 1
            work = work_for(s, d["clusters"][k])
            part = L.link(
                s,
                listen,
                role,
                work_id=work.id,
                ordinal=n,
                state="candidate",
                score=round(post[k], 4),
                detail=_detail(k, post[k], post, none, used, d["clusters"]),
                actor_kind="model",
                actor_ref=ref,
                run_id=run.id,
                reason=f"P={post[k]:.3f} of being this work"
                + (f" (runner-up {sorted(post.values(), reverse=True)[1]:.3f})" if len(post) > 1 else ""),
            )
            if state == "accepted":
                L.set_state(
                    s,
                    part,
                    "accepted",
                    actor_kind="auto_policy",
                    actor_ref=ref,
                    run_id=run.id,
                    reason=f"P={post[k]:.3f} >= {threshold}",
                    after=dict(
                        score=round(post[k], 4), p_none=round(none, 4), model=params.version(), threshold=threshold
                    ),
                )
        L.set_status(
            s,
            listen,
            status,
            actor_kind="auto_policy" if decision == "accept" else "model",
            actor_ref=ref,
            run_id=run.id,
            reason="identified"
            if decision == "accept"
            else "probably none of the candidates"
            if decision == "none"
            else "not certain enough: review the candidates",
        )
        counts[decision] += 1
    finish_run(s, run, "done")
    return counts


NOT_FOUND = "not_found"


def _one_run_at_a_time(s: Session) -> None:
    """Two runs over the same listens (the CLI while the web's Learning runs, say) would each unlink and relink the same parts and collide on the ordinals or double the event log, so a run holds a transaction-scoped Postgres advisory lock until it commits; a second run is refused rather than queued. The web's own thread lock (`service`) only covers one process."""
    if s.get_bind().dialect.name != "postgresql":
        return
    if not s.scalar(text("select pg_try_advisory_xact_lock(hashtext('listory.identify'))")):
        raise RuntimeError("another identification run is in progress")


def person_decisions(s: Session, parts: list[ListenPart]) -> set[int]:
    """Ids of the parts a person accepted, confirmed or rejected."""
    if not parts:
        return set()
    return {
        pid
        for (pid,) in s.execute(
            select(ListenEvent.listen_part_id).where(
                ListenEvent.listen_part_id.in_([p.id for p in parts]),
                ListenEvent.actor_kind == "user",
                ListenEvent.action.in_(("accept", "reject")),
            )
        )
    }


def decide(s: Session, listen: Listen, part: ListenPart | None, action: str, *, actor_ref: str = "web") -> None:
    """A person's answer on the match queue. `approve`: this work is the listen's (an automatic acceptance becomes a confirmed one); the other candidates are unlinked. `reject`: not this work; the listen goes back to review and the next model run decides among the rest. `none`: none of the candidates (all rejected, status `not_found`, left alone by the model)."""
    others = list(
        s.scalars(
            select(ListenPart).where(
                ListenPart.listen_id == listen.id,
                ListenPart.role.in_(("match", "candidate")),
                ListenPart.unlinked_at.is_(None),
                ListenPart.state != "rejected",
            )
        )
    )
    if action == "approve":
        if part is None or part.listen_id != listen.id:
            raise ValueError("approve needs one of this listen's candidates")
        if part.state == "accepted":
            L._event(
                s,
                listen,
                "accept",
                "user",
                part=part,
                actor_ref=actor_ref,
                reason="confirmed by a person",
                after={"state": "accepted", "confirmed": True},
            )
        else:
            L.set_state(s, part, "accepted", actor_kind="user", actor_ref=actor_ref, reason="approved by a person")
        for o in others:
            if o.id != part.id:
                L.unlink(s, o, actor_kind="user", actor_ref=actor_ref, reason="another work was approved")
        L.set_status(s, listen, "identified", actor_kind="user", actor_ref=actor_ref, reason="approved by a person")
    elif action == "reject":
        if part is None or part.listen_id != listen.id:
            raise ValueError("reject needs one of this listen's candidates")
        L.set_state(s, part, "rejected", actor_kind="user", actor_ref=actor_ref, reason="not this work")
        L.set_status(s, listen, "review", actor_kind="user", actor_ref=actor_ref, reason="a candidate was rejected")
    elif action == "none":
        for o in others:
            L.set_state(s, o, "rejected", actor_kind="user", actor_ref=actor_ref, reason="none of the candidates")
        L.set_status(
            s, listen, NOT_FOUND, actor_kind="user", actor_ref=actor_ref, reason="none of the candidates is this listen"
        )
    else:
        raise ValueError(f"unknown action {action}")
    s.flush()


def same_work_groups(s: Session) -> dict[int, list[int]]:
    """{work id: [listen ids]} for works more than one live listen is accepted as: different recordings or copies of one book."""
    rows = s.execute(
        select(ListenPart.work_id, ListenPart.listen_id)
        .join(Listen, Listen.id == ListenPart.listen_id)
        .where(
            ListenPart.role == "match",
            ListenPart.state == "accepted",
            ListenPart.unlinked_at.is_(None),
            Listen.merged_into_id.is_(None),
        )
    )
    out: dict[int, list[int]] = {}
    for w, lid in rows:
        out.setdefault(w, []).append(lid)
    return {w: sorted(set(ls)) for w, ls in out.items() if len(set(ls)) > 1}


def listen_minutes(s: Session, lid: int) -> tuple[ListenPart | None, float]:
    """(preferred input part, runtime): a listen of parts runs as long as all its parts together, a listen of copies as long as its preferred copy."""
    parts = list(
        s.scalars(
            select(ListenPart)
            .where(ListenPart.listen_id == lid, ListenPart.role == "input", ListenPart.unlinked_at.is_(None))
            .order_by(ListenPart.ordinal)
        )
    )
    if not parts:
        return None, 0.0
    if any(
        (p.detail or {}).get("relation") == "part" for p in parts
    ):  # the parts count once, as their sum; alternative copies lying beside them do not add to it
        return parts[0], sum(
            float((p.detail or {}).get("minutes") or 0) for p in parts if (p.detail or {}).get("relation") == "part"
        )
    return parts[0], float((parts[0].detail or {}).get("minutes") or 0)


def merge_parts(
    s: Session, *, tolerance: float = 0.12, minimum: float = 0.5, actor_ref: str = ""
) -> list[tuple[int, int]]:
    """Join listens that are the numbered parts of one recording into one listen with ordered input parts (`detail.relation` = "part"), once their work is known: each listen's best candidate is the same work (P >= `minimum`), their group names differ only by a number (`build.part_key`, `build.numbering`), each is clearly shorter than the book, and their runtimes add up to one of the work's recordings within `tolerance` (12%: another edition or narration runs a few percent longer or shorter, and the name evidence is strong) (12%: another edition or narration of a book runs a few percent longer or shorter, and the name evidence is strong). Not copies: copies share a runtime, parts sum to it."""
    from app.store.build import numbering, part_key

    run = new_run(s, "merge_parts", {"tolerance": tolerance})
    best: dict[int, ListenPart] = {}
    for p in s.scalars(
        select(ListenPart)
        .join(Listen, Listen.id == ListenPart.listen_id)
        .where(
            ListenPart.role.in_(("match", "candidate")),
            ListenPart.state != "rejected",
            ListenPart.unlinked_at.is_(None),
            Listen.merged_into_id.is_(None),
            Listen.kind == "book",
            ListenPart.score >= minimum,
        )
    ):
        if p.listen_id not in best or (p.score or 0) > (best[p.listen_id].score or 0):
            best[p.listen_id] = p
    by_work: dict[int, list[int]] = {}
    for lid, p in best.items():
        by_work.setdefault(p.work_id, []).append(lid)
    out = []
    for work_id, ids in by_work.items():
        keyed: dict[str, list[tuple[tuple[str, ...], int, ListenPart, float]]] = {}
        chapters: dict[
            str, list[tuple[tuple[str, ...], int, ListenPart, float]]
        ] = {}  # numbered folders with titles of their own under one parent: 01 The Arrest, 02 Sorry...
        for lid in sorted(ids):
            inputs = list(
                s.scalars(
                    select(ListenPart)
                    .where(ListenPart.listen_id == lid, ListenPart.role == "input", ListenPart.unlinked_at.is_(None))
                    .order_by(ListenPart.ordinal)
                )
            )
            if not inputs or any((x.detail or {}).get("relation") == "part" or not x.filegroup_id for x in inputs):
                continue
            labels = [
                s.scalar(select(PartFilegroup.label).where(PartFilegroup.id == x.filegroup_id)) or "" for x in inputs
            ]
            if len(inputs) == 1 and (lead := re.match(r"\s*(\d{1,3})\b", labels[0])) and inputs[0].detail:
                path = s.scalar(select(PartFilegroup.path).where(PartFilegroup.id == inputs[0].filegroup_id)) or ""
                chapters.setdefault(os.path.dirname(path), []).append(
                    ((str(int(lead.group(1))),), lid, inputs[0], float(inputs[0].detail.get("minutes") or 0))
                )
            keys = {part_key(label) for label in labels}
            if len(keys) != 1 or "" in keys or not all(numbering(label) for label in labels):
                continue  # a listen of several inputs is looked at as a whole: all of them numbered, all one name (merged as copies earlier, they are converted in place)
            for x, label in zip(inputs, labels):
                keyed.setdefault(part_key(label), []).append(
                    (numbering(label), lid, x, float((x.detail or {}).get("minutes") or 0))
                )
        sequences = []
        for g in chapters.values():
            numbers = sorted(int(n[0]) for n, *_ in g)
            if len(g) >= 3 and numbers == list(range(numbers[0], numbers[0] + len(g))):
                sequences.append(
                    g
                )  # at least three, numbered one after the other, in one folder: the chapters of one book, if the runtimes below agree
        for items in [*keyed.values(), *sequences]:
            if len(items) < 2 or len({n for n, *_ in items}) != len(items):
                continue
            if any(s.get(Listen, lid).merged_into_id is not None for _, lid, _, _ in items):
                continue  # already joined by the other grouping above (the same listens can be a keyed group and a chapter sequence)
            total = sum(m for *_, m in items)
            runtimes = [
                float(r.get("minutes") or 0) for r in ((best[items[0][1]].detail or {}).get("recordings") or [])
            ]
            if not total or not any(
                r and abs(total - r) / r <= tolerance and all(m < 0.8 * r for *_, m in items) for r in runtimes
            ):
                continue
            items.sort(key=lambda x: (tuple(int(v) for v in x[0]), x[1]))
            survivor = s.get(Listen, items[0][1])
            for lid in dict.fromkeys(i[1] for i in items):
                if lid == survivor.id:
                    continue
                L.merge(
                    s,
                    survivor,
                    s.get(Listen, lid),
                    actor_kind="model",
                    actor_ref=actor_ref,
                    run_id=run.id,
                    reason=f"parts of one recording of work {work_id}: numbered like parts, runtimes add up to {total:.0f} min",
                )
                out.append((survivor.id, lid))
            if not any(lid != survivor.id for _, lid, _, _ in items):
                out.append(
                    (survivor.id, survivor.id)
                )  # one listen whose inputs were taken for copies: converted in place
            order = [i[2].id for i in items]
            for part in s.scalars(select(ListenPart).where(ListenPart.id.in_(order))):
                part.detail = {**(part.detail or {}), "relation": "part"}
            s.flush()
            L.reorder(
                s, survivor, "input", order, actor_kind="model", actor_ref=actor_ref, reason="parts in listening order"
            )
            L.set_status(
                s,
                survivor,
                "review",
                actor_kind="model",
                actor_ref=actor_ref,
                run_id=run.id,
                reason="parts joined: identify again with the whole runtime",
            )
    finish_run(s, run, "done")
    return out


def merge_copies(s: Session, *, tolerance: float = 0.02, actor_ref: str = "") -> list[tuple[int, int]]:
    """Merge listens that are copies of one recording, now that their work is known: both accepted as the same work, runtimes within `tolerance` (2%, half the tolerance used before identification) and narrators that do not disagree. Different recordings of one work (another narrator, an abridgement, a radio version) have different runtimes and stay separate listens linked to the same work. The preferred copy survives (`store.build.preference`); returns [(survivor, merged)]."""
    from app.store.build import preference
    from app.store.models import ListenChoice

    run = new_run(s, "merge_copies", {"tolerance": tolerance})
    out = []
    radio = {w for (w,) in s.execute(select(Work.id).where(Work.kind == "radio_programme"))}
    for work_id, ids in same_work_groups(s).items():
        if work_id in radio:
            continue  # different episodes and series of one programme are different broadcasts, never copies of one recording (as `build.link_copies` leaves radio series alone)
        info = {}
        for lid in ids:
            part, total = listen_minutes(s, lid)
            narr = s.scalars(
                select(ListenChoice.value).where(
                    ListenChoice.listen_id == lid, ListenChoice.field == "narrator", ListenChoice.unchosen_at.is_(None)
                )
            ).first()
            if part and total:
                info[lid] = (part, total, tk.people(narr or ""))
        live = sorted(info, key=lambda lid: preference(info[lid][0], s.get(Listen, lid).label))
        while live:
            keep = live.pop(0)
            _, minutes, narr = info[keep]
            rest = []
            for other in live:
                _, m2, n2 = info[other]
                if abs(m2 - minutes) / max(minutes, 1) <= tolerance and not (
                    narr and n2 and not any(tk.person_key(a) == tk.person_key(b) for a in narr for b in n2)
                ):
                    L.merge(
                        s,
                        s.get(Listen, keep),
                        s.get(Listen, other),
                        actor_kind="model",
                        actor_ref=actor_ref,
                        run_id=run.id,
                        reason=f"a copy of the same recording: both identified as work {work_id}, runtimes {minutes:.0f} and {m2:.0f} min",
                    )
                    out.append((keep, other))
                else:
                    rest.append(other)
            live = rest
    finish_run(s, run, "done")
    return out
