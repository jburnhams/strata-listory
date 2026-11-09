import math
from array import array

from app.ingest import acoustics


def test_window_statistics_reflect_quiet_noise_and_tonal_balance():
    rate = acoustics.RATE

    def sample(noise, tone):
        return array(
            "h",
            (
                round(
                    32767
                    * (tone * math.sin(2 * math.pi * 250 * t / rate) + noise * math.sin(2 * math.pi * 4500 * t / rate))
                )
                for t in range(rate * 3)
            ),
        )

    quiet = sample(0.001, 0.001) + sample(0.001, 0.1)
    noisy = sample(0.03, 0.001) + sample(0.03, 0.1)
    clean_result = acoustics.analyse_samples(quiet)
    noisy_result = acoustics.analyse_samples(noisy)
    assert noisy_result["quiet_high_dbfs"] > clean_result["quiet_high_dbfs"] + 15
    assert noisy_result["quiet_to_active_db"] > clean_result["quiet_to_active_db"]
    assert clean_result["active_low_to_presence_db"] is not None
    assert acoustics.analyse_samples(array("h", [0]) * (rate * 3)) is None
    assert acoustics.analyse_samples(array("h", [0]) * 10) is None


def test_continuous_sound_is_a_review_cue_and_requires_two_samples():
    def assessment(*values):
        return {"samples": [{"result": {"quiet_to_active_db": value}} for value in values]}

    assert acoustics.continuous_sound_cue(assessment(-3.3, -9.2, -4.7))["flagged"]
    assert not acoustics.continuous_sound_cue(assessment(-21.3, -11.2, -6.9))["flagged"]
    assert not acoustics.continuous_sound_cue(assessment(-3, None, float("nan")))["flagged"]
    repeated = {"samples": [{"start_seconds": 0, "result": {"quiet_to_active_db": -3}}] * 3}
    assert not acoustics.continuous_sound_cue(repeated)["flagged"]
    assert acoustics.continuous_sound_cue(None)["valid_samples"] == 0


def test_quality_suggestions_are_hints_from_measurements():
    from app.ingest.pipeline import quality_suggestion

    selection = {"paths": ["a"], "acoustics": {"medians": {"quiet_to_active_db": -9}}}
    inv = {"files": [{"path": "a", "probe": {"bit_rate": 32}}]}
    reasons = quality_suggestion(selection, inv, {})
    assert len(reasons) == 2
    assert quality_suggestion(None, inv, {}) == []


def test_confirmed_medium_adds_diamond_to_the_plan():
    from app.ingest.pipeline import apply_quality_rating

    plan = {"warnings": [], "chapters": [{"duration": 60, "mode": "copy", "tier": "V5"}]}
    run = {
        "events": [
            {"stage": "quality_rating", "status": "done", "data": {"rating": "medium"}},
            {"stage": "selection", "status": "done", "data": {"acoustics": None}},
        ]
    }
    enhanced = apply_quality_rating(plan, run)
    assert enhanced["enhancement"]["method"] == "diamond-pause-level-v1"
    assert "alter words" in enhanced["warnings"][0]
    assert "enhancement" not in apply_quality_rating(plan, {"events": []})
    assert plan["chapters"][0]["mode"] == "copy"
    assert enhanced["chapters"][0]["tier"] == "V3"


def test_rating_requires_completed_ingest_and_a_book(monkeypatch):
    import pytest

    from app.ingest import pipeline

    saved = []
    run = {
        "id": "r",
        "state": "quality_review",
        "events": [
            {"id": 1, "stage": "identity", "status": "done", "data": {"kind": "book"}},
            {"id": 2, "stage": "inventory", "status": "done", "data": {"members": [{"id": 4}]}},
        ],
    }
    for stage in ("providers", "metadata", "alignment", "quality", "selection"):
        run["events"].append(dict(id=len(run["events"]) + 1, stage=stage, status="done", data={}))
    run["events"].append(
        dict(
            id=8,
            stage="metadata_approval",
            status="done",
            data=dict(signature=pipeline.review_signature(run, "metadata"), automatic=False),
        )
    )
    for event in run["events"]:
        event["metrics"] = {}
    monkeypatch.setattr(pipeline.store, "get", lambda _: run)
    monkeypatch.setattr(pipeline.catalogue, "get_plan", lambda _: None)
    monkeypatch.setattr(pipeline.store, "event", lambda *args, **kw: saved.append(kw["data"]))
    assert pipeline.rate_quality("r", "medium") == {"rating": "medium"}
    assert saved == [{"rating": "medium"}]
    run["events"][0]["data"]["kind"] = "radio"
    with pytest.raises(ValueError, match="standard audiobooks"):
        pipeline.rate_quality("r", "medium")
    run["state"] = "processed"
    with pytest.raises(ValueError, match="awaiting quality approval"):
        pipeline.rate_quality("r", "good")
