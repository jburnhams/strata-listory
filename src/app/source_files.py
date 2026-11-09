"""Saved source-location summaries, without touching the source filesystem."""

from pathlib import Path

from app import scan
from app.ingest import store


def describe(book):
    path = book.get("path") or ""
    if not path:
        return dict(folder="", files=[], count=0, available=False)
    inv = store.source_summary(book["id"])
    root = Path(inv["root"] if inv and inv["root"] else path)
    folder = root.parent if root.suffix.lower() in scan.AUDIO else root
    paths = [Path(p) for p in inv["paths"]] if inv else []
    names = [str(p.relative_to(folder)) if p.is_relative_to(folder) else str(p) for p in paths]
    result = dict(
        folder=str(folder), files=names, count=inv["count"] if inv else book.get("file_count"), available=bool(inv)
    )
    if inv and inv["grouping"]:
        result["grouping"] = inv["grouping"]
    return result
