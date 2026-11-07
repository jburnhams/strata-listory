import gzip
import json
import sqlite3
from app.identify import olindex


def row(kind, key, data): return f'{kind}\t{key}\t1\t2026-01-01\t{json.dumps(data)}\n'


def dumps(tmp_path):
    d = tmp_path / 'dumps'; d.mkdir()
    def write(name, rows):
        with gzip.open(d / f'{name}.txt.gz', 'wt', encoding='utf8') as f: f.writelines(rows)
    write('authors', [row('/type/author', '/authors/OL1A', dict(name='J. R. R. Tolkien', alternate_names=['Tolkien, J.R.R.', 'John Ronald Reuel Tolkien']))])
    write('works', [row('/type/work', '/works/OL1W', dict(title='The Hobbit', subtitle='or There and Back Again', authors=[dict(author=dict(key='/authors/OL1A'))], first_publish_date='1937', subjects=['Fantasy'])),
                    row('/type/redirect', '/works/OL2W', dict(location='/works/OL1W')), 'bad line\n', row('/type/work', '/works/OL3W', dict(title=''))])
    write('editions', [row('/type/edition', '/books/OL1M', dict(title='The Hobbit', works=[dict(key='/works/OL1W')], isbn_10=['0261102214'], isbn_13=['9780261102217'], publish_date='1999', languages=[dict(key='/languages/eng')], series=['Middle-earth'], identifiers=dict(amazon=['B002V0QK4C']), other_titles=['Der Hobbit'])),
                       row('/type/edition', '/books/OL2M', dict(title='Le Hobbit', works=[dict(key='/works/OL1W')], languages=[dict(key='/languages/fre')])),
                       row('/type/edition', '/books/OL3M', dict(title='The Hobbit', subtitle='Audio', works=[dict(key='/works/OL1W')], physical_format='Audio CD', publish_date='1991')),
                       row('/type/edition', '/books/OL4M', dict(title='Orphan'))])
    write('ratings', ['/works/OL1W\t/books/OL1M\t5\t2020\n', '/works/OL1W\t/books/OL1M\t4\t2020\n', '/works/OL1W\t\t9\t2020\n'])
    return d


def test_build_and_publish(tmp_path):
    out = tmp_path / 'identify.sqlite'
    result = olindex.build(dumps(tmp_path), workers=0, output=out)
    assert result['ready'] and result['counts']['editions'] == 2 and result['counts']['ratings'] == 2
    c = sqlite3.connect(out)
    assert c.execute("SELECT title, editions, audio_editions, first_edition_year, ratings, rating_total, year FROM works WHERE key='/works/OL1W'").fetchone() == ('The Hobbit', 2, 1, 1991, 2, 9, 1937)
    assert c.execute("SELECT count(*) FROM works").fetchone()[0] == 1
    titles = dict(c.execute("SELECT norm || '/' || kind, n FROM work_titles WHERE work='/works/OL1W'").fetchall())
    assert titles['hobbit/0'] == 1 and titles['hobbit/2'] == 2 and titles['hobbit or there and back again/1'] == 1 and titles['der hobbit/5'] == 1 and titles['hobbit audio/3'] == 1
    assert 'le hobbit/2' not in titles  # explicitly French edition is dropped
    assert {r[0] for r in c.execute('SELECT isbn FROM work_isbns')} == {'0261102214', '9780261102217'}
    assert c.execute('SELECT asin FROM work_asins').fetchall() == [('B002V0QK4C',)]
    assert c.execute('SELECT norm FROM work_series').fetchall() == [('middle earth',)]
    assert c.execute('SELECT old, new FROM redirects').fetchall() == [('/works/OL2W', '/works/OL1W')]
    assert {r[0] for r in c.execute('SELECT norm FROM author_names')} >= {'j r r tolkien', 'john ronald reuel tolkien'}
    assert c.execute("SELECT author FROM work_authors WHERE work='/works/OL1W'").fetchall() == [('/authors/OL1A',)]
    assert olindex.status()['stage'] == 'ready'


def test_missing_dump_and_failure_status(tmp_path):
    import pytest
    with pytest.raises(ValueError, match='missing'): olindex.build(tmp_path, workers=0, output=tmp_path / 'x.sqlite')
    d = dumps(tmp_path)
    (d / 'ratings.txt.gz').write_bytes(b'not gzip')
    with pytest.raises(Exception): olindex.build(d, workers=0, output=tmp_path / 'y.sqlite')
    assert olindex.status()['stage'] == 'failed' and not (tmp_path / 'y.sqlite').exists()


def test_status_default_and_helpers(tmp_path):
    assert olindex.status()['stage'] == 'not_built'
    assert olindex.year('Jan 5, 1999') == 1999 and olindex.year('n.d.') is None
    assert olindex.keep_edition([]) and olindex.keep_edition(['/languages/eng', '/languages/fre']) and not olindex.keep_edition(['/languages/fre'])
