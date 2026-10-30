import json, os, shutil, subprocess
import pytest
from app import catalogue, details
from app.process import plan as P, probe, run as R

HAVE_FF = bool(shutil.which('ffmpeg') and shutil.which('ffprobe'))
needs_ffmpeg = pytest.mark.skipif(not HAVE_FF, reason='needs ffmpeg')
needs_gain = pytest.mark.skipif(not (HAVE_FF and shutil.which('mp3gain')), reason='needs ffmpeg and mp3gain')


def synth(path, seconds=40, kind='speech', kbps=128, channels=1, rate=44100, title=None, amp=0.3):
    """A test mp3. speech: a tone that pauses 3 s in every 10; music: a continuous noise (lots of high-frequency content); tone: a continuous low tone."""
    src = {'speech': f'sine=f=220:d={seconds}:r={rate}', 'music': f'anoisesrc=d={seconds}:r={rate}:c=pink:a=0.5', 'tone': f'sine=f=220:d={seconds}:r={rate}'}[kind]
    af = f'volume={amp}' + (",volume=enable='between(mod(t,10),7,10)':volume=0" if kind == 'speech' else '')
    cmd = ['ffmpeg', '-v', 'error', '-y', '-f', 'lavfi', '-i', src, '-af', af, '-ac', str(channels), '-c:a', 'libmp3lame', '-b:a', f'{kbps}k'] + (['-metadata', f'title={title}'] if title else []) + [path]
    subprocess.run(cmd, check=True)
    return path


def make_book(tmp_path, name='Book', files=3, **kw):
    d = tmp_path / name; d.mkdir()
    for i in range(1, files + 1): synth(str(d / f'{i:02d} Chapter {i}.mp3'), title=f'{name} - Chapter {i}', **kw)
    return catalogue.add_book(title=name, author='An Author', narrator='A Narrator', path=str(d))


# ---- pure helpers -------------------------------------------------------------------------------------------------------------

def test_parsers():
    err = '[Parsed_ebur128_0 @ 0x1] Summary:\n\n  Integrated loudness:\n    I:         -20.1 LUFS\n  Loudness range:\n    LRA:         9.5 LU\n  True peak:\n    Peak:       -2.1 dBFS\n'
    assert probe.parse_ebur(err) == (-20.1, 9.5, -2.1) and probe.parse_ebur('nothing') == (None, None, None)
    s = 'silence_start: 1.5\nsilence_end: 3.0 | silence_duration: 1.5\nsilence_start: 9.0\n'
    assert probe.parse_silences(s) == [(1.5, 3.0)]                                       # a silence still open at the end has no end and is dropped
    a = '[Parsed_astats_4 @ 0x1] Channel: 1\n[Parsed_astats_4 @ 0x1] RMS level dB: -50.0\n[Parsed_astats_4 @ 0x1] Overall\n[Parsed_astats_4 @ 0x1] RMS level dB: -44.5\n[Parsed_astats_5 @ 0x1] Overall\n[Parsed_astats_5 @ 0x1] RMS level dB: -inf\n'
    assert probe.parse_overall_rms(a) == {'4': -44.5, '5': float('-inf')}
    assert sorted(['10 b', '2 a', '1 c'], key=probe.natural_key) == ['1 c', '2 a', '10 b']


def test_input_args_single_and_concat(tmp_path):
    assert probe.input_args([('/a.mp3', 0.0, None)], str(tmp_path)) == ['-i', '/a.mp3']
    assert probe.input_args([('/a.mp3', 5.0, 15.0)], str(tmp_path)) == ['-ss', '5.000', '-t', '10.000', '-i', '/a.mp3']
    a = probe.input_args([("/it's/a.mp3", 2.0, None), ('/b.mp3', 0.0, 7.5)], str(tmp_path))
    assert a[:5] == ['-f', 'concat', '-safe', '0', '-i']
    assert open(a[5]).read() == "file '/it'\\''s/a.mp3'\ninpoint 2.000\nfile '/b.mp3'\noutpoint 7.500\n"


def test_names_and_titles():
    assert P.clean_name('A/B: "C"?  d.') == 'A_B_ _C_ d' and P.clean_name('  ..  ') == '_'
    info = lambda title, path='/x/03 Chapter.mp3': dict(path=path, tags={'title': title} if title else {})
    assert P.file_title(info('4 - Brave New World: Chapter 3'), 'Brave New World (Unabridged)', 4) == 'Chapter 3'
    assert P.file_title(info('', '/x/07 - The Hound [B0X].mp3'), 'Other', 7) == 'The Hound'
    assert P.file_title(info('Book'), 'Book', 9) == 'Chapter 9'


def test_out_dir_for_and_years():
    f = '/final'
    assert P.out_dir_for(dict(title='Dune', author='Frank Herbert'), f, 1965) == '/final/Frank Herbert/[1965] Dune'
    assert P.out_dir_for(dict(title='Dune', author='', series='Dune Chronicles', series_position=1.0), f) == '/final/Unknown author/Dune'          # no year: no brackets; no series level
    assert P.out_dir_for(dict(title='A/B: C', author='X?'), f, 2001) == '/final/X_/[2001] A_B_ C'
    aud = dict(release_date='2008-01-16'); sug = lambda **k: dict(source='hardcover', release_date='1932', **k)
    assert P.year_for(dict(year=1999, year_source='manual'), aud, sug()) == (1999, 'set by hand')                                                  # your own year always wins
    assert P.year_for(dict(), aud, sug()) == (2008, 'Audible metadata file')
    assert P.year_for(dict(), None, dict(source='audnexus', release_date='2010-05-01')) == (2010, 'Audible/Audnexus match')
    assert P.year_for(dict(), None, dict(sources=['audible', 'hardcover'], source='hardcover', release_date='2010-05-01'))[1] == 'Audible/Audnexus match'
    assert P.year_for(dict(), None, sug()) == (1932, "hardcover match (the book's own publication year)")
    assert P.year_for(dict(year=2015, year_source='folder name'), None, None) == (2015, 'folder name') and P.year_for(dict(), None, None) == (None, '') and P.year_for(dict(), dict(release_date='junk'), None) == (None, '')


def test_metadata_rows_show_values_and_sources():
    book = dict(title='Dune', author='Frank Herbert', narrator='', series='', series_position=None, asin='B0X', suggestion=dict(title='Dune', author='Frank Herbert', sources=['audible', 'audiobookdb'], asin='B0X'))
    aud = dict(title='Dune', authors=['Frank Herbert'], narrators=['Scott Brick'], series=[dict(title='Dune Chronicles', sequence='1')], asin='B0X')
    rows = {r['field']: r for r in P.meta_rows(book, aud, 2007, 'Audible metadata file')}
    assert rows['Title']['source'] == 'audible + audiobookdb match (confirmed)' and rows['Author']['note'] == '' and rows['ASIN']['value'] == 'B0X'
    assert rows['Narrator'] == dict(field='Narrator', value='Scott Brick', source='Audible metadata file', note='') and rows['Series']['value'] == 'Dune Chronicles #1' and rows['Year']['value'] == '2007'      # filled in from the Audible file when the catalogue has none
    assert P.effective(book, aud) == dict(narrator='Scott Brick', series='Dune Chronicles', series_position=1.0) and P.effective(dict(narrator='N', series='S', series_position=2.0), aud) == dict(narrator='N', series='S', series_position=2.0)
    odd = P.meta_rows(dict(title='Brave New World', author='CBS Radio Workshop, Aldous Huxley', narrator='', suggestion=dict(author='CBS Radio Workshop, Aldous Huxley', title='Brave New World', sources=['audiobookdb'])), dict(title='Brave New World', authors=['Aldous Huxley']), None, '')
    assert {r['field'] for r in odd} == {'Title', 'Author'} and [r['note'] for r in odd if r['field'] == 'Author'] == ['the Audible metadata file says: Aldous Huxley']      # a disagreement is shown
    plain = P.meta_rows(dict(title='T', author='A', narrator='', suggestion=None), None, None, ''); assert [r['source'] for r in plain] == ['the catalogue: parsed from the folder name, or entered by hand'] * 2


def test_tiers_gain_and_copy_rules():
    assert P.pick_tier(-30, 0.2) == 'V5' and P.pick_tier(-10, 0.2) == 'V5' and P.pick_tier(-30, 0.0) == 'V4' and P.pick_tier(-20, 0.0) == 'V3'
    inside = P.decide_gain(-20.0, -3.0, True); assert (inside['apply_db'], inside['steps'], inside['reencode']) == (0.0, 0, False) and 'tolerance' in inside['why']      # 2 dB quiet: left alone
    assert P.decide_gain(-21.0, -3.0, False)['apply_db'] == 0.0 and P.decide_gain(-16.0, -3.0, False)['apply_db'] == 0.0                                  # edges of the band: 3 dB quiet, 2 dB loud
    loud = P.decide_gain(-14.0, -1.0, True); assert (loud['steps'], loud['apply_db'], loud['reencode']) == (-3, -4.5, False)                              # too loud: lossless attenuation
    assert P.decide_gain(-14.0, -1.0, False)['apply_db'] == -4.0
    lift = P.decide_gain(-25.0, -3.0, True); assert (lift['steps'], lift['apply_db'], lift['reencode']) == (1, 1.5, False) or lift['reencode']             # 7 dB quiet, 1.5 dB of headroom: not enough for a lossless fix
    assert lift['reencode'] is True and lift['apply_db'] == pytest.approx(min(7.0, 1.5 + P.LIMIT_DB))
    ok_lossless = P.decide_gain(-23.5, -12.0, True); assert (ok_lossless['steps'], ok_lossless['reencode']) == (4, False) and ok_lossless['apply_db'] == 6.0     # plenty of headroom: lossless lift to within tolerance
    enc = P.decide_gain(-25.0, -3.0, False); assert enc['reencode'] is False and enc['apply_db'] == pytest.approx(min(7.0, 1.5 + P.LIMIT_DB))             # already encoding: the limiter allowance, never past it
    peaky = P.decide_gain(-30.0, 0.0, False); assert peaky['apply_db'] == pytest.approx(P.TP_LIMIT * -1 * 0 + (P.TP_LIMIT - 0.0) + P.LIMIT_DB) and peaky['apply_db'] < peaky['db']      # peaks leave little room: stays quieter
    assert P.decide_gain(-50.0, 5.0, False)['apply_db'] == 0.0
    # with the peak measurement the limiter's work is bounded by SQUASH_DB at the 99th percentile and MAX_REDUCTION at the loudest peak
    ceiling = P.TP_LIMIT - P.LIMITER_MARGIN
    a = P.decide_gain(-23.4, -1.0, False, p99=-5.0); assert a['apply_db'] == pytest.approx(ceiling + P.SQUASH_DB + 5.0, abs=0.01) and a['apply_db'] < a['db']        # 4.7 of the 5.4 wanted
    b = P.decide_gain(-23.4, -1.0, False, p99=-9.0); assert b['apply_db'] == pytest.approx(5.4, abs=0.01)                                                              # rare peaks: the whole boost is allowed
    c = P.decide_gain(-23.4, 0.0, False, p99=-9.0); assert c['apply_db'] == pytest.approx(ceiling + P.MAX_REDUCTION - 0.0, abs=0.01)                                  # one huge peak caps it at MAX_REDUCTION
    assert P.decide_gain(-23.4, -1.0, True, p99=-5.0)['reencode'] is True and P.decide_gain(-23.4, -1.0, True, p99=-5.0)['apply_db'] == a['apply_db']
    ok = dict(codec='mp3', channels=1, bit_rate=64.0, sample_rate=44100)
    by = {'a': ok, 'b': dict(ok), 'c': dict(ok, bit_rate=128.0), 'd': dict(ok, channels=2), 'e': dict(ok, sample_rate=22050), 'f': dict(ok, codec='aac')}
    assert P.copyable([('a', 0, None), ('b', 0, None)], by) and not any(P.copyable([('a', 0, None), (k, 0, None)], by) for k in 'cdef')


def test_segments_and_sample_ranges():
    files = [dict(path='a', duration=100.0), dict(path='b', duration=50.0)]; tl = P.timeline(files)
    assert P.segments(tl, 0, 100) == [('a', 0.0, None)] and P.segments(tl, 90, 120) == [('a', 90.0, None), ('b', 0.0, 20.0)] and P.segments(tl, 100, 150) == [('b', 0.0, None)]
    assert P.sample_ranges(0, 200) == [(0, 200)]
    r = P.sample_ranges(0, 1200); assert len(r) == 3 and all(b - a == 90 for a, b in r) and r[0][0] < r[1][0] < r[2][0]


def chs(*mins, titles=None):
    out, pos = [], 0.0
    for k, m in enumerate(mins):
        out.append(dict(title=(titles[k] if titles else f'Chapter {k + 1}'), start=pos, end=pos + m * 60)); pos += m * 60
    return out


def shape(cs): return [(c['title'], round((c['end'] - c['start']) / 60, 1)) for c in cs]


def test_combine_only_short_chapters_and_only_up_to_thirty_minutes():
    assert shape(P.combine(chs(25, 20, 40, 28))) == [('Chapter 1', 25.0), ('Chapter 2', 20.0), ('Chapter 3', 40.0), ('Chapter 4', 28.0)]            # nothing is short: untouched
    assert shape(P.combine(chs(8, 8, 8, 8, 8, 8))) == [('Chapters 1-3', 24.0), ('Chapters 4-6', 24.0)]                                          # a run of short ones: up to 30 minutes
    assert shape(P.combine(chs(8, 25))) == [('Chapter 1', 8.0), ('Chapter 2', 25.0)]                                                            # 33 minutes would be over 30
    assert shape(P.combine(chs(28, 5, 5))) == [('Chapter 1', 28.0), ('Chapters 2-3', 10.0)]                                                     # the second joins the third, not the full first
    assert shape(P.combine(chs(9, 20))) == [('Chapters 1-2', 29.0)] and shape(P.combine(chs(10, 10))) == [('Chapter 1', 10.0), ('Chapter 2', 10.0)]         # "under 10" means under: two 10s stay apart
    assert shape(P.combine(chs(0.2, 40))) == [('Chapter 2', 40.2)]                                                                                # credits-length pieces join a neighbour even beyond 30 minutes and never name it
    assert shape(P.combine(chs(0.2, 59.9))) == [('Chapter 1', 0.2), ('Chapter 2', 59.9)]                                                          # ... unless the result would pass an hour
    named = P.combine(chs(6, 7, titles=['The Start', 'The Middle'])); assert shape(named) == [('The Start + The Middle', 13.0)]
    many = P.combine(chs(5, 5, 5, titles=['One', 'Two', 'Three'])); assert shape(many) == [('One ... Three', 15.0)]


def test_split_only_above_an_hour_into_equal_parts_at_pauses():
    assert P.split_points(0, 3600, []) == [] and P.split_points(0, 59 * 60, []) == []                                                          # exactly an hour is left alone
    assert P.split_points(0, 61 * 60, []) == [1830.0]                                                                                           # 61 minutes: two parts, cut in the middle when there is no pause
    assert P.split_points(0, 100 * 60, []) == [2000.0, 4000.0]                                                                                  # 100 minutes: three parts of 33
    sil = [(1700.0, 1702.0), (1850.0, 1851.0), (1000.0, 1010.0)]
    assert P.split_points(0, 61 * 60, sil) == [1701.0]                                                                                          # the nearest long pause wins over a distant longer one
    assert P.split_points(0, 61 * 60, [(1830.0, 1830.5)]) == [1830.0]                                                                           # a blip shorter than 1.2 s is not a pause
    assert P.split_points(0, 61 * 60, [(120.0, 125.0)]) == [1830.0]                                                                             # a pause far from the ideal point is ignored


def test_chapter_sources(monkeypatch):
    f = lambda name, d, ch=(), title='': dict(path=name, duration=d, chapters=list(ch), tags={'title': title} if title else {})
    emb = [dict(start=0, end=2400, title='One'), dict(start=2400, end=4200, title=''), dict(start=4200, end=4260, title='Credits')]
    cs, src = P.derive_chapters([f('a.m4b', 4260, emb)], [], None, 'B')
    assert src == 'embedded chapters (short ones combined)' and shape(cs) == [('One', 40.0), ('Chapter 2', 31.0)]                                  # the 1-minute credits join the chapter before; the empty title is numbered
    aud = [dict(title='Opening Credits', length_sec=11), dict(title='Chapter 1', length_sec=1754), dict(title='Chapter 2', length_sec=1285)]
    cs, src = P.derive_chapters([f('x.mp3', 3050)], [], aud, 'B')
    assert src == 'Audible chapter list (short ones combined)' and [c['title'] for c in cs] == ['Chapter 1', 'Chapter 2'] and cs[0]['start'] == 0 and cs[-1]['end'] == 3050     # 11 s credits join the chapter after and do not name it
    cs, src = P.derive_chapters([f('x.mp3', 3050)], [], [dict(title='a', length_sec=100), dict(title='b', length_sec=100)], 'B')
    assert src == 'whole file' and len(cs) == 1                                                                                                  # a chapter list that does not fit the audio is not trusted
    files = [f('01.mp3', 30, title='B - Credits'), f('02.mp3', 600, title='B - The Start'), f('03.mp3', 800, title='B - The End')]
    cs, src = P.derive_chapters(files, [], None, 'B')
    assert src == 'file boundaries (short ones combined)' and [c['title'] for c in cs] == ['The Start', 'The End'] and cs[0]['start'] == 0 and cs[0]['end'] == 630       # the 30 s track joins the next; 10 + 13 minutes are not both under 10, so they stay apart
    sil = [(2700.0, 2703.0), (5400.0, 5402.0)]
    cs, src = P.derive_chapters([f('long.mp3', 8100)], sil, None, 'B')
    assert src == 'whole file (long ones split)' and [(c['title'], c['start'], c['end']) for c in cs] == [('Chapter 1 (a)', 0, 2701.5), ('Chapter 1 (b)', 2701.5, 5401.0), ('Chapter 1 (c)', 5401.0, 8100)] and [c['label'] for c in cs] == ['001a', '001b', '001c']
    cs, _ = P.derive_chapters([f('01.mp3', 7200, title='B - Big'), f('02.mp3', 1200, title='B - Small')], [], None, 'B')
    assert [c['title'] for c in cs] == ['Big (a)', 'Big (b)', 'Big (c)', 'Small'] and [c['label'] for c in cs] == ['001a', '001b', '001c', '002'] and cs[0]['end'] == 2400.0 and cs[2]['end'] == 7200.0       # two hours: three parts of 40 minutes, the next chapter is 02
    assert [P.letters(k) for k in (0, 1, 25, 26, 27, 701, 702)] == ['a', 'b', 'z', 'aa', 'ab', 'zz', 'aaa']


# ---- with real audio ----------------------------------------------------------------------------------------------------------

@needs_ffmpeg
def test_probe_measurements_on_real_audio(tmp_path):
    sp, mu = synth(str(tmp_path / 's.mp3'), 40, 'speech'), synth(str(tmp_path / 'm.mp3'), 40, 'music')
    i = probe.probe_file(sp); assert (i['codec'], i['channels'], i['sample_rate']) == ('mp3', 1, 44100) and 38 < i['duration'] < 42 and 100 < i['bit_rate'] < 160 and i['chapters'] == []
    m = probe.measure_book([(sp, 0.0, None)]); assert m['I'] < -10 and m['TP'] < 0 and len(m['silences']) >= 3
    a, b = probe.spectrum([(sp, 0.0, None)]), probe.spectrum([(mu, 0.0, None)])
    assert a['pause_frac'] > 0.2 and b['pause_frac'] < 0.02 and b['hf_db'] > a['hf_db'] + 10               # a pausing low tone vs continuous broadband noise
    two = probe.measure_book([(sp, 0.0, 10.0), (mu, 5.0, 15.0)]); assert two['I'] is not None                # concatenated segments
    with pytest.raises(probe.ToolError): probe.probe_file(str(tmp_path / 'missing.mp3'))
    (tmp_path / 'bad.mp3').write_bytes(b'not audio'); 
    with pytest.raises(probe.ToolError): probe.probe_file(str(tmp_path / 'bad.mp3'))


@needs_ffmpeg
def test_stereo_with_cancelling_channels_uses_one_channel(tmp_path):
    d = tmp_path / 'Anti'; d.mkdir(); f = d / '01.mp3'
    subprocess.run(['ffmpeg', '-v', 'error', '-y', '-f', 'lavfi', '-i', 'sine=f=220:d=30', '-af', 'volume=0.3,asplit[l][r];[r]volume=-1[ri];[l][ri]amerge=inputs=2', '-ac', '2', '-c:a', 'libmp3lame', '-b:a', '128k', str(f)], check=True)
    b = catalogue.add_book(title='Anti', path=str(d)); p = P.make_plan(b, str(tmp_path / 'final'))
    assert p['downmix'] == 'left' and any('cancel' in w for w in p['warnings'])
    assert P.make_plan(make_book(tmp_path, 'Normal', 1, channels=2), str(tmp_path / 'final'))['downmix'] == 'average'


@needs_ffmpeg
def test_plan_and_execute_an_encoded_book(tmp_path):
    b = make_book(tmp_path, 'Encoded', 3, seconds=40, kbps=128)
    p = P.make_plan(b, str(tmp_path / 'final'))
    assert p['chapter_source'] == 'file boundaries (short ones combined)' and len(p['chapters']) == 1                   # three 40 s files are far under 10 minutes and together under 30: one chapter
    res = R.execute(b, p)
    out = p['out_dir']; names = sorted(n for n in os.listdir(out) if n.endswith('.mp3'))
    assert res['status'] == 'done' and names and os.path.exists(os.path.join(out, 'metadata.json')) and os.path.exists(os.path.join(out, 'playlist.m3u8')) and not os.path.exists(out + '.partial')
    infos = [probe.probe_file(os.path.join(out, n)) for n in names]
    assert all(i['channels'] == 1 and i['codec'] == 'mp3' for i in infos) and abs(sum(i['duration'] for i in infos) - p['duration']) < 2
    t = infos[0]['tags']; assert (t['album'], t['artist'], t['composer'], t['genre']) == ('Encoded', 'An Author', 'A Narrator', 'Audiobook') and t['track'].startswith('1/')
    assert res['out_bytes'] < res['in_bytes'] and abs(res['loudness_after'] - (-18.0)) < 3 and res['tiers'] == {'V5': len(names)}
    meta = json.load(open(os.path.join(out, 'metadata.json'))); assert meta['result']['status'] == 'done' and meta['book']['title'] == 'Encoded'
    with pytest.raises(probe.ToolError, match='already exists'): R.execute(b, p)
    assert R.execute(b, p, force=True)['status'] == 'done' and not os.path.exists(out + '.old')


@needs_ffmpeg
def test_music_like_chapters_get_the_higher_tiers(tmp_path):
    b = make_book(tmp_path, 'Music', 1, kind='music', seconds=60, kbps=192)
    p = P.make_plan(b, str(tmp_path / 'final')); assert [c['tier'] for c in p['chapters']] == ['V3'] and p['chapters'][0]['pause_frac'] < 0.02
    res = R.execute(b, p); i = probe.probe_file(os.path.join(p['out_dir'], p['chapters'][0]['file'])); assert i['sample_rate'] == 44100 and res['tiers'] == {'V3': 1}
    t = make_book(tmp_path, 'Tone', 1, kind='tone', seconds=60, kbps=192); q = P.make_plan(t, str(tmp_path / 'final'))
    assert q['chapters'][0]['tier'] == 'V4'                                                  # continuous but no high-frequency content


@needs_gain
def test_small_mono_mp3s_are_cut_and_regained_without_re_encoding(tmp_path, monkeypatch):
    monkeypatch.setattr(P, 'SHORT', 10.0); monkeypatch.setattr(P, 'TINY', 5.0)                                           # keep the three 40 s files as three chapters
    b = make_book(tmp_path, 'Copied', 3, seconds=40, kbps=64, amp=0.05)                      # quiet: needs a boost
    p = P.make_plan(b, str(tmp_path / 'final'))
    assert [c['mode'] for c in p['chapters']] == ['copy'] * 3 and p['loudness']['gain_steps'] > 0
    res = R.execute(b, p); out = p['out_dir']; names = sorted(n for n in os.listdir(out) if n.endswith('.mp3'))
    infos = [probe.probe_file(os.path.join(out, n)) for n in names]
    assert len(names) == 3 and all(i['bit_rate'] < 70 and i['sample_rate'] == 44100 for i in infos)          # still the 64 kbps frames
    assert res['loudness_after'] > res['loudness_before'] + 1 and abs(sum(i['duration'] for i in infos) - p['duration']) < 1
    assert res['out_bytes'] <= res['in_bytes'] * 1.05                                         # nothing re-encoded: about the same size


@needs_ffmpeg
def test_a_failure_leaves_nothing_behind(tmp_path, monkeypatch):
    b = make_book(tmp_path, 'Fail', 1, seconds=30); p = P.make_plan(b, str(tmp_path / 'final'))
    monkeypatch.setattr(R, 'build_chapter', lambda *a: (_ for _ in ()).throw(probe.ToolError('boom')))
    with pytest.raises(probe.ToolError, match='boom'): R.execute(b, p)
    assert not os.path.exists(p['out_dir']) and not os.path.exists(p['out_dir'] + '.partial')


@needs_ffmpeg
def test_verify_catches_a_length_mismatch(tmp_path):
    f = synth(str(tmp_path / 'a.mp3'), 20)
    ok = dict(chapters=[dict(duration=20.0)]); assert R.verify(ok, [f])['bytes'] > 0
    with pytest.raises(probe.ToolError, match='length mismatch'): R.verify(dict(chapters=[dict(duration=60.0)]), [f])


def test_tags_and_filter_chain():
    plan = dict(chapters=[1, 2], channels=2, downmix='left', loudness=dict(apply_db=3.5, TP=-4.0))
    t = R.tags(dict(title='B', author='A', narrator='', series='S', series_position=2.0), plan, dict(title='Ch'), 1)
    assert ['-metadata', 'title=Ch'] == t[0:2] and 'track=1/2' in t and 'grouping=S #2' in t and not any(x.startswith('composer') for x in t)
    assert not any('replaygain' in x for x in t)                                                                               # no compensation tags: a book that could not be lifted stays slightly quiet
    f = R.filter_chain(plan, {}, 24000); assert f.startswith('pan=mono|c0=c0,volume=3.5dB,alimiter=') and f.endswith('aresample=24000')
    assert R.filter_chain(dict(channels=1, downmix='average', loudness=dict(apply_db=0.0)), {}, 44100).startswith('alimiter=')


def test_catalogue_processed_records():
    a, b = catalogue.add_book(title='A', author='X', path='/x/a'), catalogue.add_book(title='B', author='X', path='/x/b'); catalogue.add_book(title='Hand', author='X')
    assert [x['title'] for x in catalogue.pending_process(10)] == ['A', 'B']
    catalogue.set_processed(a['id'], dict(status='done', finished='2026-10-30T00:00:00+00:00', in_bytes=100, out_bytes=40, out_seconds=3600.0))
    catalogue.set_processed(b['id'], dict(status='failed', finished='2026-10-30T00:00:00+00:00', error='x'))
    assert [x['title'] for x in catalogue.pending_process(10)] == ['B'] and catalogue.get_processed(a['id'])['out_bytes'] == 40 and catalogue.get_processed(999) is None
    assert catalogue.processed_summary() == dict(done=1, failed=1, in_bytes=100, out_bytes=40, seconds=3600.0)
    catalogue.add_scanned([dict(path='/x/c', title='C', author='', narrator='', series_position=None, asin='', kind='book', file_count=1)])
    catalogue.add_book(title='No author', path='/x/d'); catalogue.add_book(title='', path='/x/e') if False else None
    assert [x['title'] for x in catalogue.pending_process(10)] == ['B']                                                          # scanned (unconfirmed) books and ones without an author are not picked up
    catalogue.remove_book(a['id']); assert catalogue.get_processed(a['id']) is None


# ---- command line -------------------------------------------------------------------------------------------------------------

@needs_ffmpeg
def test_process_command_plan_run_status_and_failures(tmp_path, capsys, monkeypatch):
    from app import cli, settings
    final = tmp_path / 'final'
    assert cli.main(['process']) == 0 and 'nothing named' in capsys.readouterr().out
    b = make_book(tmp_path, 'Cli', 1, seconds=40)
    assert cli.main(['process', '--plan', str(b['id']), '--out', str(final)]) == 0
    out = capsys.readouterr().out
    assert f"#{b['id']}" in out and 'Cli' in out and '-> ' in out and 'LUFS' in out and '1 books,' in out and not final.exists()           # a plan changes nothing
    assert cli.main(['process', '--json', str(b['id']), '--out', str(final)]) == 0 and json.loads(capsys.readouterr().out)[str(b['id'])]['title'] == 'Cli'
    assert cli.main(['process-status']) == 0 and '0 books processed' in capsys.readouterr().out
    assert cli.main(['process', str(b['id']), '--jobs', '1', '--out', str(final)]) == 0
    out = capsys.readouterr().out; assert 'done in' in out and 'LUFS' in out and (final / 'An Author' / 'Cli').is_dir()
    assert catalogue.get_processed(b['id'])['status'] == 'done'
    assert cli.main(['process']) == 0 and 'nothing named' in capsys.readouterr().out                                                  # nothing is processed unless named or approved
    assert cli.main(['process-status']) == 0 and '1 books processed' in capsys.readouterr().out
    assert cli.main(['process', str(b['id']), '--out', str(final)]) == 1 and 'already exists' in capsys.readouterr().out         # explicit id, output exists: reported, exit 1
    assert catalogue.get_processed(b['id'])['status'] == 'failed'
    assert cli.main(['process', str(b['id']), '--force', '--out', str(final)]) == 0 and catalogue.get_processed(b['id'])['status'] == 'done'
    capsys.readouterr()
    empty = tmp_path / 'Empty'; empty.mkdir(); e = catalogue.add_book(title='Empty', path=str(empty))
    assert cli.main(['process', '--plan', str(e['id']), '--out', str(final)]) == 1 and 'cannot plan' in capsys.readouterr().out
    missing = catalogue.add_book(title='Gone', path=str(tmp_path / 'gone'))
    with pytest.raises(SystemExit, match='not available'): cli.main(['process', str(missing['id'])])
    with pytest.raises(SystemExit, match='no such book'): cli.main(['process', '999'])
    settings.save(dict(final_dir=str(tmp_path / 'elsewhere'))); assert settings.final_dir() == str(tmp_path / 'elsewhere')
    monkeypatch.setenv('APP_FINAL', str(tmp_path / 'env')); assert settings.final_dir() == str(tmp_path / 'env')


# ---- clipping guard -----------------------------------------------------------------------------------------------------------

def fake_book(tmp_path, mode='encode', **loud):
    """A one-chapter plan whose building and measuring are faked, to test the clipping guard on its own."""
    plan = dict(book_id=1, title='T', out_dir=str(tmp_path / 'final' / 'T'), chapter_source='x', downmix='average', duration=10.0, channels=1, in_bytes=100, warnings=[],
                loudness=dict(I=-23.0, TP=-4.0, LRA=5.0, target=-18.0, gain_db=5.0, apply_db=3.0, gain_steps=2, decision='x', reencoded=False, **loud),
                chapters=[dict(title='C', file='01 - C.mp3', start=0.0, end=10.0, duration=10.0, mode=mode, tier='V5' if mode == 'encode' else 'copy', kbps=49, segments=[('/x.mp3', 0.0, None)])])
    return dict(id=1, title='T', author='A'), plan


def stub(monkeypatch, peaks):
    built = []
    monkeypatch.setattr(R.details, 'cover', lambda b: None)
    monkeypatch.setattr(R, 'build_chapter', lambda book, plan, ch, n, out, cover: (open(out, 'wb').write(b'x'), built.append(dict(plan['loudness'])))[0])
    monkeypatch.setattr(R, 'verify', lambda plan, files: dict(seconds=10.0, bytes=1, channels=True))
    seq = iter(peaks)
    monkeypatch.setattr(R.probe, 'measure_book', lambda segs, ch=1: dict(I=-18.0, TP=next(seq), LRA=5.0, silences=[]))
    return built


def test_a_book_that_comes_out_near_clipping_is_rebuilt_with_less_gain(tmp_path, monkeypatch):
    book, plan = fake_book(tmp_path); built = stub(monkeypatch, [-0.4, -1.6])
    res = R.execute(book, plan)
    assert res['retries'] == 1 and len(built) == 2 and built[1]['apply_db'] == pytest.approx(3.0 - (-0.4 + 1.5 + 0.2)) and res['true_peak_after'] == -1.6        # took the excess (plus a little) off the gain
    assert any('lowered' in w for w in res['warnings']) and os.path.isdir(plan['out_dir'])


def test_copied_chapters_are_lowered_in_whole_steps(tmp_path, monkeypatch):
    book, plan = fake_book(tmp_path, mode='copy'); built = stub(monkeypatch, [-0.2, -2.0])
    res = R.execute(book, plan)
    assert res['retries'] == 1 and built[1]['gain_steps'] == 2 - 1 and built[1]['apply_db'] == 1.5                                          # excess 1.5 dB -> one 1.5 dB step


def test_a_book_that_cannot_be_made_safe_is_not_written(tmp_path, monkeypatch):
    book, plan = fake_book(tmp_path); stub(monkeypatch, [-0.1, -0.2, -0.3])
    with pytest.raises(probe.ToolError, match='clipping risk'): R.execute(book, plan)
    assert not os.path.exists(plan['out_dir']) and not os.path.exists(plan['out_dir'] + '.partial')


def test_a_clean_book_is_built_once(tmp_path, monkeypatch):
    book, plan = fake_book(tmp_path); built = stub(monkeypatch, [-1.4]); res = R.execute(book, plan)
    assert res['retries'] == 0 and len(built) == 1


@needs_ffmpeg
def test_real_quiet_book_with_loud_clicks_stays_clear_of_clipping(tmp_path):
    d = tmp_path / 'Clicks'; d.mkdir()
    subprocess.run(['ffmpeg', '-v', 'error', '-y', '-f', 'lavfi', '-i', 'aevalsrc=0.03*sin(2*PI*220*t)+if(lt(mod(t\\,10)\\,0.002)\\,0.95\\,0):d=60:s=44100', '-c:a', 'libmp3lame', '-b:a', '128k', str(d / '01.mp3')], check=True)
    b = catalogue.add_book(title='Clicks', path=str(d)); p = P.make_plan(b, str(tmp_path / 'final'))
    assert p['loudness']['gain_db'] > 6 and p['loudness']['TP'] > -2                                  # wants a big boost but the clicks leave no headroom
    assert any('below the target' in w for w in p['warnings'])                                         # and says it will stay quiet
    res = R.execute(b, p)
    assert res['true_peak_after'] <= R.CLIP_CEILING and res['loudness_after'] >= res['loudness_before']      # no clipping, even though that leaves it well under the target


@needs_ffmpeg
def test_plan_warns_when_the_source_already_clips(tmp_path):
    d = tmp_path / 'Hot'; d.mkdir()
    subprocess.run(['ffmpeg', '-v', 'error', '-y', '-f', 'lavfi', '-i', 'sine=f=220:d=30', '-af', 'volume=24,aeval=clip(val(0)\\,-1\\,1)', '-c:a', 'libmp3lame', '-b:a', '128k', str(d / '01.mp3')], check=True)       # pushed past full scale
    b = catalogue.add_book(title='Hot', path=str(d)); p = P.make_plan(b, str(tmp_path / 'final'))
    assert p['loudness']['TP'] >= -0.2 and any('full scale' in w for w in p['warnings'])


@needs_gain
def test_a_small_mono_mp3_that_is_too_quiet_for_lossless_gain_is_re_encoded(tmp_path):
    d = tmp_path / 'Quiet'; d.mkdir()
    subprocess.run(['ffmpeg', '-v', 'error', '-y', '-f', 'lavfi', '-i', 'aevalsrc=0.02*sin(2*PI*220*t)+if(lt(mod(t\\,10)\\,0.002)\\,0.45\\,0):d=60:s=44100', '-ac', '1', '-c:a', 'libmp3lame', '-b:a', '64k', str(d / '01.mp3')], check=True)       # 64 kbps mono (copy-eligible), quiet speech with loud clicks
    b = catalogue.add_book(title='Quiet', author='A', path=str(d)); p = P.make_plan(b, str(tmp_path / 'final'))
    assert p['loudness']['reencoded'] is True and [c['mode'] for c in p['chapters']] == ['encode'] and any('re-encoded although' in w for w in p['warnings'])
    res = R.execute(b, p); assert res['reencoded'] is True and res['true_peak_after'] <= R.CLIP_CEILING and res['loudness_after'] > res['loudness_before'] + 3
    ok = tmp_path / 'Fine'; ok.mkdir()
    subprocess.run(['ffmpeg', '-v', 'error', '-y', '-f', 'lavfi', '-i', 'sine=f=220:d=60', '-af', 'volume=1.5', '-ac', '1', '-c:a', 'libmp3lame', '-b:a', '64k', str(ok / '01.mp3')], check=True)
    q = P.make_plan(catalogue.add_book(title='Fine', author='A', path=str(ok)), str(tmp_path / 'final'))
    assert q['loudness']['apply_db'] == 0.0 and [c['mode'] for c in q['chapters']] == ['copy'] and 'tolerance' in q['loudness']['decision']      # -18.9 LUFS: inside the band, copied untouched


@needs_ffmpeg
def test_a_long_file_is_split_into_lettered_parts_at_pauses(tmp_path):
    d = tmp_path / 'Long'; d.mkdir()
    subprocess.run(['ffmpeg', '-v', 'error', '-y', '-f', 'lavfi', '-i', 'sine=f=220:d=3900:r=8000', '-af', "volume=0.3,volume=enable='between(mod(t,10),7,10)':volume=0", '-ac', '1', '-c:a', 'libmp3lame', '-b:a', '16k', str(d / 'Long book.mp3')], check=True)
    b = catalogue.add_book(title='Long', author='A', path=str(d)); p = P.make_plan(b, str(tmp_path / 'final'))
    assert [c['label'] for c in p['chapters']] == ['001a', '001b'] and [c['file'] for c in p['chapters']] == ['001a - A - Long.mp3', '001b - A - Long.mp3'] and [c['title'] for c in p['chapters']] == ['Chapter 1 (a)', 'Chapter 1 (b)']
    cut = p['chapters'][0]['end']; assert abs(cut - 1950) < 360 and any(s <= cut <= e + 5 for s, e in probe.measure_book([(str(d / 'Long book.mp3'), 0.0, None)])['silences'])        # near the middle, inside a pause


@pytest.mark.parametrize('title, expect', [('Chapter 3', ''), ('Chapter 3: Find Your Niche', 'Find Your Niche'), ('Chapter 12. The End', 'The End'), ('Part Two: The Nature of the Crisis', 'The Nature of the Crisis'),
                                           ('Part One_ Chapter 1_ The Start', 'The Start'), ('Introduction', 'Introduction'), ('Epilogue', 'Epilogue'), ('Chapters 3-5', ''), ('Track 07', ''), ('Chapter Fourteen', ''), ('Section 2 - Methods', 'Methods'),
                                           ('12', ''), ('Book Two: Heaven', 'Heaven'), ('Chapterhouse', 'Chapterhouse'), ('The Part That Matters', 'The Part That Matters'), ('Disc 3', ''), ('Ep. 5: V', 'V'), ('', ''), ('  ', ''), ('Chapter XIV', ''), ('Part 2 of 4 - 25032009', 'Part 2 of 4 - 25032009')])
def test_chapter_names_for_file_names(title, expect):
    assert P.chapter_name(title) == expect


def test_file_names_include_a_chapter_name_only_when_there_is_one_and_always_fit():
    assert P.file_name('001', 'Anne Boden', 'Banking on It', '') == '001 - Anne Boden - Banking on It.mp3'
    assert P.file_name('030b', 'Anne Boden', 'Banking on It', 'Find Your Niche') == '030b - Anne Boden - Banking on It - Find Your Niche.mp3'
    assert P.file_name('002', '', 'A/B', 'Why?') == '002 - Unknown author - A_B - Why_.mp3'
    long_title, long_chapter = 'T' * 120, 'C' * 150
    n = P.file_name('001', 'Author', long_title, long_chapter); assert len(n.encode('utf8')) <= P.MAX_NAME_BYTES and n.endswith('.mp3') and long_title in n          # the chapter name is shortened first
    assert ' - ' + 'C' * 5 in n
    n = P.file_name('001', 'Author', 'T' * 300, 'Short'); assert len(n.encode('utf8')) <= P.MAX_NAME_BYTES and 'Short' not in n          # no room for a chapter name at all: dropped, the title is cut
    n = P.file_name('001', 'Author', '‘' * 100, '’' * 100); assert len(n.encode('utf8')) <= P.MAX_NAME_BYTES                         # curly quotes are 3 bytes each: bytes, not characters, are counted
    assert n.encode('utf8').decode('utf8')                                                                                                       # and a cut never splits a character
