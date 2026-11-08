#!/usr/bin/env python3
"""Weekly upkeep of the local Audible index (run by listory-audible-refresh.timer), in order:

1. AudiobookDB people list, re-crawled when the last full crawl is older than --people-days (30), from the first page, upserting rows (a day or two of its request budget).
2. Newest-first refresh of every category in each region (`fetch_audible_categories.py --newest`, three shards in parallel); a category that gained more than one sorted listing holds is listed in full again.
3. Resume the author pass for people added since (UK) and the snowball over newly credited names, each three shards in parallel, ending when there is nothing left to query.
4. Rebuild the query index from the archive (atomic; the old index serves until the new one is verified).

Everything resumes from saved state, so an interrupted run is simply started again. Regions come from --regions (default uk,us). Request rates are per process (--rate, default 1 a second)."""
import argparse, os, sqlite3, subprocess, sys, time
from datetime import datetime, timedelta, timezone

HERE = os.path.dirname(os.path.abspath(__file__)); PY = [sys.executable, '-I']


def log(msg): print(f'{datetime.now(timezone.utc).isoformat(timespec="seconds")} {msg}', flush=True)


def run(args, parallel: int = 1) -> int:
    procs = [subprocess.Popen(PY + [os.path.join(HERE, args[0])] + args[1:] + (['--shard', f'{i}/{parallel}'] if parallel > 1 else [])) for i in range(parallel)]
    codes = [p.wait() for p in procs]
    if any(codes): log(f'{args[0]} {" ".join(args[1:])}: exit codes {codes}')
    return max(codes)


def people_age_days(path: str) -> float | None:
    try:
        r = sqlite3.connect(f'file:{path}?mode=ro', uri=True, timeout=30).execute("SELECT v FROM state WHERE k='done'").fetchone()
        return (datetime.now(timezone.utc) - datetime.fromisoformat(r[0])).total_seconds() / 86400 if r else None
    except sqlite3.Error: return None


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split('\n')[0])
    ap.add_argument('--regions', default='uk,us'); ap.add_argument('--rate', default='1'); ap.add_argument('--shards', type=int, default=3)
    ap.add_argument('--people', default=os.path.join(HERE, '..', 'data', 'audiobookdb', 'people.sqlite'))
    ap.add_argument('--people-days', type=float, default=30, help='re-crawl the AudiobookDB people list when the last full crawl is older than this')
    ap.add_argument('--skip-index', action='store_true')
    a = ap.parse_args(argv); failed = 0; t0 = time.time()
    age = people_age_days(a.people)
    if age is None or age > a.people_days:
        log(f'people list age {age if age is None else round(age)} days: re-crawling'); failed |= run(['crawl_audiobookdb_people.py', '--restart', '--once']) != 0
    for region in a.regions.split(','):
        log(f'{region}: newest releases'); failed |= run(['fetch_audible_categories.py', '--newest', '--region', region, '--rate', a.rate], a.shards) != 0
        log(f'{region}: authors added since'); failed |= run(['fetch_audible_people.py', '--region', region, '--roles', 'Author', '--rate', a.rate], a.shards) != 0
    log('US/UK: names credited since (snowball)'); failed |= run(['fetch_audible_people.py', '--from-credits', '--roles', 'Author', '--rate', a.rate], a.shards) != 0
    if not a.skip_index:
        log('rebuilding the index'); failed |= run(['build_audible_index.py']) != 0
    log(f'finished in {round((time.time() - t0) / 60)} min{", with failures" if failed else ""}'); return 1 if failed else 0


if __name__ == '__main__':
    sys.exit(main())
