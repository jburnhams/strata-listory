"""Adversarial checks: gain changes, reordered excerpts, edits and ambiguity."""
import io
import json
import random
from array import array
from types import SimpleNamespace

import pytest

from app import audio_compare as A


def speech(n=3000, seed=12):
    r = random.Random(seed)
    return [-45 + 16 * r.random() + 8 * r.random() for _ in range(n)]


def test_gain_and_small_encoding_perturbations_preserve_location():
    ref = speech(); r = random.Random(4)
    query = [v + 6 + r.uniform(-.15, .15) for v in ref[1000:1300]]
    m = A.Index(ref).locate(query)
    assert m['status'] == 'matched'
    assert m['reference_start'] == 100
    assert m['correlation'] > .99


def test_reordered_parts_are_located_without_filename_assumptions():
    ref = speech(); index = A.Index(ref)
    assert [index.locate(ref[a:a+300])['reference_start'] for a in (1800, 100, 900)] == [180, 10, 90]


def test_unrelated_audio_and_silence_never_establish_same_performance():
    index = A.Index(speech())
    assert index.locate(speech(300, 99))['status'] == 'unmatched'
    assert index.locate([-80] * 300)['status'] == 'uninformative'
    assert index.locate([-20] * 10)['status'] == 'too_short'
    assert A.correlation([1] * 100, [1] * 100) == 0
    assert A.correlation([1] * 100, [1] * 99) == 0


def test_repeated_passage_is_ambiguous():
    excerpt = speech(300)
    ref = speech(500) + excerpt + speech(500, 8) + excerpt + speech(100, 10)
    assert A.Index(ref).locate(excerpt)['status'] == 'ambiguous'


def test_edit_changes_timeline_and_coverage_exposes_missing_region():
    ref = speech(2000); query = ref[:600] + ref[900:1500]
    matches = A.align(A.Index(ref), query)
    assert [m['reference_start'] for m in matches] == [0, 30, 90, 120]
    cov = A.coverage([(m['reference_start'], m['reference_start'] + m['query_end'] - m['query_start']) for m in matches], 200)
    assert cov['gaps'] == [[60, 90], [150, 200]]
    assert cov['coverage_fraction'] == .6


def test_short_tail_and_window_validation():
    ref = speech(700); matches = A.align(A.Index(ref), ref[:610])
    assert matches[-1]['query_start'] == 31
    assert matches[-1]['query_end'] == 61
    assert all(m['status'] == 'matched' for m in matches)
    with pytest.raises(ValueError): A.align(A.Index(ref), ref, window=1)
    assert A.Index([]).locate(ref[:100])['status'] == 'unmatched'
    assert A.coverage([], 0)['coverage_fraction'] == 0
    assert A.coverage([(-10, 2), (1, 4), (10, 15), (50, 60)], 12)['gaps'] == [[4, 10]]


def fake_extract(monkeypatch, code=0, output=None):
    monkeypatch.setattr(A.probe, 'probe_file', lambda p: dict(path=p, duration=30, codec='aac', chapters=[]))
    class Process:
        def __init__(self, *args, **kwargs):
            self.stdout = io.BytesIO(output if output is not None else b'frame:0\nlavfi.astats.Overall.RMS_level=-20\nlavfi.astats.Overall.RMS_level=-inf\n')
        def wait(self, timeout=None): return code
        def poll(self): return code
        def kill(self): pass
    monkeypatch.setattr(A.subprocess, 'Popen', Process)


def test_extract_hashes_and_preserves_original(tmp_path, monkeypatch):
    p = tmp_path / 'audio'; p.write_bytes(b'original'); fake_extract(monkeypatch)
    values, info = A.extract(p, tmp_path / 'features')
    assert list(values) == [-20, -80]
    assert len(info['sha256']) == 64 and info['size'] == 8
    assert p.read_bytes() == b'original'
    assert (tmp_path / 'features').read_bytes() == values.tobytes()


@pytest.mark.parametrize('code,output', [(1, b''), (0, b'no features')])
def test_failed_decode_never_returns_evidence(tmp_path, monkeypatch, code, output):
    p = tmp_path / 'audio'; p.write_bytes(b'a'); fake_extract(monkeypatch, code, output)
    with pytest.raises(ValueError, match='cannot decode'): A.extract(p, tmp_path / 'features')
    assert not (tmp_path / 'features').exists()


def test_quality_reports_measurements_and_failures(monkeypatch):
    monkeypatch.setattr(A.subprocess, 'run', lambda *a, **k: SimpleNamespace(returncode=0, stderr='Overall\nPeak level dB: -1.2\nRMS level dB: -22\nDC offset: 0.001\nPeak count: 4\n'))
    assert A.quality_sample('x', 0) == dict(sample_peak_dbfs=-1.2, rms_dbfs=-22, dc_offset=.001, peak_count=4)
    monkeypatch.setattr(A.subprocess, 'run', lambda *a, **k: SimpleNamespace(returncode=1, stderr='bad file'))
    assert A.quality_sample('x', 0)['error'] == 'bad file'


def test_compare_writes_reviewable_report_without_approval(tmp_path, monkeypatch):
    ref = speech(600); one = tmp_path / 'one'; two = tmp_path / 'two'; one.touch(); two.touch()
    def extract(p, target):
        values = array('f', ref if p == one else ref[100:400]); target.write_bytes(values.tobytes())
        return values, dict(name=p.name, facts=dict(duration=len(values) * .1))
    monkeypatch.setattr(A, 'extract', extract)
    monkeypatch.setattr(A, 'quality_sample', lambda *a, **kw: dict(sample_peak_dbfs=-2))
    r = A.compare(one, [two], tmp_path / 'report')
    assert r['candidates'][0]['reference_offset'] == 10
    assert r['reference_coverage']['gaps'] == [[0, 10], [40, 60]]
    assert r['automatic_processing_approved'] is False
    assert r['recommended_source'] is None
    assert json.loads((tmp_path / 'report' / 'report.json').read_text())['version'] == A.VERSION
    with pytest.raises(ValueError, match='distinct'): A.compare(one, [one], tmp_path / 'other')
    with pytest.raises(ValueError, match='at least'): A.compare(one, [], tmp_path / 'other')
    with pytest.raises(FileExistsError): A.compare(one, [two], tmp_path / 'report')


def test_invalid_window_does_not_create_output(tmp_path):
    for window in (1, float('nan'), float('inf')):
        with pytest.raises(ValueError, match='window'): A.compare('a', ['b'], tmp_path / 'output', window)
    assert not (tmp_path / 'output').exists()


def test_anchored_verification_refines_small_timing_offsets():
    ref = speech(); index = A.Index(ref)
    query = [(ref[1000+i] + ref[1001+i]) / 2 for i in range(300)]
    m = index.verify_near(query, 100)
    assert m['status'] == 'matched'
    assert m['reference_start'] == 100.05
    assert m['correlation'] > .999
    assert index.verify_near(speech(300, 99), 100)['status'] == 'unmatched'
    assert index.verify_near(query, -100)['status'] == 'unmatched'


@pytest.mark.parametrize('initial_status', ['unmatched', 'uninformative'])
def test_anchored_fallback_requires_consistent_global_evidence(monkeypatch, initial_status):
    ref = speech(1600); index = A.Index(ref); original = index.locate; calls = 0
    def locate(q):
        nonlocal calls
        calls += 1
        return dict(status=initial_status) if calls == 3 else original(q)
    monkeypatch.setattr(index, 'locate', locate)
    windows = A.align(index, ref[:1500])
    assert windows[2]['status'] == 'matched'
    assert windows[2]['verification'] == 'anchored_local'
    assert windows[2]['initial_global_result']['status'] == initial_status


def test_command_reports_result_and_errors(tmp_path, monkeypatch, capsys):
    monkeypatch.setattr(A.sys, 'argv', ['audio-compare', 'reference', 'part', '--out', str(tmp_path / 'result')])
    monkeypatch.setattr(A, 'compare', lambda *a: dict(automatic_processing_approved=False))
    A.main()
    assert json.loads(capsys.readouterr().out) == dict(automatic_processing_approved=False)
    def failed(*a): raise ValueError('unreadable source')
    monkeypatch.setattr(A, 'compare', failed)
    with pytest.raises(SystemExit) as e: A.main()
    assert e.value.code == 1
    assert 'unreadable source' in capsys.readouterr().err
