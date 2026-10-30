"""Carry out a plan (plan.py) for one book: write every chapter to a temporary folder next to the final one, check the result against the input, then move the folder into place. The inbox is only read.

Encoded chapters: decode the chapter's segments, downmix to mono, apply the book's gain with a peak limiter, resample for the chosen tier and encode with LAME VBR.
Copied chapters: cut the mp3 on frame boundaries (stream copy, no new generation of loss) and re-gain it losslessly with mp3gain.
Every chapter gets clean ID3 tags (the source tags are dropped) and a small cover; the book gets `metadata.json`, `playlist.m3u8` and `cover.jpg`."""
import json, os, shutil, subprocess, tempfile, time
from datetime import datetime, timezone

from app import details
from app.process import plan as P, probe

COVER_PX = 400


def _cover_small(src: str | None, tmp: str) -> str | None:
    if not src: return None
    out = os.path.join(tmp, 'cover-small.jpg')
    r = probe.run(['ffmpeg', '-hide_banner', '-v', 'error', '-y', '-i', src, '-vf', f'scale={COVER_PX}:-2:force_original_aspect_ratio=decrease', '-q:v', '5', '-frames:v', '1', out], 120)
    return out if r.returncode == 0 and os.path.exists(out) else None


def tags(book: dict, plan: dict, ch: dict, n: int) -> list[str]:
    md = dict(title=ch['title'], album=book['title'], artist=book.get('author') or '', album_artist=book.get('author') or '', composer=book.get('narrator') or '', track=f'{n}/{len(plan["chapters"])}', genre='Audiobook')
    if book.get('series'): md['grouping'] = book['series'] + (f" #{book['series_position']:g}" if book.get('series_position') is not None else '')
    rem = plan['loudness'].get('remaining_db') or 0.0
    if rem: md['replaygain_album_gain'] = f'{rem:+.2f} dB'; md['replaygain_album_peak'] = f"{10 ** ((plan['loudness']['TP'] + plan['loudness']['apply_db']) / 20):.6f}"      # ReplayGain 2 uses -18 LUFS as its reference, our target: players that honour the tag make up what lossless gain could not
    out = []
    for k, v in md.items():
        if v: out += ['-metadata', f'{k}={v}']
    return out


def filter_chain(plan: dict, ch: dict, rate: int) -> str:
    parts = []
    if plan['channels'] > 1: parts.append('pan=mono|c0=c0' if plan['downmix'] == 'left' else 'pan=mono|c0=0.5*c0+0.5*c1')
    g = plan['loudness']['apply_db']
    if abs(g) >= 0.05: parts.append(f'volume={g}dB')
    parts += [f'alimiter=limit={10 ** ((P.TP_LIMIT - P.LIMITER_MARGIN) / 20):.3f}:level=0:attack=5:release=50', f'aresample={rate}']
    return ','.join(parts)


def build_chapter(book: dict, plan: dict, ch: dict, n: int, out_path: str, cover: str | None):
    with tempfile.TemporaryDirectory() as td:
        cmd = ['ffmpeg', '-hide_banner', '-v', 'error', '-y', *probe.input_args([tuple(s) for s in ch['segments']], td)]
        if cover: cmd += ['-i', cover]
        cmd += ['-map', '0:a:0'] + (['-map', '1:v', '-c:v', 'copy', '-disposition:v', 'attached_pic'] if cover else []) + ['-map_metadata', '-1', '-map_chapters', '-1']
        if ch['mode'] == 'copy': cmd += ['-c:a', 'copy']
        else:
            t = P.TIERS[ch['tier']]; cmd += ['-af', filter_chain(plan, ch, t['rate']), '-ac', '1', '-c:a', 'libmp3lame', '-q:a', str(t['q']), '-compression_level', '2', '-threads', '1']
        cmd += ['-id3v2_version', '3', *tags(book, plan, ch, n), out_path]
        r = probe.run(cmd, 7200)
        if r.returncode or not os.path.exists(out_path): raise probe.ToolError(f"ffmpeg failed on chapter {n}: {r.stderr.strip()[-300:]}")
    if ch['mode'] == 'copy' and plan['loudness']['gain_steps']:
        g = probe.run(['mp3gain', '-g', str(plan['loudness']['gain_steps']), '-T', '-k', '-c', out_path], 600)
        if g.returncode: raise probe.ToolError(f"mp3gain failed on chapter {n}: {(g.stderr or g.stdout).strip()[-200:]}")


def verify(plan: dict, files: list[str]) -> dict:
    """Every output must be readable and the total length must match the input (a second and a bit of slack for frame boundaries)."""
    infos = [probe.probe_file(f) for f in files]; got = sum(i['duration'] for i in infos); want = sum(c['duration'] for c in plan['chapters'])
    if abs(got - want) > 1.0 + 0.0015 * want + 0.05 * len(files): raise probe.ToolError(f'length mismatch: wrote {got:.1f} s, expected {want:.1f} s')
    return dict(seconds=round(got, 1), bytes=sum(os.path.getsize(f) for f in files), channels={i['channels'] for i in infos} == {1})


def execute(book: dict, plan: dict, force: bool = False) -> dict:
    """Process one planned book; returns a result summary. Raises ToolError on failure (nothing is left in the final library)."""
    out_dir = plan['out_dir']; partial = out_dir + '.partial'; t0 = time.time()
    if os.path.exists(out_dir) and not force: raise probe.ToolError(f'{out_dir} already exists (use --force to replace it)')
    shutil.rmtree(partial, ignore_errors=True); os.makedirs(partial)
    try:
        cover_src = details.cover(book); small = _cover_small(cover_src, partial); names = []
        for i, ch in enumerate(plan['chapters'], 1):
            build_chapter(book, plan, ch, i, os.path.join(partial, ch['file']), small); names.append(os.path.join(partial, ch['file']))
        v = verify(plan, names)
        after = probe.measure_book([(f, 0.0, None) for f in names], 1)
        if small: os.remove(small)
        if cover_src: shutil.copyfile(cover_src, os.path.join(partial, 'cover' + os.path.splitext(cover_src)[1]))
        with open(os.path.join(partial, 'playlist.m3u8'), 'w', encoding='utf8') as f: f.write('#EXTM3U\n' + ''.join(f"#EXTINF:{round(c['duration'])},{c['title']}\n{c['file']}\n" for c in plan['chapters']))
        tiers: dict[str, int] = {}
        for c in plan['chapters']: tiers[c['tier']] = tiers.get(c['tier'], 0) + 1
        summary = dict(status='done', out_dir=out_dir, chapters=len(names), chapter_source=plan['chapter_source'], tiers=tiers, in_bytes=plan['in_bytes'], out_bytes=v['bytes'], in_seconds=plan['duration'], out_seconds=v['seconds'],
                       loudness_before=plan['loudness']['I'], loudness_after=after['I'], true_peak_after=after['TP'], gain_db=plan['loudness']['apply_db'], downmix=plan['downmix'], warnings=plan['warnings'], seconds=round(time.time() - t0, 1),
                       finished=datetime.now(timezone.utc).isoformat())
        if after['I'] is not None and abs(after['I'] - P.TARGET_LUFS) > 2.5: summary['warnings'] = summary['warnings'] + [f"final loudness {after['I']:.1f} LUFS is {after['I'] - P.TARGET_LUFS:+.1f} dB from the target"]
        with open(os.path.join(partial, 'metadata.json'), 'w', encoding='utf8') as f: json.dump(dict(book={k: v for k, v in book.items() if k != 'suggestion'}, result=summary, chapters=[{k: c[k] for k in ('file', 'title', 'start', 'end', 'duration', 'mode', 'tier')} for c in plan['chapters']]), f, indent=1, ensure_ascii=False)
        if os.path.exists(out_dir):
            old = out_dir + '.old'; shutil.rmtree(old, ignore_errors=True); os.replace(out_dir, old); os.replace(partial, out_dir); shutil.rmtree(old, ignore_errors=True)
        else: os.makedirs(os.path.dirname(out_dir), exist_ok=True); os.replace(partial, out_dir)
        return summary
    except BaseException:
        shutil.rmtree(partial, ignore_errors=True); raise
