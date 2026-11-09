"""On-demand listening copies of the selected source; never modify source audio."""

import hashlib
import json
import random
from pathlib import Path
from tempfile import TemporaryDirectory

from app import oslib, read_models, settings
from app.ingest import pipeline, store
from app.process import plan, probe

VERSION = "listening-v1-lufs20"


def context(run_id):
    run = store.get(run_id)
    inv = pipeline.stage_data(run, "inventory")
    selection = pipeline.stage_data(run, "selection")
    if not inv or not selection:
        raise ValueError("Selected audio is not ready for listening")
    by_path = {f["path"]: f for f in inv["files"]}
    files = [by_path[p] for p in selection["paths"]]
    # Fast freshness check on every request, including cache hits. Cache identity
    # also contains the saved content hashes and exact selected file order.
    for f in files:
        stat = Path(f["path"]).stat()
        if stat.st_size != f["size"] or stat.st_mtime_ns != f["mtime_ns"]:
            raise ValueError("Source audio changed; re-ingest before listening")
    key = hashlib.sha256(
        json.dumps(
            [VERSION, run_id, [(f["path"], f["sha256"], f["size"], f["mtime_ns"]) for f in files]], sort_keys=True
        ).encode()
    ).hexdigest()
    timeline = plan.timeline([dict(f["probe"], path=f["path"]) for f in files])
    duration = timeline[-1][1]
    if duration <= 0:
        raise ValueError("Selected audio has no duration")
    folder = Path(settings.data_dir()) / "listening-previews" / key
    rng = random.Random(key)
    length = min(30.0, duration / 5)
    samples = [
        dict(
            index=i,
            start_seconds=round(
                max(0, min(duration - length, duration * ((i + 0.5) / 5 + rng.uniform(-0.06, 0.06)) - length / 2)), 3
            ),
            duration_seconds=round(length, 3),
        )
        for i in range(5)
    ]
    return folder, timeline, samples


def status(run_id, ctx=None):
    if ctx is None:
        return read_models.get("previews", run_id, dict(samples=[]))
    folder, _, samples = ctx
    return dict(
        samples=[
            dict(
                s,
                url=f"/api/ingest/runs/{run_id}/samples/{s['index']}",
                status="ready"
                if (folder / f"{s['index']}.mp3").is_file()
                else "failed"
                if (folder / f"{s['index']}.error").exists()
                else "generating",
            )
            for s in samples
        ]
    )


def generate(ctx, index):
    folder, timeline, samples = ctx
    s = samples[index]
    folder.mkdir(parents=True, exist_ok=True)
    output = folder / f"{index}.mp3"
    with (folder / f"{index}.lock").open("a+b") as lock, oslib.file_lock(lock):
        if output.is_file():
            return output
        with TemporaryDirectory(dir=folder) as td:
            temp = Path(td) / "sample.mp3"
            args = probe.input_args(
                plan.segments(timeline, s["start_seconds"], s["start_seconds"] + s["duration_seconds"]), td
            )
            result = probe.run(
                ["ffmpeg", "-nostdin", "-hide_banner", "-v", "error"]
                + args
                + [
                    "-map",
                    "0:a:0",
                    "-vn",
                    "-t",
                    str(s["duration_seconds"]),
                    "-af",
                    "loudnorm=I=-20:TP=-1.5:LRA=11",
                    "-ar",
                    "44100",
                    "-c:a",
                    "libmp3lame",
                    "-b:a",
                    "192k",
                    str(temp),
                ],
                120,
            )
            if result.returncode or not temp.is_file() or not temp.stat().st_size:
                raise ValueError("Could not generate listening sample")
            temp.replace(output)
        (folder / f"{index}.error").unlink(missing_ok=True)
    return output


def generate_all(ctx):
    folder, _, samples = ctx
    for s in samples:
        try:
            generate(ctx, s["index"])
        except (ValueError, OSError, probe.ToolError):
            folder.mkdir(parents=True, exist_ok=True)
            (folder / f"{s['index']}.error").write_text("Preview generation failed", encoding="utf8")


def prepare(run_id):
    """Called only by the shared job worker; no independent thread pool."""
    ctx = context(run_id)
    read_models.save("previews", run_id, status(run_id, ctx))
    generate_all(ctx)
    result = status(run_id, ctx)
    read_models.save("preview_path", run_id, str(ctx[0]))
    read_models.save("previews", run_id, result)
    return result


def audio(run_id, index):
    if index not in range(5):
        raise ValueError("No such listening sample")
    result = read_models.get("previews", run_id) or {}
    sample = next((s for s in result.get("samples", []) if s["index"] == index and s["status"] == "ready"), None)
    if not sample:
        raise ValueError("Listening sample is not ready yet")
    location = read_models.get("preview_path", run_id)
    if not location:
        raise ValueError("Listening sample is not ready yet")
    path = Path(location) / f"{index}.mp3"
    if not path.is_file():
        raise ValueError("Listening sample is not ready yet")
    return path
