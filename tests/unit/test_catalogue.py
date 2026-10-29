import os
import pytest
from hypothesis import given, strategies as st
from app import catalogue, settings


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


def test_corrupt_or_non_list_store_reads_as_empty():
    p = os.path.join(settings.data_dir(), 'catalogue.json')
    for text in ('{bad json', '{"not": "a list"}'):
        with open(p, 'w', encoding='utf8') as f: f.write(text)
        assert catalogue.list_books() == []
    assert catalogue.add_book(title='Recovered')['id'] == 1


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
