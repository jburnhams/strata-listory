import json, os
from app import catalogue, mirror, settings

PNG = b'\x89PNG\r\n\x1a\n' + b'0' * 20
JPG = b'\xff\xd8\xff\xe0' + b'0' * 20


def lib(tmp_path):
    root = tmp_path / 'audio'; root.mkdir(); settings.save(dict(library_dir=str(root))); return root


def test_folder_layout_mirrors_the_library(tmp_path):
    root = lib(tmp_path); (root / 'Ian Rankin' / '01 Witch Hunt').mkdir(parents=True); (root / 'Books').mkdir(); (root / 'Books' / 'Loose.mp3').write_bytes(b'x')
    rel = lambda b: os.path.relpath(mirror.dir_for(b), mirror.root()).replace(os.sep, '/')
    assert rel(dict(id=1, path=str(root / 'Ian Rankin' / '01 Witch Hunt'))) == 'Ian Rankin/01 Witch Hunt'
    assert rel(dict(id=2, path=str(root / 'Books' / 'Loose.mp3'))) == 'Books/Loose'                      # a file's folder drops the extension
    assert rel(dict(id=3, path=str(root / 'Gone' / 'Missing.m4b'))) == 'Gone/Missing'                    # even when the file is not there
    assert rel(dict(id=4, path='/elsewhere/Some Book')) == '_external/Some Book'
    assert rel(dict(id=5, path='', title='By: hand?')) == '_manual/5-By_ hand_'
    assert rel(dict(id=6, path=str(root / 'a' / '..bad' / 'x?y'))) == 'a/bad/x_y'
    settings.save(dict(library_dir=None))
    assert rel(dict(id=7, path='/audio/Book')) == '_external/Book'                                       # no library folder configured


def test_confirmed_books_are_written_new_ones_are_not(tmp_path):
    root = lib(tmp_path); d = root / 'Dune'; d.mkdir(); (d / '01.mp3').write_bytes(b'x')
    (d / 'Dune.metadata.json').write_text(json.dumps(dict(asin='B0X', title='Dune', authors=[dict(name='Frank Herbert')], narrators=[dict(name='Scott Brick')])))
    catalogue.add_scanned([dict(path=str(d), title='Dune', author='', narrator='', series_position=None, asin='', kind='book', file_count=1)])
    b = catalogue.list_books()[0]
    assert not os.path.exists(mirror.dir_for(b))                                                         # scanned only: nothing written
    catalogue.set_suggestion(b['id'], dict(title='Dune', author='Frank Herbert', sources=['audible']), 0.9)
    done = catalogue.confirm(b['id'])
    meta = mirror.read_meta(done)
    assert meta['book']['title'] == 'Dune' and meta['book']['review'] == 'confirmed' and 'suggestion' not in meta['book'] and meta['match']['suggestion']['sources'] == ['audible'] and meta['match']['score'] == 0.9
    assert meta['audible_file']['narrators'] == ['Scott Brick'] and meta['cover'] is None and meta['written']
    catalogue.update_book(b['id'], status='finished')
    assert mirror.read_meta(done)['book']['status'] == 'finished'                                        # edits follow
    hand = catalogue.add_book(title='By hand')
    assert os.path.isfile(os.path.join(mirror.dir_for(hand), 'metadata.json'))


def test_save_cover_replaces_other_types_and_keeps_the_source(tmp_path):
    root = lib(tmp_path); b = catalogue.add_book(title='C', path=str(root / 'C'))
    p = mirror.save_cover(b, JPG, '.jpg', 'folder')
    assert mirror.cover_file(b) == p and mirror.read_meta(b)['cover'] == 'cover.jpg'
    p2 = mirror.save_cover(b, PNG, '.png', 'embedded')
    assert mirror.cover_file(b) == p2 and not os.path.exists(p) and mirror.read_meta(b)['cover_source'] == 'embedded'
    mirror.write(b)                                                                                      # a plain rewrite keeps the source
    assert mirror.read_meta(b)['cover_source'] == 'embedded'


def test_write_failures_never_break_a_catalogue_change(tmp_path, monkeypatch):
    blocker = os.path.join(settings.data_dir(), 'library'); open(blocker, 'w').close()                  # a file where the mirror folder should be
    b = catalogue.add_book(title='Still saved')
    assert catalogue.get_book(b['id'])['title'] == 'Still saved' and mirror.write(b) is None
    assert mirror.read_meta(b) == {}
    os.remove(blocker); assert mirror.write_all([b]) == 1


def test_corrupt_metadata_reads_as_empty(tmp_path):
    b = catalogue.add_book(title='X'); p = os.path.join(mirror.dir_for(b), 'metadata.json')
    open(p, 'w').write('{bad'); assert mirror.read_meta(b) == {}
    open(p, 'w').write('[1]'); assert mirror.read_meta(b) == {}
