"""One durable run per group; every stage has elapsed time, metrics and evidence."""

import copy
import hashlib
import json
import math
import time
from contextlib import contextmanager, suppress
from contextvars import ContextVar
from datetime import UTC, datetime
from pathlib import Path

from app import catalogue, oslib, read_models, settings, sources
from app.ingest import acoustics, audio, collections, grouping, inventory, metadata, policy, provenance, store
from app.ingest.observe import capture_progress
from app.process import plan as P
from app.process import queue as Q

STAGES = (
    "inventory",
    "identity",
    "providers",
    "metadata",
    "recording_providers",
    "recording",
    "alignment",
    "quality",
    "selection",
    "planning",
    "eligibility",
)
_SAVED_EVIDENCE = ContextVar("saved_ingest_evidence", default=False)


@contextmanager
def saved_evidence_only():
    token = _SAVED_EVIDENCE.set(True)
    try:
        yield
    finally:
        _SAVED_EVIDENCE.reset(token)


REVIEW_STAGES = {
    "metadata": STAGES[:4],
    "audiobook": ("recording_providers", "recording"),
    "quality": ("alignment", "quality", "selection"),
}


def raw_stage(run, stage):
    return next((e["data"] for e in reversed(run["events"]) if e["stage"] == stage and e["status"] == "done"), None)


def book_review_held(run):
    latest = lambda stage: max(
        (e["id"] for e in run["events"] if e["stage"] == stage and e["status"] == "done"), default=0
    )
    return latest("metadata_revocation") > latest("metadata_approval")


def book_confirmed(run):
    decision = raw_stage(run, "match_review_decision") or {}
    approval_id = next(
        (e["id"] for e in reversed(run["events"]) if e["stage"] == "metadata_approval" and e["status"] == "done"), None
    )
    return bool(
        not book_review_held(run)
        and decision.get("action") == "approve"
        and approval_id is not None
        and decision.get("approval_id") == approval_id
        and decision.get("signature") == review_signature(run, "metadata")
    )


def legacy_metadata_consent(run):
    approval = raw_stage(run, "metadata_approval")
    return bool(approval and "scope" not in approval)


def review_phase(run):
    if run["state"] in ("split", "split_existing", "queued"):
        return "processing"
    return next((phase for phase in REVIEW_STAGES if not approval_valid(run, phase)), "processing")


def stage_data(run, stage):
    data = raw_stage(run, stage)
    if stage == "metadata":
        data = raw_stage(run, "recording") or data
    if data is None and stage in ("recording", "recording_providers") and legacy_metadata_consent(run):
        data = stage_data(run, "metadata" if stage == "recording" else "providers")
    if _SAVED_EVIDENCE.get():
        return data
    if stage == "recording" and data is not None and data.get("version", 1) < metadata.VERSION:
        return stage_data(run, "metadata")
    if stage == "metadata" and data is not None and data.get("version", 1) < metadata.VERSION:
        ids = tuple(
            (e["stage"], e["id"])
            for e in run["events"]
            if e["status"] == "done"
            and e["stage"] in ("inventory", "identity", "providers", "metadata", "recording_providers", "recording")
        )
        key = (str(settings.data_dir()), run["id"], metadata.VERSION, data.get("version", 1), ids)
        local = run.get("_metadata_projection")
        if local and local["key"] == key:
            return local["data"]
        cache_key = "ingest-metadata:" + hashlib.sha256(json.dumps(key).encode()).hexdigest()
        cached = store.projection_get(cache_key)
        if cached is not None:
            data = cached
        else:
            inv = stage_data(run, "inventory")
            identity = stage_data(run, "identity")
            providers = stage_data(run, "providers")
            if (
                inv
                and inv.get("representations")
                and identity
                and identity.get("book")
                and providers
                and "providers" in providers
            ):
                data = metadata.resolve(identity, inv, providers)[0]
                recording_providers = raw_stage(run, "recording_providers")
                if raw_stage(run, "recording") and recording_providers:
                    combined = dict(providers=dict(providers["providers"], **recording_providers["providers"]))
                    data = metadata.refine_recording(
                        dict(identity, _recording_choice=(raw_stage(run, "recording_choice") or {}).get("key")),
                        inv,
                        combined,
                        data,
                    )[0]
                store.projection_save(cache_key, data)
        run["_metadata_projection"] = dict(key=key, data=data)
    return data


def metrics(run):
    out = {}
    for e in run["events"]:
        if e["status"] == "done":
            out.update(e["metrics"])
    meta = stage_data(run, "metadata") or {}
    out.update(meta.get("analysis_metrics", {}))
    return out


def review_evaluation(run, phase, value=None):
    if phase not in REVIEW_STAGES:
        raise ValueError("review stage must be metadata or quality, or audiobook")
    identity = stage_data(run, "identity") or {}
    p = value or policy.current()
    prefixes = (
        ("inventory.", "identity.", "metadata.original_work_link", "metadata.work_families", "work.")
        if phase == "metadata"
        else ("recording.",)
        if phase == "audiobook"
        else ("alignment.", "quality.", "selection.")
    )
    result = policy.evaluate(metrics(run), identity.get("kind", "unknown"), p)
    allowed = phase == "metadata" or result["kind_allowed"]
    checks = [c for c in result["checks"] if c["metric"].startswith(prefixes)]
    complete = all(stage_data(run, s) is not None for s in REVIEW_STAGES[phase])
    groups = {}
    if phase == "metadata":
        for name in ("work", "performance", "source"):
            scoped = [
                c
                for c in result["checks"]
                if c["metric"].startswith(("inventory.", "identity.", "metadata.", "work.", "recording."))
                and (
                    "work"
                    if c["metric"].startswith(("work.", "metadata."))
                    or c["metric"] in ("identity.title_known", "identity.author_known")
                    else "performance"
                    if c["metric"].startswith("recording.")
                    else "source"
                )
                == name
            ]
            groups[name] = dict(
                checks=scoped,
                eligible=complete and allowed and all(c["passed"] for c in scoped),
                complete=complete,
                kind_allowed=allowed,
            )
    if phase == "metadata" and book_review_held(run):
        checks.append(
            dict(
                metric="review.manual_hold",
                value=None,
                threshold={},
                passed=False,
                reason="Book approval was withdrawn; accept a book match to release manual review.",
            )
        )
        groups["work"]["eligible"] = False
    return dict(
        checks=checks,
        groups=groups,
        eligible=complete
        and allowed
        and identity.get("book", {}).get("review") != "ignored"
        and all(c["passed"] for c in checks),
        complete=complete,
        kind_allowed=allowed,
        policy_revision=p.get("revision", 0),
    )


def review_signature(run, phase):
    stages = (
        ("inventory",)
        + REVIEW_STAGES[phase]
        + (
            ("metadata_approval", "quality_rating")
            if phase == "quality" and legacy_metadata_consent(run)
            else ("metadata_approval", "audiobook_approval", "quality_rating")
            if phase == "quality"
            else ("metadata", "metadata_approval")
            if phase == "audiobook"
            else ()
        )
    )
    ids = {
        s: next((e["id"] for e in reversed(run["events"]) if e["stage"] == s and e["status"] == "done"), None)
        for s in stages
    }
    return hashlib.sha256(json.dumps(ids, sort_keys=True).encode()).hexdigest()


def approval_valid(run, phase):
    if book_review_held(run):
        return False
    if phase in ("audiobook", "quality") and not approval_valid(run, "metadata"):
        return False
    if phase == "quality" and not approval_valid(run, "audiobook"):
        return False
    if phase == "audiobook" and legacy_metadata_consent(run):
        approval = raw_stage(run, "metadata_approval")
        return not approval.get("automatic") or review_evaluation(run, "audiobook")["eligible"]
    approval = stage_data(run, phase + "_approval")
    return bool(
        approval
        and approval.get("signature") == review_signature(run, phase)
        and (
            not approval.get("automatic")
            or (phase == "metadata" and book_confirmed(run))
            or (
                approval.get("policy_revision") == policy.current().get("revision", 0)
                and review_evaluation(run, phase)["eligible"]
            )
        )
    )


def require_approvals(run):
    if not all(approval_valid(run, phase) for phase in REVIEW_STAGES):
        raise ValueError(
            "approve metadata and quality, including audiobook identification, before proposing processing"
        )


def _assert_review_inputs(run):
    inv = stage_data(run, "inventory")
    if not inv or not inventory.snapshot_unchanged(inv):
        raise ValueError("source files changed; start a new ingest run")
    if len(grouping.partition(inv["files"], inv["root"])) > 1:
        raise ValueError("source contains multiple books; start a new ingest to split this collection")
    if any(inventory.signature(m) != inventory.signature(catalogue.get_book(m["id"])) for m in inv["members"]):
        raise ValueError("catalogue metadata changed; start a new ingest run")
    providers = stage_data(run, "providers") or {}
    recording_providers = stage_data(run, "recording_providers") or {}
    planned = (stage_data(run, "planning") or {}).get("plan") or {}
    manifest = providers.get(
        "manifest", planned.get("ingest", {}).get("evidence_manifest", [])
    ) + recording_providers.get("manifest", [])
    if not inventory.unchanged(manifest):
        raise ValueError("provider evidence changed; refresh metadata")
    return inv


def _record_approval(run, phase, automatic=False, value=None):
    evaluation = review_evaluation(run, phase, value)
    p = value or policy.current()
    if automatic and (
        not p.get(
            "auto_approve_" + phase,
            p.get("auto_approve_metadata", p["auto_approve"]) if phase == "audiobook" else p["auto_approve"],
        )
        or not evaluation["eligible"]
        or evaluation["policy_revision"] != policy.current()["revision"]
    ):
        raise ValueError("current thresholds do not permit automatic " + phase + " approval")
    store.event(
        run["id"],
        phase + "_approval",
        "done",
        data=dict(
            scope="book" if phase == "metadata" else phase,
            resolution_version=metadata.VERSION,
            automatic=automatic,
            signature=review_signature(run, phase),
            policy_revision=evaluation["policy_revision"],
            checks=evaluation["checks"],
        ),
    )
    if phase == "metadata":
        # A fresh approved match can supply a cover after an earlier miss.
        with suppress(OSError):
            (Path(settings.data_dir()) / "covers" / f"{run['book_id']}.none").unlink(missing_ok=True)


def approve_review(run_id, phase, automatic=False, *, _validated_inputs=None):
    if phase not in REVIEW_STAGES:
        raise ValueError("review stage must be metadata or quality, or audiobook")
    lock = Path(settings.data_dir()) / "ingest" / (run_id + ".approval.lock")
    # Resolve the ID before opening a path supplied by a caller.
    store.get(run_id)
    with lock.open("a+b") as f, oslib.file_lock(f):
        run = store.get(run_id)
        if approval_valid(run, phase):
            return dict(changed=False, phase=phase)
        if run["state"] not in ("metadata_review", "audiobook_review", "quality_review", "review", "eligible"):
            raise ValueError("this run is not awaiting stage approval")
        if phase in ("audiobook", "quality") and not approval_valid(run, "metadata"):
            raise ValueError("approve metadata before reviewing " + phase)
        if phase == "quality" and not approval_valid(run, "audiobook"):
            raise ValueError("approve audiobook identification before reviewing quality")
        performance = (stage_data(run, "metadata") or {}).get("performance") or {}
        if phase == "audiobook" and len(performance.get("choices", [])) > 1 and not performance.get("selected_choice"):
            raise ValueError("choose a recording before approving audiobook identification")
        evaluation = review_evaluation(run, phase)
        if automatic and (not policy.current().get("auto_approve_" + phase, False) or not evaluation["eligible"]):
            raise ValueError("current thresholds do not permit automatic " + phase + " approval")
        if not evaluation["complete"]:
            raise ValueError(phase + " analysis is not complete")
        if not evaluation["kind_allowed"]:
            raise ValueError("content kind requires a supported processing recipe")
        # Explicit shortlist selection already verified these exact immutable inputs
        # immediately before appending its metadata choice. Do not hash the audio twice.
        inv = (
            _validated_inputs
            if phase == "metadata" and _validated_inputs == stage_data(run, "inventory")
            else _assert_review_inputs(run)
        )
        for member in inv["members"]:
            latest = store.latest(member["id"])
            if latest and latest["id"] != run_id:
                raise ValueError("a newer run exists; review that run instead")
            if catalogue.get_plan(member["id"]):
                raise ValueError("a processing plan already exists; explicitly replan first")
        _record_approval(run, phase, automatic=automatic)
        store.finish(run_id, "running")
        return dict(changed=True, phase=phase)


def approve_passing_metadata():
    """Apply metadata automation to existing waiting runs without approving quality."""
    approved = []
    for candidate in store.all_runs():
        if candidate["state"] not in ("metadata_review", "audiobook_review", "quality_review", "review", "eligible"):
            continue
        inv = stage_data(candidate, "inventory") or {}
        if store.latest_id(inv.get("primary_id", candidate["book_id"])) != candidate["id"]:
            continue
        phase = review_phase(candidate)
        if (
            phase not in ("metadata", "audiobook")
            or not policy.current().get("auto_approve_" + phase)
            or not review_evaluation(candidate, phase)["eligible"]
        ):
            continue
        try:
            if approve_review(candidate["id"], phase, automatic=True)["changed"]:
                approved.append(candidate["id"])
        except (ValueError, OSError) as error:
            store.event(candidate["id"], "auto_metadata", "failed", error=str(error))
    return approved


def continue_review(run_id, propose=True):
    result = run(resume=run_id)[0]
    if propose and result["state"] in ("review", "eligible") and all(approval_valid(result, p) for p in REVIEW_STAGES):
        if not catalogue.get_plan(stage_data(result, "inventory")["primary_id"]):
            enqueue(run_id)
    return store.get(run_id)


def evaluate(run_id, value=None, save=True):
    run = store.get(run_id)
    identity = stage_data(run, "identity") or {}
    result = policy.evaluate(metrics(run), identity.get("kind", "unknown"), value)
    if identity.get("book", {}).get("review") == "ignored":
        result.update(eligible=False, auto_approve=False, auto_process=False, review_blocked=True)
    if run["state"] == "failed" or any(stage_data(run, s) is None for s in STAGES[:-1]):
        result.update(eligible=False, auto_approve=False, auto_process=False, incomplete=True)
    if save:
        store.event(run_id, "evaluation", "done", data=result)
    return result


def _plan(book, inv, selected, meta, identity, run_id, archives=()):
    if not inventory.unchanged(selected["manifest"]):
        raise ValueError("input changed since inventory")
    # Do not give an episode/radio source a print-work processing identity by accident.
    if identity["kind"] != "book":
        return dict(plan=None, reason="non-book content requires a separate processing recipe"), {
            "planning.warning_count": None
        }
    selected_paths = set(selected["paths"])
    facts = {f["path"]: f["probe"] for f in inv["files"] if f["path"] in selected_paths}
    plan = P.make_plan(
        book,
        settings.final_dir(),
        None,
        None,
        meta["records"],
        input_paths=selected["paths"],
        measurements=selected["measurements"],
        input_facts=[facts[path] for path in selected["paths"]],
    )
    plan["ingest"] = dict(
        run_id=run_id,
        group=inv["group"],
        primary_id=inv["primary_id"],
        members=[b["id"] for b in inv["members"]],
        root=inv["root"],
        sidecars=inv["sidecars"],
        member_signatures={str(b["id"]): inventory.signature(b) for b in inv["members"]},
        manifest=selected["manifest"],
        book_signature=inventory.signature(catalogue.get_book(inv["primary_id"])),
    )
    plan["metadata_evidence"] = {
        k: meta[k] for k in ("records", "resolved", "recording", "community", "candidates", "rejected")
    }
    plan["metadata_evidence"].update({k: meta.get(k) for k in ("version", "work", "performance")})
    plan["metadata_evidence"]["corroboration"] = meta.get("corroboration", {})
    plan["ingest"]["inventory_manifest"] = inv["files"]
    plan["ingest"]["source_scope"] = {k: inv[k] for k in ("scope_root", "scope_paths", "scope_sidecars") if k in inv}
    plan["ingest"]["evidence_manifest"] = [
        dict(path=str(p), size=p.stat().st_size, sha256=inventory.digest(p)) for path in archives for p in [Path(path)]
    ]
    plan["ingest"]["stage_approvals"] = {
        phase: stage_data(store.get(run_id), phase + "_approval") for phase in REVIEW_STAGES
    }
    plan["source_paths"] = selected["paths"]
    return dict(plan=plan), {"planning.warning_count": len(plan["warnings"])}


def quality_with_cache(inv, aligned, run_id):
    key = lambda x: sorted((f["path"], f["sha256"]) for f in x["files"])
    for old in store.runs(200):
        if old["id"] == run_id:
            continue
        prior = stage_data(old, "inventory")
        data = stage_data(old, "quality")
        if (
            prior
            and data
            and data.get("version", 1) == audio.QUALITY_VERSION
            and key(prior) == key(inv)
            and prior["representations"] == inv["representations"]
            and all(not r["decode_messages"] for r in aligned["representations"])
            and all(
                len((r.get("acoustics") or {}).get("samples", [])) == 3
                and all(s.get("result") is not None for s in r["acoustics"]["samples"])
                for r in data["representations"]
            )
        ):
            completed = [e for e in old["events"] if e["stage"] == "quality" and e["status"] == "done"]
            if completed and completed[-1]["metrics"].get("quality.complete") == 1:
                return dict(data, reused_from=old["id"]), dict(completed[-1]["metrics"], **{"quality.cache_hit": 1})
    data, metrics = audio.quality(inv, aligned)
    return data, dict(metrics, **{"quality.cache_hit": 0})


def run_one(book_id, run_id=None, online=True, book_only=False):
    if run_id is None:
        book = catalogue.get_book(book_id)
        run_id = store.create(
            book_id, dict(policy=policy.current(), online=online, book_signature=inventory.signature(book))
        )
    last = [0.0, ""]

    def progress(entry):
        now = time.monotonic()
        stage = entry.get("stage", "inventory")
        if (
            stage != last[1]
            or entry.get("current") is None
            or now - last[0] >= 1
            or (entry.get("current") == entry.get("total") and entry.get("total") is not None)
        ):
            store.event(run_id, stage, "progress", data=entry)
            last[:] = [now, stage]

    with capture_progress(progress):
        return _run_one(book_id, run_id, online, book_only)


def _run_one(book_id, run_id, online=True, book_only=False):
    book = catalogue.get_book(book_id)
    p = policy.current()
    if run_id:
        run = store.get(run_id)
        if run["book_id"] != book_id:
            raise ValueError("run/book mismatch")
        if run["state"] not in ("failed", "running", "queued"):
            raise ValueError("only interrupted or failed runs can resume")
        p = run["options"]["policy"]
        online = run["options"]["online"]
        inv = stage_data(run, "inventory")
        if inv and (
            not inventory.snapshot_unchanged(inv)
            or any(inventory.signature(m) != inventory.signature(catalogue.get_book(m["id"])) for m in inv["members"])
            or run["options"]["book_signature"] != inventory.signature(book)
        ):
            raise ValueError("inputs or metadata changed; start a new run")
        store.finish(run_id, "running")
    else:
        run_id = store.create(book_id, dict(policy=p, online=online, book_signature=inventory.signature(book)))
    folder = Path(settings.data_dir()) / "ingest" / run_id
    folder.mkdir(parents=True, exist_ok=True)
    data = {}
    replanned = False
    for stage in STAGES:
        phase = (
            "metadata"
            if stage == "recording_providers"
            else "audiobook"
            if stage == "alignment"
            else "quality"
            if stage == "planning"
            else None
        )
        if phase:
            current = store.get(run_id)
            if not approval_valid(current, phase):
                # Execution keeps its original provider policy; consent at each
                # new review gate uses the policy currently chosen by the user.
                current_policy = policy.current()
                check = review_evaluation(current, phase, current_policy)
                if (
                    current_policy.get(
                        "auto_approve_" + phase,
                        current_policy.get("auto_approve_metadata", current_policy["auto_approve"])
                        if phase == "audiobook"
                        else current_policy["auto_approve"],
                    )
                    and check["eligible"]
                ):
                    _assert_review_inputs(current)
                    _record_approval(current, phase, automatic=True, value=current_policy)
                else:
                    store.finish(run_id, phase + "_review")
                    return store.get(run_id)
        if book_only and stage == "recording_providers":
            store.finish(run_id, "queued")
            return store.get(run_id)
        prior = stage_data(store.get(run_id), stage)
        if (
            stage == "planning"
            and prior
            and (prior.get("plan") or {}).get("ingest", {}).get("stage_approvals")
            != {p: stage_data(store.get(run_id), p + "_approval") for p in REVIEW_STAGES}
        ):
            prior = None
        if stage == "eligibility" and replanned:
            prior = None
        if prior is not None:
            if stage == "inventory" and prior.get("work_groups"):
                collections.split(run_id, prior, p, online)
                return store.get(run_id)
            data[stage] = prior
            if stage == "inventory":
                book = catalogue.get_book(prior["primary_id"])
            continue
        started = time.monotonic()
        store.event(run_id, stage, "running")
        try:
            if stage == "inventory":
                value, measure = inventory.discover(book)
                if value.get("work_groups"):
                    store.event(run_id, stage, "done", measure, value, elapsed=time.monotonic() - started)
                    collections.split(run_id, value, p, online)
                    return store.get(run_id)
                if value.get("scope_root"):
                    catalogue.set_extra(
                        value["primary_id"],
                        "source_group",
                        dict(
                            version=grouping.VERSION,
                            root=value["scope_root"],
                            paths=[f["path"] for f in value["files"]],
                            scope_paths=value["scope_paths"],
                            anchor_members=value.get("work_hint", {}).get("anchor_members", []),
                            layout_overrides=inventory.layout_overrides(value),
                        ),
                    )
                    for member in value["members"]:
                        catalogue.set_extra(member["id"], "source_group_ref", dict(primary_id=value["primary_id"]))
                store.link(run_id, [b["id"] for b in value["members"]])
                book = catalogue.get_book(value["primary_id"])
            elif stage == "identity":
                value, measure = inventory.recover(book, data["inventory"])
            elif stage == "providers":
                lookup_identity = dict(
                    data["identity"],
                    _work_discovery_providers=[n for n in p["providers"] if n not in ("hardcover", "openlibrary")],
                )
                value, measure = (
                    metadata.collect(
                        lookup_identity,
                        data["inventory"],
                        folder / "sources",
                        [n for n in p["providers"] if n in ("hardcover", "openlibrary")],
                    )
                    if online
                    else (
                        dict(providers={}, archives=[], offline=True),
                        {"metadata.provider_attempts": 0, "metadata.provider_errors": 0, "metadata.raw_responses": 0},
                    )
                )
                value["manifest"] = [
                    dict(path=str(path), size=Path(path).stat().st_size, sha256=inventory.digest(path))
                    for path in value["archives"]
                ]
            elif stage == "metadata":
                value, measure = metadata.resolve(data["identity"], data["inventory"], data["providers"])
            elif stage == "recording_providers":
                approved = metadata.approved_identity(data["identity"], data["metadata"])
                value, measure = (
                    metadata.collect(
                        approved,
                        data["inventory"],
                        folder / "recording-sources",
                        [n for n in p["providers"] if n not in ("hardcover", "openlibrary")],
                    )
                    if online
                    else (dict(providers={}, archives=[], offline=True), {})
                )
                value["manifest"] = [
                    dict(path=str(path), size=Path(path).stat().st_size, sha256=inventory.digest(path))
                    for path in value["archives"]
                ]
                value["approved_book"] = data["metadata"]["work"]["resolved"]
            elif stage == "recording":
                combined = dict(
                    providers=dict(data["providers"]["providers"], **data["recording_providers"]["providers"])
                )
                value, measure = metadata.refine_recording(
                    data["identity"], data["inventory"], combined, data["metadata"]
                )
            elif stage == "alignment":
                value, measure = audio.alignment(data["inventory"], folder / ("fingerprints-" + str(time.time_ns())))
            elif stage == "quality":
                value, measure = quality_with_cache(data["inventory"], data["alignment"], run_id)
            elif stage == "selection":
                value, measure = audio.select(data["inventory"], data["alignment"], data["quality"])
            elif stage == "planning":
                value, measure = _plan(
                    book,
                    data["inventory"],
                    data["selection"],
                    data.get("recording", data["metadata"]),
                    data["identity"],
                    run_id,
                    data["providers"]["archives"] + data["recording_providers"]["archives"],
                )
                replanned = True
            else:
                value = evaluate(run_id, p, save=False)
                measure = {
                    "eligibility.passed": sum(c["passed"] for c in value["checks"]),
                    "eligibility.failed": sum(not c["passed"] for c in value["checks"]),
                }
                if not inventory.unchanged(data["selection"]["manifest"]):
                    raise ValueError("inputs changed before eligibility decision")
            data[stage] = value
            store.event(run_id, stage, "done", measure, value, elapsed=time.monotonic() - started)
        except Exception as e:
            store.event(run_id, stage, "failed", error=str(e), elapsed=time.monotonic() - started)
            store.finish(run_id, "failed")
            return store.get(run_id)
    eligible = data["eligibility"]["eligible"]
    store.finish(run_id, "eligible" if eligible else "review")
    if data["eligibility"]["auto_approve"]:
        try:
            enqueue(run_id, automatic=True)
            if data["eligibility"]["auto_process"]:
                Q.run_approved(ids=[data["inventory"]["primary_id"]])
        except Exception as e:
            store.event(run_id, "queue", "failed", error=str(e))
            store.finish(run_id, "failed")
    return store.get(run_id)


def run(ids=None, limit=5, online=True, resume=None):
    # Explicit selections can cover a whole library; automatic discovery stays bounded.
    if not 1 <= limit <= max(200, len(ids or [])):
        raise ValueError("limit must be 1..200, or at most the number of explicitly selected book IDs")
    lock = Path(settings.data_dir()) / "ingest.lock"
    with lock.open("a+b") as f, oslib.file_lock(f):
        if resume:
            previous = store.get(resume)
            result = run_one(previous["book_id"], resume)
            return [result] + continue_children(result)
        books = (
            [catalogue.get_book(i) for i in ids]
            if ids
            else [
                b
                for b in catalogue.list_books(kind="book")
                if b["review"] != "ignored" and store.latest(b["id"]) is None
            ]
        )
        seen = set()
        results = []
        selected_count = 0
        for b in books:
            if selected_count >= limit:
                break
            if b["id"] in seen:
                continue
            selected_count += 1
            result = run_one(b["id"], online=online)
            results.append(result)
            inv = stage_data(result, "inventory")
            if inv:
                seen.update(m["id"] for m in inv["members"])
            child_results = continue_children(result)
            results.extend(child_results)
            for child_run in child_results:
                child_inv = stage_data(child_run, "inventory")
                if child_inv:
                    seen.update(m["id"] for m in child_inv["members"])
        return results


def continue_children(parent):
    results = []
    for child in (stage_data(parent, "split") or {}).get("children", []):
        current = store.get(child["run_id"])
        results.append(run_one(child["book_id"], child["run_id"]) if current["state"] == "queued" else current)
    return results


def refresh_metadata(run_id, book_only=False):
    """Recheck a completed group online, retaining verified audio evidence in a new run.

    This does not take the batch worker's lock: it reads immutable audio for a
    completed group and refuses groups with an active run or a processing plan.
    A per-group lock serialises metadata refreshes without interrupting a batch
    that is analysing other books.
    """
    previous = store.get(run_id)
    inv = stage_data(previous, "inventory")
    if (
        previous["state"] not in ("review", "eligible", "metadata_review", "audiobook_review", "quality_review")
        or not inv
    ):
        raise ValueError("metadata refresh requires a completed ingest run")
    folder = Path(settings.data_dir()) / "ingest"
    lock = folder / (inv["group"] + ".metadata.lock")
    with lock.open("a+b") as f, oslib.file_lock(f):
        for member in inv["members"]:
            latest = store.latest(member["id"])
            if latest and latest["state"] == "running":
                raise ValueError("this group already has an active ingest run")
            if (
                catalogue.get_plan(member["id"])
                or (catalogue.get_processed(member["id"]) or {}).get("status") == "done"
            ):
                raise ValueError("explicitly replan before refreshing a group with a processing plan or output")
            if inventory.signature(member) != inventory.signature(catalogue.get_book(member["id"])):
                raise ValueError("catalogue metadata changed; start a new ingest run")
        if not inventory.snapshot_unchanged(inv):
            raise ValueError("source files changed; start a new ingest run")
        if stage_data(previous, "metadata") is None:
            raise ValueError("metadata evidence is incomplete; start a new ingest run")
        reuse = (
            ("inventory", "alignment", "quality", "selection")
            if all(stage_data(previous, s) is not None for s in ("alignment", "quality", "selection"))
            else ("inventory",)
        )
        book = catalogue.get_book(previous["book_id"])
        fresh = store.create(
            book["id"],
            dict(
                policy=policy.current(),
                online=True,
                book_signature=inventory.signature(book),
                metadata_refresh_of=run_id,
            ),
        )
        store.link(fresh, [m["id"] for m in inv["members"]])
        for stage in reuse:
            event = next(e for e in reversed(previous["events"]) if e["stage"] == stage and e["status"] == "done")
            store.event(fresh, stage, "done", event["metrics"], dict(event["data"], reused_from=run_id))
        if book_review_held(previous):
            store.event(
                fresh,
                "metadata_revocation",
                "done",
                data=dict(reused_from=run_id, reason="Manual book review retained during metadata refresh"),
            )
        return run_one(book["id"], run_id=fresh, book_only=book_only)


def quality_suggestion(selection, inv, metadata):
    """Weak listening-review hints; no claim that a bitrate proves perceived quality."""
    if not selection:
        return []
    clues = provenance.describe(inv or {}, selection, metadata or {})
    reasons = []
    rates = clues["bitrates_kbps"]
    if rates and max(rates) <= 48:
        reasons.append("Low encoded bitrate (48 kbps or less)")
    median = ((selection.get("acoustics") or {}).get("medians") or {}).get("quiet_to_active_db")
    if isinstance(median, (float, int)) and -12 <= median < 0:
        reasons.append("Limited sampled level variation")
    return reasons


def choose_recording(run_id, key):
    run = store.get(run_id)
    if (
        store.latest_id(run["book_id"]) != run_id
        or run["state"] != "audiobook_review"
        or not approval_valid(run, "metadata")
    ):
        raise ValueError("choose a recording while awaiting audiobook identification review")
    inv = _assert_review_inputs(run)
    meta = stage_data(run, "metadata") or {}
    if key not in {choice["key"] for choice in (meta.get("performance") or {}).get("choices", [])}:
        raise ValueError("recording is not a compatible saved candidate")
    identity = dict(stage_data(run, "identity"), _recording_choice=key)
    providers = dict(
        providers=dict(
            (stage_data(run, "providers") or {}).get("providers", {}),
            **(stage_data(run, "recording_providers") or {}).get("providers", {}),
        )
    )
    result, measure = metadata.refine_recording(identity, inv, providers, meta)
    store.event(run_id, "recording_choice", "done", data=dict(key=key, automatic=False))
    store.event(run_id, "recording", "done", measure, result)
    return dict(key=key, changed=True)


def rate_quality(run_id, rating):
    if rating not in ("good", "medium", "poor", "unrated"):
        raise ValueError("quality must be good, medium, poor or unrated")
    run = store.get(run_id)
    inv = stage_data(run, "inventory") or {}
    if (
        run["state"] not in ("quality_review", "review", "eligible")
        or not approval_valid(run, "metadata")
        or not approval_valid(run, "audiobook")
        or approval_valid(run, "quality")
        or not review_evaluation(run, "quality")["complete"]
    ):
        raise ValueError("quality can only be rated while awaiting quality approval")
    if any(catalogue.get_plan(m["id"]) for m in inv.get("members", [])):
        raise ValueError("remove the existing processing plan before changing quality")
    if rating == "medium" and (stage_data(run, "identity") or {}).get("kind") != "book":
        raise ValueError("Diamond is only available for standard audiobooks")
    store.event(run_id, "quality_rating", "done", data=dict(rating=rating))
    return dict(rating=rating)


def apply_quality_rating(plan, run):
    rating = (stage_data(run, "quality_rating") or {}).get("rating", "unrated")
    plan = dict(plan, warnings=list(plan["warnings"]))
    if rating == "medium":
        selection = stage_data(run, "selection") or {}
        plan = copy.deepcopy(plan)
        for chapter in plan["chapters"]:
            chapter["mode"] = "encode"
            chapter["tier"] = "V3"
        plan["est_bytes"] = int(
            sum(chapter["duration"] * P.TIERS["V3"]["kbps"] * 1000 / 8 for chapter in plan["chapters"])
        )
        plan["enhancement"] = dict(
            method="diamond-pause-level-v1",
            quality_rating="medium",
            seed=0,
            device="configured",
            level="gated rolling RMS ±3 dB",
        )
        plan["warnings"].append(
            "Diamond generates speech and may alter words; listen to the output. Processing can take a long time."
        )
        if acoustics.continuous_sound_cue(selection.get("acoustics"))["flagged"]:
            plan["warnings"].append("Continuous background detected: music or effects may be removed by Diamond.")
    return plan


def enqueue(run_id, automatic=False):
    run = store.get(run_id)
    inv = stage_data(run, "inventory")
    planned = stage_data(run, "planning")
    selection = stage_data(run, "selection")
    require_approvals(run)
    if run["state"] not in ("review", "eligible") or not inv or not planned or not planned.get("plan"):
        raise ValueError("ingest has no completed processing plan")
    if not inventory.snapshot_unchanged(inv):
        raise ValueError("source files or metadata changed; ingest again")
    plan = planned["plan"]
    bid = inv["primary_id"]
    if not inventory.unchanged(plan["ingest"].get("evidence_manifest", [])):
        raise ValueError("archived evidence changed; ingest again")
    for member in inv["members"]:
        if inventory.signature(member) != inventory.signature(catalogue.get_book(member["id"])):
            raise ValueError("group catalogue metadata changed; ingest again")
    if plan["ingest"]["book_signature"] != inventory.signature(catalogue.get_book(bid)):
        raise ValueError("catalogue metadata changed; ingest again")
    if automatic:
        current = policy.current()
        if not evaluate(run_id, current)["auto_approve"]:
            raise ValueError("thresholds do not permit automatic approval")
    for member in inv["members"]:
        prior = catalogue.get_plan(member["id"])
        if prior:
            raise ValueError("group already has a processing plan; explicitly replan before replacing it")
        if (catalogue.get_processed(member["id"]) or {}).get("status") == "done":
            raise ValueError("group already processed")
    checks = evaluate(run_id, save=False)
    plan = dict(
        plan,
        warnings=list(plan["warnings"])
        + [f"ingest: {c['metric']} {c['reason']}" for c in checks["checks"] if not c["passed"]],
    )
    plan = apply_quality_rating(plan, run)
    if automatic:
        plan["ingest"]["auto_policy_revision"] = policy.current()["revision"]
    catalogue.enqueue_ingest_plan(bid, [m["id"] for m in inv["members"]], plan)
    if automatic:
        Q.decide([bid], True)
    store.event(
        run_id,
        "queue",
        "done",
        data=dict(book_id=bid, approved=automatic, policy_revision=policy.current()["revision"]),
    )
    return dict(book_id=bid, approved=automatic)


def header_phase(run, value):
    """Classify immutable consent IDs cheaply; only the selected page loads evidence."""

    def valid(phase):
        if book_review_held(run):
            return False
        if run["state"] == phase + "_review":
            return False
        approval = raw_stage(run, phase + "_approval")
        return bool(
            approval
            and approval.get("signature") == review_signature(run, phase)
            and (
                not approval.get("automatic")
                or (phase == "metadata" and book_confirmed(run))
                or approval.get("policy_revision") == value.get("revision", 0)
            )
        )

    if not valid("metadata"):
        return "metadata"
    if run["state"] == "audiobook_review" or (not legacy_metadata_consent(run) and not valid("audiobook")):
        return "audiobook"
    if not valid("quality"):
        return "quality"
    return "processing"


def book_lookup_status(run, work):
    """Expose saved lookup outcomes without transferring candidate payloads or searching again."""
    attempts = (stage_data(run, "providers") or {}).get("providers", {})
    return [
        dict(
            source=name,
            status="confirmed"
            if name in work.get("records", {})
            else "error"
            if result.get("status") == "error"
            else "no_match",
            reason=result.get("reason", "")
            if result.get("status") == "error"
            else ""
            if name in work.get("records", {})
            else "No matching book identity was accepted from this source.",
        )
        for name, result in attempts.items()
    ]


def review_sort_key(run, value, sort):
    """Rank compact saved evidence before pagination; opening a queue never fetches providers."""
    measure = {}
    identity = {}
    meta = {}
    for event in run["events"]:
        measure.update(event["metrics"])
        if event["stage"] == "identity":
            identity = event["summary"]
        if event["stage"] in ("metadata", "recording"):
            meta = event["summary"]
    measure.update(meta.get("analysis_metrics") or {})
    checks = policy.evaluate(measure, identity.get("kind", "unknown"), value)["checks"]
    passed = sum(
        c["passed"]
        for c in checks
        if c["metric"].startswith(
            ("inventory.", "identity.", "metadata.original_work_link", "metadata.work_families", "work.")
        )
    )

    def number(name):
        n = measure.get(name)
        return n if type(n) in (int, float) and math.isfinite(n) else 0

    score = number("work.score")
    families = number("metadata.work_families")
    fields = sum(bool(meta.get(field)) for field in ("title", "authors", "year", "publisher"))
    title = (meta.get("title") or identity.get("title") or run["title"] or "").casefold()
    if sort == "title":
        return (title, run["id"])
    if sort == "metadata":
        return (-fields, -families, -passed, -score, title, run["id"])
    return (-passed, -score, -families, -fields, title, run["id"])


def dashboard(offset=0, limit=50, phase=None, sort=None):
    total = store.run_count()
    if phase:
        if phase not in REVIEW_STAGES:
            raise ValueError("review stage must be metadata or quality, or audiobook")
        value = policy.current()
        order = sort or ("strongest" if phase == "metadata" else "newest")
        if order not in ("strongest", "metadata", "newest", "oldest", "title"):
            raise ValueError("invalid review sort")
        waiting = [r for r in store.review_headers() if header_phase(r, value) == phase]
        if order == "oldest":
            waiting.reverse()
        elif order != "newest":
            waiting.sort(key=lambda run: review_sort_key(run, value, order))
        total = len(waiting)
        headers = waiting[offset : offset + limit]
        rows = (store.get(r["id"]) for r in headers)
    else:
        headers = store.page_headers(limit, offset)
        rows = (store.get(r["id"]) for r in headers)
    if _SAVED_EVIDENCE.get():
        cached = read_models.many("ingest", [h["id"] for h in headers])
        views = []
        for header in headers:
            view = dict(cached.get(header["id"]) or _pending_view(header), state=header["state"])
            if phase:
                current = header_phase(header, value)
                view["review_phase"] = current
                order = list(REVIEW_STAGES) + ["processing"]
                view["reviews"] = {
                    name: dict(review, approved=order.index(name) < order.index(current))
                    for name, review in view["reviews"].items()
                }
            views.append(view)
    else:
        views = _review_rows(rows)
    return dict(runs=views, policy=policy.current(), stages=list(STAGES), offset=offset, total=total)


def _review_rows(rows):
    output = []
    groups = set()
    for r in rows:
        inv = stage_data(r, "inventory") or {}
        identity = stage_data(r, "identity") or {}
        ev = stage_data(r, "eligibility")
        sel = stage_data(r, "selection")
        latest = r["events"][-1] if r["events"] else {}
        output.append(
            dict(
                id=r["id"],
                book_id=r["book_id"],
                title=identity.get("book", {}).get("title", ""),
                quality_rating=(stage_data(r, "quality_rating") or {}).get("rating", "unrated"),
                quality_suggestions=quality_suggestion(sel, inv, stage_data(r, "metadata")),
                state=r["state"],
                created=r["created"],
                stage=latest.get("stage"),
                metrics=metrics(r),
                stages=[
                    dict(
                        stage=s,
                        **dict(
                            {k: e[k] for k in ("status", "elapsed", "error", "metrics")},
                            elapsed=(datetime.now(UTC) - datetime.fromisoformat(e["created"])).total_seconds()
                            if e["status"] == "running"
                            else e["elapsed"],
                        ),
                    )
                    for s in STAGES + ("auto_metadata", "queue", "processing")
                    for e in [next((e for e in reversed(r["events"]) if e["stage"] == s), None)]
                    if e
                ],
                selection=dict(
                    files=[Path(p).name for p in sel["paths"]],
                    reason=sel["reason"],
                    ranking=sel["ranking"],
                    quiet_passages=sel.get("quiet_passages"),
                    acoustics=sel.get("acoustics"),
                    continuous_sound=acoustics.continuous_sound_cue(sel.get("acoustics")),
                    loudness_range=sel.get("measurements", {}).get("LRA"),
                    provenance=provenance.describe(inv, sel, stage_data(r, "metadata") or {}),
                )
                if sel
                else None,
                eligibility=ev,
                current_evaluation=policy.evaluate(metrics(r), identity.get("kind", "unknown")),
                members=[b["id"] for b in inv.get("members", [])],
            )
        )
        reviews = {
            phase: dict(
                review_evaluation(r, phase),
                approved=approval_valid(r, phase),
                approval=stage_data(r, phase + "_approval"),
            )
            for phase in REVIEW_STAGES
        }
        phase = review_phase(r)
        if r["state"] in ("processing", "processed", "processing_failed"):
            phase = "processing"
        meta = stage_data(r, "metadata") or {}
        resolved = meta.get("resolved")
        group = inv.get("group", str(r["book_id"]))
        latest_id = store.latest_id(inv.get("primary_id", r["book_id"]))
        output[-1].update(
            review_phase=phase,
            reviews=reviews,
            superseded=group in groups or bool(latest_id and latest_id != r["id"]),
            processing_proposed=store.plan_exists(inv.get("primary_id", r["book_id"])),
            metadata=dict(
                rows=sources.rows(identity.get("book", {}), resolved) if resolved else [],
                recording={
                    k: (meta.get("recording") or {}).get(k) for k in ("asin", "language", "runtime_minutes", "abridged")
                },
                source_families=metrics(r).get("metadata.work_families"),
                match_score=metrics(r).get("work.score"),
                corroboration=meta.get("corroboration", {}),
                work=dict(meta["work"], lookups=book_lookup_status(r, meta["work"])) if meta.get("work") else None,
                performance=meta.get("performance"),
            ),
        )
        if resolved:
            output[-1]["title"] = resolved["title"]
        groups.add(group)
    return output


def regroup_collection(run_id):
    """Replace a pending mixed-folder run with fresh, separately reviewed works."""
    previous = store.get(run_id)
    inv = stage_data(previous, "inventory") or {}
    bid = previous["book_id"]
    if store.latest_id(bid) != run_id or not inv.get("files") or len(grouping.partition(inv["files"], inv["root"])) < 2:
        return []
    book = catalogue.get_book(bid)
    if (
        Path(book["path"]).resolve() != Path(inv["root"]).resolve()
        or catalogue.get_plan(bid)
        or catalogue.get_processed(bid)
    ):
        return []
    if not inventory.snapshot_unchanged(inv):
        raise ValueError("collection sources changed; start a fresh ingest")
    return [r["id"] for r in run(ids=[bid], limit=1, online=previous["options"].get("online", True))]


def publish_run(run_id):
    """Materialize a single archived review on the worker; HTTP never loads its raw evidence."""
    with saved_evidence_only():
        view = _review_rows([store.get(run_id)])[0]
    read_models.save("ingest", run_id, view)


def _pending_view(header):
    # A new/legacy view is populated by its job. Unknown analysis cannot authorize approval.
    measure = {}
    title = header.get("title") or f"Book {header['book_id']}"
    for event in header.get("events", []):
        measure.update(event.get("metrics", {}))
        if event["stage"] in ("metadata", "recording"):
            title = event.get("summary", {}).get("title") or title
    value = policy.current()
    phase = header_phase(header, value)
    reviews = {
        name: dict(approved=False, eligible=False, complete=False, checks=[], kind_allowed=False)
        for name in REVIEW_STAGES
    }
    return dict(
        id=header["id"],
        book_id=header["book_id"],
        created=header["created"],
        state=header["state"],
        title=title,
        review_phase=phase,
        reviews=reviews,
        metrics=measure,
        stages=[],
        members=[],
        quality_rating="unrated",
        quality_suggestions=[],
        selection=None,
        eligibility=None,
        current_evaluation=policy.evaluate(measure, "unknown", value),
        superseded=False,
        metadata=None,
    )
