"""Separate-environment Diamond worker. Its input/output files are disposable PCM WAVs."""

import json
import os
import re
import subprocess
import sys
from pathlib import Path

import numpy as np
import soundfile as sf
import torch
from scipy.ndimage import gaussian_filter1d
from scipy.signal import resample_poly

sys.path.insert(0, os.environ["STRATA_DIAMOND_CODE"])
from diamond.infer import Diamond


def cuts(path, duration):
    result = subprocess.run(
        [
            "ffmpeg",
            "-nostdin",
            "-hide_banner",
            "-i",
            str(path),
            "-af",
            "silencedetect=noise=-35dB:d=0.12",
            "-f",
            "null",
            "-",
        ],
        capture_output=True,
        text=True,
        check=True,
    )
    starts = [float(x) for x in re.findall(r"silence_start:\s*([\d.]+)", result.stderr)]
    ends = [float(x) for x in re.findall(r"silence_end:\s*([\d.]+)", result.stderr)]
    pauses = [(a, b) for a, b in zip(starts, ends) if b - a >= 0.2]
    bounds = [0.0]
    while duration - bounds[-1] > 6:
        start = bounds[-1]
        allowed = [(a, b) for a, b in pauses if start + 1.5 <= (a + b) / 2 <= min(start + 6, duration - 1)]
        if not allowed:
            raise ValueError(f"No suitable pause after {start:.2f}s; cannot restore safely")
        a, b = min(allowed, key=lambda x: abs(sum(x) / 2 - (start + 3)))
        bounds.append((a + b) / 2)
    return bounds + [duration]


def level(audio, rate):
    frame = round(rate * 0.25)
    powers = np.array([np.mean(audio[i : i + frame] ** 2) for i in range(0, len(audio), frame)])
    active = powers > 10 ** (-38 / 10)
    numerator = gaussian_filter1d(powers * active, sigma=5, mode="nearest")
    denominator = gaussian_filter1d(active.astype(float), sigma=5, mode="nearest")
    local = np.sqrt(numerator / np.maximum(denominator, 1e-9))
    db = np.clip(20 * np.log10(10 ** (-22 / 20) / np.maximum(local, 1e-8)), -3, 3)
    centers = np.minimum(np.arange(len(powers)) * frame + frame / 2, len(audio) - 1)
    # Interpolate in bounded blocks instead of allocating several book-length float64 arrays.
    for start in range(0, len(audio), rate * 30):
        end = min(len(audio), start + rate * 30)
        gain = np.interp(np.arange(start, end), centers, db)
        audio[start:end] *= (10 ** (gain / 20)).astype("float32")
    return audio


def restore(path, out, model, bounds):
    audio, sr = sf.read(path, dtype="float32")
    if audio.ndim != 1 or not len(audio) or not np.isfinite(audio).all():
        raise ValueError("Invalid decoded chapter")
    duration = len(audio) / sr
    rate = 44100
    original = resample_poly(audio, rate, sr)
    output = np.empty(len(original), dtype="float32")
    for start, end in zip(bounds, bounds[1:]):
        left = max(0, start - 1)
        right = min(duration, end + 0.35)
        excerpt = np.concatenate((audio[round(left * sr) : round(right * sr)], np.zeros(sr, dtype="float32")))
        restored, actual = model.restore(excerpt, sr, chunk_sec=0, normalize=False, trim_leadin=False)
        a = round(start * rate)
        b = len(output) if end == duration else min(len(output), round(end * rate))
        offset = round((start - left) * rate)
        segment = restored[offset : offset + b - a]
        if actual != rate or len(segment) != b - a or not np.isfinite(segment).all():
            raise ValueError("Invalid restored segment")
        output[a:b] = segment
    for cut in bounds[1:-1]:
        center = round(cut * rate)
        half = round(0.1 * rate)
        a = max(0, center - half)
        b = min(len(output), center + half)
        weights = np.abs(np.linspace(-1, 1, b - a, dtype="float32"))
        output[a:b] = output[a:b] * weights + original[a:b] * (1 - weights)
    sf.write(out, level(output, rate), rate, subtype="FLOAT")
    return len(bounds) - 2


def main():
    manifest = json.loads(Path(sys.argv[1]).read_text())
    torch.manual_seed(0)
    np.random.seed(0)
    torch.set_num_threads(int(os.environ.get("STRATA_DIAMOND_THREADS", "4")))
    boundaries = []
    for chapter in manifest:
        info = sf.info(chapter["input"])
        boundaries.append(cuts(chapter["input"], info.frames / info.samplerate))
    model = Diamond.from_pretrained(
        os.environ["STRATA_DIAMOND_MODEL"], device=os.environ.get("STRATA_DIAMOND_DEVICE", "cpu")
    )
    for chapter, bounds in zip(manifest, boundaries):
        count = restore(chapter["input"], chapter["output"], model, bounds)
        print(f"{chapter['output']}: {count} pause cuts", flush=True)


if __name__ == "__main__":
    main()
