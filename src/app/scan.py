"""Find audiobooks in a folder tree and turn their names into catalogue fields. Read-only: nothing under the library folder is ever written or moved.

A *candidate* is one book: a single `.m4b`/`.m4a` file, a folder of chapter files (or of CD1/CD2 subfolders), or one loose file when a folder holds unrelated tracks. `kind` is 'radio' for things that look like broadcast episodes (S01E02, WebRip, BBC Radio, dated recordings), else 'book'."""
import errno, os, re

AUDIO = {'.mp3', '.m4b', '.m4a', '.aac', '.ogg', '.opus', '.flac', '.wma'}
WHOLE_BOOK = {'.m4b', '.m4a', '.aac'}          # one file is one book (mp3 is usually one chapter among many)
DISC = re.compile(r'(^|[\s(\[._-])(cd|disc|disk|part)[\s._-]*\d+[)\]]?$', re.I)
CHAPTERISH = re.compile(r'^(\d|chapter\b|chap\b|ch\b|track\b|part\b|disc\b|cd\b)', re.I)
RADIO = re.compile(r'\bS\d{1,2}E\d{1,2}\b|\b(series|season)\s*\d+\b|\bepisodes?\b|webrip|dabrip|\bbbc radio\b|\b(19|20)\d\d[-.]?\d\d[-.]?\d\d\b|\b\d{1,2}(st|nd|rd|th)? (jan|feb|mar|apr|may|jun|jul|aug|sep|oct|nov|dec)\w* (19|20)\d\d\b', re.I)
GENERIC = {'audible', 'books', 'audiobooks', 'audio', 'spoken', 'misc', '_misc', 'new', 'unsorted', 'downloads'}      # container folders that say nothing about the author
SKIP = {'desktop.ini', 'thumbs.db'}


def is_audio(name: str) -> bool:
    return os.path.splitext(name)[1].lower() in AUDIO and not name.startswith('.')


def _norm(stem: str) -> str:
    s = re.sub(r'\b(chapter|chap|ch|part|track|disc|cd)\b', ' ', stem.lower())
    return re.sub(r'[^a-z]+', '', s)         # digits and punctuation vary between chapter files; the words do not


def one_work(files: list[str]) -> bool:
    """True when these audio files look like chapters of a single book rather than unrelated tracks or broadcast episodes."""
    if len(files) == 1: return True
    if any(os.path.splitext(f)[1].lower() in WHOLE_BOOK for f in files): return False
    stems = [os.path.splitext(f)[0] for f in files]
    if sum(bool(RADIO.search(x)) for x in stems) * 2 >= len(stems): return False
    if sum(bool(CHAPTERISH.match(x)) for x in stems) >= 0.7 * len(stems): return True      # 01 ..., 050, Chapter 6, Track 12
    if len(files) >= 3 and len({re.sub(r'[\d\W_]+', '', re.sub(r'\b(chapter|chap|ch|part|track|disc|cd)\b', ' ', x.lower())) for x in stems}) <= 2: return True      # AB_022, ch0, Part06: the names differ only in numbers
    return len(os.path.commonprefix([_norm(x) for x in stems])) >= 6


def _split_volumes(dirpath: str, dirnames: list[str]) -> bool:
    """True when `dirpath` has no audio of its own and every audio-bearing subfolder is a part of the same book (CD1/CD2, 'Title (Disc 3)', 'Title 02-10')."""
    kids = [d for d in dirnames if _count_audio(os.path.join(dirpath, d))]
    if len(kids) < 2: return False
    if all(DISC.search(d) for d in kids): return True
    norm = [_norm(d) for d in kids]; common = os.path.commonprefix(norm + [_norm(os.path.basename(dirpath))])
    return len(common) >= 6 and len(common) >= 0.6 * min(len(n) for n in norm)


def kind_of(*names: str) -> str:
    return 'radio' if any(RADIO.search(n) for n in names) else 'book'


def parse_name(name: str, parent: str = '') -> dict:
    """Best-effort title/author/narrator/year/asin/series_position from a folder or file name; `parent` (a container folder such as an author folder) is the fallback author."""
    s = os.path.splitext(name)[0] if is_audio(name) else name
    out = dict(title='', author='', narrator='', year=None, asin='', series_position=None)
    if m := re.search(r'\[([A-Z0-9]{10})\]', s): out['asin'] = m[1]
    s = re.sub(r'\[[^\]]*\]', ' ', s)
    s = re.sub(r'\((?:un)?abridged[^)]*\)|\baudible[^)]*\)?|\bunabridged\b', ' ', s, flags=re.I)
    if m := re.search(r'\(((?:19|20)\d\d)\)', s): out['year'] = int(m[1]); s = s.replace(m[0], ' ')
    if ' ' not in s.strip() and '_' in s: s = s.replace('_', ' ')
    s = re.sub(r'\s+', ' ', s).strip(' -._')
    for pat in (r'^(?P<author>[^.]{3,60})\. (?P<title>.+?)\.? read by (?P<narrator>.+)$',
                r'^(?P<title>.+?)(?: (?P<year>(?:19|20)\d\d))? by (?P<author>.+?)(?:[.,]? read by (?P<narrator>.+))?$',
                r'^(?P<author>.+?) (?P<year>(?:19|20)\d\d) (?P<title>.+)$',
                r'^(?P<author>.+?) - (?P<title>.+)$'):
        if m := re.match(pat, s, re.I):
            for k, v in m.groupdict().items():
                if v: out[k] = int(v) if k == 'year' else v.strip(' -._')
            break
    else: out['title'] = s
    if not out['author'] and parent.lower() not in GENERIC: out['author'] = parent
    if m := re.match(r'^(\d{1,3})\s*[-.:]*\s+(.+)$', out['title']): out['series_position'] = float(m[1]); out['title'] = m[2]
    out['title'] = out['title'].strip(' -._') or s
    return out


def _count_audio(root: str) -> int:
    return sum(is_audio(f) for _, _, fs in os.walk(root, onerror=_walk_error) for f in fs)


DOWN = {errno.EHOSTDOWN, errno.ETIMEDOUT, errno.EIO, errno.ENOTCONN, errno.ESTALE, errno.EHOSTUNREACH, errno.ENETUNREACH}


def _walk_error(e: OSError):
    if e.errno in DOWN: raise e                          # the share went away mid-scan: stop rather than return half a library (a permission error on one folder is just skipped)


def scan(library: str):
    """Yield candidate books under `library`: dicts of path, kind, file_count and the parsed fields (title, author, narrator, year, asin, series_position)."""
    library = os.path.abspath(library)
    for dirpath, dirnames, filenames in os.walk(library, onerror=_walk_error):
        dirnames[:] = sorted(d for d in dirnames if not d.startswith(('.', '@')))
        files = sorted(f for f in filenames if is_audio(f) and f.lower() not in SKIP)
        parent = os.path.basename(os.path.dirname(dirpath)) if os.path.dirname(dirpath) != os.path.dirname(library) and dirpath != library else ''
        here = os.path.basename(dirpath)
        if dirpath != library and not files and _split_volumes(dirpath, dirnames):
            dirnames[:] = []                                          # a book split over CD1/CD2-style folders
            yield dict(path=dirpath, kind=kind_of(here), file_count=_count_audio(dirpath), **parse_name(here, parent)); continue
        if not files: continue
        if dirpath != library and one_work(files):
            yield dict(path=dirpath, kind=kind_of(here, parent), file_count=len(files), **parse_name(here, parent))
        else:
            container = '' if dirpath == library else here
            for f in files: yield dict(path=os.path.join(dirpath, f), kind=kind_of(f, container), file_count=1, **parse_name(f, container))
