"""Listening preview selection, caching, freshness and failure boundaries."""

from pathlib import Path
from types import SimpleNamespace

import pytest

from app import settings
from app.ingest import previews as V
from app.ingest import store


@pytest.fixture
def preview(tmp_path):
    path = tmp_path / "source.mp3"
    path.write_bytes(b"source")
    stat = path.stat()
    f = dict(
        path=str(path),
        size=stat.st_size,
        mtime_ns=stat.st_mtime_ns,
        sha256="savedhash",
        probe=dict(path=str(path), duration=1000),
    )
    rid = store.create(1, {})
    store.event(rid, "inventory", "done", {}, dict(files=[f]))
    store.event(rid, "selection", "done", {}, dict(paths=[str(path)]))
    return rid, path


def test_preview_positions_are_stable_and_reads_do_not_inspect_source(preview, monkeypatch):
    rid, path = preview
    monkeypatch.setattr(V.probe, "run", lambda *a: pytest.fail("listing must not decode"))
    assert V.status(rid) == dict(samples=[])
    ctx = V.context(rid)
    a = V.status(rid, ctx)
    assert a == V.status(rid, ctx) and len(a["samples"]) == 5
    assert all(i * 200 <= s["start_seconds"] <= i * 200 + 170 for i, s in enumerate(a["samples"]))
    assert not (Path(settings.data_dir()) / "listening-previews").exists()
    path.write_bytes(b"changed")
    with pytest.raises(ValueError, match="changed"):
        V.context(rid)
    with pytest.raises(ValueError, match="No such"):
        V.audio(rid, -1)
    with pytest.raises(ValueError, match="not ready"):
        V.context(store.create(1, {}))


def test_worker_preparation_records_failures_and_retries(preview, monkeypatch):
    rid, _ = preview
    monkeypatch.setattr(V.probe, "run", lambda *args: SimpleNamespace(returncode=1))
    assert all(s["status"] == "failed" for s in V.prepare(rid)["samples"])

    def encode(cmd, timeout):
        Path(cmd[-1]).write_bytes(b"mp3")
        return SimpleNamespace(returncode=0)

    monkeypatch.setattr(V.probe, "run", encode)
    assert all(s["status"] == "ready" for s in V.prepare(rid)["samples"])
    monkeypatch.setattr(V, "context", lambda *a: pytest.fail("GET must not stat or load source evidence"))
    assert len(V.status(rid)["samples"]) == 5 and V.audio(rid, 0).read_bytes() == b"mp3"


def test_preview_normalizes_and_caches_atomically(preview, monkeypatch):
    rid, _ = preview
    calls = []

    def encode(cmd, timeout):
        calls.append(cmd)
        Path(cmd[-1]).write_bytes(b"mp3")
        return SimpleNamespace(returncode=0)

    monkeypatch.setattr(V.probe, "run", encode)
    V.prepare(rid)
    assert len(calls) == 5 and all("loudnorm=I=-20:TP=-1.5:LRA=11" in c for c in calls)
    assert all(s["status"] == "ready" for s in V.status(rid)["samples"])
    V.prepare(rid)
    assert len(calls) == 5
    assert V.audio(rid, 0).read_bytes() == b"mp3"


@pytest.mark.parametrize("failure", [V.probe.ToolError("timeout"), OSError("disk"), None])
def test_failed_encoding_leaves_no_partial_audio(preview, monkeypatch, failure):
    rid, _ = preview

    def fail(cmd, timeout):
        Path(cmd[-1]).write_bytes(b"partial")
        if failure:
            raise failure
        return SimpleNamespace(returncode=1)

    monkeypatch.setattr(V.probe, "run", fail)
    ctx = V.context(rid)
    V.generate_all(ctx)
    assert not list(ctx[0].glob("*.mp3")) and len(list(ctx[0].glob("*.error"))) == 5
    assert not [p for p in ctx[0].iterdir() if p.is_dir()]
