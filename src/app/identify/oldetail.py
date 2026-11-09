"""Full Open Library records (authors, works, editions) from `data/openlibrary/detail.sqlite`, built by `scripts/build_ol_detail.py`.

The identify index finds a work fast but keeps only normalised title variants; this store holds the whole dump record per OL key as a zstd blob (a trained dictionary per kind, its id stored on every row so retraining never breaks old rows), read only for works that matched: output tags, edition subtitles, original-case titles, links to the full record. Redirected (merged) keys are resolved by the caller via the identify index. `compression.zstd` is Python 3.14+; older interpreters use the `zstandard` package (same frame format)."""
import json
import sqlite3
from pathlib import Path

from app import settings

try:
    from compression import zstd as _z

    def _dictionary(data): return _z.ZstdDict(data)
    def _decompress(blob, dictionary): return _z.decompress(blob, zstd_dict=dictionary)
except ImportError:  # Python < 3.14
    import zstandard as _z

    def _dictionary(data): return _z.ZstdCompressionDict(data)
    def _decompress(blob, dictionary): return _z.ZstdDecompressor(dict_data=dictionary).decompress(blob)

KINDS = {'A': 'authors', 'W': 'works', 'M': 'editions'}  # the letter before the dot-less id suffix: /works/OL1W, /books/OL1M, /authors/OL1A


def path() -> Path: return Path(settings.data_dir()) / 'openlibrary' / 'detail.sqlite'


class Detail:
    """A read-only handle: the connection plus the per-kind dictionaries."""
    def __init__(self, db: Path):
        self.c = sqlite3.connect(f'file:{db}?mode=ro', uri=True)
        self.dicts = {i: _dictionary(d) for i, d in self.c.execute('SELECT id, data FROM dict')}

    def get(self, key: str) -> dict | None:
        """The full record for one key (`/works/OL..W`, `/books/OL..M`, `/authors/OL..A`), or None. Bare ids get their prefix from the type letter."""
        if not key.startswith('/'): key = {'W': '/works/', 'M': '/books/', 'A': '/authors/'}[key[-1]] + key
        row = self.c.execute(f'SELECT dict, blob FROM {table(key)} WHERE key=?', (key,)).fetchone()
        return json.loads(_decompress(row[1], self.dicts[row[0]])) if row else None

    def snapshot(self) -> dict: return dict(self.c.execute('SELECT k, v FROM meta'))
    def close(self): self.c.close()


def connect(db: str | Path | None = None) -> Detail | None:
    target = Path(db) if db else path()
    return Detail(target) if target.is_file() else None


def table(key: str) -> str:
    """The table for an OL key such as `/works/OL45883W` (or the bare id); raises ValueError for anything else."""
    try: return KINDS[key.rstrip('/')[-1]]
    except (KeyError, IndexError): raise ValueError(f'not an Open Library key: {key!r}') from None
