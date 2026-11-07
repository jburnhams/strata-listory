import json, sqlite3, zlib

from app.identify import audibleindex as ai


def archive(path):
    c = sqlite3.connect(path)
    c.execute('CREATE TABLE product (asin TEXT, region TEXT, raw BLOB)')
    books = [
        dict(asin='B000000001', title='The Gift of the Emperor', authors=[dict(name='E. W. Hornung', asin='A1')], narrators=[dict(name='Gordon Griffin')], runtime_length_min=64, format_type='unabridged', language='english', isbn='9781407986197'),
        dict(asin='B000000002', title='Dune: Special Edition', authors=[dict(name='Frank Herbert')], narrators=[dict(name='Scott Brick')], runtime_length_min=1260, format_type='abridged'),
    ]
    for b in books: c.execute('INSERT INTO product VALUES (?,?,?)', (b['asin'], 'us', zlib.compress(json.dumps(b).encode()) if b['asin'].endswith('1') else json.dumps(b)))      # both stored forms
    c.commit(); c.close()


def built(tmp_path):
    archive(tmp_path / 'a.sqlite'); info = ai.build(tmp_path / 'a.sqlite', tmp_path / 'i.sqlite'); assert info['books'] == 2
    return ai.connect(tmp_path / 'i.sqlite')


def test_asin_lookup_and_missing(tmp_path):
    c = built(tmp_path)
    assert ai.find(c, asin='b000000001')[0]['grade'] == 'asin'
    assert ai.find(c, asin='B999999999') == [] and ai.find(None, title='x', author='y') == []


def test_title_alone_never_matches(tmp_path):
    c = built(tmp_path)
    assert ai.find(c, title='Dune') == [] and ai.find(c, title='Dune', author='Somebody Else') == []


def test_author_and_title_is_weak_until_recording_agrees(tmp_path):
    c = built(tmp_path)
    assert [r['grade'] for r in ai.find(c, title='Gift of the Emperor', author='Hornung, E. W.')] == ['weak']      # article dropped, name reordered
    strong = ai.find(c, title='The Gift of the Emperor', author='E. W. Hornung', narrators=['Gordon Griffin'], runtime_min=64.5)[0]
    assert strong['grade'] == 'strong' and strong['isbn'] == '9781407986197' and strong['snapshot']
    conflict = ai.find(c, title='The Gift of the Emperor', author='E. W. Hornung', runtime_min=200)[0]
    assert conflict['grade'] == 'weak' and conflict['evidence']['runtime'] == 'conflict'
    assert ai.find(c, title='The Gift of the Emperor', author='E. W. Hornung', narrators=['Someone Else'])[0]['evidence']['narrators'] == 'conflict'


def test_main_title_match_is_not_strong(tmp_path):
    c = built(tmp_path)
    r = ai.find(c, title='Dune', author='Frank Herbert', narrators=['Scott Brick'])[0]
    assert r['evidence']['title'] == 'main' and r['grade'] == 'weak' and r['abridged'] is True
