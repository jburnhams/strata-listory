"""Short, reproducible acoustic review measurements; these are not quality scores."""
from array import array
import math
import statistics
import sys
import tempfile
import wave
from pathlib import Path

from app.process import plan, probe

RATE = 16000
WINDOW = RATE // 4


def continuous_sound_cue(assessment):
    """Flag consistently narrow sampled dynamics for content review, not noise detection."""
    samples = (assessment or {}).get('samples', [])
    values = []
    positions = set()
    for sample in samples:
        value = (sample.get('result') or {}).get('quiet_to_active_db')
        if isinstance(value, (int, float)) and not isinstance(value, bool) and math.isfinite(value):
            position = sample.get('start_seconds')
            if position is not None and position in positions:
                continue
            if position is not None:
                positions.add(position)
            values.append(value)
    hits = sum(value >= -8 for value in values)
    return dict(flagged=len(values) >= 2 and hits >= 2, valid_samples=len(values), sustained_samples=hits,
                threshold_db=-8, method='relative-window-review-v1')


def analyse_samples(samples):
    """Quarter-second RMS windows; relative quietest/active windows are not speech detection."""
    windows = []
    state = [0.0] * 4
    alpha = [1 - math.exp(-2 * math.pi * f / RATE) for f in (300, 1000, 3000, 7000)]
    for offset in range(0, len(samples) - WINDOW + 1, WINDOW):
        sums = [0.0] * 4
        total = 0.0
        for raw in samples[offset:offset+WINDOW]:
            x = raw / 32768.0
            total += x*x
            for i in range(4): state[i] += alpha[i] * (x - state[i])
            # Approximate one-pole bands, retaining spectral character rather than a calibrated equalizer.
            bands = (state[0], state[1]-state[0], state[2]-state[1], state[3]-state[2])
            for i in range(4): sums[i] += bands[i]*bands[i]
        windows.append((total/WINDOW, [v/WINDOW for v in sums]))
    if len(windows) < 8: return None
    ordered = sorted(windows, key=lambda x: x[0]); n = max(1, len(ordered)//5)
    quiet, active = ordered[:n], ordered[-n:]
    q = statistics.mean(x[0] for x in quiet); a = statistics.mean(x[0] for x in active)
    if a <= 1e-12: return None
    db = lambda x: round(10*math.log10(x), 1) if x > 1e-12 else None
    bands = [statistics.mean(x[1][i] for x in active) for i in range(4)]
    quiet_high = statistics.mean(x[1][3] for x in quiet)
    return dict(quiet_rms_dbfs=db(q), active_rms_dbfs=db(a), quiet_to_active_db=round(10*math.log10(q/a), 1) if q > 1e-12 else None,
                quiet_high_dbfs=db(quiet_high), active_low_to_presence_db=round(10*math.log10((bands[0]+bands[1])/bands[2]), 1) if bands[2] > 1e-12 and bands[0]+bands[1] > 1e-12 else None,
                windows=len(windows), quiet_windows=n, active_windows=n)


def sample(timeline, start, duration):
    with tempfile.TemporaryDirectory() as td:
        path = Path(td)/'sample.wav'
        args = probe.input_args(plan.segments(timeline, start, start+duration), td)
        result = probe.run(['ffmpeg', '-nostdin', '-v', 'error'] + args + ['-map', '0:a:0', '-ac', '1', '-ar', str(RATE), '-c:a', 'pcm_s16le', '-y', str(path)], 120)
        if result.returncode: raise probe.ToolError('cannot decode acoustic sample')
        with wave.open(str(path), 'rb') as source:
            samples = array('h'); samples.frombytes(source.readframes(source.getnframes()))
        if sys.byteorder != 'little': samples.byteswap()
        return analyse_samples(samples)


def assess(timeline, duration, source_sample_rate=None):
    samples = []
    for fraction in (.1, .5, .9):
        length = min(12.0, duration)
        start = max(0, min(duration-length, duration*fraction-length/2))
        try: result = sample(timeline, start, length) if length >= 2 else None
        except (probe.ToolError, OSError, EOFError, ValueError, wave.Error): result = None
        if result is not None and (source_sample_rate is None or source_sample_rate < 14000): result['quiet_high_dbfs'] = None
        samples.append(dict(start_seconds=round(start, 3), duration_seconds=round(length, 3), result=result))
    metrics = {}
    for name in ('quiet_rms_dbfs', 'quiet_high_dbfs', 'quiet_to_active_db', 'active_low_to_presence_db'):
        values = [s['result'][name] for s in samples if s['result'] and s['result'][name] is not None]
        metrics[name] = round(statistics.median(values), 1) if values else None
    return dict(method='relative-window-bands-v1', sample_rate=RATE, source_sample_rate=source_sample_rate, windows_seconds=.25, samples=samples, medians=metrics,
                note='Relative quiet and active windows are not speech classification. Music, pauses, breath, gating, source bandwidth and mastering affect these indicators; review the audio before judging noise, tone or dynamics.')
