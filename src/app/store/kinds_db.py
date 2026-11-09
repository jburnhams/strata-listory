"""Classifying the listens in the database: gather each listen's evidence (tags, folder, chosen fields, file lengths, matched Audible recordings, the radio programme index), run `kinds.classify`, store the verdict in `listen_kind` and move `Listen.kind`.

Rules of the road: a verdict a person set (`decided_by = 'user'`) is never recomputed over; a rerun with nothing new changes nothing (no event, no new row version); a listen whose kind changes gets a `reclassify` event with the signals. Audible and the radio index are optional evidence: when they are missing the other signals still decide."""

from __future__ import annotations

import re
from datetime import UTC, datetime

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.store import kinds as K
from app.store import kinds_model as KM
from app.store.models import (
    Listen,
    ListenChoice,
    ListenEvent,
    ListenKind,
    ListenPart,
    PartFile,
    PartFilegroupMember,
    PartRecord,
)

ACTOR = "kinds-v%d" % K.VERSION
IMPLICIT_WEIGHT = 0.5  # a label implied by an accepted Audible match counts half a person's choice
CLOSE = 0.02  # the matched recording is the same length (2%) before it can imply a label
DIFF_ROLES = ("match", "candidate")


def _tag(payload: dict, key: str) -> str:
    v = payload.get(key)
    return str(v.get("value") if isinstance(v, dict) else v or "") if v else ""


def gather(s: Session, ids: list[int], *, audible=None, radio=None) -> dict[int, K.Features]:
    """Features for each listen id. `audible(asins) -> {asin: {content_type, keywords, publisher}}` and `radio(titles) -> [hits]` default to the local indexes; pass fakes (or `lambda *_: {}`) to leave them out."""
    if audible is None:
        from app.identify import audibleindex

        audible = audibleindex.content_info
    if radio is None:
        from app.identify import radioindex

        radio = radioindex.find
    feats = {i: K.Features() for i in ids}
    for lid, kind, label in s.execute(select(Listen.id, Listen.kind, Listen.label).where(Listen.id.in_(ids))):
        feats[lid].label = label or ""
    chosen: dict[int, dict] = {i: {} for i in ids}
    for lid, field, value in s.execute(
        select(ListenChoice.listen_id, ListenChoice.field, ListenChoice.value).where(
            ListenChoice.listen_id.in_(ids), ListenChoice.unchosen_at.is_(None)
        )
    ):
        chosen[lid][field] = value
    for lid, source, payload in s.execute(
        select(ListenPart.listen_id, PartRecord.source, PartRecord.payload)
        .join(PartRecord, PartRecord.id == ListenPart.record_id)
        .where(ListenPart.listen_id.in_(ids), ListenPart.role == "record", ListenPart.unlinked_at.is_(None))
    ):
        f = feats[lid]
        if source == "embedded_tags":
            f.album, f.track_title, f.artist, f.album_artist, f.composer, f.genre, f.tag_publisher = (
                _tag(payload, k) for k in ("album", "title", "artist", "album_artist", "composer", "genre", "publisher")
            )
            f.comment = _tag(payload, "comment")
            f.narrator = f.narrator or _tag(payload, "narratedby")
            f.asin = f.asin or _tag(payload, "audible_asin")
        elif source == "folder_name":
            f.folder = str(payload.get("folder") or payload.get("title") or "")
    groups: dict[int, list[int]] = {}
    for p in s.scalars(
        select(ListenPart)
        .where(ListenPart.listen_id.in_(ids), ListenPart.role == "input", ListenPart.unlinked_at.is_(None))
        .order_by(ListenPart.ordinal)
    ):
        d = p.detail or {}
        f = feats[p.listen_id]
        if (d.get("minutes") or 0) >= f.minutes and p.filegroup_id is not None:
            f.minutes = float(d.get("minutes") or 0)
            f.files = int(d.get("files") or 0)
            groups[p.listen_id] = [p.filegroup_id]
        if d.get("group_kind") == "programme":
            f.structure_kind = "programme"
    for (
        lid,
        gids,
    ) in groups.items():  # per-file lengths of the largest copy (files directly in the group and in its discs)
        todo, seen, mins = list(gids), set(), []
        while todo:
            g = todo.pop()
            if g in seen:
                continue
            seen.add(g)
            for fid, child in s.execute(
                select(PartFilegroupMember.file_id, PartFilegroupMember.child_group_id).where(
                    PartFilegroupMember.group_id == g
                )
            ):
                if child:
                    todo.append(child)
        d = {
            fid: dur
            for fid, dur in s.execute(
                select(PartFilegroupMember.file_id, PartFile.duration_s)
                .join(PartFile, PartFile.id == PartFilegroupMember.file_id)
                .where(PartFilegroupMember.group_id.in_(seen), PartFilegroupMember.file_id.is_not(None))
            )
            if dur
        }
        feats[lid].part_minutes = sorted(v / 60 for v in d.values())
    for lid, value in chosen.items():
        f = feats[lid]
        f.label = str(value.get("title") or f.label or "")
        f.author = str(value.get("author") or "")
        f.narrator = str(value.get("narrator") or f.narrator or "")
        f.publisher = str(value.get("publisher") or "")
        f.asin = f.asin or str(value.get("asin") or "")
    # structure reason + Audible recordings the model matched
    for lid, reason in s.execute(
        select(ListenEvent.listen_id, ListenEvent.reason).where(
            ListenEvent.listen_id.in_(ids), ListenEvent.action == "create"
        )
    ):
        if feats[lid].structure_kind == "programme":
            feats[lid].structure_reason = reason.split(":", 1)[-1].strip()
    cands: dict[int, list[dict]] = {i: [] for i in ids}
    for p in s.scalars(
        select(ListenPart)
        .where(
            ListenPart.listen_id.in_(ids),
            ListenPart.role.in_(DIFF_ROLES),
            ListenPart.unlinked_at.is_(None),
            ListenPart.state != "rejected",
        )
        .order_by(ListenPart.score.desc())
    ):
        for r in ((p.detail or {}).get("recordings") or [])[:6]:
            cands[p.listen_id].append(dict(r, p=p.score or 0.0))
    info = audible({r.get("asin") for rs in cands.values() for r in rs} | {f.asin for f in feats.values()}) or {}
    for lid, rs in cands.items():
        f = feats[lid]
        seen_asin = set()
        for r in rs:
            a = r.get("asin")
            if not a or a in seen_asin or a not in info or not f.minutes or not r.get("minutes"):
                continue
            seen_asin.add(a)
            i = info[a]
            f.audible.append(
                dict(
                    asin=a,
                    content_type=i.get("content_type"),
                    keywords=i.get("keywords"),
                    publisher=i.get("publisher"),
                    format=r.get("fmt"),
                    minutes=r.get("minutes"),
                    p=r.get("p"),
                    diff=abs(r["minutes"] - f.minutes) / max(f.minutes, 1),
                )
            )
        f.audible.sort(key=lambda a: a["diff"])
        if f.asin and f.asin in info and not any(a["asin"] == f.asin for a in f.audible):
            f.audible.append(
                dict(
                    asin=f.asin,
                    content_type=info[f.asin].get("content_type"),
                    keywords=info[f.asin].get("keywords"),
                    publisher=info[f.asin].get("publisher"),
                    format=None,
                    minutes=None,
                    p=1.0,
                    diff=0.0,
                )
            )
        titles = [t for t in (f.label, f.album, f.artist, f.folder) if t]
        f.radio_hits = radio(titles) or []
    return feats


def classify_listens(
    s: Session,
    ids: list[int] | None = None,
    *,
    audible=None,
    radio=None,
    apply: bool = True,
    actor_ref: str = ACTOR,
    params: K.Params | str | None = "saved",
) -> list[dict]:
    """Classify live listens (all, or `ids`). Returns one dict per listen: id, label, before (kind), the verdict, and `changed`. With apply=False nothing is written."""
    q = select(Listen.id, Listen.kind, Listen.label).where(Listen.merged_into_id.is_(None))
    if ids is not None:
        q = q.where(Listen.id.in_(ids))
    rows = list(s.execute(q))
    if not rows:
        return []
    if params == "saved":
        params = KM.load()
    feats = gather(s, [r[0] for r in rows], audible=audible, radio=radio)
    existing = _existing(s, [r[0] for r in rows])
    out = []
    for lid, kind, label in rows:
        cur = existing.get(lid)
        if cur is not None and cur.decided_by == "user":
            out.append(dict(id=lid, label=label, before=kind, changed=False, user=True, **cur_dict(cur)))
            continue
        v = K.classify(feats[lid], params)
        guess = K._kind(feats[lid], v.signals) if v.band != "book" else "book"
        new_kind = v.kind
        d = v.as_dict()
        d.update(guess=guess)
        changed = new_kind != kind
        out.append(dict(id=lid, label=label, before=kind, changed=changed, user=False, **d))
        if not apply:
            continue
        listen = s.get(Listen, lid)
        if changed:
            listen.kind = new_kind
            s.flush()
            s.add(
                ListenEvent(
                    listen_id=lid,
                    action="reclassify",
                    actor_kind="rule",
                    actor_ref=actor_ref,
                    reason=f"{kind} -> {new_kind} (p broadcast {v.p_broadcast:.2f}): "
                    + "; ".join(f"{x.name} {x.weight:+.1f}" for x in v.signals[:6]),
                    before={"kind": kind},
                    after={"kind": new_kind, "p_broadcast": round(v.p_broadcast, 3)},
                )
            )
        row = dict(
            kind=new_kind,
            guess=guess,
            p_broadcast=v.p_broadcast,
            confidence=v.confidence,
            band=v.band,
            signals=d["signals"],
            version=K.VERSION,
        )
        if cur is None:
            s.add(ListenKind(listen_id=lid, decided_by="rule", **row))
        else:
            for k_, v_ in row.items():
                setattr(cur, k_, v_)
            cur.computed_at = datetime.now(UTC).replace(tzinfo=None)
    if apply:
        s.flush()
    return out


def _existing(s: Session, ids: list[int]) -> dict[int, ListenKind]:
    return {k.listen_id: k for k in s.scalars(select(ListenKind).where(ListenKind.listen_id.in_(ids)))}


def cur_dict(k: ListenKind) -> dict:
    return dict(
        kind=k.kind,
        guess=k.guess,
        p_broadcast=k.p_broadcast,
        confidence=k.confidence,
        band=k.band,
        signals=k.signals,
        version=k.version,
    )


def set_kind(s: Session, listen_id: int, kind: str, *, actor_ref: str = "") -> ListenKind:
    """A person's decision: the listen has this kind from now on and no rerun changes it."""
    if kind not in ("book", "radio_series", "radio_show", "play", "other"):
        raise ValueError(f"unknown kind {kind!r}")
    listen = s.get(Listen, listen_id)
    if listen is None:
        raise LookupError(f"no listen {listen_id}")
    before = listen.kind
    listen.kind = kind
    s.flush()
    cur = s.get(ListenKind, listen_id)
    if cur is None:
        cur = ListenKind(
            listen_id=listen_id,
            kind=kind,
            guess=kind,
            p_broadcast=0.0 if kind == "book" else 1.0,
            confidence=1.0,
            band="book" if kind == "book" else "broadcast",
            signals=[dict(name="your decision", weight=0, detail="set by a person")],
            version=K.VERSION,
            decided_by="user",
        )
        s.add(cur)
    else:
        cur.kind = kind
        cur.guess = kind
        cur.band = "book" if kind == "book" else "broadcast"
        cur.p_broadcast = 0.0 if kind == "book" else 1.0
        cur.confidence = 1.0
        cur.signals = [
            dict(name="your decision", weight=0, detail="set by a person"),
            *[x for x in cur.signals if x.get("name") != "your decision"],
        ]
        cur.decided_by = "user"
    if before != kind:
        s.add(
            ListenEvent(
                listen_id=listen_id,
                action="reclassify",
                actor_kind="user",
                actor_ref=actor_ref,
                reason=f"{before} -> {kind} by a person",
                before={"kind": before},
                after={"kind": kind},
            )
        )
    s.flush()
    return cur


def kind_infos(s: Session, ids: list[int]) -> dict[int, dict]:
    """The stored verdict of each listen, shaped for the pages: kind, what it would be if not a book (`guess`), band, probability, who decided, and the signals (strongest first)."""
    if not ids:
        return {}
    return {
        k.listen_id: dict(
            kind=k.kind,
            guess=k.guess,
            band=k.band,
            confidence=round(k.confidence, 3),
            p_broadcast=round(k.p_broadcast, 3),
            decided_by=k.decided_by,
            signals=sorted(k.signals, key=lambda x: -abs(x.get("weight", 0))),
        )
        for k in s.scalars(select(ListenKind).where(ListenKind.listen_id.in_(ids)))
    }


def implied(f: K.Features, accepted: bool) -> bool | None:
    """The kind an accepted Audible match implies: True broadcast, False book, None when it implies nothing. Only the safe cases: an ordinary audiobook (content type Product) from a publisher that is not the BBC, or a recording that Audible keywords as a dramatisation and files as a radio programme or show. BBC Audio files readings as "Radio/TV Program" too, so that alone implies nothing."""
    if not accepted:
        return None
    for a in sorted(f.audible, key=lambda a: a["diff"]):
        if a["diff"] > CLOSE or (a.get("p") or 0) < 0.9:
            continue
        c = K._content_class(a.get("content_type") or "")
        kw = " ".join(a.get("keywords") or []).lower()
        if (
            c == "book"
            and not K.BBC_PUBLISHERS.match((a.get("publisher") or "").strip())
            and "radio" not in (a.get("publisher") or "").lower()
        ):
            return False
        if c in ("broadcast", "show") and re.search(r"dramati[sz]", kw):
            return True
        return None
    return None


def labels(s: Session, ids: list[int], feats: dict[int, K.Features]) -> dict[int, tuple[bool, float, str]]:
    """{listen id: (broadcast?, sample weight, source)}: a person's choice or confirmation of the kind (`user`, weight 1), a radio programme accepted as the listen's match (`radio match`: broadcast, weight 1 when a person accepted it, else half), else the kind an accepted Audible match implies (`audible match`, half weight)."""
    out: dict[int, tuple[bool, float, str]] = {}
    for k in s.scalars(select(ListenKind).where(ListenKind.listen_id.in_(ids), ListenKind.decided_by == "user")):
        out[k.listen_id] = (k.kind != "book", 1.0, "user")
    accepted: set[int] = set()
    radio: dict[int, float] = {}
    from app.linkage import apply as A

    parts = list(
        s.scalars(
            select(ListenPart).where(
                ListenPart.listen_id.in_(ids),
                ListenPart.role.in_(("match", "candidate")),
                ListenPart.state == "accepted",
                ListenPart.unlinked_at.is_(None),
            )
        )
    )  # a person's approval of a candidate keeps its role and becomes state accepted
    by_person = A.person_decisions(s, parts)
    for p in parts:
        if str((p.detail or {}).get("candidate") or "").startswith("radio:"):
            radio[p.listen_id] = (
                1.0 if p.id in by_person or p.linked_by == "user" else IMPLICIT_WEIGHT
            )  # accepted as a radio programme: broadcast
        else:
            accepted.add(p.listen_id)
    for lid, w in radio.items():
        if lid not in out:
            out[lid] = (True, w, "radio match")
    for lid in ids:
        if lid in out:
            continue
        y = implied(feats[lid], lid in accepted)
        if y is not None:
            out[lid] = (y, IMPLICIT_WEIGHT, "audible match")
    return out


def learn(s: Session, *, audible=None, radio=None) -> tuple[K.Params, dict]:
    """Fit the weights on every labelled listen (person's choices and confirmations clamped, implied labels at half weight), save them, and return them with the label counts. The Audible signals are dropped from the features of implied labels: a label is never learned from the evidence that made it."""
    ids = [i for (i,) in s.execute(select(Listen.id).where(Listen.merged_into_id.is_(None)))]
    feats = gather(s, ids, audible=audible, radio=radio)
    lab = labels(s, ids, feats)
    rows = []
    for lid, (y, w, src) in lab.items():
        sigs = K.signals(feats[lid])
        if src == "audible match":
            sigs = [x for x in sigs if x.name not in KM.AUDIBLE_SIGNALS]
        elif src == "radio match":
            sigs = [
                x for x in sigs if x.name != "programme catalogue"
            ]  # the label came from the radio index: do not learn it back from that
        rows.append((sigs, y, w))
    counts = {src: sum(1 for v in lab.values() if v[2] == src) for src in ("user", "audible match", "radio match")}
    counts.update(broadcast=sum(1 for v in lab.values() if v[0]), book=sum(1 for v in lab.values() if not v[0]))
    params = KM.fit(rows, counts)
    KM.save(params)
    return params, counts


def learn_and_classify(s: Session, *, audible=None, radio=None) -> dict:
    """What a retrain does: learn from the labels, then re-judge every listen a person has not decided. Returns label counts and how many kinds changed."""
    params, counts = learn(s, audible=audible, radio=radio)
    res = classify_listens(s, audible=audible, radio=radio, params=params)
    return dict(
        labels=counts,
        version=params.version(),
        changed=sum(r["changed"] for r in res),
        review=sum(r["band"] == "review" for r in res),
    )


_lock = __import__("threading").Lock()


def start_learning(session_factory, **kw) -> bool:
    """Retrain in the background after a person's answer; False when one is already running."""
    if not _lock.acquire(blocking=False):
        return False

    def run():
        try:
            with session_factory() as s:
                learn_and_classify(s, **kw)
                s.commit()
        except Exception:
            pass  # the next answer retrains again; nothing depends on this one
        finally:
            _lock.release()

    __import__("threading").Thread(target=run, name="kinds-learn", daemon=True).start()
    return True
