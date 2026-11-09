"""Derived evidence is persisted once; adding feature columns does not rerun old analysis."""

from copy import deepcopy

import pytest

from app import read_models
from app.ingest import match_audit as A
from app.ingest import training_cache as C
from app.providers.base import candidate


@pytest.fixture
def run():
    c = candidate("openlibrary", id="/works/OL1W", title="A Book", authors=["A Writer"], ratings_count=100, rating=4)
    return dict(
        id="saved",
        book_id=1,
        state="metadata_review",
        events=[
            dict(id=i, stage=s, status="done", data=d)
            for i, (s, d) in enumerate(
                [
                    ("identity", dict(book=dict(title="A Book", author="A Writer", asin=""))),
                    ("providers", dict(providers=dict(openlibrary=dict(candidates=[c])))),
                ],
                1,
            )
        ],
    )


def test_cached_features_survive_new_columns_and_labels_without_reanalysing_inputs(run, monkeypatch, tmp_path):
    cache = C.FeatureCache(tmp_path / "cache.sqlite")
    [original] = cache.candidates(run)
    raw = cache.get("candidates", C.input_key(run))
    raw[0]["features"]["title_edit"] = 0.123
    for name in A.RATING_FEATURES:
        raw[0]["features"].pop(name)
    cache.save("candidates", C.input_key(run), raw)
    monkeypatch.setattr(A, "features", lambda *a, **kw: pytest.fail("old title features were recalculated"))
    [updated] = C.FeatureCache(tmp_path / "cache.sqlite").candidates(run)
    assert updated["features"]["title_edit"] == 0.123
    assert updated["features"]["ratings_count_log"] > 4
    assert updated["features"]["title_rule_conflict"] == original["features"]["title_rule_conflict"]
    approved = deepcopy(run)
    approved["events"].append(dict(id=3, stage="metadata_approval", status="done", data=dict(automatic=False)))
    assert C.input_key(approved) == C.input_key(run)
    assert cache.candidates(approved)[0]["approval"] == dict(automatic=False)
    changed = deepcopy(run)
    changed["events"][1]["id"] = 4
    with pytest.raises(pytest.fail.Exception):
        cache.candidates(changed)


def test_seed_keeps_evidence_but_does_not_reuse_old_training_labels(run, tmp_path):
    cache = C.FeatureCache(tmp_path / "cache.sqlite")
    rows = A.candidate_rows(run)
    rows[0].update(label=1, label_origin="manual_approval", target=dict(title="Other"), split="test")
    cache.seed(run, rows)
    [saved] = cache.candidates(run)
    assert (
        saved["label"] is None and saved["work_group"] is None and "label_origin" not in saved and "target" not in saved
    )


def test_generated_examples_are_reused_and_only_new_queries_are_mined(run, tmp_path):
    from app.ingest import match_model as M

    cache = C.FeatureCache(tmp_path / "cache.sqlite")
    [a] = cache.candidates(run)
    a.update(
        label=1,
        label_origin="manual_approval",
        work_group="g1",
        split="train",
        target=dict(title="A Book", author="A Writer"),
    )
    b = deepcopy(a)
    b.update(
        run_id="other", candidate_key="other", work_group="g2", target=dict(title="Other Book", author="Other Writer")
    )
    b["local_book"] = dict(title="Other Book", author="Other Writer")
    b["candidate"] = dict(
        b["candidate"], id="/works/OL2W", title="Other Book", authors=["Other Writer"], ratings_count=500
    )
    rows = [a, b]
    calls = []

    def build(rows, **kwargs):
        calls.append(kwargs["query_ids"])
        return M.add_cross_work_negatives(rows, **kwargs)

    first = cache.negatives(rows, build)
    second = cache.negatives(rows, lambda *args, **kw: pytest.fail("training examples were regenerated"))
    assert calls == [{"saved", "other"}] and len(first) == len(second) == 4
    c = deepcopy(b)
    c.update(run_id="new", candidate_key="new", work_group="g3", target=dict(title="Third Book", author="Third Writer"))
    c["local_book"] = dict(title="Third Book", author="Third Writer")
    c["candidate"] = dict(c["candidate"], id="/works/OL3W", title="Third Book", authors=["Third Writer"])
    cache.negatives(rows + [c], build)
    assert calls[-1] == {"new"}
    changed = deepcopy(b)
    changed["candidate"]["ratings_count"] = 900
    updated = cache.negatives([a, changed], build)
    assert calls[-1] == {"saved", "other"}
    assert next(r for r in updated if r.get("foreign_run_id") == "other")["candidate"]["ratings_count"] == 900


def test_label_evidence_is_cached_until_a_new_decision_arrives(run, monkeypatch, tmp_path):
    from app.ingest import match_model as M

    cache = C.FeatureCache(tmp_path / "cache.sqlite")
    calls = []
    monkeypatch.setattr(M, "trusted_rows", lambda run, **kw: calls.append(run["id"]) or [])
    assert cache.trusted(run) == [] and cache.trusted(run) == []
    assert calls == ["saved"]
    fresh = deepcopy(run)
    fresh["events"].append(dict(id=3, stage="metadata_approval", status="done", data={}))
    cache.trusted(fresh)
    assert calls == ["saved", "saved"]


def test_published_runs_queue_derived_evidence_work_without_computing_it(run, monkeypatch):
    dirty = set()
    token = C.DIRTY.set(dirty)
    try:
        C.mark_dirty(run)
    finally:
        C.DIRTY.reset(token)
    calls = []

    class Jobs:
        def submit(self, *args, **kwargs):
            calls.append((args, kwargs))

    C.enqueue(Jobs(), dirty)
    assert calls == [(("system", "training_features"), dict(retry=True, run_id="saved", revision=2))]
    monkeypatch.setattr("app.ingest.store.get", lambda rid: run)
    result = C.update("saved", 2)
    assert result == dict(training_candidates=0, current_candidates=1)
    assert read_models.get("training_dirty", "saved") is None


def test_background_example_pool_excludes_withdrawn_or_superseded_consent(tmp_path):
    from app import catalogue
    from app.ingest import store

    book = catalogue.add_book(title="A Book")
    rid = store.create(book["id"], {})
    store.event(rid, "metadata_approval", "done", data={})
    cache = C.FeatureCache(tmp_path / "cache.sqlite")
    key = C.label_key(store.get(rid))
    cache.save("labels", key, [dict(run_id=rid, label=1)])
    cache.save("label_head", rid, dict(key=key))
    assert cache.current_labels() == [dict(run_id=rid, label=1)]
    store.event(rid, "metadata_revocation", "done", data={})
    assert cache.current_labels() == []
    current = C.label_key(store.get(rid))
    cache.save("labels", current, [dict(run_id=rid, label=1)])
    cache.save("label_head", rid, dict(key=current))
    assert cache.current_labels()
    store.create(book["id"], {})
    assert cache.current_labels() == []
