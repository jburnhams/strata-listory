"""Read-only evidence snapshots. Agreement is evidence, never a probability of correctness.

Fresh Open Library requests are opt-in; no catalogue changes or processing approvals. Keep the
JSON output as a dated artifact: it contains every input used by this assessment.
"""
import glob
import hashlib
import json
import math
import os
from pathlib import Path
import sqlite3
import re
import uuid
import urllib.parse
from datetime import datetime, timezone

from app import catalogue, details, fs, merge, scan, settings, sources
from app.process import probe
from app.providers.base import Http, MatchError
from app.providers.openlibrary import FIELDS, to_candidate

POLICY = 'evidence-v1'
OPENLIBRARY_HTTP = Http('Open Library evidence', min_interval=1.1)
AUDIBLE_FAMILY = {'audible_file', 'audible', 'audnexus'}
RECORDING_FIELDS = ('asin', 'narrators', 'publisher', 'release_year', 'runtime_minutes', 'language', 'abridged')
WORK_FIELDS = ('title', 'authors', 'original_year', 'series', 'series_position', 'genres', 'description', 'isbns')


def _family(source):
    return 'audible' if source in AUDIBLE_FAMILY else source


def _key(field, value):
    if isinstance(value, list): return tuple(sorted(merge.norm(x) for x in value))
    return merge.norm(value)


def assess(records, files, errors=()):
    """Conservative, explainable checks over explicit evidence; no percentage confidence."""
    blockers = list(errors); checks = []
    def check(name, passed, reason):
        checks.append(dict(check=name, passed=bool(passed), explanation=reason))
        if not passed: blockers.append(reason)
    # Catalogue and book catalogues cannot establish the identity of a recording.
    audio = {s: r for s, r in records.items() if s in merge.AUDIOBOOK}
    work = {s: r for s, r in records.items() if s in merge.WORK}
    identity = [r for r in audio.values() if r.get('title') and r.get('authors')]
    titles = {merge.norm(r['title']) for r in identity}
    authors = {_key('authors', r['authors']) for r in identity}
    check('work_identity', bool(identity) and len(titles) == len(authors) == 1,
          'Recording sources must agree on the full title and complete author list.')
    families = {_family(s) for s, r in audio.items() if r.get('title') and r.get('authors')}
    check('independent_evidence', len(families) >= 2,
          'Need at least two source families; Audible, its sidecar and Audnexus count as one.')
    anchor = identity[0] if identity else {}
    linked = [r for r in work.values() if merge.norm(r.get('title')) == merge.norm(anchor.get('title')) and r.get('authors') and _key('authors', r['authors']) == _key('authors', anchor.get('authors', []))]
    check('work_link', bool(linked), 'Need a book-catalogue record agreeing with the recording title and authors.')
    check('files_readable', bool(files) and not errors and all(f.get('duration', 0) > 0 and math.isfinite(f.get('duration', 0)) for f in files),
          'All discovered audio files must be readable and have a positive finite duration.')
    duration = sum(f.get('duration', 0) for f in files)
    runtimes = [r['runtime_minutes'] * 60 for r in audio.values() if isinstance(r.get('runtime_minutes'), (float, int)) and r['runtime_minutes'] > 0]
    check('runtime', bool(runtimes) and all(abs(t - duration) <= max(120, t * .02) for t in runtimes),
          'Every known recording runtime must agree with measured audio within 2% or two minutes.')
    for s, r in records.items():
        if s in ('catalogue', 'filename'):
            for field in ('title', 'authors', 'asin'):
                if r.get(field) and anchor.get(field):
                    check(s + ':' + field, _key(field, r[field]) == _key(field, anchor[field]), f'{s} {field} conflicts with recording identity.')
    for field in RECORDING_FIELDS:
        values = {_key(field, r[field]) for r in audio.values() if r.get(field) not in (None, '', [])}
        check('agreement:' + field, len(values) <= 1, f'Recording sources disagree on {field}.') if field != 'runtime_minutes' else None
    check('recording_edition', any(r.get('narrators') for r in audio.values()) and any(r.get('asin') for r in audio.values()),
          'Need a narrator and recording identifier to distinguish audiobook editions.')
    check('abridgement_known', any(r.get('abridged') is not None for r in audio.values()),
          'Abridgement must be explicitly established; absence of a label is not evidence.')
    # Local tags are cross-checks, not independent provider votes. Chapter titles are
    # deliberately not treated as book titles; album and album_artist identify a book.
    for f in files:
        tags = f.get('tags') or {}
        for tag, field in (('album', 'title'), ('album_artist', 'authors')):
            if tags.get(tag) and anchor.get(field):
                actual = sources._names(tags[tag]) if field == 'authors' else tags[tag]
                check('tag:' + tag + ':' + f['path'], _key(field, actual) == _key(field, anchor[field]),
                      f"Embedded {tag} conflicts with recording identity in {f['path']}.")
    merged = merge.merge({s: r for s, r in records.items() if s != 'filename'})
    return dict(work_identity='supported' if checks[0]['passed'] and bool(linked) else 'review',
                recording_identity='supported' if not blockers else 'review',
                eligible_for_auto_processing=False, assessment='candidate' if not blockers else 'review',
                blockers=list(dict.fromkeys(blockers)), checks=checks, measured_duration_seconds=duration,
                work={k: v for k, v in merged['fields'].items() if k in WORK_FIELDS},
                recording={k: v for k, v in merged['fields'].items() if k in RECORDING_FIELDS},
                merged=merged)


def fetch_openlibrary(book, records, evidence, archive_dir):
    """Archive each response before using it. Exact unique work links only; no fuzzy auto-selection."""
    audio = [r for s, r in records.items() if s in merge.AUDIOBOOK and r.get('title') and r.get('authors')]
    identities = {(merge.norm(r['title']), _key('authors', r['authors'])) for r in audio}
    if len(identities) > 1:
        return dict(status='ambiguous_recording', selected=None)
    anchor = audio[0] if audio else sources.from_catalogue(book)
    title = anchor.get('title') or ''; authors = anchor.get('authors') or []
    if not title or not authors: return dict(status='missing_identity', selected=None)
    client = OPENLIBRARY_HTTP
    def request(url):
        entry = dict(source='openlibrary', book_id=book['id'], query=dict(title=title, authors=authors), url=url, fetched_at=datetime.now(timezone.utc).isoformat())
        try:
            entry['raw'] = client.request(url)
        except MatchError as e:
            entry['error'] = str(e)
        folder = Path(archive_dir); folder.mkdir(parents=True, exist_ok=True)
        target = folder / (datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%S%fZ') + '-' + uuid.uuid4().hex + '.json')
        with target.open('x', encoding='utf8') as f: json.dump(entry, f, ensure_ascii=False, indent=2, allow_nan=False)
        evidence.append(dict(entry, archive=str(target)))
        if 'error' in entry: raise MatchError(entry['error'])
        return entry['raw']
    try:
        url = 'https://openlibrary.org/search.json?' + urllib.parse.urlencode(dict(title=title[:120], author=', '.join(authors)[:120], limit=5, fields=FIELDS + ',author_key'))
        raw = request(url) or {}
        candidates = [to_candidate(d) for d in raw.get('docs', [])]
        matches = [c for c in candidates if merge.norm(c['title']) == merge.norm(title) and _key('authors', c['authors']) == _key('authors', authors)]
        keys = {c['id'] for c in matches}
        if len(keys) != 1: return dict(status='ambiguous' if keys else 'not_found', selected=None, candidates=candidates)
        c = matches[0]; key = c['id']
        search_doc = next(d for d in raw['docs'] if d.get('key') == key)
        if re.fullmatch(r'OL[0-9]+W', key): key = '/works/' + key
        if not re.fullmatch(r'/works/OL[0-9]+W', key): return dict(status='invalid_work_id', selected=None, candidates=candidates)
        work = request('https://openlibrary.org' + key + '.json')
        if not work or merge.norm(work.get('title')) != merge.norm(title):
            return dict(status='work_conflict', selected=None, candidates=candidates)
        expected_authors = {'/authors/' + a for a in search_doc.get('author_key', [])}
        actual_authors = {a.get('author', {}).get('key') for a in work.get('authors', [])}
        if (work.get('key') and work['key'] != key) or (expected_authors and expected_authors != actual_authors):
            return dict(status='work_conflict', selected=None, candidates=candidates)
        ratings = request('https://openlibrary.org' + key + '/ratings.json') or {}
        r = sources.from_candidate(c)
        description = work.get('description') or ''
        r['description'] = description.get('value', '') if isinstance(description, dict) else description
        r['genres'] = work.get('subjects') or c.get('subjects') or []
        summary = ratings.get('summary') or {}
        r['rating'] = summary.get('average', c.get('rating')); r['ratings_count'] = summary.get('count', c.get('ratings_count'))
        r['url'] = 'https://openlibrary.org' + key
        records['openlibrary'] = r
        return dict(status='linked', selected=key, candidates=candidates, rating_scale=5, scope='work')
    except MatchError as e:
        return dict(status='unavailable', selected=None, error=str(e))


def collect(book, extras, fresh_openlibrary=False, archive_dir=None):
    """Read every file and sidecar, preserving raw JSON and file signatures in output."""
    now = datetime.now(timezone.utc).isoformat(); path = book.get('path') or ''
    evidence = [dict(source='catalogue', observed_at=now, raw=book), dict(source='stored_extras', observed_at=now, raw=extras)]
    records = sources.local_records(book, extras=extras); files = []; errors = []
    if path:
        parsed = scan.parse_name(os.path.basename(path), os.path.basename(os.path.dirname(path)))
        evidence.append(dict(source='filename', path=path, observed_at=now, raw=parsed))
        records['filename'] = sources.from_catalogue(parsed)
    for s in merge.WORK:
        r = records.get(s)
        # Do not let an unrelated work pollute the selected final metadata.
        if r and (merge.norm(r.get('title')) != merge.norm(book.get('title')) or _key('authors', r.get('authors', [])) != _key('authors', sources._names(book.get('author', '')))):
            records.pop(s); errors.append(f'{s}: stored work does not exactly match catalogue identity; raw data retained.')
    if not path or not fs.reachable(path) or not os.path.exists(path):
        errors.append('Audio path is missing or unreachable.')
    else:
        audio = []
        if os.path.isfile(path): audio = [path]
        else:
            for root, dirs, names in os.walk(path, onerror=lambda e: errors.append(str(e))):
                dirs[:] = sorted(d for d in dirs if not d.startswith(('.', '@')))
                audio.extend(os.path.join(root, n) for n in sorted(names) if scan.is_audio(n))
        for f in sorted(audio, key=probe.natural_key):
            try:
                st = os.stat(f); raw = details._ffprobe(f)
                evidence.append(dict(source='ffprobe', path=f, observed_at=now, size=st.st_size, mtime_ns=st.st_mtime_ns, raw=raw))
                if not raw: raise ValueError('ffprobe failed or is unavailable')
                fmt = raw.get('format') or {}; stream = next((s for s in raw.get('streams', []) if s.get('codec_type') == 'audio'), None)
                if not stream: raise ValueError('no audio stream')
                files.append(dict(path=f, duration=float(fmt.get('duration') or stream.get('duration') or 0), tags={k.lower(): v for k, v in (fmt.get('tags') or {}).items()}))
            except (OSError, ValueError) as e: errors.append(f'{f}: {e}')
        sidecars = [os.path.splitext(path)[0] + '.metadata.json'] if os.path.isfile(path) else sorted(glob.glob(os.path.join(glob.escape(path), '*.metadata.json')))
        for f in sidecars:
            if not os.path.exists(f): continue
            try:
                raw_text = Path(f).read_text(encoding='utf8'); raw = json.loads(raw_text)
                evidence.append(dict(source='audible_file', path=f, observed_at=now, sha256=hashlib.sha256(raw_text.encode()).hexdigest(), raw=raw))
                r = sources.from_audible_file(details.audible_summary(raw))
                r['abridged'] = raw.get('is_abridged') if isinstance(raw.get('is_abridged'), bool) else None
                if 'audible_file' in records: errors.append('Multiple Audible sidecars: recording association needs review.')
                else: records['audible_file'] = r
            except (OSError, ValueError, TypeError, KeyError, AttributeError) as e: errors.append(f'{f}: {e}')
    lookup = fetch_openlibrary(book, records, evidence, archive_dir) if fresh_openlibrary else None
    result = assess(records, files, errors)
    # Manual locks affect selection, never manufacture identity evidence.
    result['merged'] = merge.merge({s: r for s, r in records.items() if s != 'filename'}, sources.locked_values(book))
    abridgement = [(s, r['abridged']) for s, r in records.items() if s in merge.AUDIOBOOK and isinstance(r.get('abridged'), bool)]
    if abridgement:
        value = abridgement[0][1]
        result['merged']['fields']['abridged'] = dict(value=value, sources=[s for s, v in abridgement if v == value], alternatives=[dict(value=v, sources=[s]) for s, v in abridgement if v != value], confidence=None)
    result['work'] = {k: v for k, v in result['merged']['fields'].items() if k in WORK_FIELDS}
    result['recording'] = {k: v for k, v in result['merged']['fields'].items() if k in RECORDING_FIELDS}
    return dict(openlibrary_lookup=lookup, book_id=book['id'], title=book['title'], policy=POLICY, observed_at=now, evidence=evidence, records=records, files=files, **result)


def report(ids=(), limit=20, stride=1, fresh_openlibrary=False):
    if limit < 1 or stride < 1: raise ValueError('limit and stride must be positive')
    folder = os.environ.get('APP_DATA') or settings.load().get('data_dir') or settings.DEFAULT_DATA_DIR
    path = Path(folder).expanduser().absolute() / 'catalogue.db'
    c = sqlite3.connect(path.as_uri() + '?mode=ro', uri=True); c.row_factory = sqlite3.Row
    try:
        c.execute('BEGIN')  # one consistent snapshot; never migrate or back up the catalogue
        rows = c.execute('SELECT * FROM books ORDER BY id').fetchall()
        books = [catalogue._row(r) for r in rows if (r['id'] in ids if ids else r['kind'] == 'book' and r['review'] != 'ignored')]
        missing = set(ids) - {b['id'] for b in books}
        if missing: raise ValueError(f'Unknown book IDs: {sorted(missing)}')
        selected = books[::stride][:limit]
        inputs = [(b, {r['source']: dict(fetched=r['fetched'], data=json.loads(r['data'])) for r in c.execute('SELECT * FROM book_extra WHERE book_id=?', (b['id'],))}) for b in selected]
    finally: c.close()
    results = [collect(b, ex, fresh_openlibrary, path.parent / 'evidence' / 'openlibrary') for b, ex in inputs]
    return dict(policy=POLICY, mode='fresh-openlibrary' if fresh_openlibrary else 'stored-evidence-only', automatic_processing_enabled=False,
                limitations=['Only Open Library is refreshed; other providers use stored evidence.' if fresh_openlibrary else 'No fresh provider lookups; missing evidence requires review.', 'File size and mtime are not content hashes.', 'Candidate assessments do not approve processing.'],
                books=results, summary=dict(total=len(results), candidates=sum(b['assessment'] == 'candidate' for b in results), review=sum(b['assessment'] == 'review' for b in results)))
