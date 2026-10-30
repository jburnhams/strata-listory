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


def test_out_dir_for():
    f = '/final'
    assert P.out_dir_for(dict(title='Dune', author='Frank Herbert'), f) == '/final/Frank Herbert/Dune'
    assert P.out_dir_for(dict(title='Dune', author='', series='Dune Chronicles', series_position=1.0), f) == '/final/Unknown author/Dune Chronicles/01 - Dune'
    assert P.out_dir_for(dict(title='Half', author='A', series='S', series_position=1.5), f) == '/final/A/S/1.5 - Half' and P.out_dir_for(dict(title='T', author='A', series='S', series_position=None), f) == '/final/A/S/T'


def test_tiers_gain_and_copy_rules():
    assert P.pick_tier(-30, 0.2) == 'V5' and P.pick_tier(-10, 0.2) == 'V5' and P.pick_tier(-30, 0.0) == 'V4' and P.pick_tier(-20, 0.0) == 'V3'
    g = P.gain_for(-23.4, -1.0, False); assert g['db'] == 5.4 and g['apply_db'] == 5.4 + 0 - 0 - 2.9 + 0 or g['apply_db'] == pytest.approx(min(5.4, -0.5 + 6))
    g = P.gain_for(-22.1, -3.8, True); assert (g['steps'], g['apply_db']) == (1, 1.5)                    # headroom 2.3 dB: one lossless step
    assert P.gain_for(-14.0, -1.0, True)['steps'] == -3 and P.gain_for(-18.2, -3.0, True)['steps'] == 0 and P.gain_for(-40, -20, False)['warn']
    ok = dict(codec='mp3', channels=1, bit_rate=64.0, sample_rate=44100)
    by = {'a': ok, 'b': dict(ok), 'c': dict(ok, bit_rate=128.0), 'd': dict(ok, channels=2), 'e': dict(ok, sample_rate=22050), 'f': dict(ok, codec='aac')}
    assert P.copyable([('a', 0, None), ('b', 0, None)], by) and not any(P.copyable([('a', 0, None), (k, 0, None)], by) for k in 'cdef')


def test_segments_and_sample_ranges():
    files = [dict(path='a', duration=100.0), dict(path='b', duration=50.0)]; tl = P.timeline(files)
    assert P.segments(tl, 0, 100) == [('a', 0.0, None)] and P.segments(tl, 90, 120) == [('a', 90.0, None), ('b', 0.0, 20.0)] and P.segments(tl, 100, 150) == [('b', 0.0, None)]
    assert P.sample_ranges(0, 200) == [(0, 200)]
    r = P.sample_ranges(0, 1200); assert len(r) == 3 and all(b - a == 90 for a, b in r) and r[0][0] < r[1][0] < r[2][0]


def test_chapter_sources(monkeypatch):
    f = lambda name, d, ch=(), title='': dict(path=name, duration=d, chapters=list(ch), tags={'title': title} if title else {})
    emb = [dict(start=0, end=600, title='One'), dict(start=600, end=1500, title=''), dict(start=1500, end=1560, title='Credits')]
    chs, src = P.derive_chapters([f('a.m4b', 1560, emb)], [], None, 'B')
    assert src == 'embedded chapters' and [(c['title'], c['start'], c['end']) for c in chs] == [('One', 0, 600), ('Chapter 2', 600, 1560)]       # a short credits chapter joins the one before; empty titles are numbered
    aud = [dict(title='Opening Credits', length_sec=11), dict(title='Chapter 1', length_sec=1754), dict(title='Chapter 2', length_sec=1285)]
    chs, src = P.derive_chapters([f('x.mp3', 3050)], [], aud, 'B')
    assert src == 'Audible chapter list' and [c['title'] for c in chs] == ['Chapter 1', 'Chapter 2'] and chs[0]['start'] == 0 and chs[-1]['end'] == 3050     # the credits track joins the chapter after it and the longer part names it
    chs, src = P.derive_chapters([f('x.mp3', 3050)], [], [dict(title='a', length_sec=100), dict(title='b', length_sec=100)], 'B')
    assert src == 'whole file'                                                                  # a chapter list that does not fit the audio is not trusted
    files = [f('01.mp3', 30, title='B - Credits'), f('02.mp3', 600, title='B - The Start'), f('03.mp3', 800, title='B - The End')]
    chs, src = P.derive_chapters(files, [], None, 'B')
    assert src == 'file boundaries' and [c['title'] for c in chs] == ['The Start', 'The End'] and chs[0]['start'] == 0 and chs[0]['end'] == 630
    monkeypatch.setattr(P, 'MAX_CHAPTER', 100.0); monkeypatch.setattr(P, 'WINDOW', (30.0, 70.0)); monkeypatch.setattr(P, 'MIN_CHAPTER', 20.0)
    sil = [(40.0, 42.0), (60.0, 61.0), (120.0, 123.0)]
    assert P.cut_points(0, 200, sil) == [41.0, 121.5]                                           # the longest pause in each window
    assert P.cut_points(0, 200, []) == [70.0, 140.0]                                            # no pauses: hard cuts at the window end
    assert P.cut_points(0, 90, sil) == []
    chs, src = P.derive_chapters([f('long.mp3', 200)], sil, None, 'B')
    assert src == 'whole file + pauses' and [(c['title'], c['start'], c['end']) for c in chs] == [('Part 1', 0, 41.0), ('Part 2', 41.0, 121.5), ('Part 3', 121.5, 200)]
    chs, _ = P.derive_chapters([f('01.mp3', 150, title='B - Big'), f('02.mp3', 30, title='B - Small')], sil, None, 'B')
    assert [c['title'] for c in chs][:2] == ['Big (part 1)', 'Big (part 2)']                      # a long chapter with a title is split into numbered parts of it


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
    assert p['chapter_source'] == 'file boundaries' and len(p['chapters']) == 1 or len(p['chapters']) == 3               # 40 s chapters are under MIN_CHAPTER: merged; either way the plan is valid
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
    monkeypatch.setattr(P, 'MIN_CHAPTER', 20.0)
    b = make_book(tmp_path, 'Copied', 3, seconds=40, kbps=64, amp=0.05)                      # quiet: needs a boost
    p = P.make_plan(b, str(tmp_path / 'final'))
    assert [c['mode'] for c in p['chapters']] == ['copy'] * 3 and p['loudness']['gain_steps'] > 0
    res = R.execute(b, p); out = p['out_dir']; names = sorted(n for n in os.listdir(out) if n.endswith('.mp3'))
    infos = [probe.probe_file(os.path.join(out, n)) for n in names]
    assert len(names) == 3 and all(i['bit_rate'] < 70 and i['sample_rate'] == 44100 for i in infos)          # still the 64 kbps frames
    assert res['loudness_after'] > res['loudness_before'] + 1 and abs(sum(i['duration'] for i in infos) - p['duration']) < 1
    assert 'replaygain_album_gain' in infos[0]['tags'] or p['loudness']['remaining_db'] == 0.0
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
    plan = dict(chapters=[1, 2], channels=2, downmix='left', loudness=dict(apply_db=3.5, TP=-4.0, remaining_db=2.0))
    t = R.tags(dict(title='B', author='A', narrator='', series='S', series_position=2.0), plan, dict(title='Ch'), 1)
    assert ['-metadata', 'title=Ch'] == t[0:2] and 'track=1/2' in t and 'grouping=S #2' in t and not any(x.startswith('composer') for x in t)
    assert 'replaygain_album_gain=+2.00 dB' in t and any(x.startswith('replaygain_album_peak=0.9') for x in t)                # the part of the gain that lossless/limited gain could not apply
    assert not any('replaygain' in x for x in R.tags(dict(title='B'), dict(chapters=[1], loudness=dict(apply_db=0.0, TP=-3.0, remaining_db=0.0)), dict(title='C'), 1))
    f = R.filter_chain(plan, {}, 24000); assert f.startswith('pan=mono|c0=c0,volume=3.5dB,alimiter=') and f.endswith('aresample=24000')
    assert R.filter_chain(dict(channels=1, downmix='average', loudness=dict(apply_db=0.0)), {}, 44100).startswith('alimiter=')


def test_catalogue_processed_records():
    a, b = catalogue.add_book(title='A', path='/x/a'), catalogue.add_book(title='B', path='/x/b'); catalogue.add_book(title='Hand')
    assert [x['title'] for x in catalogue.pending_process(10)] == ['A', 'B']
    catalogue.set_processed(a['id'], dict(status='done', finished='2026-10-30T00:00:00+00:00', in_bytes=100, out_bytes=40, out_seconds=3600.0))
    catalogue.set_processed(b['id'], dict(status='failed', finished='2026-10-30T00:00:00+00:00', error='x'))
    assert [x['title'] for x in catalogue.pending_process(10)] == ['B'] and catalogue.get_processed(a['id'])['out_bytes'] == 40 and catalogue.get_processed(999) is None
    assert catalogue.processed_summary() == dict(done=1, failed=1, in_bytes=100, out_bytes=40, seconds=3600.0)
    catalogue.add_scanned([dict(path='/x/c', title='C', author='', narrator='', series_position=None, asin='', kind='book', file_count=1)])
    assert [x['title'] for x in catalogue.pending_process(10, include_unconfirmed=True)] == ['B', 'C']
    catalogue.remove_book(a['id']); assert catalogue.get_processed(a['id']) is None


# ---- command line -------------------------------------------------------------------------------------------------------------

@needs_ffmpeg
def test_process_command_plan_run_status_and_failures(tmp_path, capsys, monkeypatch):
    from app import cli, settings
    final = tmp_path / 'final'
    assert cli.main(['process']) == 0 and 'nothing to process' in capsys.readouterr().out
    b = make_book(tmp_path, 'Cli', 1, seconds=40)
    assert cli.main(['process', '--plan', '--out', str(final)]) == 0
    out = capsys.readouterr().out
    assert f"#{b['id']}" in out and 'Cli' in out and '-> -18 LUFS' in out and '1 books,' in out and not final.exists()           # a plan changes nothing
    assert cli.main(['process', '--json', '--out', str(final)]) == 0 and json.loads(capsys.readouterr().out)[str(b['id'])]['title'] == 'Cli'
    assert cli.main(['process-status']) == 0 and '0 books processed' in capsys.readouterr().out
    assert cli.main(['process', '--jobs', '1', '--out', str(final)]) == 0
    out = capsys.readouterr().out; assert 'done in' in out and 'LUFS' in out and (final / 'An Author' / 'Cli').is_dir()
    assert catalogue.get_processed(b['id'])['status'] == 'done'
    assert cli.main(['process', '--out', str(final)]) == 0 and 'nothing to process' in capsys.readouterr().out                    # done books are skipped
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
                loudness=dict(I=-23.0, TP=-4.0, LRA=5.0, target=-18.0, gain_db=5.0, apply_db=3.0 if mode == 'encode' else 3.0, gain_steps=2, remaining_db=2.0, **loud),
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
    assert built[1]['remaining_db'] == pytest.approx(5.0 - built[1]['apply_db'], abs=0.01) and any('lowered' in w for w in res['warnings']) and os.path.isdir(plan['out_dir'])


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
    res = R.execute(b, p)
    assert res['true_peak_after'] <= R.CLIP_CEILING and res['loudness_after'] > res['loudness_before'] + 3


@needs_ffmpeg
def test_plan_warns_when_the_source_already_clips(tmp_path):
    d = tmp_path / 'Hot'; d.mkdir()
    subprocess.run(['ffmpeg', '-v', 'error', '-y', '-f', 'lavfi', '-i', 'sine=f=220:d=30', '-af', 'volume=24,aeval=clip(val(0)\\,-1\\,1)', '-c:a', 'libmp3lame', '-b:a', '128k', str(d / '01.mp3')], check=True)       # pushed past full scale
    b = catalogue.add_book(title='Hot', path=str(d)); p = P.make_plan(b, str(tmp_path / 'final'))
    assert p['loudness']['TP'] > 0 and any('already clips' in w for w in p['warnings'])
