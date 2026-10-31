# Local audio analysis tools

Check the environment with `./app doctor --audio`. Optional Python analysis dependencies are declared in `requirements-audio.txt`; install them into the project virtual environment with `.venv/bin/python -m pip install -r requirements-audio.txt`.

On 2026-10-31 this machine was verified with FFmpeg/ffprobe 9.0.2, the existing user-local mp3gain, Chromaprint/fpcalc 1.6.2, NumPy 2.5.4 and SciPy 1.18.1. NumPy and SciPy are installed in the project virtual environment.

System package installation required a sudo password, so Chromaprint was installed under `~/.local/lib/strata-listory/chromaprint/1.6.2-1/`, with a launcher at `~/.local/bin/fpcalc`. The Arch package `chromaprint-1.6.2-1-x86_64.pkg.tar.zst` came from the configured Omarchy mirror; its detached signature verified against the installed Arch keyring. The extracted installation contains `PACKAGE-SHA256`. No system files were replaced.

Installation verification is retained locally in `data/chromaprint-install-verification-20261031/`, including a repeatable script, fingerprint samples and `report.json`. The 120-second Hitch-22 fingerprint comparison scored 0.9015, versus 0.5328 for a different book. Waveform alignment of three ten-second excerpts from parts 1, 8 and 15 produced normalized correlations of 0.9793, 0.9671 and 0.9751; an unrelated excerpt scored 0.0520. All comparisons ran locally.

These are installation smoke tests, not calibrated acceptance thresholds or probabilities. The waveform search grid spacing does not establish alignment accuracy. The ingest pipeline continues to use its existing energy-based alignment; Chromaprint and SciPy are available for developing and validating a stronger comparison stage before changing that behavior.
