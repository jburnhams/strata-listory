import math
import shutil
import struct
import wave

import pytest

from app.ingest import acoustics
from app.process.plan import timeline


def test_real_audio_acoustic_samples_and_low_rate_limit(tmp_path):
    if not shutil.which('ffmpeg'): pytest.skip('ffmpeg is required')
    path = tmp_path / 'sample.wav'; rate = 16000
    with wave.open(str(path), 'wb') as out:
        out.setparams((1, 2, rate, rate * 15, 'NONE', 'not compressed'))
        out.writeframes(b''.join(struct.pack('<h', round(32767 * (.005 if (i//rate)%2 else .15) * math.sin(2 * math.pi * 440 * i / rate))) for i in range(rate*15)))
    tl = timeline([{'path': str(path), 'duration': 15}])
    measured = acoustics.assess(tl, 15, rate)
    assert all(s['result'] is not None for s in measured['samples'])
    assert measured['medians']['quiet_to_active_db'] < -15
    assert acoustics.assess(tl, 15, 8000)['medians']['quiet_high_dbfs'] is None
