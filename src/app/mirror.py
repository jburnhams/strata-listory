"""The data mirror: for every book we keep what we know about it on disk, in a folder tree that mirrors the audio library, under `<data_dir>/library/`. The audio share is read-only, so this is where the catalogue's own files live:

    <data_dir>/library/<path of the book relative to library_dir>/metadata.json   the final catalogue record, the Audible metadata summary and the match that produced it
    <data_dir>/library/<path of the book relative to library_dir>/cover.jpg|png   the cover image and where it came from (metadata.json: cover_source)

A book that is a single file gets a folder named after the file without its extension. A book outside the library folder goes under `_external/`, one entered by hand under `_manual/<id>-<title>/`. Writes are atomic and failures never break a catalogue change; the database remains the source of truth and `./app mirror` rewrites everything from it."""
import json, os, re
from datetime import datetime, timezone

from app import settings

IMAGES = ('.jpg', '.png', '.webp')


def root() -> str:
    return os.path.join(settings.data_dir(), 'library')


def _safe(part: str) -> str:
    return re.sub(r'[\x00-\x1f<>:"|?*\\/]+', '_', part).strip(' .') or '_'


def dir_for(book: dict) -> str:
    """The mirror folder of a book (not created)."""
    path = book.get('path') or ''
    if not path: return os.path.join(root(), '_manual', _safe(f"{book['id']}-{book.get('title', '')}")[:120])
    lib = settings.library_dir()
    if lib:
        rel = os.path.relpath(os.path.abspath(path), lib)
        if not rel.startswith('..') and not os.path.isabs(rel):
            parts = rel.split(os.sep)
            if os.path.isfile(path) or os.path.splitext(parts[-1])[1].lower() in {'.mp3', '.m4b', '.m4a', '.aac', '.ogg', '.opus', '.flac', '.wma'}: parts[-1] = os.path.splitext(parts[-1])[0]
            return os.path.join(root(), *[_safe(p) for p in parts])
    return os.path.join(root(), '_external', _safe(os.path.basename(path.rstrip(os.sep)) or str(book['id'])))


def cover_file(book: dict) -> str | None:
    d = dir_for(book)
    return next((p for e in IMAGES if os.path.isfile(p := os.path.join(d, 'cover' + e))), None)


def _atomic(path: str, data: bytes):
    os.makedirs(os.path.dirname(path), exist_ok=True); tmp = path + '.tmp'
    with open(tmp, 'wb') as f: f.write(data)
    os.replace(tmp, path)


def read_meta(book: dict) -> dict:
    try:
        with open(os.path.join(dir_for(book), 'metadata.json'), encoding='utf8') as f: d = json.load(f)
        return d if isinstance(d, dict) else {}
    except (OSError, ValueError): return {}


def save_cover(book: dict, data: bytes, ext: str, source: str) -> str:
    """Store the cover image in the book's mirror folder (replacing any other image type) and note its source in metadata.json."""
    d = dir_for(book)
    for e in IMAGES:
        if os.path.isfile(old := os.path.join(d, 'cover' + e)) and e != ext: os.remove(old)
    p = os.path.join(d, 'cover' + ext); _atomic(p, data); write(book, cover_source=source); return p


def write(book: dict, cover_source: str | None = None) -> str | None:
    """Write `metadata.json` for a book; returns its path, or None when the data folder cannot be written."""
    from app import details                      # late import: details needs the mirror too
    try:
        d = dir_for(book); old = read_meta(book)
        side = details.sidecar(book['path']) if book.get('path') and os.path.exists(book['path']) else None
        sug = book.get('suggestion') or {}
        rec = {k: v for k, v in book.items() if k not in ('suggestion', 'match_score')}
        meta = dict(book=rec, match=dict(suggestion=sug, score=book.get('match_score')) if sug else None, audible_file=details.audible_summary(side) if side else None,
                    cover=os.path.basename(cover_file(book) or '') or None, cover_source=cover_source or old.get('cover_source'), written=datetime.now(timezone.utc).isoformat())
        p = os.path.join(d, 'metadata.json'); _atomic(p, json.dumps(meta, indent=1, ensure_ascii=False).encode('utf8')); return p
    except OSError: return None


def write_all(books: list[dict]) -> int:
    return sum(write(b) is not None for b in books)
