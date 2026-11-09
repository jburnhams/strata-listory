from types import SimpleNamespace

import pytest

from app.process import enhance, probe


def test_medium_plan_restores_each_chapter_and_remeasures(monkeypatch, tmp_path):
    code = tmp_path / "code"
    (code / "diamond").mkdir(parents=True)
    (code / "diamond/infer.py").touch()
    model = tmp_path / "model"
    model.touch()
    python = tmp_path / "python"
    python.touch()
    for k, v in [("STRATA_DIAMOND_CODE", code), ("STRATA_DIAMOND_MODEL", model), ("STRATA_DIAMOND_PYTHON", python)]:
        monkeypatch.setenv(k, str(v))
    monkeypatch.setattr(probe, "input_args", lambda segments, folder: ["-i", segments[0][0]])

    def decode(cmd, timeout):
        from pathlib import Path

        Path(cmd[-1]).touch()
        return SimpleNamespace(returncode=0, stderr="")

    monkeypatch.setattr(probe, "run", decode)

    def worker(args, **kwargs):
        import json
        from pathlib import Path

        for job in json.loads(Path(args[-1]).read_text()):
            Path(job["output"]).touch()
        return SimpleNamespace(returncode=0, stderr="")

    monkeypatch.setattr(enhance.subprocess, "run", worker)
    monkeypatch.setattr(probe, "measure_book", lambda *args: {"I": -22, "TP": -4})
    plan = {
        "chapters": [{"segments": [("source", 0, 10)], "mode": "copy", "tier": "speech"}],
        "channels": 2,
        "loudness": {},
        "enhancement": {"method": "diamond-pause-level-v1"},
    }
    out = enhance.prepare(plan, str(tmp_path))
    assert out["chapters"][0]["mode"] == "encode"
    assert out["chapters"][0]["segments"][0][0].endswith("diamond-output-0.wav")
    assert out["loudness"]["apply_db"] == 2.5
    assert plan["chapters"][0]["mode"] == "copy"


def test_missing_model_fails_instead_of_silent_bypass(monkeypatch, tmp_path):
    monkeypatch.setenv("STRATA_DIAMOND_CODE", str(tmp_path / "missing"))
    with pytest.raises(probe.ToolError, match="environment or model missing"):
        enhance.prepare({"chapters": []}, str(tmp_path))
