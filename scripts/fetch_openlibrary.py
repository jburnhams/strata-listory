#!/usr/bin/env python3
"""Fetch a dated official Open Library snapshot with resumable transfers and checksums."""

import argparse
import hashlib
import json
import subprocess
import urllib.parse
import urllib.request
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

KINDS = (
    "authors",
    "works",
    "ratings",
    "redirects",
    "deletes",
    "editions",
)  # what the identification index uses; wikidata, lists, covers and reading-log are skipped


def latest_snapshot():
    query = urllib.parse.urlencode(
        {"q": "identifier:ol_dump_*", "fl[]": "identifier", "sort[]": "identifier desc", "rows": 1, "output": "json"}
    )
    with urllib.request.urlopen("https://archive.org/advancedsearch.php?" + query, timeout=60) as response:
        return json.load(response)["response"]["docs"][0]["identifier"].removeprefix("ol_dump_")


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("snapshot", help="monthly snapshot date, e.g. 2026-10-31, or 'latest'")
    parser.add_argument(
        "--directory", type=Path, default=Path(__file__).resolve().parent.parent / "data/openlibrary/dumps"
    )
    args = parser.parse_args()
    if args.snapshot == "latest":
        args.snapshot = latest_snapshot()
        print("latest snapshot", args.snapshot, flush=True)
    if len(args.snapshot) != 10 or any(c not in "0123456789-" for c in args.snapshot):
        parser.error("expected YYYY-MM-DD snapshot date")
    identifier = "ol_dump_" + args.snapshot
    with urllib.request.urlopen("https://archive.org/metadata/" + identifier, timeout=60) as response:
        metadata = json.load(response)
    files = {f["name"]: f for f in metadata["files"]}
    args.directory.mkdir(parents=True, exist_ok=True)
    (args.directory / "snapshot.json").write_text(
        json.dumps(dict(identifier=identifier, files=metadata["files"]), indent=2)
    )

    def fetch(kind):
        name = f"ol_dump_{kind}_{args.snapshot}.txt.gz"
        info = files[name]
        target = args.directory / f"{kind}.txt.gz"
        partial = target.with_suffix(target.suffix + ".part")
        url = f"https://archive.org/download/{identifier}/{name}"
        if not target.exists():
            print(f"{kind}: downloading {info['size']} bytes", flush=True)
            subprocess.run(
                [
                    "curl",
                    "--fail",
                    "--location",
                    "--silent",
                    "--show-error",
                    "--retry",
                    "8",
                    "--retry-delay",
                    "5",
                    "--connect-timeout",
                    "30",
                    "--continue-at",
                    "-",
                    "--output",
                    str(partial),
                    url,
                ],
                check=True,
            )
            candidate = partial
        else:
            candidate = target
        digest = hashlib.sha1()
        with candidate.open("rb") as stream:
            for chunk in iter(lambda: stream.read(4 * 1024 * 1024), b""):
                digest.update(chunk)
        if candidate.stat().st_size != int(info["size"]) or digest.hexdigest() != info["sha1"]:
            raise ValueError(f"{kind}: size/checksum mismatch; retained for inspection")
        if candidate != target:
            candidate.replace(target)
        (args.directory / f"{kind}.download.json").write_text(
            json.dumps(
                dict(url_effective=url, bytes=int(info["size"]), sha1=info["sha1"], snapshot=args.snapshot), indent=2
            )
        )
        print(f"{kind}: checksum verified", flush=True)

    with ThreadPoolExecutor(max_workers=3) as pool:
        list(pool.map(fetch, KINDS))


if __name__ == "__main__":
    main()
