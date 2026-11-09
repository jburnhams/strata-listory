"""Full Open Library records (authors, works, editions) from `data/openlibrary/detail.sqlite`, built by `scripts/build_ol_detail.py`.

The identify index finds a work fast but keeps only normalised title variants; this store holds the whole dump record per OL key as a zstd blob (a trained dictionary per kind, its id stored on every row so retraining never breaks old rows), read only for works that matched: output tags, edition subtitles, original-case titles, links to the full record. Redirected (merged) keys are resolved by the caller via the identify index. `compression.zstd` is Python 3.14+; older interpreters use the `zstandard` package (same frame format)."""
import gzip
import itertools
import json
import multiprocessing
import os
import sqlite3
import time
from pathlib import Path

from app import settings
from app.identify import olindex

try:
    from compression import zstd as _z

    def _dictionary(data): return _z.ZstdDict(data) if data else None
    def _decompress(blob, dictionary): return _z.decompress(blob, zstd_dict=dictionary)
    def _compressor(level, data):
        c = _z.ZstdCompressor(level=level, zstd_dict=_dictionary(data)); return lambda raw: c.compress(raw, _z.ZstdCompressor.FLUSH_FRAME)
    def _train(samples, size): return _z.train_dict(samples, size).dict_content
except ImportError:  # Python < 3.14
    import zstandard as _z

    def _dictionary(data): return _z.ZstdCompressionDict(data) if data else None
    def _decompress(blob, dictionary): return _z.ZstdDecompressor(dict_data=dictionary).decompress(blob) if dictionary else _z.ZstdDecompressor().decompress(blob)
    def _compressor(level, data):
        c = _z.ZstdCompressor(level=level, dict_data=_dictionary(data)); return c.compress
    def _train(samples, size): return _z.train_dictionary(size, samples).as_bytes()

KINDS = {'A': 'authors', 'W': 'works', 'M': 'editions'}  # the letter before the dot-less id suffix: /works/OL1W, /books/OL1M, /authors/OL1A


def path() -> Path: return Path(settings.data_dir()) / 'openlibrary' / 'detail.sqlite'


class Detail:
    """A read-only handle: the connection plus the per-kind dictionaries."""
    def __init__(self, db: Path):
        self.c = sqlite3.connect(f'file:{db}?mode=ro', uri=True)
        self.dicts = {i: _dictionary(d) for i, d in self.c.execute('SELECT id, data FROM dict')}      # a kind too small to train a dictionary has none

    def get(self, key: str) -> dict | None:
        """The full record for one key (`/works/OL..W`, `/books/OL..M`, `/authors/OL..A`), or None. Bare ids get their prefix from the type letter."""
        if not key.startswith('/'): key = {'W': '/works/', 'M': '/books/', 'A': '/authors/'}[key[-1]] + key
        row = self.c.execute(f'SELECT dict, blob FROM {table(key)} WHERE key=?', (key,)).fetchone()
        return dict(json.loads(_decompress(row[1], self.dicts[row[0]])), key=key) if row else None

    def snapshot(self) -> dict: return dict(self.c.execute('SELECT k, v FROM meta'))
    def close(self): self.c.close()


def connect(db: str | Path | None = None) -> Detail | None:
    target = Path(db) if db else path()
    return Detail(target) if target.is_file() else None


def table(key: str) -> str:
    """The table for an OL key such as `/works/OL45883W` (or the bare id); raises ValueError for anything else."""
    try: return KINDS[key.rstrip('/')[-1]]
    except (KeyError, IndexError): raise ValueError(f'not an Open Library key: {key!r}') from None


LEVEL = 6
DICT_BYTES = 112640
SAMPLE = 20000
MIN_SAMPLES = 200      # below this a dictionary cannot be trained (tiny test dumps): records are then compressed without one
_state = {}


def _lines(path, limit=0):
    with gzip.open(path, 'rt', encoding='utf8') as stream:
        yield from (itertools.islice(stream, limit) if limit else stream)


def _record(line):
    fields = line.rstrip('\n').split('\t', 4)
    return (fields[1], fields[4]) if len(fields) == 5 else None


def _init(kind, dict_bytes): _state.update(kind=kind, compress=_compressor(LEVEL, dict_bytes))


def _work(chunk):
    """One chunk of dump lines compressed in a worker: (key, blob, work keys for an edition) rows and the count of skipped non-English editions."""
    kind, compress, rows, skipped = _state['kind'], _state['compress'], [], 0
    for line in chunk:
        if not (rec := _record(line)): continue
        links = []
        if kind == 'editions':
            data = json.loads(rec[1])
            if not olindex.keep_edition([x.get('key', '') for x in data.get('languages') or [] if isinstance(x, dict)]): skipped += 1; continue
            links = [w.get('key') for w in data.get('works') or [] if isinstance(w, dict) and w.get('key')]
        rows.append((rec[0], compress(rec[1].encode()), links))
    return rows, skipped


def _chunks(path, limit, size=4000):
    it = _lines(path, limit)
    while chunk := list(itertools.islice(it, size)): yield chunk


def build(dumps=None, output=None, limit=0, workers=None, log=None) -> dict:
    """Stream authors, works and editions (explicitly non-English editions skipped, as in the identify index) into `detail.sqlite.building`, verify, then rename it over the published file."""
    root = Path(settings.data_dir()) / 'openlibrary'; source = Path(dumps) if dumps else root / 'dumps'; published = Path(output) if output else root / 'detail.sqlite'
    target = published.with_name(published.name + '.building'); target.unlink(missing_ok=True); workers = workers or max(2, (os.cpu_count() or 4) - 3); t0 = time.time()
    c = sqlite3.connect(target); c.execute('PRAGMA journal_mode=OFF'); c.execute('PRAGMA synchronous=OFF')
    c.execute('CREATE TABLE dict(id INTEGER PRIMARY KEY, kind TEXT, data BLOB)'); c.execute('CREATE TABLE meta(k TEXT PRIMARY KEY, v TEXT)'); c.execute('CREATE TABLE edition_work(work TEXT NOT NULL, edition TEXT NOT NULL)')
    counts = {}
    for i, kind in enumerate(KINDS.values(), 1):
        path = source / f'{kind}.txt.gz'
        sample = [rec[1].encode() for n, line in enumerate(itertools.islice(_lines(path), limit or 4_000_000)) if n % (1 if limit and limit < 40000 else 200) == 0 and (rec := _record(line))][:SAMPLE]
        data = _train(sample, DICT_BYTES) if len(sample) >= MIN_SAMPLES else None
        c.execute('INSERT INTO dict VALUES(?,?,?)', (i, kind, data)); c.commit(); c.execute(f'CREATE TABLE {kind}(key TEXT NOT NULL, dict INTEGER NOT NULL, blob BLOB NOT NULL)')
        n = skipped = 0
        with multiprocessing.get_context(olindex.start_method()).Pool(workers, _init, (kind, data)) as pool:
            for rows, sk in pool.imap(_work, _chunks(path, limit), chunksize=1):
                c.executemany(f'INSERT INTO {kind} VALUES(?,?,?)', [(k, i, b) for k, b, _ in rows]); n += len(rows); skipped += sk
                if kind == 'editions': c.executemany('INSERT INTO edition_work VALUES(?,?)', [(w, k) for k, _, ws in rows for w in ws])
                if log and n // 1_000_000 != (n - len(rows)) // 1_000_000: log(f'{kind} {n} {round(time.time() - t0)}s')
        c.commit(); counts[kind] = n; counts[kind + '_skipped'] = skipped
        c.execute(f'CREATE UNIQUE INDEX {kind}_key ON {kind}(key)'); c.commit()
    c.execute('CREATE INDEX edition_work_work ON edition_work(work)'); c.execute('INSERT INTO meta VALUES(?,?)', ('counts', json.dumps(counts)))
    snapshot = source / 'snapshot.json'; c.execute('INSERT INTO meta VALUES(?,?)', ('snapshot', snapshot.read_text() if snapshot.is_file() else '')); c.commit()
    if (r := c.execute('PRAGMA quick_check').fetchone()[0]) != 'ok': raise ValueError('detail store verification failed: ' + r)
    c.close(); target.replace(published)
    return dict(counts, seconds=round(time.time() - t0))
