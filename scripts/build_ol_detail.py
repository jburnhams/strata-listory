#!/usr/bin/env python3
"""Build data/openlibrary/detail.sqlite: the full Open Library record for every author, work and edition, one zstd blob per key
(trained dictionary per kind, dictionary id stored per row). Read-only on the dumps; writes `detail.sqlite.building`, verifies,
then atomically renames. Explicitly non-English editions are skipped (same policy as the identify index).

    scripts/build_ol_detail.py [--dumps DIR] [--limit N] [--output PATH]
"""
import argparse, gzip, itertools, json, multiprocessing, os, sqlite3, sys, time
from compression import zstd

KINDS = {'authors': 'authors', 'works': 'works', 'editions': 'editions'}
LEVEL = 6
DICT_BYTES = 112640
SAMPLE = 20000


def lines(path, limit=0):
    with gzip.open(path, 'rt', encoding='utf8') as stream:
        yield from (itertools.islice(stream, limit) if limit else stream)


def record(line):
    fields = line.rstrip('\n').split('\t', 4)
    return (fields[1], fields[4]) if len(fields) == 5 else None


def keep(kind, body):
    if kind != 'editions' or '"languages"' not in body: return True
    langs = json.loads(body).get('languages') or []
    keys = [x.get('key') for x in langs if isinstance(x, dict)]
    return not keys or '/languages/eng' in keys or not any(k and k.startswith('/languages/') for k in keys)


_state = {}


def _init(kind, dict_bytes):
    d = zstd.ZstdDict(dict_bytes); _state.update(kind=kind, comp=zstd.ZstdCompressor(level=LEVEL, zstd_dict=d))


def _work(chunk):
    """Compress one chunk of dump lines in a worker (a reusable compressor: the one-shot call re-digests the dictionary every time, ~10x slower)."""
    kind, comp, out, skipped = _state['kind'], _state['comp'], [], 0
    for line in chunk:
        if not (rec := record(line)): continue
        if not keep(kind, rec[1]): skipped += 1; continue
        out.append((rec[0], comp.compress(rec[1].encode(), zstd.ZstdCompressor.FLUSH_FRAME)))
    return out, skipped


def chunks(path, limit, size=4000):
    it = lines(path, limit)
    while chunk := list(itertools.islice(it, size)): yield chunk


def train(path, limit):
    sample = []
    for n, line in enumerate(lines(path, limit or 4_000_000)):
        if n % 200 == 0 and (rec := record(line)): sample.append(rec[1].encode())
        if len(sample) >= SAMPLE: break
    return zstd.train_dict(sample, DICT_BYTES)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--dumps'); ap.add_argument('--limit', type=int, default=0); ap.add_argument('--output')
    a = ap.parse_args()
    root = os.path.join(os.path.dirname(os.path.abspath(__file__)), '..', 'data', 'openlibrary')
    dumps = a.dumps or os.path.join(root, 'dumps'); out = a.output or os.path.join(root, 'detail.sqlite')
    target = out + '.building'
    if os.path.exists(target): os.unlink(target)
    c = sqlite3.connect(target); c.execute('PRAGMA journal_mode=OFF'); c.execute('PRAGMA synchronous=OFF')
    c.execute('CREATE TABLE dict(id INTEGER PRIMARY KEY, kind TEXT, data BLOB)')
    c.execute('CREATE TABLE meta(k TEXT PRIMARY KEY, v TEXT)')
    t0 = time.time(); counts = {}
    for i, kind in enumerate(KINDS, 1):
        path = os.path.join(dumps, kind + '.txt.gz')
        d = train(path, a.limit); c.execute('INSERT INTO dict VALUES(?,?,?)', (i, kind, d.dict_content)); c.commit()
        c.execute(f'CREATE TABLE {kind}(key TEXT NOT NULL, dict INTEGER NOT NULL, blob BLOB NOT NULL)')
        n = skipped = 0; workers = max(2, (os.cpu_count() or 4) - 3)
        with multiprocessing.get_context('fork').Pool(workers, _init, (kind, d.dict_content)) as pool:
            for rows, sk in pool.imap(_work, chunks(path, a.limit), chunksize=1):
                c.executemany(f'INSERT INTO {kind} VALUES(?,?,?)', [(k, i, b) for k, b in rows]); n += len(rows); skipped += sk
                if n // 1_000_000 != (n - len(rows)) // 1_000_000: print(kind, n, round(time.time() - t0), 's', flush=True)
        c.commit(); counts[kind] = n; counts[kind + '_skipped'] = skipped
        print('indexing', kind, flush=True); c.execute(f'CREATE UNIQUE INDEX {kind}_key ON {kind}(key)'); c.commit()
    c.execute('INSERT INTO meta VALUES(?,?)', ('counts', json.dumps(counts)))
    c.execute('INSERT INTO meta VALUES(?,?)', ('snapshot', open(os.path.join(dumps, 'snapshot.json')).read() if os.path.exists(os.path.join(dumps, 'snapshot.json')) else ''))
    c.commit()
    if (r := c.execute('PRAGMA quick_check').fetchone()[0]) != 'ok': sys.exit('verification failed: ' + r)
    c.close(); os.replace(target, out)
    print('done', counts, round(time.time() - t0), 's', os.path.getsize(out) / 1e9, 'GB')


if __name__ == '__main__':
    main()
