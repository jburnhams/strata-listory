"""The offline Open Library provider reads the identify index and the full-record detail store built from the same dumps."""
import pytest

from app.identify import oldetail, olindex
from app.providers import openlibrary_local as local
from identify_dumps import dumps, row


@pytest.fixture
def stores(tmp_path):
    d = dumps(tmp_path)
    import gzip
    with gzip.open(d / 'works.txt.gz', 'at', encoding='utf8') as f:      # a second author's work with the same title, and a cover on The Hobbit
        f.write(row('/type/work', '/works/OL5W', dict(title='The Hobbit', authors=[dict(author=dict(key='/authors/OL9A'))], covers=[-1, 4242], first_publish_date='1950')))
    with gzip.open(d / 'authors.txt.gz', 'at', encoding='utf8') as f: f.write(row('/type/author', '/authors/OL9A', dict(name='Somebody Else')))
    with gzip.open(d / 'editions.txt.gz', 'at', encoding='utf8') as f: f.write(row('/type/edition', '/books/OL7M', dict(title='The Hobbit', subtitle='Illustrated', works=[dict(key='/works/OL1W')], publishers=['Allen & Unwin'], publish_date='1966', isbn_13=['9780048231222'], physical_format='Hardcover', languages=[dict(key='/languages/eng')])))
    olindex.build(d, workers=0); oldetail.build(d, workers=2)
    return d


def test_search_uses_author_identity_ratings_and_detail_cover(stores):
    [c] = local.search('The Hobbit', 'J. R. R. Tolkien')
    assert (c['id'], c['authors'], c['original_year'], c['genres'], c['url']) == ('/works/OL1W', ['J. R. R. Tolkien'], 1937, ['Fantasy'], 'https://openlibrary.org/works/OL1W')
    assert c['rating'] == 4.5 and c['ratings_count'] == 2 and c['cover_url'] == ''
    assert local.search('The Hobbit', 'Somebody Else')[0]['cover_url'] == 'https://covers.openlibrary.org/b/id/4242-L.jpg'
    assert local.search('') == [] and local.search('Nothing like this at all') == []


def test_linked_work_lookup_follows_redirects_and_ignores_bad_keys(stores):
    assert [c['id'] for c in local.work_candidates(['/works/OL2W', '/works/OL1W', 'nonsense', '/works/OL404W'])] == ['/works/OL1W']
    assert local.work_candidates([]) == []


def test_editions_by_work_and_by_isbn_with_equivalent_isbn10_and_13(stores):
    by_work = local.editions('/works/OL1W')
    assert [e['key'] for e in by_work] == ['/books/OL7M', '/books/OL3M', '/books/OL1M']       # oldest first; the French edition is not in the store
    illustrated = by_work[0]
    assert (illustrated['subtitle'], illustrated['publishers'], illustrated['physical_format'], illustrated['year'], illustrated['work_key'], illustrated['works'], illustrated['languages']) == ('Illustrated', ['Allen & Unwin'], 'Hardcover', 1966, '/works/OL1W', ['/works/OL1W'], [dict(key='/languages/eng')])
    assert [e['key'] for e in local.editions(isbn='0-261-10221-4')] == ['/books/OL1M'] and local.editions(isbn='9780261102217')[0]['isbns'] == ['0261102214', '9780261102217']
    assert local.editions('bad') == [] and local.editions(isbn='12') == [] and local.editions() == [] and len(local.editions('/works/OL1W', limit=1)) == 1


def test_stores_missing_means_unavailable_and_status_reports_them(tmp_path):
    assert local.search('The Hobbit') == [] and local.work_candidates(['/works/OL1W']) == [] and local.editions('/works/OL1W') is None
    assert local.status()['detail'] is False


def test_status_after_build_and_helpers(stores):
    assert local.status()['ready'] and local.status()['detail']
    assert local.isbn_keys('0-261-10221-4') == ['0261102214', '9780261102217'] and local.isbn_keys('9780261102218') == [] and local.year('Jan 5, 1999') == 1999
    assert local.normalise('Blood Méridian') == 'blood meridian'


def test_cli_status(stores, capsys):
    from app.cli import main
    assert main(['openlibrary-status']) == 0 and '"detail": true' in capsys.readouterr().out
