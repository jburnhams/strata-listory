"""Decide what to do with one book, without changing anything: its chapters, how each is produced and the loudness gain for the whole book.

Chapters, first source that exists wins: chapters embedded in a single file; the chapter list of the Audible metadata file (when it matches the audio length); the files themselves (tiny ones merged into a neighbour); pauses in a single long file. Chapters longer than MAX_CHAPTER are split again at pauses.
Each chapter is then `copy` (mono mp3 source at or below COPY_MAX_KBPS: cut on frame boundaries and re-gained losslessly, never re-encoded) or `encode` (LAME VBR, mono, with a tier chosen from how much high-frequency content the chapter really has).
Loudness is one gain per book towards TARGET_LUFS, so chapters keep their level relative to each other."""
import math, os, re

from app import details
from app.process import probe

TARGET_LUFS, TP_LIMIT = -18.0, -1.5
COPY_MAX_KBPS = 96
MIN_CHAPTER, MAX_CHAPTER = 90.0, 75 * 60.0
SPLIT_SILENCE = 1.5                         # seconds of quiet that may end a chapter
WINDOW = (12 * 60.0, 40 * 60.0)             # where to look for a cut when splitting a long stretch
LIMITER_MARGIN = 0.8                        # dB below TP_LIMIT for the limiter, because the mp3 encoder overshoots a little (measured: -0.7 dBFS true peak after encoding with a -1.5 limiter)
GAIN_STEP = 1.5                             # dB: the resolution of lossless mp3 gain
TIERS = {'V5': dict(q=5, rate=24000, kbps=49), 'V4': dict(q=4, rate=32000, kbps=70), 'V3': dict(q=3, rate=44100, kbps=100)}
SAMPLE = (90.0, 3)                          # the spectrum is measured on up to 3 stretches of 90 s per chapter, not all of it
BUSY = 0.02                                 # a chapter silent less than this fraction of the time is music-like (calibrated 2026-10-30 on 30 + 30 real excerpts: speech median 19% silent, only 1 of 30 under 2%; music-heavy folders median 0%, 27 of 30 under 2%)
MUSIC_HF = -24.0                            # music-like chapters: energy above 10 kHz relative to the total (dB) at or below this -> V4 (32 kHz), above -> V3 (44.1 kHz). That ratio alone does not separate speech (median -25, p90 -21) from music (median -23, p90 -19), so it only chooses between the two music tiers
LIMIT_DB = 6.0                              # encoded chapters may be boosted up to this far past the true-peak headroom, the limiter taking the few peaks that stick out


def clean_name(s: str) -> str:
    return re.sub(r'\s+', ' ', re.sub(r'[\x00-\x1f<>:"|?*\\/]+', '_', s)).strip(' .') or '_'


def file_title(info: dict, book_title: str, n: int) -> str:
    t = info['tags'].get('title') or os.path.splitext(os.path.basename(info['path']))[0]
    t = re.sub(r'\[[^\]]*\]', ' ', t); bt = re.escape(re.sub(r'\s*\(unabridged\)', '', book_title, flags=re.I))
    t = re.sub(rf'^\s*\d+\s*[-.:]\s*', '', t); t = re.sub(rf'^{bt}\s*[-:]?\s*', '', t, flags=re.I) if bt else t
    t = re.sub(r'^\s*\d+\s*[-.:]\s*', '', t).strip(' -:.')
    return t or f'Chapter {n}'


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


def cut_points(start: float, end: float, silences: list[tuple[float, float]]) -> list[float]:
    """Where to cut [start, end] into chapters no longer than MAX_CHAPTER: at the longest pause 12 to 40 minutes on, else at the first pause after that (up to MAX_CHAPTER on), else hard at 40 minutes."""
    cuts, pos = [], start
    while end - pos > MAX_CHAPTER:
        lo, hi = pos + WINDOW[0], min(pos + WINDOW[1], end - MIN_CHAPTER)
        if lo >= hi: break
        pauses = [(e - s, (s + e) / 2) for s, e in silences if e - s >= SPLIT_SILENCE]
        inside = [p for p in pauses if lo <= p[1] <= hi]; later = [p for p in pauses if hi < p[1] <= pos + MAX_CHAPTER and p[1] <= end - MIN_CHAPTER]
        pos = max(inside)[1] if inside else min(later, key=lambda p: p[1])[1] if later else hi
        cuts.append(round(pos, 2))
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
    merged: list[dict] = []
    for c in chs:                                           # tiny chapters (credits, jingles) join their neighbour
        if merged and (c['end'] - c['start'] < MIN_CHAPTER or merged[-1]['end'] - merged[-1]['start'] < MIN_CHAPTER) and merged[-1]['end'] - merged[-1]['start'] + c['end'] - c['start'] < MAX_CHAPTER:
            if (c['end'] - c['start']) > (merged[-1]['end'] - merged[-1]['start']): merged[-1]['title'] = c['title'] or merged[-1]['title']      # the merged chapter is named after its longer part (a short credits track joins the chapter after it)
            merged[-1]['end'] = c['end']
        else: merged.append(dict(c))
    out = []
    for c in merged:
        cuts = cut_points(c['start'], c['end'], silences) if c['end'] - c['start'] > MAX_CHAPTER else []
        edges = [c['start'], *cuts, c['end']]; split = len(cuts) > 0
        for k, (a, b) in enumerate(zip(edges, edges[1:])): out.append(dict(title=(f"{c['title'] or 'Part'} (part {k + 1})" if split and c['title'] else '' if not split else f'Part {k + 1}') or c['title'], start=round(a, 2), end=round(b, 2)))
        if split and not source.endswith('+ pauses'): source += ' + pauses'
    for i, c in enumerate(out):
        if not c['title']: c['title'] = f'Chapter {i + 1}'
    return out, source


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


def gain_for(i: float, tp: float, has_copy: bool) -> dict:
    """One gain for the whole book towards TARGET_LUFS. Copied chapters can only take whole 1.5 dB steps and never past the true-peak limit; encoded chapters can take any gain, up to LIMIT_DB beyond the headroom (peaks above it are limited). A book with both uses the copy gain so all its chapters stay level with each other. `apply_db` is what every chapter gets."""
    want = TARGET_LUFS - i; headroom = TP_LIMIT - tp
    steps = round(want / GAIN_STEP)
    if steps > 0: steps = min(steps, max(0, math.floor(headroom / GAIN_STEP)))
    apply = steps * GAIN_STEP if has_copy else min(want, headroom + LIMIT_DB)
    return dict(db=round(want, 2), steps=steps, apply_db=round(apply, 2), warn=('large gain (%+.1f dB)' % want) if abs(want) > 12 else '')


def out_dir_for(book: dict, final_dir: str) -> str:
    author = clean_name(book.get('author') or 'Unknown author'); title = clean_name(book['title'])
    if book.get('series'):
        pos = book.get('series_position'); prefix = f'{int(pos):02d} - ' if pos is not None and float(pos).is_integer() else (f'{pos} - ' if pos is not None else '')
        return os.path.join(final_dir, author, clean_name(book['series']), prefix + title)
    return os.path.join(final_dir, author, title)


def make_plan(book: dict, final_dir: str, audible: dict | None = None) -> dict:
    """Everything needed to process the book, as plain data (JSON-serialisable)."""
    paths = sorted(details.audio_files(book['path']), key=probe.natural_key)
    if not paths: raise probe.ToolError(f"no audio files in {book['path']}")
    files = [probe.probe_file(p) for p in paths]; by_path = {f['path']: f for f in files}; tl = timeline(files)
    warnings = []
    m = probe.measure_book([(f['path'], 0.0, None) for f in files], max(f['channels'] for f in files))
    downmix = 'average'
    if m.get('mono_I') is not None and m['mono_I'] < m['I'] - 3:
        downmix = 'left'; warnings.append(f"stereo channels partly cancel in a mono mix ({m['mono_I']:.1f} vs {m['I']:.1f} LUFS): using the left channel only")
    chapters, source = derive_chapters(files, m['silences'], (audible or {}).get('chapters'), book['title'])
    n = len(chapters); width = max(2, len(str(n)))
    for i, c in enumerate(chapters):
        segs = segments(tl, c['start'], c['end']); c['segments'] = segs; c['duration'] = round(c['end'] - c['start'], 2); c['file'] = f"{i + 1:0{width}d} - {clean_name(c['title'])}.mp3"
        if copyable(segs, by_path): c.update(mode='copy', tier='copy', kbps=round(max(by_path[p]['bit_rate'] for p, _, _ in segs), 1))
        else:
            sp = probe.spectrum([sg for a, b in sample_ranges(c['start'], c['end']) for sg in segments(tl, a, b)]); t = pick_tier(sp['hf_db'], sp['pause_frac']); c.update(mode='encode', tier=t, kbps=TIERS[t]['kbps'], hf_db=sp['hf_db'], pause_frac=sp['pause_frac'])
    g = gain_for(m['I'], m['TP'], any(c['mode'] == 'copy' for c in chapters))
    if g['warn']: warnings.append(g['warn'])
    remaining = round(g['db'] - g['apply_db'], 2)
    if abs(remaining) >= 0.4: warnings.append(f"peaks limit the change to {g['apply_db']:+.1f} dB of the {g['db']:+.1f} dB wanted; a ReplayGain tag carries the other {remaining:+.1f} dB")
    in_bytes = sum(os.path.getsize(f['path']) for f in files); est = sum(c['duration'] * c['kbps'] * 1000 / 8 for c in chapters)
    return dict(book_id=book['id'], title=book['title'], author=book.get('author', ''), out_dir=out_dir_for(book, final_dir), chapter_source=source, downmix=downmix, duration=round(sum(f['duration'] for f in files), 1),
                loudness=dict(I=m['I'], TP=m['TP'], LRA=m['LRA'], target=TARGET_LUFS, gain_db=g['db'], apply_db=g['apply_db'], gain_steps=g['steps'], remaining_db=remaining if abs(remaining) >= 0.4 else 0.0), chapters=chapters, warnings=warnings,
                in_bytes=in_bytes, est_bytes=int(est), in_files=len(files), channels=max(f['channels'] for f in files))
