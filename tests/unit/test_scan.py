import os
import pytest
from app import scan


def tree(root, *paths):
    for p in paths:
        f = os.path.join(root, p); os.makedirs(os.path.dirname(f), exist_ok=True); open(f, 'w').close()


def found(root):
    return {os.path.relpath(c['path'], root).replace(os.sep, '/'): c for c in scan.scan(str(root))}      # forward slashes on every OS


@pytest.mark.parametrize('name, parent, expect', [
    ('Aldous Huxley 2008 Brave New World [B004EVP6BY]', 'Audible', dict(author='Aldous Huxley', title='Brave New World', year=2008, asin='B004EVP6BY')),
    ('Banking on It [024199215X]', 'Audible', dict(author='', title='Banking on It', asin='024199215X')),
    ('Born a Crime- Stories from a South African Childhood (Unabridged)', '', dict(title='Born a Crime- Stories from a South African Childhood')),
    ('Andrew Peterson - Right to Kill Nathan McBride, Book 6 (Unabridged)', '', dict(author='Andrew Peterson', title='Right to Kill Nathan McBride, Book 6')),
    ('Anathem by Neal Stephenson', 'Neal Stephenson', dict(author='Neal Stephenson', title='Anathem')),
    ('The Doomsday Machine 2017 by Daniel Ellsberg (Unabridged)', '', dict(author='Daniel Ellsberg', title='The Doomsday Machine', year=2017)),
    ('Iain Banks. The Wasp Factory. Read by Peter Kenny', 'Iain Banks', dict(author='Iain Banks', title='The Wasp Factory', narrator='Peter Kenny')),
    ('Ian Rankin - 01- Knots & Crosses', 'Ian Rankin', dict(author='Ian Rankin', title='Knots & Crosses', series_position=1.0)),
    ('01 Witch Hunt', 'Ian Rankin', dict(author='Ian Rankin', title='Witch Hunt', series_position=1.0)),
    ('Frankie_Boyle_Prometheus.mp3', '', dict(title='Frankie Boyle Prometheus')),
    ('Little Women', 'Audible', dict(author='', title='Little Women')),
])
def test_parse_name(name, parent, expect):
    got = scan.parse_name(name, parent)
    assert {k: got[k] for k in expect} == expect


def test_one_work_decisions():
    assert scan.one_work(['a.mp3'])
    assert not scan.one_work(['a.m4b', 'b.m4b'])                                               # whole-book files are separate books
    assert scan.one_work([f'{i:03d}.mp3' for i in range(1, 12)])                               # numbered chapters
    assert scan.one_work(['Chapter 6. The Skull.mp3', 'Chapter 7. Bone.mp3', 'Track 3.mp3'])
    assert scan.one_work(['Right to Kill Ch01.mp3', 'Right to Kill Ch02.mp3', 'Right to Kill Ch03.mp3'])
    assert not scan.one_work(['Great Lives S31E01 Ivor.mp3', 'Great Lives S31E02 Maya.mp3'])      # broadcast episodes
    assert not scan.one_work(['Frankie Boyle.mp3', 'Mark Thomas.mp3', 'Isy Suttie.mp3'])          # unrelated tracks


def test_scan_finds_books_in_the_shapes_the_library_has(tmp_path):
    tree(tmp_path, 'Ian Rankin/01 Witch Hunt/001.mp3', 'Ian Rankin/01 Witch Hunt/002.mp3',
         'Single.m4b', 'Audible/Aldous Huxley 2008 Brave New World [B004EVP6BY]/Brave New World.m4b',
         'Neal Stephenson/Anathem by Neal Stephenson/Anathem (Disc 1)/Track01.mp3', 'Neal Stephenson/Anathem by Neal Stephenson/Anathem (Disc 2)/Track01.mp3',
         'Big Book/CD1/01.mp3', 'Big Book/CD2/01.mp3',
         'Spoken/Frankie Boyle.mp3', 'Spoken/Mark Thomas.mp3', 'Spoken/cover.jpg', 'Spoken/desktop.ini',
         'Great Lives - Series 31/Great Lives S31E01 Ivor.mp3', 'Great Lives - Series 31/Great Lives S31E02 Maya.mp3', '.hidden/x.mp3', 'empty/readme.txt')
    got = found(tmp_path)
    assert set(got) == {'Ian Rankin/01 Witch Hunt', 'Single.m4b', 'Audible/Aldous Huxley 2008 Brave New World [B004EVP6BY]', 'Neal Stephenson/Anathem by Neal Stephenson', 'Big Book',
                        'Spoken/Frankie Boyle.mp3', 'Spoken/Mark Thomas.mp3', 'Great Lives - Series 31/Great Lives S31E01 Ivor.mp3', 'Great Lives - Series 31/Great Lives S31E02 Maya.mp3'}
    assert got['Ian Rankin/01 Witch Hunt']['file_count'] == 2 and got['Ian Rankin/01 Witch Hunt']['author'] == 'Ian Rankin'
    assert got['Neal Stephenson/Anathem by Neal Stephenson']['file_count'] == 2 and got['Neal Stephenson/Anathem by Neal Stephenson']['title'] == 'Anathem'
    assert got['Big Book']['file_count'] == 2
    assert got['Spoken/Mark Thomas.mp3']['author'] == ''                                       # "Spoken" is a container, not an author
    assert got['Great Lives - Series 31/Great Lives S31E01 Ivor.mp3']['kind'] == 'radio' and got['Single.m4b']['kind'] == 'book'
    asin = got['Audible/Aldous Huxley 2008 Brave New World [B004EVP6BY]']                      # a folder holding one file is the book, named by the folder
    assert asin['title'] == 'Brave New World' and asin['author'] == 'Aldous Huxley' and asin['asin'] == 'B004EVP6BY'


def test_scan_never_writes(tmp_path):
    tree(tmp_path, 'a/b.mp3'); before = sorted(os.walk(tmp_path)); list(scan.scan(str(tmp_path))); assert sorted(os.walk(tmp_path)) == before
