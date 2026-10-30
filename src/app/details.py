"""Everything we can learn about one book from its own files, for the book page: the Audible `*.metadata.json` file that sits next to purchased books, the audio files and their embedded tags (ffprobe), and a cover image. All read-only on the library; covers are cached under `<data_dir>/covers/`.

Cover order: an image file in the book's folder (cover/folder/front, then any picture; for a single loose file only a picture with the same name), then art embedded in the audio (ffmpeg), then the cover URL named in the book's own Audible metadata file, and last the covers offered by the metadata providers for the book's match (confirmed, or a likely match): the match's own cover, then each provider by ASIN. The cover is saved once in the book's mirror folder (mirror.py) with its source noted; AudiobookDB's terms do not license its images, so they are kept only in this private catalogue."""
import glob, html, json, os, re, shutil, subprocess, time, urllib.request
from datetime import datetime, timedelta, timezone

from app import db, fs, mirror, providers, scan, settings

IMAGES = ('.jpg', '.jpeg', '.png', '.webp')
COVER_NAMES = ('cover', 'folder', 'front', 'albumart', 'albumartsmall', 'artwork')
MAX_FILES = 500
PROBE_FILES = 60                 # a book with more files than this is probed on its first few only
NONE_FOR = timedelta(days=1)     # how long "no cover found" is remembered


def _natural(s: str):
    return [int(t) if t.isdigit() else t.lower() for t in re.split(r'(\d+)', s)]


def audio_files(path: str) -> list[str]:
    """The audio files of a book (a single file, or everything under its folder), sorted, at most MAX_FILES."""
    if os.path.isfile(path): return [path]
    out = []
    for root, dirs, files in os.walk(path):
        dirs[:] = sorted((d for d in dirs if not d.startswith(('.', '@'))), key=_natural)                # Disc 2 before Disc 10
        out += [os.path.join(root, f) for f in sorted(files, key=_natural) if scan.is_audio(f)]
        if len(out) >= MAX_FILES: break
    return out[:MAX_FILES]


def sidecar(path: str) -> dict | None:
    """The Audible metadata file (`*.metadata.json`) belonging to the book, parsed, or None."""
    if os.path.isfile(path): cands = [os.path.splitext(path)[0] + '.metadata.json']
    else: cands = sorted(glob.glob(os.path.join(glob.escape(path), '*.metadata.json')))
    for c in cands:
        try:
            with open(c, encoding='utf8') as f: d = json.load(f)
            if isinstance(d, dict): return d
        except (OSError, ValueError): continue
    return None


def _text(s: str | None) -> str:
    return html.unescape(re.sub(r'\s+', ' ', re.sub(r'<[^>]+>', ' ', s or ''))).strip()


def audible_summary(d: dict) -> dict:
    """The useful fields of an Audible metadata file (reviews, prices, plans and similar are left out)."""
    rating = (d.get('rating') or {}).get('overall_distribution') or {}
    chapters = (d.get('ChapterInfo') or {}).get('chapters') or []
    imgs = d.get('product_images') or {}
    return dict(asin=d.get('asin', ''), title=d.get('title', ''), subtitle=d.get('subtitle', ''), authors=[a['name'] for a in d.get('authors') or []], narrators=[n['name'] for n in d.get('narrators') or []],
                series=[dict(title=s.get('title', ''), sequence=s.get('sequence', '')) for s in d.get('series') or []], publisher=d.get('publisher_name', ''), release_date=d.get('release_date', ''), language=d.get('language', ''),
                format=d.get('format_type', ''), runtime_minutes=d.get('runtime_length_min'), summary=_text(d.get('publisher_summary') or d.get('merchandising_summary')), rating=rating.get('display_average_rating'), rating_count=rating.get('num_ratings'),
                categories=sorted({c['name'] for l in d.get('category_ladders') or [] for c in l.get('ladder') or []}), sample_url=d.get('sample_url', ''),
                chapters=[dict(title=c.get('title', ''), start_sec=c.get('start_offset_sec', 0), length_sec=round((c.get('length_ms') or 0) / 1000)) for c in chapters],
                cover_url=imgs.get('500') or next(iter(imgs.values()), ''))


def _ffprobe(path: str) -> dict | None:
    if not shutil.which('ffprobe'): return None
    try:
        r = subprocess.run(['ffprobe', '-v', 'quiet', '-print_format', 'json', '-show_format', '-show_streams', '-show_chapters', path], capture_output=True, text=True, timeout=60)
        return json.loads(r.stdout) if r.returncode == 0 and r.stdout.strip() else None
    except (OSError, ValueError, subprocess.TimeoutExpired): return None


def probe(files: list[str]) -> dict:
    """Embedded tags and technical facts from the audio files: {available, duration_seconds, files_probed, tags, codec, bitrate_kbps, chapters, has_cover_art}. Tags and codec come from the first file; the duration sums the probed files (and is exact only when all were probed)."""
    if not shutil.which('ffprobe'): return dict(available=False)
    first = None; total = 0.0; n = 0; chapters = 0
    for f in files[:PROBE_FILES]:
        d = _ffprobe(f)
        if not d: continue
        n += 1; total += float(d.get('format', {}).get('duration') or 0); chapters += len(d.get('chapters') or [])
        if first is None: first = d
    if first is None: return dict(available=True, files_probed=0)
    audio = next((s for s in first.get('streams', []) if s.get('codec_type') == 'audio'), {})
    tags = {k.lower(): v for k, v in (first.get('format', {}).get('tags') or {}).items()}
    return dict(available=True, files_probed=n, complete=n == len(files), duration_seconds=round(total), tags=tags, codec=audio.get('codec_name', ''), bitrate_kbps=round(int(first.get('format', {}).get('bit_rate') or 0) / 1000) or None,
                chapters=chapters, has_cover_art=any(s.get('disposition', {}).get('attached_pic') for s in first.get('streams', [])))


def _signature(files: list[str]) -> str:
    st = [os.stat(f) for f in files]
    return f'{len(files)}:{sum(s.st_size for s in st)}:{max((int(s.st_mtime) for s in st), default=0)}'


def cached_probe(path: str, files: list[str]) -> dict:
    """`probe`, remembered in the database until the files change (probing a large book over SMB takes seconds)."""
    key = f'probe:{path}:{_signature(files)}'; c = db.connect()
    try:
        r = c.execute('SELECT body FROM match_cache WHERE key=?', (key,)).fetchone()
        if r: return json.loads(r['body'])
        body = probe(files)
        if body.get('available') and body.get('files_probed'):
            with c: c.execute('INSERT OR REPLACE INTO match_cache (key, fetched, body) VALUES (?, ?, ?)', (key, datetime.now(timezone.utc).isoformat(), json.dumps(body)))
        return body
    finally: c.close()


def _image_kind(b: bytes) -> str:
    return '.png' if b[:8] == b'\x89PNG\r\n\x1a\n' else '.jpg' if b[:3] == b'\xff\xd8\xff' else '.webp' if b[:4] == b'RIFF' and b[8:12] == b'WEBP' else ''


def _folder_image(path: str) -> str | None:
    if os.path.isfile(path):
        stem = os.path.splitext(path)[0]
        return next((stem + e for e in IMAGES if os.path.isfile(stem + e)), None)
    pics = sorted(f for f in os.listdir(path) if os.path.splitext(f)[1].lower() in IMAGES and not f.lower().startswith('icon'))
    pref = [f for f in pics if os.path.splitext(f)[0].lower() in COVER_NAMES or os.path.splitext(f)[0].lower() == os.path.basename(path).lower()]
    pick = (pref or pics or [None])[0]
    return os.path.join(path, pick) if pick else None


def _embedded(files: list[str]) -> bytes | None:
    if not files or not shutil.which('ffmpeg'): return None
    try:
        r = subprocess.run(['ffmpeg', '-v', 'error', '-i', files[0], '-an', '-map', '0:v:0', '-frames:v', '1', '-f', 'image2pipe', '-c:v', 'copy', '-'], capture_output=True, timeout=60)
        return r.stdout if r.returncode == 0 and _image_kind(r.stdout) else None
    except (OSError, subprocess.TimeoutExpired): return None


def _download(url: str) -> bytes | None:
    if not url.startswith('https://'): return None
    try:
        with urllib.request.urlopen(urllib.request.Request(url, headers={'User-Agent': 'strata-listory/0.1 (personal audiobook catalogue)'}), timeout=20) as r: b = r.read(6_000_000)
        return b if _image_kind(b) else None
    except (OSError, ValueError): return None


def _provider_cover(book: dict) -> tuple[bytes, str] | None:
    """A cover from the providers for a book's match: the suggestion's own cover URL, then each enabled provider by ASIN, then AudiobookDB by its id. Raises MatchError only when nothing was found and a provider was unreachable (so it is retried later)."""
    sug = book.get('suggestion') or {}
    if not (sug.get('id') and (book.get('review') == 'confirmed' or (book.get('match_score') or 0) >= 0.85)): return None
    tried = [(sug.get('source') or '?', sug.get('cover_url') or '')]; failure = None
    asin = sug.get('asin') or book.get('asin') or ''
    for p in providers.enabled():
        try:
            if p.name == sug.get('source') and not tried[0][1] and sug.get('id'): tried.append((p.name, p.cover_url(sug['id'])))
            if asin and (c := p.by_asin(asin)): tried.append((p.name, c['cover_url']))
        except providers.MatchError as e: failure = e
    for source, url in tried:
        if url and (data := _download(url)): return data, source
    if failure: raise failure
    return None


def cover(book: dict) -> str | None:
    """Path of the book's cover image (found or built on first request and saved in its mirror folder), or None when there is none."""
    if (p := mirror.cover_file(book)): return p
    old = os.path.join(settings.data_dir(), 'covers'); os.makedirs(old, exist_ok=True)       # before the mirror, covers lived here; they are moved in on first use
    for e in IMAGES:
        if os.path.isfile(prev := os.path.join(old, f"{book['id']}{e}")):
            with open(prev, 'rb') as f: data = f.read()
            os.remove(prev); return mirror.save_cover(book, data, _image_kind(data) or e, 'cache')
    none = os.path.join(old, f"{book['id']}.none")
    if os.path.isfile(none) and datetime.now(timezone.utc) - datetime.fromtimestamp(os.path.getmtime(none), timezone.utc) < NONE_FOR: return None
    path = book.get('path') or ''
    if path and not fs.reachable(path): return None                            # the share is down: no verdict, and nothing remembered (try again when it is back)
    try: return _build_cover(book, path, none)
    except OSError: return None


def _build_cover(book: dict, path: str, none: str) -> str | None:
    data = None; source = ''
    if path and os.path.exists(path):
        if (img := _folder_image(path)):
            try:
                with open(img, 'rb') as f: data = f.read(6_000_000)
            except OSError: data = None
            data = data if data and _image_kind(data) else None; source = 'folder' if data else ''
        if not data and (data := _embedded(audio_files(path))): source = 'embedded'
        if not data and (side := sidecar(path)) and (data := _download(audible_summary(side)['cover_url'])): source = 'audible-file'
    if not data:
        try: found = _provider_cover(book)
        except providers.MatchError: return None               # a service is down or has no key: try again next time rather than remembering "no cover"
        data, source = found if found else (None, '')
        source = f'provider:{source}' if data else ''
    if not data:
        open(none, 'w').close(); os.utime(none, (time.time(), time.time())); return None
    return mirror.save_cover(book, data, _image_kind(data), source)


def describe(book: dict) -> dict:
    """The data behind the book page: files, embedded tags and technical facts, and the Audible metadata file, when the book's path exists on this machine."""
    path = book.get('path') or ''; out = dict(path_exists=False, share_down=False, files=None, probe=None, audible=None)
    if path and not fs.reachable(path): out['share_down'] = True; return out           # the share is down: say so instead of hanging
    out['path_exists'] = bool(path) and os.path.exists(path)
    if not out['path_exists']: return out
    try: return _describe(book, path, out)
    except OSError: out.update(path_exists=False, share_down=True, files=None, probe=None, audible=None); return out


def _describe(book: dict, path: str, out: dict) -> dict:
    files = audio_files(path)
    out['files'] = dict(count=len(files), total_bytes=sum(os.path.getsize(f) for f in files), truncated=len(files) >= MAX_FILES, list=[dict(name=os.path.relpath(f, path if os.path.isdir(path) else os.path.dirname(path)), bytes=os.path.getsize(f)) for f in files[:200]])
    out['probe'] = cached_probe(path, files)
    side = sidecar(path); out['audible'] = audible_summary(side) if side else None
    return out
