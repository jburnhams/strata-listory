import pytest
from app import catalogue, enrich, providers, sources as S
from app.providers.base import MatchError, candidate


def cand(source, **kw):
    return candidate(source, id=kw.pop('id', 'x'), **kw)


pytestmark = pytest.mark.year_lookup          # these tests exercise the real gather() with scripted providers


def book(**kw):
    return dict(dict(id=1, title='Brave New World', author='CBS Radio Workshop, Aldous Huxley', narrator='', series='', series_position=None, asin='B004EVP6BY', year=None, year_source='', locked=[], suggestion=None), **kw)


def test_records_from_each_kind_of_source():
    c = cand('audnexus', title='T', authors=['A B'], narrators=['N N'], publisher='P', series='S', series_position=2.0, release_date='2008-01-16T00:00:00', runtime_minutes=480, isbns=['978'], asin='B004EVP6BY', language='english', genres=['G'], description='D')
    r = S.from_candidate(c); assert (r['authors'], r['narrators'], r['release_year'], r['original_year'], r['asin'], r['isbns'], r['publisher']) == (['A B'], ['N N'], 2008, None, 'B004EVP6BY', ['978'], 'P')
    assert S.from_candidate(cand('hardcover', title='T', author='A B, C D', original_year=1932, release_date='1986'))['release_year'] is None                    # a book catalogue's date is not the audiobook's
    assert S.from_candidate(cand('audible', title='T', author='A B & C D'))['authors'] == ['A B', 'C D'] and S.from_candidate(cand('audible', asin='024199215X'))['asin'] == ''             # an ISBN-style code is not an ASIN
    a = S.from_audible_file(dict(title='T', authors=['A'], narrators=['N'], publisher='P', series=[dict(title='S', sequence='3')], release_date='2007-05-01', runtime_minutes=60, asin='B0X', language='en', categories=['Cat'], summary='Sum', rating='4.2', rating_count=9))
    assert (a['series'], a['series_position'], a['release_year'], a['genres'], a['rating'], a['ratings_count']) == ('S', 3.0, 2007, ['Cat'], 4.2, 9) and S.from_audible_file(dict(series=[dict(title='S', sequence='x')]))['series_position'] is None
    assert S.from_openlibrary(dict(title='T', author='A B', first_publish_year=1932, subjects=['s'] * 20, rating=3.9, ratings_count=5))['original_year'] == 1932 and len(S.from_openlibrary(dict(subjects=['s'] * 20))['genres']) == 12
    h = S.from_hardcover(dict(title='T', authors=['A B'], release_date='1932-01-01', genres=['g'], description='d', series=[dict(name='S', position=1)], rating=4.0)); assert (h['original_year'], h['series'], h['series_position']) == (1932, 'S', 1)
    cat = S.from_catalogue(dict(title='T', author='A B, C D and E F', narrator='N N', series='S', series_position=1.0, asin='B004EVP6BY')); assert cat['authors'] == ['A B', 'C D', 'E F'] and cat['narrators'] == ['N N'] and cat['asin'] == 'B004EVP6BY'


def test_local_records_use_what_is_stored():
    sug = dict(source='audiobookdb', title='Brave New World', author='CBS Radio Workshop, Aldous Huxley')
    ex = dict(openlibrary=dict(data=dict(title='Brave New World', author='Aldous Huxley', first_publish_year=1932)), hardcover=dict(data=dict(not_found=True)))
    recs = S.local_records(book(suggestion=sug), dict(title='Brave New World', authors=['Aldous Huxley']), ex)
    assert sorted(recs) == ['audible_file', 'audiobookdb', 'catalogue', 'openlibrary']                                    # "not found" is not a source
    assert sorted(S.local_records(book())) == ['catalogue']


def test_locked_values_only_include_fields_set_by_hand():
    b = book(locked=['author', 'year', 'series'], year=1931, series='S')
    assert S.locked_values(b) == dict(authors=['CBS Radio Workshop', 'Aldous Huxley'], original_year=1931, series='S') and S.locked_values(book()) == {}


def test_resolve_prefers_the_original_year_then_flags_the_fallbacks():
    full = {'catalogue': dict(title='T', authors=['A B']), 'openlibrary': dict(original_year=1932), 'hardcover': dict(original_year=1986), 'audnexus': dict(release_year=2008)}
    r = S.resolve(book(), full); assert (r['year'], r['year_source'], r['year_note']) == (1932, 'Open Library (first published)', 'Hardcover says 1986')
    agree = S.resolve(book(), {**full, 'hardcover': dict(original_year=1932)}); assert (agree['year'], agree['year_source'], agree['year_note']) == (1932, 'Open Library + Hardcover (first published)', '')
    audio = S.resolve(book(), {'catalogue': dict(title='T'), 'audnexus': dict(release_year=2008)}); assert (audio['year'], audio['year_source']) == (2008, 'Audnexus (audiobook release)') and 'no original publication year' in audio['year_note']
    folder = S.resolve(book(year=2015, year_source='folder name'), {'catalogue': dict(title='T')}); assert (folder['year'], folder['year_source']) == (2015, 'folder name') and 'folder name' in folder['year_note']
    none = S.resolve(book(), {'catalogue': dict(title='T')}); assert none['year'] is None and none['year_source'] == ''
    hand = S.resolve(book(locked=['year'], year=1999, year_source='manual'), full); assert (hand['year'], hand['year_source']) == (1999, 'set by hand')


def test_resolve_and_rows_show_value_sources_and_what_was_outvoted():
    recs = {'catalogue': dict(title='Brave New World', authors=['CBS Radio Workshop', 'Aldous Huxley']), 'audible_file': dict(title='Brave New World', authors=['Aldous Huxley'], narrators=['Michael York'], publisher='Blackstone', series='S', series_position=2.0, asin='B004EVP6BY', genres=['Classics']),
            'audnexus': dict(title='Brave New World', authors=['Aldous Huxley'], narrators=['Michael York'], release_year=2008), 'openlibrary': dict(authors=['Aldous Huxley'], original_year=1932)}
    r = S.resolve(book(), recs); rows = {x['field']: x for x in S.rows(book(), r)}
    assert r['authors'] == ['Aldous Huxley'] and r['narrators'] == ['Michael York'] and r['series'] == 'S' and r['genres'] == ['Classics']
    assert rows['Author']['value'] == 'Aldous Huxley' and rows['Author']['source'].startswith('Audible metadata file + Audnexus + Open Library') and rows['Author']['note'] == 'others say: CBS Radio Workshop (the catalogue (folder name or your confirmation))'
    assert rows['Narrator']['source'] == 'Audible metadata file + Audnexus' and rows['Series']['value'] == 'S #2' and rows['Year']['value'] == '1932' and rows['Publisher']['value'] == 'Blackstone' and rows['ASIN']['value'] == 'B004EVP6BY'
    nobody = book(author='', asin=''); empty = S.rows(nobody, S.resolve(nobody, {'catalogue': dict(title='T')})); assert [x['field'] for x in empty] == ['Title']


class P:
    """A scripted provider for gather()."""
    def __init__(self, name, by_asin=None, results=None, fail=False, unavailable=None):
        self.name, self.asins, self.results, self.fail, self._un, self.calls = name, by_asin or {}, results or [], fail, unavailable, []
    def unavailable(self): return self._un
    def by_asin(self, a):
        self.calls.append(('asin', a))
        if self.fail: raise MatchError('down')
        return self.asins.get(a)
    def search(self, t, a=''):
        self.calls.append(('search', t))
        if self.fail: raise MatchError('down')
        return self.results


def test_gather_asks_the_audiobook_sources_then_the_book_catalogues_about_the_agreed_work(monkeypatch):
    nx = P('audnexus', {'B004EVP6BY': cand('audnexus', title='Brave New World', authors=['Aldous Huxley'], narrators=['Michael York'], asin='B004EVP6BY')}); au = P('audible', {'B004EVP6BY': cand('audible', title='Brave New World', authors=['Aldous Huxley'], narrators=['Michael York'])})
    db = P('audiobookdb', unavailable='no key'); asked = []
    monkeypatch.setattr(providers, 'get', lambda name: {'audnexus': nx, 'audible': au, 'audiobookdb': db}[name])
    monkeypatch.setattr(enrich, 'ensure_year_sources', lambda b: asked.append((b['title'], b['author'])) or dict(openlibrary=dict(data=dict(title='Brave New World', author='Aldous Huxley', first_publish_year=1932))))
    recs = S.gather(book(), None)
    assert asked == [('Brave New World', 'Aldous Huxley')]                                   # the work catalogues were asked with the author the audiobook sources agree on, not the catalogue's wrong one
    assert sorted(recs) == ['audible', 'audnexus', 'catalogue', 'openlibrary'] and db.calls == []
    assert S.resolve(book(), recs)['year'] == 1932


def test_gather_searches_the_audible_catalogue_when_there_is_no_asin_and_survives_outages(monkeypatch):
    found = cand('audible', title='Right to Kill', authors=['Andrew Peterson'], narrators=['Dick Hill'], asin='B01LY8XM4W')
    nx = P('audnexus', fail=True); au = P('audible', results=[cand('audible', title='Other Book', authors=['Someone'] ), found]); db = P('audiobookdb', fail=True)
    monkeypatch.setattr(providers, 'get', lambda name: {'audnexus': nx, 'audible': au, 'audiobookdb': db}[name]); monkeypatch.setattr(enrich, 'ensure_year_sources', lambda b: {})
    recs = S.gather(dict(book(), title='Right to Kill', author='Andrew Peterson', asin=''), None)
    assert recs['audible']['narrators'] == ['Dick Hill'] and 'audnexus' not in recs and 'audiobookdb' not in recs and nx.calls == []                    # no ASIN: Audnexus (ASIN only) is not asked; the search found the right book
    au.results = [cand('audible', title='Nothing Like It', authors=['X Y'])]
    assert 'audible' not in S.gather(dict(book(), title='Right to Kill', author='Andrew Peterson', asin=''), None)                                      # a poor match is not trusted
