import math
import shutil
import struct
import wave

import pytest

from app.ingest.audio import quiet_passages
from app.process.plan import timeline


def test_quiet_passages_measure_real_audio_and_digital_silence(tmp_path):
    if not shutil.which("ffmpeg"):
        pytest.skip("ffmpeg is required")
    path = tmp_path / "quiet.wav"
    rate = 8000
    with wave.open(str(path), "wb") as out:
        out.setparams((1, 2, rate, rate * 8, "NONE", "not compressed"))
        out.writeframes(
            b"".join(
                struct.pack("<h", round(32767 * 0.01 * math.sin(2 * math.pi * 440 * i / rate))) for i in range(rate * 4)
            )
            + b"\0\0" * (rate * 4)
        )
    result = quiet_passages(timeline([{"path": str(path), "duration": 8}]), [(0, 3), (5, 7)], 8)
    assert result["samples"][0]["rms_dbfs"] == pytest.approx(-43, abs=1)
    assert result["samples"][1]["status"] == "digital_silence"
    assert result["median_dbfs"] == pytest.approx(-43, abs=1)
