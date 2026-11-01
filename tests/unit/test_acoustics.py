from array import array
import math

from app.ingest import acoustics


def test_window_statistics_reflect_quiet_noise_and_tonal_balance():
    rate = acoustics.RATE
    def sample(noise, tone):
        return array('h', (round(32767 * (tone * math.sin(2*math.pi*250*t/rate) + noise * math.sin(2*math.pi*4500*t/rate))) for t in range(rate*3)))
    quiet = sample(.001, .001) + sample(.001, .1)
    noisy = sample(.03, .001) + sample(.03, .1)
    clean_result = acoustics.analyse_samples(quiet)
    noisy_result = acoustics.analyse_samples(noisy)
    assert noisy_result['quiet_high_dbfs'] > clean_result['quiet_high_dbfs'] + 15
    assert noisy_result['quiet_to_active_db'] > clean_result['quiet_to_active_db']
    assert clean_result['active_low_to_presence_db'] is not None
    assert acoustics.analyse_samples(array('h', [0]) * (rate*3)) is None
    assert acoustics.analyse_samples(array('h', [0]) * 10) is None


def test_continuous_sound_is_a_review_cue_and_requires_two_samples():
    def assessment(*values):
        return {'samples': [{'result': {'quiet_to_active_db': value}} for value in values]}
    assert acoustics.continuous_sound_cue(assessment(-3.3, -9.2, -4.7))['flagged']
    assert not acoustics.continuous_sound_cue(assessment(-21.3, -11.2, -6.9))['flagged']
    assert not acoustics.continuous_sound_cue(assessment(-3, None, float('nan')))['flagged']
    repeated = {'samples': [{'start_seconds': 0, 'result': {'quiet_to_active_db': -3}}] * 3}
    assert not acoustics.continuous_sound_cue(repeated)['flagged']
    assert acoustics.continuous_sound_cue(None)['valid_samples'] == 0
