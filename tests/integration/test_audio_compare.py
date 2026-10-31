"""Actual FFmpeg encodings: excerpt matching and a mismatched recording control."""
from array import array
import math
import random
import shutil
import subprocess
import wave

import pytest

from app import audio_compare as A


def make_audio(path, seed):
    rng = random.Random(seed); samples = array('h'); rate = 8000
    for block in range(900):
        amplitude = rng.uniform(.02, .7)
        for i in range(800):
            t = (block * 800 + i) / rate
            samples.append(int(25000 * amplitude * (math.sin(2*math.pi*183*t) + .25*math.sin(2*math.pi*713*t))))
    with wave.open(str(path), 'wb') as f:
        f.setnchannels(1); f.setsampwidth(2); f.setframerate(rate); f.writeframes(samples.tobytes())


@pytest.mark.skipif(not shutil.which('ffmpeg'), reason='ffmpeg required')
def test_encoded_excerpt_matches_but_other_recording_does_not(tmp_path):
    source = tmp_path / 'source.wav'; other = tmp_path / 'other.wav'; make_audio(source, 41); make_audio(other, 99)
    reference = tmp_path / 'reference.m4b'; excerpt = tmp_path / 'excerpt.mp3'
    for cmd in ([ '-i', str(source), '-c:a', 'aac', '-b:a', '24k', str(reference)], ['-ss', '21', '-i', str(source), '-t', '30', '-c:a', 'libmp3lame', '-b:a', '48k', '-ar', '22050', str(excerpt)]):
        p = subprocess.run(['ffmpeg', '-nostdin', '-v', 'error'] + cmd, capture_output=True)
        assert p.returncode == 0, p.stderr.decode()
    ref, _ = A.extract(reference, tmp_path / 'ref.f32'); query, _ = A.extract(excerpt, tmp_path / 'query.f32'); control, _ = A.extract(other, tmp_path / 'other.f32')
    index = A.Index(ref)
    match = index.locate(query)
    assert match['status'] == 'matched'
    assert abs(match['reference_start'] - 21) <= .2
    assert index.locate(control[100:400])['status'] == 'unmatched'
