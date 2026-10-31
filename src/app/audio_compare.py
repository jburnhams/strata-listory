"""Offline experimental alignment of near-duplicate spoken-word recordings.

Fingerprints are 64-bit energy-change sketches, NOT recording identifiers. Candidate
matches must pass full-window correlation and ambiguity checks. Unmatched material
stays unresolved; it never proves another performer. Originals are never written.
"""
from array import array
from collections import defaultdict
from datetime import datetime, timezone
import hashlib
import heapq
import json
import math
from pathlib import Path
import subprocess
import sys
import tempfile
import threading

from app.process import probe

STEP = 0.1
BITS = 64
VERSION = 'energy-alignment-v1'


def correlation(a, b):
    if len(a) != len(b) or len(a) < BITS: return 0.0
    ma = sum(a) / len(a); mb = sum(b) / len(b)
    aa = sum((x - ma) ** 2 for x in a); bb = sum((x - mb) ** 2 for x in b)
    if min(aa, bb) < len(a): return 0.0  # silence/constant levels must not match
    return sum((x - ma) * (y - mb) for x, y in zip(a, b)) / math.sqrt(aa * bb)


def sketches(values):
    out = array('Q'); bits = 0; mask = (1 << BITS) - 1
    for i in range(1, len(values)):
        bits = ((bits << 1) | int(values[i] > values[i - 1])) & mask
        if i >= BITS: out.append(bits)
    return out


class Index:
    def __init__(self, values):
        self.values = values; self.hashes = sketches(values); self.buckets = defaultdict(lambda: array('I'))
        for i, h in enumerate(self.hashes):
            if not 12 <= h.bit_count() <= 52: continue
            for k in range(8): self.buckets[k, (h >> (k * 8)) & 255].append(i)

    def locate(self, query, minimum=.92, margin=.04):
        if len(query) < BITS + 1: return dict(status='too_short')
        # Use a non-flat fingerprint inside the query, while verifying all frames.
        hashes = sketches(query)
        pick = next((i for i, h in enumerate(hashes) if 20 <= h.bit_count() <= 44), None)
        if pick is None: return dict(status='uninformative')
        h = hashes[pick]; positions = set()
        for k in range(8): positions.update(self.buckets.get((k, (h >> (k * 8)) & 255), ()))
        candidates = heapq.nsmallest(48, positions, key=lambda i: (self.hashes[i] ^ h).bit_count())
        offsets = set(i - pick for i in candidates if 0 <= i - pick <= len(self.values) - len(query))
        ranked = sorted(((correlation(query, self.values[o:o + len(query)]), o) for o in offsets), reverse=True)
        if not ranked: return dict(status='unmatched')
        best, offset = ranked[0]
        runner_up = max((s for s, o in ranked[1:] if abs(o - offset) > 20), default=0)
        status = 'matched' if best >= minimum and best - runner_up >= margin else 'ambiguous' if best >= minimum else 'unmatched'
        return dict(status=status, reference_start=round(offset * STEP, 3), correlation=round(best, 6), runner_up=round(runner_up, 6))


    def verify_near(self, query, expected, minimum=.92):
        """Verify the entire window near an independently established timeline.

        This is not a global identity search. Fractional frame interpolation handles
        small decoder/encoder timing differences; it cannot repair speed changes.
        """
        centre = expected / STEP; ranked = []
        for delta in range(-8, 9):
            location = centre + delta / 4
            start = math.floor(location); fraction = location - start
            if start < 0 or start + len(query) + 1 > len(self.values): continue
            segment = [self.values[start+i]*(1-fraction) + self.values[start+i+1]*fraction for i in range(len(query))]
            ranked.append((correlation(query, segment), location * STEP))
        if not ranked: return dict(status='unmatched')
        score, position = max(ranked)
        return dict(status='matched' if score >= minimum else 'unmatched', reference_start=round(position, 3), correlation=round(score, 6), verification='anchored_local')


def align(index, values, window=30):
    n = round(window / STEP)
    if n < BITS + 1: raise ValueError('window must be at least 6.5 seconds')
    out = []
    for start in range(0, len(values), n):
        end = min(start + n, len(values))
        # Merge the short last fragment into the preceding window for useful evidence.
        if end - start < BITS + 1 and out:
            start = max(0, end - n)
        q = values[start:end]; m = index.locate(q)
        out.append(dict(query_start=round(start * STEP, 3), query_end=round(end * STEP, 3), **m))
    anchors = [m for m in out if m['status'] == 'matched']
    offsets = sorted(m['reference_start'] - m['query_start'] for m in anchors)
    # Require multiple globally located windows spread over time, all agreeing.
    # An edit that shifts the timeline disables this shortcut instead of being hidden.
    if len(anchors) >= 3 and anchors[-1]['query_start'] - anchors[0]['query_start'] >= 2 * window and offsets[-1] - offsets[0] <= .3:
        offset = offsets[len(offsets)//2]
        for m in out:
            if m['status'] not in ('unmatched', 'uninformative'): continue
            start = round(m['query_start'] / STEP); end = round(m['query_end'] / STEP)
            verified = index.verify_near(values[start:end], m['query_start'] + offset)
            if verified['status'] == 'matched':
                m['initial_global_result'] = {k: v for k, v in m.items() if k not in ('query_start', 'query_end')}
                m.update(verified)
    return out


def coverage(intervals, duration, tolerance=.25):
    spans = []; clipped = sorted((max(0, a), min(duration, b)) for a, b in intervals if b > a and b > 0 and a < duration)
    for a, b in clipped:
        if spans and a <= spans[-1][1]: spans[-1][1] = max(spans[-1][1], b)
        else: spans.append([a, b])
    gaps = []; at = 0
    for a, b in spans:
        if a - at > tolerance: gaps.append([round(at, 3), round(a, 3)])
        at = max(at, b)
    if duration - at > tolerance: gaps.append([round(at, 3), round(duration, 3)])
    return dict(covered_seconds=round(sum(b - a for a, b in spans), 3), coverage_fraction=round(sum(b - a for a, b in spans) / duration, 6) if duration else 0, gaps=gaps)


def extract(path, target):
    """Full decode at 10 frames/s, preserving the resulting feature stream and stderr."""
    path = Path(path); before = path.stat(); digest = hashlib.sha256()
    with path.open('rb') as f:
        for block in iter(lambda: f.read(1024 * 1024), b''): digest.update(block)
    facts = probe.probe_file(str(path)); values = array('f')
    filters = 'aformat=channel_layouts=mono,aresample=8000,asetnsamples=n=800:p=0,astats=metadata=1:reset=1,ametadata=print:key=lavfi.astats.Overall.RMS_level:file=-'
    cmd = ['ffmpeg', '-nostdin', '-hide_banner', '-v', 'error', '-i', str(path), '-map', '0:a:0', '-af', filters, '-f', 'null', '-']
    with tempfile.TemporaryFile() as errors:
        process = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=errors)
        timer = threading.Timer(900, process.kill); timer.daemon = True; timer.start()
        try:
            for line in process.stdout:
                if line.startswith(b'lavfi.astats.Overall.RMS_level='):
                    value = float(line.split(b'=')[1]); values.append(max(-80.0, value) if math.isfinite(value) else -80.0)
            code = process.wait(timeout=120)
        finally:
            timer.cancel()
            process.stdout.close()
            if process.poll() is None: process.kill(); process.wait()
        errors.seek(0); stderr = errors.read().decode('utf8', errors='replace')
    after = path.stat()
    if (before.st_size, before.st_mtime_ns) != (after.st_size, after.st_mtime_ns): raise ValueError(f'input changed: {path.name}')
    if code or not values: raise ValueError(f'cannot decode {path.name}: {stderr[:300]}')
    with Path(target).open('xb') as f: values.tofile(f)
    return values, dict(name=path.name, sha256=digest.hexdigest(), size=before.st_size, mtime_ns=before.st_mtime_ns, facts={k: v for k, v in facts.items() if k != 'path'}, frames=len(values), frame_seconds=STEP, decode_messages=stderr)


def quality_sample(path, start, seconds=30):
    """Original-rate peak/RMS diagnostics on a matched excerpt; not a noise/quality score."""
    cmd = ['ffmpeg', '-nostdin', '-hide_banner', '-v', 'info', '-ss', str(start), '-i', str(path), '-t', str(seconds), '-map', '0:a:0', '-af', 'astats=metadata=0:reset=0', '-f', 'null', '-']
    r = subprocess.run(cmd, capture_output=True, text=True, timeout=120)
    if r.returncode: return dict(error=r.stderr[-300:])
    tail = r.stderr.rsplit('Overall', 1)[-1]; out = {}
    for label, key in [('Peak level dB', 'sample_peak_dbfs'), ('RMS level dB', 'rms_dbfs'), ('DC offset', 'dc_offset'), ('Peak count', 'peak_count')]:
        import re
        m = re.search(label + r':\s*(-?[\d.]+|[-+]?inf)', tail)
        if m:
            v = float(m[1]); out[key] = v if math.isfinite(v) else None
    return out


def compare(reference, parts, output, window=30):
    if not parts: raise ValueError('at least one candidate file is required')
    if not math.isfinite(window) or window < (BITS + 1) * STEP: raise ValueError('window must be at least 6.5 seconds')
    paths = [Path(reference).resolve()] + [Path(p).resolve() for p in parts]
    if len(set(paths)) != len(paths): raise ValueError('reference and candidates must be distinct files')
    output = Path(output); output.mkdir(parents=True, exist_ok=False)
    print('Decoding reference', file=sys.stderr, flush=True)
    ref, ref_info = extract(paths[0], output / 'reference.energy.f32')
    print('Indexing reference', file=sys.stderr, flush=True); index = Index(ref); rows = []; intervals = []
    for i, p in enumerate(paths[1:]):
        print(f'Comparing {p.name}', file=sys.stderr, flush=True)
        values, info = extract(p, output / f'candidate-{i:03d}.energy.f32'); windows = align(index, values, window)
        matched = [w for w in windows if w['status'] == 'matched']
        offsets = sorted(w['reference_start'] - w['query_start'] for w in matched)
        for w in matched: intervals.append((w['reference_start'], w['reference_start'] + w['query_end'] - w['query_start']))
        samples = []
        for w in (matched[0:1] + matched[len(matched)//2:len(matched)//2+1] + matched[-1:]):
            samples.append(dict(query_start=w['query_start'], reference_start=w['reference_start'], candidate=quality_sample(p, w['query_start']), reference=quality_sample(paths[0], w['reference_start'])))
        row = dict(**info, windows=windows, matched_windows=len(matched), total_windows=len(windows), reference_offset=round(offsets[len(offsets)//2], 3) if offsets else None, offset_spread=round(offsets[-1]-offsets[0], 3) if offsets else None, quality_samples=samples, query_coverage=coverage([(w['query_start'], w['query_end']) for w in matched], info['facts']['duration']))
        rows.append(row)
        with (output / f'candidate-{i:03d}.json').open('x') as f: json.dump(row, f, indent=2, allow_nan=False)
    result = dict(version=VERSION, created=datetime.now(timezone.utc).isoformat(), reference=ref_info, candidates=rows, reference_coverage=coverage(intervals, ref_info['facts']['duration']),
                  limitations=['Experimental energy fingerprints: no calibrated probability or different-performer conclusion.', 'Coverage describes correlated windows, not sample-exact equality or proof of completeness.', 'Quality diagnostics are sampled; no perceptual-quality or noise ranking is claimed.', 'No speed-change correction; silence, edits and repeated passages may remain unresolved.'], recommended_source=None, automatic_processing_approved=False)
    with (output / 'report.json').open('x') as f: json.dump(result, f, indent=2, allow_nan=False)
    return result


def main():
    import argparse
    p = argparse.ArgumentParser(description=__doc__); p.add_argument('reference'); p.add_argument('parts', nargs='+'); p.add_argument('--out', required=True); p.add_argument('--window', type=float, default=30)
    a = p.parse_args()
    try: r = compare(a.reference, a.parts, a.out, a.window)
    except (ValueError, OSError, probe.ToolError) as e: p.exit(1, str(e) + '\n')
    print(json.dumps(r, indent=2, allow_nan=False))


if __name__ == '__main__': main()
