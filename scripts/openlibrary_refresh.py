#!/usr/bin/env python3
"""Check whether Open Library has published a newer monthly dump and, if so, adopt it (run weekly by listory-openlibrary-refresh.timer). Safe to run any time; a run with nothing new is a few seconds.

1. Latest snapshot on archive.org vs the one in data/openlibrary/dumps/snapshot.json. Same: stop.
2. Download the six dumps we use into data/openlibrary/dumps-<date>/ (resumable, SHA-1 verified) while the current dumps and indexes keep serving.
3. Build identify.sqlite (match index, ~2 h) and detail.sqlite (full records, ~30 min) from them; each is built as `.building` and renamed over the old one only when verified.
4. Swap the new dumps in as dumps/ and delete the old ones (they can be re-downloaded).

Needs ~120 GB free (dumps 17 + identify 36 + detail 23, plus the old copies until each swap). Python 3.14 (detail build uses compression.zstd). `--check` only reports. An interrupted run is started again and resumes the download; builds restart."""
import argparse, json, os, shutil, subprocess, sys, time
from datetime import datetime, timezone
from pathlib import Path

HERE = Path(__file__).resolve().parent; ROOT = HERE.parent / 'data' / 'openlibrary'; NEED_FREE_GB = 120
sys.path.insert(0, str(HERE)); import fetch_openlibrary  # noqa: E402


def log(msg): print(f'{datetime.now(timezone.utc).isoformat(timespec="seconds")} {msg}', flush=True)


def current_snapshot(dumps: Path) -> str | None:
    try: return json.loads((dumps / 'snapshot.json').read_text())['identifier'].removeprefix('ol_dump_')
    except (OSError, ValueError, KeyError): return None


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split('\n')[0]); ap.add_argument('--check', action='store_true'); ap.add_argument('--force', help='adopt this snapshot date even if not newer')
    a = ap.parse_args(argv); dumps = ROOT / 'dumps'; have = current_snapshot(dumps)
    latest = a.force or fetch_openlibrary.latest_snapshot()
    log(f'current {have}, latest {latest}')
    if latest == have or (have and latest < have and not a.force): log('up to date'); return 0
    if a.check: log('a newer snapshot is available'); return 0
    free = shutil.disk_usage(ROOT).free / 1e9
    if free < NEED_FREE_GB: log(f'only {free:.0f} GB free, need {NEED_FREE_GB}: not starting'); return 1
    new = ROOT / f'dumps-{latest}'; t0 = time.time(); py = [sys.executable, '-I']
    log(f'downloading {latest} into {new.name}'); subprocess.run(py + [str(HERE / 'fetch_openlibrary.py'), latest, '--directory', str(new)], check=True)
    log('building identify.sqlite'); subprocess.run(py + [str(HERE / 'build_ol_identify.py'), '--dumps', str(new)], check=True)
    log('building detail.sqlite'); subprocess.run(py + [str(HERE / 'build_ol_detail.py'), '--dumps', str(new)], check=True)
    old = ROOT / f'dumps-old-{have or "none"}'
    if dumps.exists(): dumps.rename(old)
    new.rename(dumps)
    if dumps.exists() and old.exists(): shutil.rmtree(old)
    log(f'adopted {latest} in {round((time.time() - t0) / 60)} min'); return 0


if __name__ == '__main__':
    sys.exit(main())
