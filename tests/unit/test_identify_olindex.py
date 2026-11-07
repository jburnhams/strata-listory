import sqlite3
from app.identify import olindex


from identify_dumps import dumps


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


def test_start_method_has_a_windows_fallback(monkeypatch):
    import multiprocessing
    assert olindex.start_method() in ('fork', 'spawn')
    monkeypatch.setattr(multiprocessing, 'get_all_start_methods', lambda: ['spawn'])
    assert olindex.start_method() == 'spawn'
