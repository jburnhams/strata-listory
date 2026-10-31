import sys
import os
import pytest
from hypothesis import given, strategies as st
from app import backup, catalogue, db


def test_add_applies_defaults_and_assigns_increasing_ids():
    b = catalogue.add_book(title='  Dune ', author='Frank Herbert')
    assert b['id'] == 1 and b['title'] == 'Dune' and b['status'] == 'unlistened' and b['series_position'] is None and b['added']
    assert catalogue.add_book(title='Emma')['id'] == 2
    catalogue.remove_book(1)
    assert catalogue.add_book(title='Persuasion')['id'] == 3          # ids are never reused while a later book exists


def test_validation():
    for bad, msg in ((dict(), 'title is required'), (dict(title='  '), 'title is required'), (dict(title='x' * 301), 'too long'), (dict(title='a', status='lost'), 'status must be'),
                     (dict(title='a', duration_seconds='abc'), 'must be a number'), (dict(title='a', series_position=-1), 'cannot be negative')):
        with pytest.raises(catalogue.CatalogueError, match=msg): catalogue.add_book(**bad)
    with pytest.raises(catalogue.CatalogueError, match='title is required'): catalogue.update_book(catalogue.add_book(title='a')['id'], title=' ')


def test_numbers_are_coerced():
    b = catalogue.add_book(title='a', duration_seconds='3600', series_position='2.5')
    assert b['duration_seconds'] == 3600 and b['series_position'] == 2.5


def test_update_get_and_remove():
    b = catalogue.add_book(title='Dune')
    assert catalogue.update_book(b['id'], status='finished', narrator='Scott Brick', author=None)['status'] == 'finished'
    got = catalogue.get_book(b['id'])
    assert got['narrator'] == 'Scott Brick' and got['author'] == '' and got['title'] == 'Dune'      # None means "leave unchanged"
    assert catalogue.remove_book(b['id'])['title'] == 'Dune'
    for fn in (catalogue.get_book, catalogue.remove_book, lambda i: catalogue.update_book(i, title='x')):
        with pytest.raises(catalogue.NotFound): fn(b['id'])


def test_list_sorts_searches_and_filters():
    catalogue.add_book(title='Book Two', author='Zed', series='Saga', series_position=2)
    catalogue.add_book(title='Book One', author='Zed', series='Saga', series_position=1, status='finished')
    catalogue.add_book(title='Alpha', author='Amy', narrator='Bob Reader')
    assert [b['title'] for b in catalogue.list_books()] == ['Alpha', 'Book One', 'Book Two']
    assert [b['title'] for b in catalogue.list_books('saga')] == ['Book One', 'Book Two']
    assert [b['title'] for b in catalogue.list_books('reader')] == ['Alpha']
    assert [b['title'] for b in catalogue.list_books(status='finished')] == ['Book One']
    assert catalogue.list_books('nothing') == []


def test_a_corrupt_database_is_refused_with_restore_advice(monkeypatch):
    catalogue.add_book(title='Good')
    good = backup.backup('manual')
    monkeypatch.setattr(db, '_checked', set())              # a new process opening the database
    with open(db.path(), 'wb') as f: f.write(b'not a database' * 100)
    with pytest.raises(db.CorruptDatabase, match='./app restore'): catalogue.list_books()
    safety = backup.restore(good)
    assert safety is None                                   # the damaged file is not worth keeping
    assert [b['title'] for b in catalogue.list_books()] == ['Good']


@given(title=st.text(min_size=1, max_size=40).filter(lambda s: s.strip()))
def test_title_is_stripped_property(title):
    assert catalogue.clean(dict(title=title))['title'] == title.strip()


def test_operations_find_the_right_book_among_several():
    a, b, c = (catalogue.add_book(title=t) for t in 'abc')
    assert catalogue.get_book(c['id'])['title'] == 'c'
    assert catalogue.update_book(b['id'], title='B')['title'] == 'B'
    catalogue.remove_book(b['id'])
    assert [x['title'] for x in catalogue.list_books()] == ['a', 'c']
    for fn in (catalogue.get_book, catalogue.remove_book): 
        with pytest.raises(catalogue.NotFound): fn(99)
    with pytest.raises(catalogue.NotFound): catalogue.update_book(99, title='x')


def test_clean_keeps_explicit_none_numbers_and_ignores_unknown_fields():
    out = catalogue.clean(dict(title='a', duration_seconds=None, series_position=None, bogus='x'))
    assert out['duration_seconds'] is None and 'bogus' not in out
    assert catalogue.clean(dict(status='finished'), partial=True) == dict(status='finished')
    assert catalogue.clean(dict(), partial=True) == {}


@pytest.mark.skipif(sys.platform == 'win32', reason='uses POSIX-style absolute paths')
def test_rebase_paths_moves_only_books_under_the_old_folder():
    for p in ('/share/audio/A', '/share/audio/B/01.mp3', '/share/audio', '/share/audio2/C', '/other/D', ''): catalogue.add_book(title=p or 'hand', path=p)
    assert sorted(catalogue.paths_under('/share/audio/')) == ['/share/audio', '/share/audio/A', '/share/audio/B/01.mp3']
    assert catalogue.rebase_paths('/share/audio', '/local/audio-local/') == 3
    assert sorted(b['path'] for b in catalogue.list_books()) == ['', '/local/audio-local', '/local/audio-local/A', '/local/audio-local/B/01.mp3', '/other/D', '/share/audio2/C']      # audio2 is not under audio
    assert catalogue.rebase_paths('/share/audio', '/x') == 0
    catalogue.add_book(title='odd', path='/share/100%_real/a')
    assert catalogue.rebase_paths('/share/100%_real', '/y') == 1                                                 # LIKE wildcards in names are literal


def test_fields_you_set_are_locked_but_internal_updates_and_matches_are_not():
    hand = catalogue.add_book(title='By hand', author='Me', year=1999)
    assert hand['locked'] == ['title', 'author', 'year']                                                      # everything entered by hand is yours
    scanned = catalogue.add_scanned([dict(path='/x/s', title='Scanned', author='Auth', narrator='', series_position=None, asin='', kind='book', file_count=1, year=2001)]) and catalogue.list_books(review='new')[0]
    assert scanned['locked'] == [] and scanned['year'] == 2001 and scanned['year_source'] == 'folder name'
    assert catalogue.update_book(scanned['id'], author='Corrected')['locked'] == ['author']                  # an edit locks the field
    assert catalogue.update_book(scanned['id'], title='Also', author='Corrected')['locked'] == ['author', 'title']
    assert catalogue.update_book(scanned['id'], status='finished')['locked'] == ['author', 'title']           # status is not metadata
    catalogue.set_suggestion(scanned['id'], dict(title='Suggested', author='From Match'), 0.9)
    done = catalogue.confirm(scanned['id']); assert done['title'] == 'Suggested' and done['locked'] == ['author', 'title']       # confirming applies the match without locking
    fresh = catalogue.add_scanned([dict(path='/x/t', title='T', author='A', narrator='', series_position=None, asin='', kind='book', file_count=1)]) and [b for b in catalogue.list_books(review='new')][0]
    catalogue.set_suggestion(fresh['id'], dict(title='T2', author='A2'), 0.9); assert catalogue.confirm(fresh['id'])['locked'] == [] and catalogue.ignore(fresh['id'])['locked'] == []
