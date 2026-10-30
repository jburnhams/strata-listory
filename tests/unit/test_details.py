import json, os, shutil, subprocess
import pytest
from app import catalogue, details

PNG = b'\x89PNG\r\n\x1a\n' + b'0' * 20
JPG = b'\xff\xd8\xff\xe0' + b'0' * 20
SIDE = dict(asin='B004EVP6BY', title='Brave New World', authors=[dict(name='Aldous Huxley')], narrators=[dict(name='Michael York')], series=[dict(title='S', sequence='2')], publisher_name='Blackstone', release_date='2008-01-16', language='english',
            format_type='unabridged', runtime_length_min=480, publisher_summary='<p>Great <b>book</b> &amp; more</p>', rating=dict(overall_distribution=dict(display_average_rating='4.2', num_ratings=5556)),
            category_ladders=[dict(ladder=[dict(name='Fiction'), dict(name='Classics')]), dict(ladder=[dict(name='Fiction')])], product_images={'500': 'https://img.example/c.jpg'}, sample_url='https://s/x.mp3',
            ChapterInfo=dict(chapters=[dict(title='Opening', start_offset_sec=0, length_ms=10924), dict(title='Chapter 1', start_offset_sec=11, length_ms=1754094)]))


def book(tmp_path, name='Book', files=('01.mp3', '02.mp3'), sidecar=None, extra=()):
    d = tmp_path / name; d.mkdir()
    for f in files: (d / f).write_bytes(b'audio' * 10)
    for f, data in extra: (d / f).write_bytes(data)
    if sidecar: (d / f'{name}.metadata.json').write_text(json.dumps(sidecar))
    return catalogue.add_book(title=name, path=str(d))


def test_sidecar_summary_is_read_and_cleaned(tmp_path):
    b = book(tmp_path, sidecar=SIDE)
    a = details.audible_summary(details.sidecar(b['path']))
    assert (a['title'], a['authors'], a['narrators'], a['series'], a['runtime_minutes']) == ('Brave New World', ['Aldous Huxley'], ['Michael York'], [dict(title='S', sequence='2')], 480)
    assert a['summary'] == 'Great book & more' and a['categories'] == ['Classics', 'Fiction'] and (a['rating'], a['rating_count']) == ('4.2', 5556)
    assert a['chapters'] == [dict(title='Opening', start_sec=0, length_sec=11), dict(title='Chapter 1', start_sec=11, length_sec=1754)] and a['cover_url'] == 'https://img.example/c.jpg'
    assert details.audible_summary({})['title'] == '' and details.audible_summary(dict(product_images={}))['cover_url'] == ''


def test_sidecar_beside_a_single_file_and_bad_files(tmp_path):
    f = tmp_path / 'One.m4b'; f.write_bytes(b'x'); (tmp_path / 'One.metadata.json').write_text(json.dumps(SIDE))
    assert details.sidecar(str(f))['asin'] == 'B004EVP6BY'
    d = tmp_path / 'D'; d.mkdir(); (d / 'x.metadata.json').write_text('{bad'); (d / 'y.metadata.json').write_text('[1]')
    assert details.sidecar(str(d)) is None and details.sidecar(str(tmp_path / 'none.m4b')) is None


def test_describe_lists_files_and_handles_a_missing_path(tmp_path, monkeypatch):
    monkeypatch.setattr(details, 'probe', lambda files: dict(available=True, files_probed=2, complete=True, duration_seconds=100))
    b = book(tmp_path, sidecar=SIDE); d = details.describe(b)
    assert d['files']['count'] == 2 and d['files']['total_bytes'] == 100 and [f['name'] for f in d['files']['list']] == ['01.mp3', '02.mp3'] and d['audible']['asin'] == 'B004EVP6BY'
    assert d['probe']['duration_seconds'] == 100 and details.cached_probe(b['path'], details.audio_files(b['path']))['duration_seconds'] == 100
    monkeypatch.setattr(details, 'probe', lambda files: 1 / 0)
    assert details.cached_probe(b['path'], details.audio_files(b['path']))['duration_seconds'] == 100          # served from the cache, not probed again
    assert details.describe(catalogue.add_book(title='Hand entered')) == dict(path_exists=False, files=None, probe=None, audible=None)
    one = tmp_path / 'one.mp3'; one.write_bytes(b'x'); assert details.audio_files(str(one)) == [str(one)]


def test_cover_prefers_folder_images_and_remembers_the_result(tmp_path):
    b = book(tmp_path, extra=[('other.jpg', JPG), ('cover.png', PNG), ('Icon.ico', b'ico')])
    p = details.cover(b)
    assert p.endswith(f"{b['id']}.png") and open(p, 'rb').read() == PNG and details.cover(b) == p
    b2 = book(tmp_path, 'Other', extra=[('z.jpg', JPG), ('Icon.jpg', PNG)])
    assert details.cover(b2).endswith('.jpg')                                                                 # any picture, but not Icon
    b3 = book(tmp_path, 'Text', extra=[('cover.jpg', b'not an image')])
    assert details.cover(b3) is None                                                                          # looks like a picture by name only
    b4 = catalogue.add_book(title='Nowhere', path=str(tmp_path / 'missing'))
    assert details.cover(b4) is None and details.cover(b4) is None and os.path.exists(os.path.join(os.path.dirname(p), f"{b4['id']}.none"))


def test_cover_for_a_loose_file_needs_a_picture_of_the_same_name(tmp_path):
    f = tmp_path / 'Loose.mp3'; f.write_bytes(b'x'); (tmp_path / 'Loose.jpg').write_bytes(JPG); (tmp_path / 'Neighbour.jpg').write_bytes(PNG)
    assert open(details.cover(catalogue.add_book(title='Loose', path=str(f))), 'rb').read() == JPG
    g = tmp_path / 'Other.mp3'; g.write_bytes(b'x')
    assert details.cover(catalogue.add_book(title='Other', path=str(g))) is None


def test_cover_falls_back_to_embedded_art_then_the_audible_url(tmp_path, monkeypatch):
    b = book(tmp_path, 'Emb'); monkeypatch.setattr(details, '_embedded', lambda files: JPG)
    assert open(details.cover(b), 'rb').read() == JPG
    monkeypatch.setattr(details, '_embedded', lambda files: None); urls = []
    monkeypatch.setattr(details, '_download', lambda u: urls.append(u) or PNG)
    c = book(tmp_path, 'Web', sidecar=SIDE)
    assert details.cover(c).endswith('.png') and urls == ['https://img.example/c.jpg']
    d = book(tmp_path, 'NoWeb'); assert details.cover(d) is None and urls == ['https://img.example/c.jpg']


def test_download_rules(monkeypatch, fake_urlopen):
    assert details._download('http://insecure/x.jpg') is None
    fake_urlopen.reply(PNG, b'<html>', OSError('down'))
    assert details._download('https://x/y.png') == PNG and details._download('https://x/y.png') is None and details._download('https://x/y.png') is None


@pytest.mark.skipif(not (shutil.which('ffmpeg') and shutil.which('ffprobe')), reason='needs ffmpeg')
def test_probe_and_embedded_cover_with_real_ffmpeg(tmp_path):
    img = tmp_path / 'c.jpg'; mp3 = tmp_path / 'a.mp3'
    subprocess.run(['ffmpeg', '-v', 'error', '-f', 'lavfi', '-i', 'color=c=red:s=32x32', '-frames:v', '1', str(img)], check=True)
    subprocess.run(['ffmpeg', '-v', 'error', '-f', 'lavfi', '-i', 'sine=d=2', '-i', str(img), '-map', '0', '-map', '1', '-c:a', 'libmp3lame', '-id3v2_version', '3', '-metadata', 'title=T', '-metadata', 'artist=A', str(mp3)], check=True)
    p = details.probe([str(mp3), str(tmp_path / 'missing.mp3')])
    assert p['files_probed'] == 1 and p['complete'] is False and p['duration_seconds'] == 2 and p['tags']['title'] == 'T' and p['has_cover_art'] and p['codec'] == 'mp3'
    assert details._image_kind(details._embedded([str(mp3)])) in ('.jpg', '.png') and details._embedded([str(tmp_path / 'missing.mp3')]) is None
    assert details.probe([str(tmp_path / 'missing.mp3')]) == dict(available=True, files_probed=0)


def test_probe_degrades_without_ffmpeg(monkeypatch):
    monkeypatch.setattr(shutil, 'which', lambda n: None)
    assert details.probe(['x']) == dict(available=False) and details._embedded(['x']) is None
