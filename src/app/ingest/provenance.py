"""Source-format facts and explicitly labelled, unverified provenance clues."""

import re
from pathlib import Path

MEDIUM = re.compile(
    r"\b(cassette|tape|vinyl|lp|cd|compact disc|disc|web[- ]?rip|digital download|stream(?:ing)?)\b", re.I
)
KEYS = ("album", "title", "comment", "description", "source", "media", "originaldate", "date", "encoder")


def describe(inv, selection, metadata):
    selected = set(selection.get("paths", []))
    files = [f for f in inv.get("files", []) if f["path"] in selected]
    codecs = sorted({f["probe"].get("codec") for f in files if f["probe"].get("codec")})
    bitrates = sorted(
        {f["probe"].get("bit_rate") for f in files if f["probe"].get("bit_rate") and f["probe"]["bit_rate"] > 0}
    )
    rates = sorted(
        {
            f["probe"].get("sample_rate")
            for f in files
            if f["probe"].get("sample_rate") and f["probe"]["sample_rate"] > 0
        }
    )
    extensions = sorted({Path(f["path"]).suffix.lower() for f in files if Path(f["path"]).suffix})
    clues = []
    seen = set()

    def add(source, value):
        value = str(value).strip()
        if value and MEDIUM.search(value) and (source, value) not in seen:
            seen.add((source, value))
            clues.append(dict(source=source, value=value))

    # Paths and tags are assertions about naming/metadata, not proof of the original medium.
    for f in files:
        p = Path(f["path"])
        add("folder name", p.parent.name)
        add("file name", p.name)
        tags = f["probe"].get("tags") or {}
        for key in KEYS:
            if tags.get(key):
                add("tag: " + key, tags[key])
    dates = []
    for name, record in (metadata.get("records") or {}).items():
        if name in ("openlibrary", "hardcover", "googlebooks", "catalogue"):
            continue
        if record.get("release_date") or record.get("release_year"):
            dates.append(dict(source=name, release_date=record.get("release_date") or str(record["release_year"])))
    tag_dates = sorted(
        {
            str(f["probe"].get("tags", {}).get(k))
            for f in files
            for k in ("date", "originaldate")
            if f["probe"].get("tags", {}).get(k)
        }
    )
    return dict(
        file_count=len(files),
        codecs=codecs,
        extensions=extensions,
        bitrates_kbps=bitrates,
        sample_rates_hz=rates,
        medium_clues=clues[:12],
        medium_clues_truncated=len(clues) > 12,
        recording_release_dates=dates,
        embedded_dates=tag_dates,
        note="Media and dates are source assertions; neither proves the recording medium or the date of the performance. Bitrate and codec alone do not establish quality.",
    )
