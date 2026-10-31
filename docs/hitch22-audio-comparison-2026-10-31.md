# Hitch-22: recording alignment and source-quality pilot

## Finding

**Use the 15 MP3 parts as the preferred input candidate.** The comparison gives strong evidence that they and the complete M4B contain the same narrated performance. It does not establish bit-identical audio, perfect preservation of every sample, or semantic chapter boundaries. No originals were changed and no processing was approved.

All **2,108 evaluated windows** matched after conservative global-anchor and local verification. Correlated windows cover **99.9849%** of the M4B timeline. The typical correlation is about 0.985, not a probability of correctness.

## Why the MP3 representation wins this pilot

| Measurement | Complete M4B | MP3 set |
|---|---|---|
| Codec / bitrate | AAC, about 16 kbps | MP3, about 80 kbps |
| Sample rate / channels | 12 kHz, mono | 22.05 kHz, mono |
| Whole decoded duration | 17:30:01.9 | 17:29:57.95 (concatenated decoded output) |
| Integrated loudness | -20.3 LUFS | -23.5 LUFS |
| Loudness range | 8.7 LU | 8.3 LU |
| True peak | +0.8 dBTP | -1.1 dBTP |
| Full decode | Success; no reported decoder errors | Success; no reported decoder errors |
| Embedded chapter markers | None | None |

Three matched 30-second excerpts near the beginning, middle and end were compared after resampling both to a common analysis rate. Four cascaded second-order high-pass filters were used for each band measurement. Relative to full-band RMS, the M4B had about 14–15 dB less energy in the 4 kHz high-pass measurement and 36–39 dB less in the 6 kHz measurement. Together with its aggressive encoding and over-full-scale peaks, this supports preferring the MP3s. Higher-frequency energy alone does not prove better perceptual quality; it can include noise or artifacts.

The M4B peaks exceeding full scale imply clipping risk on integer output without attenuation. They do not prove the original performance was irreversibly clipped. The MP3s are quieter: the existing processing plan must evaluate safe gain/limiting rather than blindly adding 5.5 dB.

## Mapping the parts

| Part | Reference start | Source duration | Matched windows | Offset variation within part |
|---|---:|---:|---:|---:|
| 01 | 00:00:00.2 | 01:10:07.8 | 141/141 | 0.025 s |
| 02 | 01:10:08.2 | 01:12:45.3 | 146/146 | 0.025 s |
| 03 | 02:22:53.7 | 01:11:14.3 | 143/143 | 0.000 s |
| 04 | 03:34:08.2 | 01:11:05.0 | 143/143 | 0.025 s |
| 05 | 04:45:13.5 | 01:12:44.8 | 146/146 | 0.025 s |
| 06 | 05:57:58.6 | 01:11:09.7 | 143/143 | 0.025 s |
| 07 | 07:09:08.6 | 01:07:05.6 | 135/135 | 0.050 s |
| 08 | 08:16:14.4 | 01:13:25.0 | 147/147 | 0.025 s |
| 09 | 09:29:39.7 | 01:12:36.1 | 146/146 | 0.025 s |
| 10 | 10:42:16.0 | 01:09:28.7 | 139/139 | 0.050 s |
| 11 | 11:51:45.0 | 01:13:21.6 | 147/147 | 0.000 s |
| 12 | 13:05:06.9 | 01:11:24.0 | 143/143 | 0.000 s |
| 13 | 14:16:31.2 | 01:12:13.0 | 145/145 | 0.100 s |
| 14 | 15:28:44.5 | 00:59:33.7 | 120/120 | 0.100 s |
| 15 | 16:28:18.4 | 01:01:43.4 | 124/124 | 0.025 s |

The reference starts about 0.2 seconds before the first MP3. Inter-part mapped gaps are roughly 0.15–0.30 seconds and mostly sit inside the long pauses visible in the full silence scan. The concatenated MP3 decode is about 3.9 seconds shorter across 17.5 hours. This is consistent with priming/padding or silence differences, but the method cannot certify each small boundary. No substantial missing passage was detected.

The coverage metric leaves small holes from feature quantization, independently refined window offsets and file boundaries. Four gaps above the 0.25-second reporting tolerance are approximately 0.275–0.30 seconds; they occur at quiet boundaries. These are unresolved below this method's resolution, not evidence that speech is missing.

These are **part boundaries**, not recovered literary chapters. Neither input includes embedded chapter markers. A chapter-level claim needs a trusted chapter list or a separate chapter-boundary analysis.

## Method and validation

The prototype decodes locally to a mono 8 kHz analysis stream, computes log-RMS every 100 ms, indexes 64-bit energy-change sketches and verifies candidates by full-window normalized correlation. The sketch is only a retrieval aid, not a recording ID.

Initial global search verified about 90.69% of the timeline. Many retrieval misses came from encoding/phase differences. A second pass only verifies near a timeline when at least three globally located windows spread across the file agree within 0.3 seconds; ambiguous global hits are not forced. Every recovered window still must pass the correlation threshold. This raised verified window coverage to 99.9849% without lowering the threshold. Initial, refined and final reports are all preserved.

Synthetic tests cover gain/encoding perturbations, reordered parts, deletion/edit offsets, unrelated audio, silence and repeated passages. A real-FFmpeg test compares an AAC reference with an MP3 excerpt and rejects an unrelated generated recording. A failure to match remains unresolved and never proves a different performer. This is not yet a benchmark across many narrators or a general-purpose fingerprinting system.

Chromaprint/fpcalc and scientific Python libraries were unavailable on this machine; no packages were installed. The prototype uses Python standard-library code and FFmpeg. Chromaprint or landmark fingerprints remain candidates for improving retrieval and speed-change handling.

## Reuse

```bash
PYTHONPATH=src .venv/bin/python -m app.audio_compare REFERENCE PART1 PART2 --out data/new-comparison
```

The output directory must be new. Source hashes, probe facts, feature streams, per-window matches, coverage and sampled quality diagnostics are saved. The generic command does not perform the whole-book loudness scan or spectral experiment; those were additional pilot measurements. It never selects, changes, deletes, converts or approves originals automatically. Long extraction processes have a 15-minute kill timer.

## Artifacts

- Reusable module: `src/app/audio_compare.py`.
- Unit/adversarial tests: `tests/unit/test_audio_compare.py`.
- Actual FFmpeg integration test: `tests/integration/test_audio_compare.py`.
- Initial and refined maps, feature streams and final `report-verified.json`: `data/hitch22-comparison-20261031T102134/`.
- Investigator selection, per-input hashes and quality evidence: `data/hitch22-comparison-20261031T102134/source-selection-review.json`.
- Full loudness/true-peak/silence/astats logs: `data/hitch22-quality-20261031T102135/`.
- Matched spectral samples and raw logs: `data/hitch22-spectral-steep-20261031/`.

All 16 source files were rehashed after analysis; SHA-256, size and modification time still match their initial records. No audio or fingerprints were sent to an external service.
