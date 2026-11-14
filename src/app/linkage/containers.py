"""Anthologies kept as one folder of numbered books: find the listens that are really pieces of one larger work, and join them.

A folder `Author; The Complete X` holding `01 Book A`, `02 Book B`... is listened to one book at a time, so every piece matches its own book (rightly), yet the folder as a whole is a single audiobook of the collection. Per-listen matching never asks that question, so this one asks it per folder: the folder's pieces are read as one pseudo-listen (its name, the shared title and author of the pieces, the **sum** of their runtimes), matched with the same model, and the join is made when

* the work is accepted at the model's threshold with that summed runtime,
* the pieces add up to one of the work's recordings within `TOLERANCE` (2%), and
* every piece is clearly shorter than that recording (under `SHORTER`): a piece as long as the book is a copy, not a part.

Pieces that are already matched, even approved by a person, take part: a better match for the whole supersedes the sub-book matches (the person's approvals stay in the event log). A folder of separate books that merely has "Complete" in its name does not add up to any recording, so it is left alone. Sums that are close but not exact (`PROPOSE_TOLERANCE`) or whose name match is not certain enough are only reported, never joined."""

from __future__ import annotations

import os
import re
from dataclasses import dataclass, field

from sqlalchemy import select
from sqlalchemy.orm import Session

from app import scan
from app.identify import textkeys as tk
from app.linkage import apply as A
from app.linkage import dataset, query
from app.linkage import model as M
from app.store import listens as L
from app.store.filegroups import finish_run, new_run
from app.store.models import Listen, ListenChoice, ListenPart, PartFilegroup

TOLERANCE = 0.02
PROPOSE_TOLERANCE = 0.12
SHORTER = 0.8
MAX_PIECES = 40  # a folder of hundreds of books (a whole author or library) is not one work


@dataclass
class Piece:
    listen_id: int
    part_id: int
    label: str
    path: str
    minutes: float
    relation: str = ""


@dataclass
class Group:
    parent: str
    pieces: list[Piece] = field(default_factory=list)

    @property
    def listens(self) -> list[int]:
        return list(dict.fromkeys(p.listen_id for p in self.pieces))

    @property
    def total(self) -> float:
        return sum(p.minutes for p in self.pieces)


def lead_number(label: str) -> int | None:
    m = re.match(r"\s*(\d{1,3})\b", label)
    return int(m.group(1)) if m else None


def find_groups(s: Session) -> list[Group]:  # pragma: no cover - Postgres: tests/unit/store/test_containers.py
    """Live book listens' inputs grouped by the folder they sit in: two or more pieces, a plausible number of them."""
    rows = s.execute(
        select(ListenPart, PartFilegroup.label, PartFilegroup.path)
        .join(Listen, Listen.id == ListenPart.listen_id)
        .join(PartFilegroup, PartFilegroup.id == ListenPart.filegroup_id)
        .where(
            ListenPart.role == "input",
            ListenPart.unlinked_at.is_(None),
            Listen.merged_into_id.is_(None),
            Listen.kind == "book",
        )
    ).all()
    groups: dict[str, Group] = {}
    for part, label, path in rows:
        minutes = float((part.detail or {}).get("minutes") or 0)
        if not minutes:
            continue
        parent = os.path.dirname(path or "")
        groups.setdefault(parent, Group(parent)).pieces.append(
            Piece(part.listen_id, part.id, label or "", path, minutes, (part.detail or {}).get("relation") or "")
        )
    out = []
    for g in groups.values():
        done = len(g.listens) == 1 and all(
            p.relation == "part" for p in g.pieces
        )  # already one listen of ordered parts
        if 2 <= len(g.pieces) <= MAX_PIECES and not done:
            g.pieces.sort(key=lambda p: (lead_number(p.label) if lead_number(p.label) is not None else 10**6, p.path))
            out.append(g)
    return out


def _choices(
    s: Session, listens: list[int], field_name: str
) -> list[str]:  # pragma: no cover - Postgres: tests/unit/store/test_containers.py
    return [
        v
        for (v,) in s.execute(
            select(ListenChoice.value).where(
                ListenChoice.listen_id.in_(listens),
                ListenChoice.field == field_name,
                ListenChoice.unchosen_at.is_(None),
            )
        )
        if v
    ]


def group_query(
    s: Session, g: Group
) -> query.Query:  # pragma: no cover - Postgres: tests/unit/store/test_containers.py
    """The folder read as one listen: its name (and the segments of a `A; B; C` name), the title and author all pieces agree on, and the summed runtime."""
    titles = {str(t) for t in _choices(s, g.listens, "title")}
    authors = {str(a) for a in _choices(s, g.listens, "author")}
    choices: dict = {}
    if len(titles) == 1:
        choices["title"] = next(iter(titles))
    if len(authors) == 1:
        choices["author"] = next(iter(authors))
    name = os.path.basename(g.parent)
    readings = query.make_readings(choices, {}, scan.parse_name(name))
    known = choices.get("author", "")
    for seg in (x.strip() for x in name.split(";")):
        if seg and not (known and tk.person_key(seg) == tk.person_key(known)):
            readings.append(query.Reading(seg, known, "split"))
    return query.Query(0, "book", name, readings, minutes=[g.total], path=g.parent)


@dataclass
class Verdict:
    group: Group
    action: str  # join | propose | skip
    reason: str
    key: str | None = None
    p: float = 0.0
    recording: float = 0.0
    summary: dict | None = None
    detail: dict | None = None
    data: dict | None = None  # the vectors the verdict came from (the cache entry for a listen that is joined)


def _same_title(a: str, b: str) -> bool:
    """Two titles name one work when one's words are all in the other (a bracketed or subtitle tail is ignored)."""
    x, y = set(tk.title_key(tk.title_main(a) or a).split()), set(tk.title_key(tk.title_main(b) or b).split())
    return not x or not y or x <= y or y <= x


def judge(
    g: Group, data: dict, params: M.Params, *, threshold: float = A.THRESHOLD, titles: frozenset[str] = frozenset()
) -> Verdict:
    """Decide one folder from its pseudo-listen's vectors: join, propose (a person should look) or skip."""
    post, none, used = M.posterior(params, data["vectors"])
    if not post:
        return Verdict(g, "skip", "no candidate work for the folder")
    key = max(post, key=lambda k: post[k])
    cluster = data["clusters"][key]
    runtimes = [float(r.get("minutes") or 0) for r in cluster.get("recordings") or [] if r.get("minutes")]
    if not runtimes:
        return Verdict(g, "skip", "the work has no known recording length", key, post[key])
    best = min(runtimes, key=lambda r: abs(g.total - r))
    off = abs(g.total - best) / best
    base = dict(
        data=data,
        key=key,
        p=post[key],
        recording=best,
        summary=cluster,
        detail=A._detail(key, post[key], post, none, used, data["clusters"]),
    )
    rec = next((r for r in cluster.get("recordings") or [] if float(r.get("minutes") or 0) == best), {})
    if (
        rec.get("title") and rec.get("asin") and not _same_title(cluster.get("title") or "", rec["title"])
    ):  # the recording that fits is titled differently from its cluster ("Complete X" filed under the work "X: Book One"): the clustering joined them, the match for the whole is the recording itself
        if tk.title_key(rec["title"]) in titles and off <= TOLERANCE:
            asin = rec["asin"]
            base.update(
                key=f"asin:{asin}",
                summary=dict(
                    key=f"asin:{asin}",
                    title=rec["title"],
                    authors=cluster.get("authors") or [],
                    year=rec.get("year"),
                    ol=[],
                    editions=0,
                    asins=[asin],
                    isbns=[],
                    recordings=[rec],
                    programmes=[],
                ),
            )
            base["detail"] = dict(
                base["detail"], candidate=base["key"], title=rec["title"], recordings=[rec], recording=asin
            )
        else:
            return Verdict(
                g,
                "propose" if off <= TOLERANCE else "skip",
                f"the recording that fits ({rec['title']}) is not titled like the work ({cluster.get('title')})",
                **base,
            )
    if not all(p.minutes < SHORTER * best for p in g.pieces):
        return Verdict(g, "skip", "a piece runs as long as the work: copies, not parts", **base)
    if off > PROPOSE_TOLERANCE:
        return Verdict(g, "skip", f"the pieces sum to {g.total:.0f} min, {off:.0%} from the closest recording", **base)
    if off <= TOLERANCE and post[key] >= threshold:
        return Verdict(
            g, "join", f"pieces sum to {g.total:.0f} min = recording {best:.0f} min (P={post[key]:.3f})", **base
        )
    return Verdict(
        g, "propose", f"sum {g.total:.0f} min vs recording {best:.0f} min ({off:.1%} off), P={post[key]:.3f}", **base
    )


def join(
    s: Session, g: Group, v: Verdict, *, actor_ref: str = "", run_id: str | None = None
) -> int:  # pragma: no cover - Postgres: tests/unit/store/test_containers.py
    """Merge the group's listens into one (the first piece's listen survives), mark every piece a `part` in listening order, replace the sub-book matches with the match for the whole and set the status accepted."""
    ids = g.listens
    survivor = s.get(Listen, ids[0])
    if survivor is None or v.summary is None:
        raise ValueError("nothing to join")
    for lid in ids[1:]:
        L.merge(
            s,
            survivor,
            s.get(Listen, lid),  # type: ignore[arg-type]
            actor_kind="model",
            actor_ref=actor_ref,
            run_id=run_id,
            reason=f"pieces of one collection: {v.reason}",
        )
    order = [p.part_id for p in g.pieces]
    order += [  # a copy of a piece kept in another folder came along with its listen: it stays, after the pieces
        x.id
        for x in s.scalars(
            select(ListenPart)
            .where(ListenPart.listen_id == survivor.id, ListenPart.role == "input", ListenPart.unlinked_at.is_(None))
            .order_by(ListenPart.ordinal)
        )
        if x.id not in order
    ]
    for part in s.scalars(select(ListenPart).where(ListenPart.id.in_([p.part_id for p in g.pieces]))):
        part.detail = {**(part.detail or {}), "relation": "part"}
    s.flush()
    L.reorder(s, survivor, "input", order, actor_kind="model", actor_ref=actor_ref, reason="pieces in listening order")
    for p in s.scalars(
        select(ListenPart).where(
            ListenPart.listen_id == survivor.id,
            ListenPart.role.in_(("match", "candidate")),
            ListenPart.unlinked_at.is_(None),
        )
    ):
        L.unlink(
            s,
            p,
            actor_kind="model",
            actor_ref=actor_ref,
            run_id=run_id,
            reason="superseded: the folder as a whole is the better match",
        )
    work = A.work_for(s, v.summary)
    part = L.link(
        s,
        survivor,
        "match",
        work_id=work.id,
        ordinal=1,
        state="candidate",
        score=round(v.p, 4),
        detail=dict(v.detail or {}, container=True, pieces=len(g.pieces), folder=os.path.basename(g.parent)),
        actor_kind="model",
        actor_ref=actor_ref,
        run_id=run_id,
        reason=v.reason,
    )
    L.set_state(
        s,
        part,
        "accepted",
        actor_kind="auto_policy",
        actor_ref=actor_ref,
        run_id=run_id,
        reason=v.reason,
        after=dict(score=round(v.p, 4), sum_minutes=round(g.total), recording_minutes=round(v.recording)),
    )
    L.set_status(
        s, survivor, "identified", actor_kind="auto_policy", actor_ref=actor_ref, run_id=run_id, reason=v.reason
    )
    return survivor.id


def merge_containers(  # pragma: no cover - Postgres: tests/unit/store/test_containers.py
    s: Session,
    oc,
    ac,
    params: M.Params,
    *,
    threshold: float = A.THRESHOLD,
    actor_ref: str = "",
    vectors=dataset.vectors,
    only: set[int] | None = None,
    dry_run: bool = False,
) -> list[Verdict]:
    """Judge every folder (or those holding a listen in `only`) and join the certain ones. Returns the verdicts that joined or are proposals (the rest are skipped silently). `dry_run` judges without writing."""
    run = None if dry_run else new_run(s, "merge_containers", {"threshold": threshold})
    out = []
    fresh: dict = {}
    for g in find_groups(s):
        if only and not only & set(g.listens):
            continue
        q = group_query(s, g)
        v = judge(
            g,
            vectors(oc, ac, q),
            params,
            threshold=threshold,
            titles=frozenset(tk.title_key(r.title) for r in q.readings),
        )
        if v.action == "join" and not dry_run:
            survivor = join(s, g, v, actor_ref=actor_ref, run_id=run.id if run else None)
            fresh[str(survivor)] = dict(
                v.data or {}, sig="container"
            )  # the prepared per-book vectors would otherwise decide the joined listen again
        if v.action != "skip":
            out.append(v)
    if run:
        finish_run(s, run, "done")
    if fresh:
        cache = dataset.load_cache()
        cache.update(fresh)
        dataset.save_cache(cache)
    return out


def overlong(
    s: Session, *, ratio: float = 1.25
) -> list[tuple[int, float, float, str]]:  # pragma: no cover - Postgres: tests/unit/store/test_containers.py
    """Listens whose runtime is far beyond the longest recording of the work they are matched to: probably a collection matched to one of its books (listen id, minutes, longest recording, work title). A review flag only."""
    out = []
    for lid, detail in s.execute(
        select(ListenPart.listen_id, ListenPart.detail)
        .join(Listen, Listen.id == ListenPart.listen_id)
        .where(
            ListenPart.role == "match",
            ListenPart.state == "accepted",
            ListenPart.unlinked_at.is_(None),
            Listen.merged_into_id.is_(None),
            Listen.kind == "book",
        )
    ):
        runtimes = [float(r.get("minutes") or 0) for r in (detail or {}).get("recordings") or []]
        _, minutes = A.listen_minutes(s, lid)
        if runtimes and max(runtimes) > 0 and minutes > ratio * max(runtimes) and not (detail or {}).get("container"):
            out.append((lid, minutes, max(runtimes), (detail or {}).get("title") or ""))
    return out
