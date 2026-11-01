from app.ingest.provenance import describe


def test_provenance_reports_selected_files_and_distinguishes_dates():
    inv = {'files': [
        {'path': '/audio/tape transfer/Disc 01.mp3', 'probe': {'codec': 'mp3', 'bit_rate': 64, 'sample_rate': 22050, 'tags': {'date': '1990', 'comment': 'Cassette transfer'}}},
        {'path': '/audio/other.flac', 'probe': {'codec': 'flac', 'bit_rate': 900, 'sample_rate': 44100, 'tags': {}}},
    ]}
    metadata = {'records': {'openlibrary': {'original_year': 1890}, 'audible': {'release_date': '2010-02-01'}, 'catalogue': {'release_year': 1990}}}
    result = describe(inv, {'paths': ['/audio/tape transfer/Disc 01.mp3']}, metadata)
    assert result['codecs'] == ['mp3'] and result['bitrates_kbps'] == [64]
    assert result['sample_rates_hz'] == [22050] and result['extensions'] == ['.mp3']
    assert result['embedded_dates'] == ['1990']
    assert result['recording_release_dates'] == [{'source': 'audible', 'release_date': '2010-02-01'}]
    assert {'source': 'tag: comment', 'value': 'Cassette transfer'} in result['medium_clues']
    assert {'source': 'file name', 'value': 'Disc 01.mp3'} in result['medium_clues']


def test_unknown_provenance_stays_unknown():
    result = describe({'files': [{'path': '/audio/chapter.m4b', 'probe': {'tags': {}}}]}, {'paths': ['/audio/chapter.m4b']}, {})
    assert not result['medium_clues'] and not result['recording_release_dates'] and not result['bitrates_kbps']
