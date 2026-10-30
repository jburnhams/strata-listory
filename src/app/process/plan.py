"""Decide what to do with one book, without changing anything: its chapters, how each is produced and the loudness gain for the whole book.

Chapters, first source that exists wins: chapters embedded in a single file; the chapter list of the Audible metadata file (when it matches the audio length); the files themselves; or, for one long file without chapters, equal-ish parts cut at pauses. Then the size rules: a chapter under 10 minutes is combined with a neighbour if the two stay within 30 minutes (credits-length pieces always join one, up to an hour); a chapter over 60 minutes is split into parts of about 45 minutes at the longest pause near each ideal cut; the parts keep the chapter's number with a letter (30a, 30b, 30c). Nothing else is split or combined.
Each chapter is then `copy` (mono mp3 source at or below COPY_MAX_KBPS: cut on frame boundaries and re-gained losslessly, never re-encoded) or `encode` (LAME VBR, mono, with a tier chosen from how much high-frequency content the chapter really has).
Loudness is one gain per book towards TARGET_LUFS (so chapters keep their level relative to each other), only when the book is outside a tolerance band, and never at the price of clipping: a book may stay slightly quiet."""
import math, os, re

from app import details
from app.process import probe

TARGET_LUFS, TP_LIMIT = -18.0, -1.5
COPY_MAX_KBPS = 96
SHORT, COMBINE_MAX = 10 * 60.0, 30 * 60.0   # a chapter under SHORT minutes may be combined with its neighbour, but only if the two together are at most COMBINE_MAX
SPLIT_ABOVE, SPLIT_TARGET = 60 * 60.0, 45 * 60.0   # a chapter is split only above SPLIT_ABOVE, into equal-ish parts of about SPLIT_TARGET
TINY = 2 * 60.0                             # credits and jingles: joined to a neighbour even beyond COMBINE_MAX (up to SPLIT_ABOVE), because a file of seconds is no use as a chapter
PAUSE_MIN, CUT_WINDOW = 1.2, 6 * 60.0       # a cut goes at a pause of at least this many seconds within this many seconds of the ideal point
LIMITER_MARGIN = 0.8                        # dB below TP_LIMIT for the limiter, because the mp3 encoder overshoots a little (measured: -0.7 dBFS true peak after encoding with a -1.5 limiter)
GAIN_STEP = 1.5                             # dB: the resolution of lossless mp3 gain
TIERS = {'V5': dict(q=5, rate=24000, kbps=49), 'V4': dict(q=4, rate=32000, kbps=70), 'V3': dict(q=3, rate=44100, kbps=100)}
SAMPLE = (90.0, 3)                          # the spectrum is measured on up to 3 stretches of 90 s per chapter, not all of it
BUSY = 0.02                                 # a chapter silent less than this fraction of the time is music-like (calibrated 2026-10-30 on 30 + 30 real excerpts: speech median 19% silent, only 1 of 30 under 2%; music-heavy folders median 0%, 27 of 30 under 2%)
MUSIC_HF = -24.0                            # music-like chapters: energy above 10 kHz relative to the total (dB) at or below this -> V4 (32 kHz), above -> V3 (44.1 kHz). That ratio alone does not separate speech (median -25, p90 -21) from music (median -23, p90 -19), so it only chooses between the two music tiers
LIMIT_DB = 3.0                              # without a measurement of the peaks: encoded chapters may be boosted up to this far past the true-peak headroom, the limiter taking the few peaks that stick out (kept small: quieter is better than squashed or clipped)
SQUASH_DB = 2.0                             # with the measurement: how much the limiter may take off the peaks of all but the loudest 1% of the book's short windows (the 99th percentile peak) ...
MAX_REDUCTION = 7.0                         # ... and off the single loudest peak. At 2 dB / 7 dB the limiter only ever shaves isolated peaks; raise SQUASH_DB to reach the target more often at the price of more limiting
QUIET_TOL, LOUD_TOL = 3.0, 2.0              # a book at most this many dB quieter / louder than the target is left exactly as it is; outside that band it is normalised, even if it would otherwise be copied


def clean_name(s: str) -> str:
    return re.sub(r'\s+', ' ', re.sub(r'[\x00-\x1f<>:"|?*\\/]+', '_', s)).strip(' .') or '_'


def file_title(info: dict, book_title: str, n: int) -> str:
    t = info['tags'].get('title') or os.path.splitext(os.path.basename(info['path']))[0]
    t = re.sub(r'\[[^\]]*\]', ' ', t); bt = re.escape(re.sub(r'\s*\(unabridged\)', '', book_title, flags=re.I))
    t = re.sub(rf'^\s*\d+\s*[-.:]\s*', '', t); t = re.sub(rf'^{bt}\s*[-:]?\s*', '', t, flags=re.I) if bt else t
    t = re.sub(r'^\s*\d+\s*[-.:]\s*', '', t).strip(' -:.')
    return t or f'Chapter {n}'


WORDS = 'one two three four five six seven eight nine ten eleven twelve thirteen fourteen fifteen sixteen seventeen eighteen nineteen twenty thirty forty fifty first second third fourth fifth sixth seventh eighth ninth tenth'.split()
GENERIC = re.compile(r'^\s*(?:chapters?|chap\.?|ch\.?|parts?|sections?|tracks?|books?|discs?|disks?|cd|episodes?|ep\.?|volumes?|vol\.?)\s*(?:\d+(?:\s*[-\u2013]\s*\d+)?|[ivxlcdm]+|' + '|'.join(WORDS) + r')?\s*(?:[:.\-\u2013\u2014_]+\s*|$)', re.I)
MAX_NAME_BYTES = 200                         # file names stay well under the 255-byte limit of ext4 (some titles are very long)


def chapter_name(title: str, author: str = '') -> str:
    """The part of a chapter title worth putting in a file name: "Chapter 3: Find Your Niche" -> "Find Your Niche", "Introduction" stays, "Chapter 3", "Part Two", "Chapters 3-5" and "Track 7" -> "" (the number is already in the file name)."""
    t = title.strip(); prev = None
    if author: t = re.sub(rf'^{re.escape(author)}\s*[-:\u2013]\s*', '', t, flags=re.I)       # "Sue Townsend - The Author Talks" in a book by Sue Townsend
    while t and t != prev:                                  # "Part One: Chapter 1: The Start" loses both prefixes
        prev = t; m = GENERIC.match(t)
        if m: t = t[m.end():].strip(' -:._')
    return t if re.search(r'[^\W\d_]', t) else ''


def file_name(label: str, author: str, title: str, chapter: str) -> str:
    """`001 - Author - Title - Chapter name.mp3`, the last part only when there is a chapter name; shortened (chapter name first, then title) to fit MAX_NAME_BYTES."""
    def cut(x: str, n: int) -> str:
        b = x.encode('utf8')[:max(n, 0)]; return b.decode('utf8', 'ignore').rstrip(' -:._')
    a, t, c = clean_name(author or 'Unknown author'), clean_name(title), clean_name(chapter) if chapter else ''
    fixed = len(f'{label} - {a} - ') + len('.mp3')
    room = MAX_NAME_BYTES - fixed
    tb, cb = len(t.encode('utf8')), len(c.encode('utf8')) + 3 if c else 0
    if tb + cb > room:
        if c: c = cut(c, room - tb - 3) if room - tb - 3 >= 12 else ''; cb = len(c.encode('utf8')) + 3 if c else 0
        if tb + cb > room: t = cut(t, room - cb)
    return f'{label} - {a} - {t}' + (f' - {c}' if c else '') + '.mp3'


def timeline(files: list[dict]) -> list[tuple[float, float, dict]]:
    out, t = [], 0.0
    for f in files: out.append((t, t + f['duration'], f)); t += f['duration']
    return out


def segments(tl, start: float, end: float) -> list[tuple]:
    """The (file, in, out) pieces of the book timeline between `start` and `end`."""
    out = []
    for a, b, f in tl:
        if b <= start or a >= end: continue
        s, e = max(start, a) - a, min(end, b) - a
        out.append((f['path'], round(s, 3), None if e >= f['duration'] - 0.01 else round(e, 3)))
    return out


def _num(title: str):
    m = re.fullmatch(r'(?:Chapter|Part|Track|Ch\.?)\s*(\d+)', title.strip(), re.I)
    return int(m[1]) if m else None


def _merge(parts: list[dict]) -> dict:
    """One chapter from consecutive ones. Credits-length pieces do not name it; chapters numbered in a row become "Chapters 3-5"; otherwise the first and last names are joined."""
    named = [p for p in parts if p['end'] - p['start'] >= TINY] or parts
    titles = [p['title'] for p in named]; nums = [_num(t) for t in titles]
    if len(titles) == 1: title = titles[0]
    elif all(n is not None for n in nums): title = f'Chapters {nums[0]}-{nums[-1]}'
    else: title = ' + '.join(titles) if len(titles) == 2 else f'{titles[0]} ... {titles[-1]}'
    return dict(title=title, start=parts[0]['start'], end=parts[-1]['end'])


def combine(chs: list[dict]) -> list[dict]:
    """Join a chapter shorter than SHORT to its neighbour when the two together stay within COMBINE_MAX (reading left to right, so a run of short ones makes chapters of up to 30 minutes). Credits-length pieces (under TINY) join a neighbour whenever the result stays within SPLIT_ABOVE."""
    out: list[list[dict]] = []
    for c in chs:
        if out:
            run = out[-1]; a = run[-1]['end'] - run[0]['start']; b = c['end'] - c['start']; small = min(a, b)
            if (small < SHORT and a + b <= COMBINE_MAX) or (small < TINY and a + b <= SPLIT_ABOVE): run.append(c); continue
        out.append([c])
    return [_merge(r) if len(r) > 1 else dict(r[0]) for r in out]


def split_points(start: float, end: float, silences: list[tuple[float, float]]) -> list[float]:
    """Where to cut a chapter longer than SPLIT_ABOVE into equal-ish parts of about SPLIT_TARGET: each cut at the longest pause near its ideal position, or exactly there when there is none. Shorter chapters are not cut."""
    length = end - start
    if length <= SPLIT_ABOVE: return []
    n = math.ceil(length / SPLIT_TARGET); each = length / n; cuts: list[float] = []
    for k in range(1, n):
        ideal = start + k * each; floor = (cuts[-1] if cuts else start) + 300
        cands = [(e - s, (s + e) / 2) for s, e in silences if e - s >= PAUSE_MIN and abs((s + e) / 2 - ideal) <= min(CUT_WINDOW, each * 0.25) and floor < (s + e) / 2 < end - 300]
        cuts.append(round(max(cands)[1] if cands else ideal, 2))
    return cuts


def derive_chapters(files: list[dict], silences, audible_chapters: list[dict] | None, book_title: str) -> tuple[list[dict], str]:
    total = sum(f['duration'] for f in files); source = ''
    if len(files) == 1 and len(files[0]['chapters']) >= 2:
        chs = [dict(title=c['title'] or '', start=c['start'], end=c['end']) for c in files[0]['chapters']]; source = 'embedded chapters'
    elif audible_chapters and len(audible_chapters) >= 2 and abs(sum(c['length_sec'] for c in audible_chapters) - total) <= 0.02 * total + 5:
        pos, chs = 0.0, []
        for c in audible_chapters: chs.append(dict(title=c['title'], start=pos, end=pos + c['length_sec'])); pos += c['length_sec']
        chs[-1]['end'] = total; source = 'Audible chapter list'
    elif len(files) > 1:
        chs = [dict(title=file_title(f, book_title, i + 1), start=a, end=b) for i, (a, b, f) in enumerate(timeline(files))]; source = 'file boundaries'
    else:
        chs = [dict(title='', start=0.0, end=total)]; source = 'whole file'
    for i, c in enumerate(chs):
        if not c['title']: c['title'] = f'Chapter {i + 1}'
    n_before = len(chs); chs = combine(chs)
    if len(chs) < n_before: source += ' (short ones combined)'
    out = []
    for g, c in enumerate(chs, 1):                          # g: the chapter's number; a split chapter keeps it and its parts are 30a, 30b, 30c ...
        cuts = split_points(c['start'], c['end'], silences); edges = [c['start'], *cuts, c['end']]
        for k, (a, b) in enumerate(zip(edges, edges[1:])):
            suffix = letters(k) if cuts else ''
            out.append(dict(title=f"{c['title']} ({suffix})" if cuts else c['title'], base=c['title'], start=round(a, 2), end=round(b, 2), group=g, suffix=suffix))
        if cuts and 'split' not in source: source += ' (long ones split)'
    width = max(3, len(str(len(chs))))
    for c in out: c['label'] = f"{c['group']:0{width}d}{c['suffix']}"
    return out, source


def letters(k: int) -> str:
    """a, b, ... z, aa, ab ... for the k-th part (0-based) of a split chapter."""
    s = ''
    k += 1
    while k: k, r = divmod(k - 1, 26); s = chr(97 + r) + s
    return s


def sample_ranges(start: float, end: float) -> list[tuple[float, float]]:
    length, n = SAMPLE
    if end - start <= length * n * 1.5: return [(start, end)]
    return [(start + (end - start - length) * f, start + (end - start - length) * f + length) for f in (0.15, 0.5, 0.85)[:n]]


def pick_tier(hf_db: float, pause_frac: float) -> str:
    """Speech (paused at least BUSY of the time) needs no more than V5; music-like chapters keep their top end."""
    if pause_frac >= BUSY: return 'V5'
    return 'V4' if hf_db <= MUSIC_HF else 'V3'


def copyable(segs: list[tuple], by_path: dict) -> bool:
    fs = [by_path[p] for p, _, _ in segs]
    return all(f['codec'] == 'mp3' and f['channels'] == 1 and f['bit_rate'] <= COPY_MAX_KBPS for f in fs) and len({f['sample_rate'] for f in fs}) == 1


def decide_gain(i: float, tp: float, any_copy: bool, p99: float | None = None) -> dict:
    """The one gain for the whole book. Inside the tolerance band (QUIET_TOL below to LOUD_TOL above the target) nothing is changed. Louder than that: attenuated (lossless in 1.5 dB steps for copied chapters). Quieter than that: boosted, but never past the true-peak headroom for copied chapters; when that still leaves the book more than QUIET_TOL short, the whole book is re-encoded (`reencode`) so a limiter can take the rest. How far a limiter may lift is measured, not guessed: up to the gain at which the 99th percentile peak is reduced by at most SQUASH_DB and the loudest peak by at most MAX_REDUCTION; without the measurement, LIMIT_DB beyond the headroom. A book that still ends up short stays slightly quiet: no tags, no compensation. `apply_db` is what every chapter gets."""
    want = TARGET_LUFS - i; headroom = TP_LIMIT - tp
    out = dict(db=round(want, 2), steps=0, apply_db=0.0, reencode=False, why='within tolerance, left as it is')
    if -LOUD_TOL <= want <= QUIET_TOL: return out
    if want < 0:
        steps = round(want / GAIN_STEP); out.update(steps=steps if any_copy else 0, apply_db=round(steps * GAIN_STEP if any_copy else want, 2), why='too loud: attenuated'); return out
    steps = max(0, min(round(want / GAIN_STEP), math.floor(headroom / GAIN_STEP)))
    if any_copy and want - steps * GAIN_STEP <= QUIET_TOL: out.update(steps=steps, apply_db=round(steps * GAIN_STEP, 2), why='too quiet: lifted losslessly'); return out
    ceiling = TP_LIMIT - LIMITER_MARGIN
    room = min(ceiling + SQUASH_DB - p99, ceiling - tp + MAX_REDUCTION) if p99 is not None else headroom + LIMIT_DB
    out.update(steps=0, apply_db=round(max(0.0, min(want, room)), 2), reencode=any_copy, why='too quiet: lifted with a limiter' + (' (re-encoded, because lossless gain was not enough)' if any_copy else ''))
    return out


def year_for(book: dict, audible: dict | None, sug: dict | None) -> tuple[int | None, str]:
    """The year for the folder name, and where it came from. We want the year of this audiobook release: a year you set yourself comes first, then the Audible metadata file, then an Audible/Audnexus match, then other providers (these give the book's own publication year), then a year read from the folder name."""
    def yr(x): m = re.match(r'(1\d{3}|2[01]\d{2})', str(x or '')); return int(m[1]) if m else None
    if book.get('year') and book.get('year_source') == 'manual': return book['year'], 'set by hand'
    if audible and yr(audible.get('release_date')): return yr(audible['release_date']), 'Audible metadata file'
    sug = sug or {}; srcs = set(sug.get('sources') or [sug.get('source')])
    if yr(sug.get('release_date')) and srcs & {'audible', 'audnexus'}: return yr(sug['release_date']), 'Audible/Audnexus match'
    if yr(sug.get('release_date')): return yr(sug['release_date']), f"{sug.get('source', 'provider')} match (the book's own publication year)"
    if book.get('year'): return book['year'], book.get('year_source') or 'folder name'
    return None, ''


def effective(book: dict, audible: dict | None) -> dict:
    """The narrator and series that go in the tags: the catalogue's, or the Audible metadata file's when the catalogue has none."""
    a = audible or {}; aseries = (a.get('series') or [{}])[0]
    narrator = book.get('narrator') or ', '.join(a.get('narrators') or [])
    series = book.get('series') or aseries.get('title', '')
    pos = book.get('series_position')
    if not book.get('series') and aseries.get('sequence'):
        try: pos = float(aseries['sequence'])
        except ValueError: pass
    return dict(narrator=narrator, series=series, series_position=pos if series else None)


def meta_rows(book: dict, audible: dict | None, year, year_src: str) -> list[dict]:
    """The metadata that will be written to the files, each value with where it came from (the provider match you confirmed, the Audible metadata file, or the catalogue: parsed from the folder name or entered by hand) and a note where the Audible metadata file says something different."""
    sug = book.get('suggestion') or {}; srcs = ' + '.join(s for s in dict.fromkeys(sug.get('sources') or ([sug['source']] if sug.get('source') else []))); a = audible or {}; eff = effective(book, audible)
    n = lambda x: re.sub(r'\W+', '', str(x or '').lower())
    def src(value, sug_val, aud_val) -> str:
        if value in (None, ''): return ''
        if sug and n(sug_val) == n(value): return f'{srcs} match (confirmed)'
        if audible and n(aud_val) == n(value): return 'Audible metadata file'
        return 'the catalogue: parsed from the folder name, or entered by hand'
    def note(value, aud_val) -> str:
        return f'the Audible metadata file says: {aud_val}' if audible and aud_val and value and n(aud_val) != n(value) else ''
    a_title, a_author, a_narr = a.get('title'), ', '.join(a.get('authors') or []), ', '.join(a.get('narrators') or [])
    aseries = (a.get('series') or [{}])[0].get('title')
    rows = [('Title', book['title'], src(book['title'], sug.get('title'), a_title), note(book['title'], a_title)),
            ('Author', book.get('author') or '', src(book.get('author'), sug.get('author'), a_author), note(book.get('author'), a_author)),
            ('Narrator', eff['narrator'], src(eff['narrator'], ', '.join(sug.get('narrators') or []), a_narr) if book.get('narrator') else ('Audible metadata file' if eff['narrator'] else ''), note(book.get('narrator'), a_narr)),
            ('Series', eff['series'] + (f" #{eff['series_position']:g}" if eff['series'] and eff['series_position'] is not None else ''), src(eff['series'], sug.get('series'), aseries) if book.get('series') else ('Audible metadata file' if eff['series'] else ''), note(book.get('series'), aseries)),
            ('Year', str(year) if year else '', year_src, ''), ('ASIN', book.get('asin') or '', src(book.get('asin'), sug.get('asin'), a.get('asin')), '')]
    return [dict(field=f, value=v, source=sr, note=nt) for f, v, sr, nt in rows if v]


def out_dir_for(book: dict, final_dir: str, year: int | None = None) -> str:
    """<final>/<Author>/[<year>] <Title>: no series level (series is kept in the tags and metadata.json)."""
    author = clean_name(book.get('author') or 'Unknown author'); title = clean_name(book['title'])
    return os.path.join(final_dir, author, f'[{year}] {title}' if year else title)


def make_plan(book: dict, final_dir: str, audible: dict | None = None) -> dict:
    """Everything needed to process the book, as plain data (JSON-serialisable)."""
    year, year_src = year_for(book, audible, book.get('suggestion'))
    if not year: warnings_pre = ['no year found: the folder will have no [year]; set one with `./app edit ID --year YYYY`']
    else: warnings_pre = []
    paths = sorted(details.audio_files(book['path']), key=probe.natural_key)
    if not paths: raise probe.ToolError(f"no audio files in {book['path']}")
    files = [probe.probe_file(p) for p in paths]; by_path = {f['path']: f for f in files}; tl = timeline(files)
    warnings = list(warnings_pre)
    m = probe.measure_book([(f['path'], 0.0, None) for f in files], max(f['channels'] for f in files))
    downmix = 'average'
    if m.get('mono_I') is not None and m['mono_I'] < m['I'] - 3:
        downmix = 'left'; warnings.append(f"stereo channels partly cancel in a mono mix ({m['mono_I']:.1f} vs {m['I']:.1f} LUFS): using the left channel only")
    if m['TP'] is not None and m['TP'] >= -0.2: warnings.append(f"the source reaches full scale (true peak {m['TP']:+.1f} dBFS), so it is probably already clipped; processing keeps it from getting worse but cannot repair it")
    chapters, source = derive_chapters(files, m['silences'], (audible or {}).get('chapters'), book['title'])
    for c in chapters:
        segs = segments(tl, c['start'], c['end']); c['segments'] = segs; c['duration'] = round(c['end'] - c['start'], 2); c['file'] = file_name(c['label'], book.get('author') or '', book['title'], chapter_name(c['base'], book.get('author') or ''))
        if copyable(segs, by_path): c.update(mode='copy', tier='copy', kbps=round(max(by_path[p]['bit_rate'] for p, _, _ in segs), 1))
        else:
            sp = probe.spectrum([sg for a, b in sample_ranges(c['start'], c['end']) for sg in segments(tl, a, b)]); t = pick_tier(sp['hf_db'], sp['pause_frac']); c.update(mode='encode', tier=t, kbps=TIERS[t]['kbps'], hf_db=sp['hf_db'], pause_frac=sp['pause_frac'])
    g = decide_gain(m['I'], m['TP'], any(c['mode'] == 'copy' for c in chapters), m.get('peak_p99'))
    if g['reencode']:                                       # lossless gain could not make the book loud enough: re-encode all of it
        for c in chapters:
            if c['mode'] == 'copy':
                sp = probe.spectrum([sg for a, b in sample_ranges(c['start'], c['end']) for sg in segments(tl, a, b)]); t = pick_tier(sp['hf_db'], sp['pause_frac'])
                c.update(mode='encode', tier=t, kbps=TIERS[t]['kbps'], hf_db=sp['hf_db'], pause_frac=sp['pause_frac'])
        warnings.append('re-encoded although the source is small enough to copy, because it needs normalising and lossless gain cannot reach it')
    if abs(g['db']) > 12: warnings.append('large gain (%+.1f dB)' % g['db'])
    short = round(g['db'] - g['apply_db'], 1)
    if g['db'] > QUIET_TOL and short > 0.5: warnings.append(f"ends {short:.1f} dB below the target because its peaks leave no more room")
    in_bytes = sum(os.path.getsize(f['path']) for f in files); est = sum(c['duration'] * c['kbps'] * 1000 / 8 for c in chapters)
    return dict(book_id=book['id'], title=book['title'], author=book.get('author', ''), out_dir=out_dir_for(book, final_dir, year), year=year, year_source=year_src, metadata=meta_rows(book, audible, year, year_src), **effective(book, audible), chapter_source=source, downmix=downmix, duration=round(sum(f['duration'] for f in files), 1),
                loudness=dict(I=m['I'], TP=m['TP'], LRA=m['LRA'], target=TARGET_LUFS, gain_db=g['db'], apply_db=g['apply_db'], gain_steps=g['steps'], decision=g['why'], reencoded=g['reencode'], peak_p99=m.get('peak_p99')), chapters=chapters, warnings=warnings,
                in_bytes=in_bytes, est_bytes=int(est), in_files=len(files), channels=max(f['channels'] for f in files))
