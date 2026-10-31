"""ffprobe / ffmpeg measurements for the processor: file facts, whole-book loudness and pauses, and per-range spectrum.

A *segment* is (file, start, end) in seconds within that file (end None = to the end); a chapter or a whole book is a list of segments. They are fed to ffmpeg directly (one segment) or through the concat demuxer with in/out points (several)."""
import json, os, re, subprocess, tempfile

FFMPEG = ['ffmpeg', '-hide_banner', '-nostats', '-v', 'info']
SILENCE_DB = -38


class ToolError(RuntimeError):
    """Raised when ffmpeg/ffprobe is missing or fails on a file."""


def run(cmd: list[str], timeout: int = 3600, cwd: str | None = None) -> subprocess.CompletedProcess:
    try: return subprocess.run(cmd, capture_output=True, text=True, timeout=timeout, cwd=cwd)
    except (OSError, subprocess.TimeoutExpired) as e: raise ToolError(f'{cmd[0]} failed: {e}')


def natural_key(s: str):
    return [int(t) if t.isdigit() else t.lower() for t in re.split(r'(\d+)', s)]


def probe_file(path: str) -> dict:
    """Facts about one audio file: duration, codec, channels, sample_rate, bit_rate (kbps), embedded chapters, tags."""
    r = run(['ffprobe', '-v', 'error', '-print_format', 'json', '-show_format', '-show_streams', '-show_chapters', path], 120)
    if r.returncode or not r.stdout.strip(): raise ToolError(f'cannot read {path}: {r.stderr.strip()[:200]}')
    d = json.loads(r.stdout); a = next((s for s in d.get('streams', []) if s.get('codec_type') == 'audio'), None)
    if not a: raise ToolError(f'no audio stream in {path}')
    fmt = d.get('format', {}); dur = float(fmt.get('duration') or a.get('duration') or 0)
    br = int(a.get('bit_rate') or fmt.get('bit_rate') or 0) / 1000 or (os.path.getsize(path) * 8 / 1000 / dur if dur else 0)
    return dict(path=path, duration=dur, codec=a.get('codec_name', ''), channels=int(a.get('channels') or 1), sample_rate=int(a.get('sample_rate') or 0), bit_rate=round(br, 1),
                chapters=[dict(start=float(c['start_time']), end=float(c['end_time']), title=(c.get('tags') or {}).get('title', '')) for c in d.get('chapters') or []],
                tags={k.lower(): v for k, v in (fmt.get('tags') or {}).items()})


def _q(path: str) -> str:
    return path.replace("'", "'\\''")


def input_args(segments: list[tuple], tmpdir: str) -> list[str]:
    """ffmpeg input arguments that play the segments back to back."""
    if len(segments) == 1:
        f, s, e = segments[0]
        return (['-ss', f'{s:.3f}'] if s else []) + (['-t', f'{e - s:.3f}'] if e is not None else []) + ['-i', f]
    lst = os.path.join(tmpdir, 'concat.txt')
    with open(lst, 'w', encoding='utf8') as fh:
        for f, s, e in segments:
            fh.write(f"file '{_q(os.path.abspath(f))}'\n")
            if s: fh.write(f'inpoint {s:.3f}\n')
            if e is not None: fh.write(f'outpoint {e:.3f}\n')
    return ['-f', 'concat', '-safe', '0', '-i', lst]


def _summary(err: str) -> str:
    return err.split('Summary:')[-1] if 'Summary:' in err else err


def measure_book(segments: list[tuple], channels: int = 1) -> dict:
    """Loudness (integrated LUFS, true peak dBFS, loudness range) and pauses of a whole book in one decoding pass. For stereo also the loudness after a plain mono downmix, to catch channels that cancel each other."""
    with tempfile.TemporaryDirectory() as td:
        args = input_args(segments, td); pk = os.path.join(td, 'peaks.txt')
        r = run(FFMPEG + args + ['-af', f'ebur128=peak=true,silencedetect=noise={SILENCE_DB}dB:d=1.2,astats=metadata=1:reset=1:length=0.4,ametadata=print:key=lavfi.astats.Overall.Peak_level:file=peaks.txt', '-f', 'null', '-'], cwd=td)        # a relative name: a Windows drive letter's colon cannot be put inside a filter argument
        if r.returncode: raise ToolError('loudness analysis failed: ' + r.stderr.strip()[-200:])
        out = dict(zip(('I', 'LRA', 'TP'), parse_ebur(r.stderr)))
        out['silences'] = parse_silences(r.stderr)
        out['peak_p99'] = parse_peak_percentile(pk)
        if channels > 1:
            m = run(FFMPEG + args + ['-af', 'pan=mono|c0=0.5*c0+0.5*c1,ebur128', '-f', 'null', '-'])
            out['mono_I'] = parse_ebur(m.stderr)[0] if not m.returncode else None
        return out


def parse_ebur(err: str):
    s = _summary(err); g = lambda pat: (m := re.search(pat, s)) and float(m[1])
    return g(r'I:\s+(-?[\d.]+) LUFS'), g(r'LRA:\s+(-?[\d.]+) LU'), g(r'Peak:\s+(-?[\d.]+) dBFS')


def parse_peak_percentile(path: str, q: float = 0.99) -> float | None:
    """The q-th percentile of the peak level (dBFS) over short windows of the whole book: how high the peaks stand in all but the loudest 1%."""
    try:
        with open(path, encoding='utf8') as f: vals = sorted(float(x) for x in re.findall(r'Peak_level=(-?[\d.]+)', f.read()))
    except OSError: return None
    return vals[int(q * (len(vals) - 1))] if vals else None


def parse_silences(err: str) -> list[tuple[float, float]]:
    starts = [float(x) for x in re.findall(r'silence_start: (-?[\d.]+)', err)]; ends = [float(x) for x in re.findall(r'silence_end: (-?[\d.]+)', err)]
    return list(zip(starts, ends))                       # a silence still open at the end of the file has no end line and is dropped


def spectrum(segments: list[tuple]) -> dict:
    """High-frequency share and pauses of a stretch of audio: {hf_db: energy above 10 kHz relative to the total (dB; -inf-ish values clamp to -90), pause_frac: fraction of time below -38 dB for at least 0.25 s}."""
    with tempfile.TemporaryDirectory() as td:
        args = input_args(segments, td); dur = sum((e if e is not None else probe_file(f)['duration']) - s for f, s, e in segments)
        graph = f'[0:a]asplit=2[a][b];[a]silencedetect=noise={SILENCE_DB}dB:d=0.25,astats=metadata=0:reset=0[ao];[b]highpass=f=10000,astats=metadata=0:reset=0[bo]'
        r = run(FFMPEG + args + ['-filter_complex', graph, '-map', '[ao]', '-f', 'null', '-', '-map', '[bo]', '-f', 'null', '-'])
        if r.returncode: raise ToolError('spectrum analysis failed: ' + r.stderr.strip()[-200:])
        vals = [-90.0 if v == float('-inf') else v for _, v in sorted(parse_overall_rms(r.stderr).items(), key=lambda kv: int(kv[0]))]
        sil = sum(e - s for s, e in parse_silences(r.stderr))
        if len(vals) < 2: raise ToolError('could not read the spectrum measurements')
        return dict(hf_db=round(max(-90.0, vals[1] - vals[0]), 1), pause_frac=round(min(1.0, sil / dur), 3) if dur else 0.0)


def parse_overall_rms(err: str) -> dict[str, float]:
    """astats prints a per-channel block and an 'Overall' block per filter instance; return {instance: overall RMS level dB}."""
    out, overall = {}, set()
    for line in err.splitlines():
        m = re.match(r'\[Parsed_astats_(\d+) @ [^\]]*\] (.*)', line)
        if not m: continue
        inst, text = m[1], m[2].strip()
        if text == 'Overall': overall.add(inst)
        elif text.startswith('RMS level dB:') and inst in overall: out[inst] = float(text.split(':')[1])
    return out
