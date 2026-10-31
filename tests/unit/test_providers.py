import time
import pytest
from fakes import http_error
from app import providers, settings
from app.providers import audible, audiobookdb, audnexus
from app.providers.base import MatchError


@pytest.fixture(autouse=True)
def fast(monkeypatch):
    monkeypatch.setenv('APP_ADB_KEY', 'adb_test'); monkeypatch.setenv('APP_HARDCOVER_KEY', 'hc_test'); monkeypatch.setattr(time, 'sleep', lambda s: None)
    for p in providers.ALL: monkeypatch.setattr(providers.get(p.name).http if hasattr(providers.get(p.name), 'http') else object(), 'min_interval', 0, raising=False)


def adb_raw(title, authors=('Frank Herbert',), series=None, ordinal='1', subtitle='', id='x1'):
    return dict(id=id, title=title, subtitle=subtitle, people=[dict(person=dict(name=a), role=dict(name='Author')) for a in authors] + [dict(person=dict(name='Some Narrator'), role=dict(name='Narrator'))],
                **(dict(seriesWithId=[dict(title=series, ordinal=ordinal)]) if series else {}))


AUDIBLE = dict(asin='B00VVVY0PI', title='Flashman at the Charge', subtitle='The Flashman Papers, Book 7', authors=[dict(name='George MacDonald Fraser')], narrators=[dict(name='Colin Mace')], series=[dict(title='The Flashman Papers', sequence='7')],
               runtime_length_min=643, language='english', release_date='2015-05-28', product_images={'500': 'https://img/f.jpg'})


def test_registry_order_selection_and_status(monkeypatch, tmp_path):
    assert providers.configured() == ['audnexus', 'audible', 'hardcover', 'audiobookdb']
    monkeypatch.setenv('APP_PROVIDERS', 'audiobookdb, audible')
    assert [p.name for p in providers.enabled()] == ['audiobookdb', 'audible'] and providers.get('audible') is providers.get('audible')
    st = {s['name']: s for s in providers.status()}
    assert st['audiobookdb']['order'] == 1 and st['audnexus']['selected'] is False and st['audnexus']['order'] is None and st['audible']['available']
    monkeypatch.delenv('APP_ADB_KEY'); monkeypatch.setenv('HOME', str(tmp_path)); monkeypatch.setenv('USERPROFILE', str(tmp_path))
    assert [p.name for p in providers.enabled()] == ['audible'] and 'no API key' in {s['name']: s for s in providers.status()}['audiobookdb']['reason']
    monkeypatch.setenv('APP_PROVIDERS', 'nope')
    with pytest.raises(KeyError, match='unknown provider'): providers.configured()
    monkeypatch.delenv('APP_PROVIDERS'); settings.save(dict(match_providers='audible,audnexus'))
    assert providers.configured() == ['audible', 'audnexus']


def test_audiobookdb_search_asin_cover_and_key(fake_urlopen, monkeypatch, tmp_path):
    p = audiobookdb.AudiobookDB()
    fake_urlopen.reply([adb_raw('Dune', series='Dune Chronicles', ordinal='2')], adb_raw('Dune', id='asin-hit'), http_error(404), dict(coverImage=dict(url='https://cdn/book')))
    c = p.search('Dune', 'Frank Herbert')[0]
    assert (c['source'], c['author'], c['series'], c['series_position']) == ('audiobookdb', 'Frank Herbert', 'Dune Chronicles', 2.0) and [x['title'] for x in p.search('dune')] == ['Dune']      # second from cache
    [call] = fake_urlopen.calls
    assert call['url'].endswith('/api/search') and call['body'] == dict(q='Dune', type='books') and call['headers']['x-api-key'] == 'adb_test' and 'strata-listory' in call['headers']['user-agent']
    assert p.search('ab') == []
    assert p.by_asin('B000000001')['id'] == 'asin-hit' and p.by_asin('B000000002') is None
    assert p.cover_url('b1') == 'https://cdn/book/large.jpg'
    assert audiobookdb.image_url(dict(image='https://cdn/a.png')) == 'https://cdn/a.png' and audiobookdb.image_url({}) == '' and audiobookdb.image_url(dict(image=dict(url=None))) == ''
    monkeypatch.delenv('APP_ADB_KEY'); monkeypatch.setenv('HOME', str(tmp_path)); monkeypatch.setenv('USERPROFILE', str(tmp_path))
    assert p.unavailable() and audiobookdb.key() == ''
    with pytest.raises(MatchError, match='no AudiobookDB API key'): p.search('Another Title')
    (tmp_path / '.config' / 'strata-listory').mkdir(parents=True); (tmp_path / '.config' / 'strata-listory' / 'audiobookdb.key').write_text(' adb_file \n')
    assert audiobookdb.key() == 'adb_file' and p.unavailable() is None


def test_audible_search_and_asin(fake_urlopen, monkeypatch):
    p = audible.AudibleSearch()
    fake_urlopen.reply(dict(products=[AUDIBLE]), dict(product=AUDIBLE), http_error(404))
    c = p.search('Flashman at the Charge', 'George MacDonald Fraser')[0]
    assert (c['asin'], c['narrators'], c['series'], c['series_position'], c['runtime_minutes'], c['cover_url']) == ('B00VVVY0PI', ['Colin Mace'], 'The Flashman Papers', 7.0, 643, 'https://img/f.jpg')
    [call] = fake_urlopen.calls
    assert call['url'].startswith('https://api.audible.co.uk/1.0/catalog/products?') and 'title=Flashman+at+the+Charge' in call['url'] and 'author=George+MacDonald+Fraser' in call['url']
    assert p.by_asin('B00VVVY0PI')['title'] == 'Flashman at the Charge' and p.by_asin('B000000009') is None and p.search('x') == []
    monkeypatch.setenv('APP_AUDIBLE_REGION', 'us'); fake_urlopen.reply(dict(products=[]))
    assert p.search('Another one') == [] and fake_urlopen.calls[-1]['url'].startswith('https://api.audible.com/1.0/')
    assert audible.to_candidate(dict(series=[dict(title='S', sequence='x')]))['series_position'] is None


def test_audnexus_tries_the_region_then_us(fake_urlopen, monkeypatch):
    p = audnexus.Audnexus()
    fake_urlopen.reply(http_error(404), dict(asin='B0US', title='US Only', authors=[dict(name='A B')], narrators=[dict(name='N N')], seriesPrimary=dict(name='Saga', position='3'), runtimeLengthMin=100, image='https://img/a.jpg', releaseDate='2020-01-02T00:00:00.000Z'), http_error(404), http_error(404))
    c = p.by_asin('B0US')
    assert (c['title'], c['series'], c['series_position'], c['runtime_minutes'], c['release_date'], c['source']) == ('US Only', 'Saga', 3.0, 100, '2020-01-02', 'audnexus')
    assert 'region=uk' in fake_urlopen.calls[0]['url'] and 'region=us' in fake_urlopen.calls[1]['url']
    assert p.by_asin('B0MISSING') is None and p.search('anything') == []


def test_http_errors_are_reported_clearly(fake_urlopen):
    p = audible.AudibleSearch()
    fake_urlopen.reply(http_error(500), OSError('down'), http_error(429), http_error(429))
    for t, msg in (('aaa', 'answered 500'), ('bbb', 'unreachable'), ('ccc', 'answered 429')):
        with pytest.raises(MatchError, match=msg): p.search(t)
    fake_urlopen.reply(http_error(429), dict(products=[AUDIBLE]))                              # a 429 is retried once
    assert p.search('ddd')[0]['asin'] == 'B00VVVY0PI'
    fake_urlopen.reply(http_error(503), http_error(503), dict(products=[AUDIBLE]), http_error(503), http_error(503), http_error(503))      # an outage is retried twice, then reported
    assert p.search('eee')[0]['asin'] == 'B00VVVY0PI'
    with pytest.raises(MatchError, match='answered 503'): p.search('fff')


def test_cache_expires_after_a_week(fake_urlopen):
    from app import db
    p = audible.AudibleSearch(); fake_urlopen.reply(dict(products=[AUDIBLE]), dict(products=[dict(AUDIBLE, title='Newer')]))
    p.search('Flashman'); c = db.connect()
    with c: c.execute("UPDATE match_cache SET fetched='2000-01-01T00:00:00+00:00'")
    c.close()
    assert p.search('Flashman')[0]['title'] == 'Newer'


def test_each_provider_declares_its_own_rate_limit_and_cache_lifetime(fake_urlopen):
    limits = {c.name: (c.min_interval, c.cache_days) for c in providers.ALL}
    assert limits == {'audnexus': (0.3, 14), 'audible': (0.3, 14), 'hardcover': (1.1, 14), 'audiobookdb': (0.15, 7), 'openlibrary': (0.5, 30)}
    assert all(c().http.min_interval == c.min_interval for c in providers.ALL)                 # the HTTP client throttles at the provider's own rate
    assert {s['name']: (s['min_interval'], s['cache_days']) for s in providers.status()} == limits
    p = audnexus.Audnexus(); p.cache_days = 0                                                      # a lifetime of zero: always refetched
    fake_urlopen.reply(dict(asin='B0X', title='One'), dict(asin='B0X', title='Two'))
    assert p.by_asin('B0X')['title'] == 'One' and p.by_asin('B0X')['title'] == 'Two'


def test_throttle_spaces_requests(monkeypatch, fake_urlopen):
    from app.providers.base import Http
    now = [100.0]; slept = []
    monkeypatch.setattr(time, 'monotonic', lambda: now[0]); monkeypatch.setattr(time, 'sleep', lambda s: (slept.append(round(s, 3)), now.__setitem__(0, now[0] + s)))
    h = Http('T', min_interval=0.5); fake_urlopen.reply({'a': 1}, {'b': 2})
    h.request('https://x/1'); h.request('https://x/2')
    assert slept[0] == 0 and slept[1] == 0.5                                                       # the second request waits out the interval


HC_HIT = dict(id=375699, title='Born A Crime', subtitle='Stories', author_names=['Trevor Noah'], series_names=[], featured_series={}, image=dict(url='https://assets.hardcover.app/x.jpg'), release_year=2016, rating=4.5, ratings_count=1194, genres=['Memoir', 'Humor'], pages=288, slug='born-a-crime')
HC_DETAILS = dict(books=[dict(id=375699, title='Born A Crime', subtitle='Stories', slug='born-a-crime', description='A memoir.', pages=288, release_date='2016-11-15', rating=4.5, ratings_count=1194, reviews_count=164, users_count=2262, users_read_count=1468, lists_count=948,
                              cached_tags={'Genre': [dict(tag='Memoir', count=5, spoiler_ratio=0), dict(tag='Biography', count=9, spoiler_ratio=0)], 'Mood': [dict(tag='funny', count=3, spoiler_ratio=0)], 'Tag': [dict(tag='Africa', count=1, spoiler_ratio=0.8)], 'Content Warning': [dict(tag='Racism', count=2, spoiler_ratio=0.9)]},
                              book_series=[dict(position=1, series=dict(name='Noah'))])],
                  user_books=[dict(rating=5.0, review_raw='x' * 4000, review_has_spoilers=True, likes_count=7, reviewed_at='2020-01-02T03:04:05', user=dict(username='reader'))],
                  editions=[dict(asin='B01IW9TQPK', audio_seconds=31440, edition_format='Audible.com', release_date='2016-11-15', publisher=dict(name='Audible Studios')), dict(asin=None, audio_seconds=None, edition_format=None, release_date=None, publisher=None)])


def test_hardcover_search_asin_cover_and_details(fake_urlopen, monkeypatch, tmp_path):
    from app.providers import hardcover
    p = hardcover.Hardcover()
    fake_urlopen.reply(dict(data=dict(search=dict(results=dict(hits=[dict(document=HC_HIT)])))), dict(data=dict(editions=[dict(book=dict(id=1, title='Via ASIN', slug='s', rating=4.0, cached_contributors=[dict(author=dict(name='A B'), contribution='Author')], cached_image=dict(url='https://i/c.jpg')))])), dict(data=dict(editions=[])),
                       dict(data=dict(books=[dict(cached_image=dict(url='https://i/cover.jpg'))])), dict(data=HC_DETAILS))
    c = p.search('Born a Crime', 'Trevor Noah')[0]
    assert (c['source'], c['id'], c['author'], c['rating'], c['ratings_count'], c['genres'], c['url'], c['cover_url']) == ('hardcover', '375699', 'Trevor Noah', 4.5, 1194, ['Memoir', 'Humor'], 'https://hardcover.app/books/born-a-crime', 'https://assets.hardcover.app/x.jpg')
    call = fake_urlopen.calls[0]
    assert call['url'] == hardcover.API and call['headers']['authorization'] == 'Bearer hc_test' and call['body']['variables'] == dict(q='Born a Crime Trevor Noah', n=8) and 'query_type: "Book"' in call['body']['query']
    a = p.by_asin('B01IW9TQPK'); assert (a['title'], a['author'], a['cover_url']) == ('Via ASIN', 'A B', 'https://i/c.jpg') and p.by_asin('B000MISSING') is None
    assert p.cover_url('375699') == 'https://i/cover.jpg' and p.search('x') == []
    d = p.details('375699')
    assert (d['rating'], d['ratings_count'], d['reviews_count'], d['readers'], d['genres'], d['moods'], d['content_warnings']) == (4.5, 1194, 164, 2262, ['Biography', 'Memoir'], ['funny'], ['Racism'])      # most-used tags first; spoiler-heavy tags hidden, except warnings
    assert d['tags'] == [] and d['series'] == [dict(name='Noah', position=1)] and d['audiobook_editions'][0] == dict(asin='B01IW9TQPK', hours=8.7, publisher='Audible Studios', released='2016-11-15', format='Audible.com')
    assert d['reviews'] == [dict(user='reader', rating=5.0, text='x' * 3000, spoilers=True, likes=7, date='2020-01-02')] and p.details('375699') == d             # the second call is cached


def test_hardcover_errors_and_key(fake_urlopen, monkeypatch, tmp_path):
    from app.providers import hardcover
    p = hardcover.Hardcover()
    fake_urlopen.reply(dict(errors=[dict(message='bad query')]), None, dict(data=dict(books=[])))
    for t, msg in (('aaa', 'Hardcover: bad query'), ('bbb', 'returned nothing')):
        with pytest.raises(MatchError, match=msg): p.search(t)
    assert p.details('5') is None
    assert hardcover.hit_to_candidate(dict(id=1, series_names=['S'], featured_series=dict(position=2.5), image=None))['series_position'] == 2.5
    monkeypatch.delenv('APP_HARDCOVER_KEY'); monkeypatch.setenv('HOME', str(tmp_path)); monkeypatch.setenv('USERPROFILE', str(tmp_path))
    assert p.unavailable() and hardcover.key() == ''
    with pytest.raises(MatchError, match='no Hardcover API token'): p.search('Another')
    (tmp_path / '.config' / 'strata-listory').mkdir(parents=True); (tmp_path / '.config' / 'strata-listory' / 'hardcover.key').write_text('Bearer hc_file\n')
    assert hardcover.key() == 'hc_file' and p.unavailable() is None


OL_DOC = dict(key='/works/OL1W', title='Brave New World', author_name=['Aldous Huxley'], first_publish_year=1932, ratings_average=3.97, ratings_count=483, subject=['Utopias', 'Brainwashing'] + [f's{i}' for i in range(40)], edition_count=300, cover_i=8231823)


@pytest.mark.year_lookup
def test_openlibrary_search_gives_the_first_published_year_and_is_not_a_default_matcher(fake_urlopen):
    from app.providers import openlibrary
    p = openlibrary.OpenLibrary(); fake_urlopen.reply(dict(docs=[OL_DOC]), dict(docs=[]))
    c = p.search('Brave New World', 'Aldous Huxley')[0]
    assert (c['source'], c['original_year'], c['rating'], c['ratings_count'], c['author'], c['edition_count'], c['url']) == ('openlibrary', 1932, 3.97, 483, 'Aldous Huxley', 300, 'https://openlibrary.org/works/OL1W')
    assert c['cover_url'] == 'https://covers.openlibrary.org/b/id/8231823-L.jpg' and len(c['subjects']) == 25 and c['genres'][:2] == ['Utopias', 'Brainwashing'] and len(c['genres']) == 8
    call = fake_urlopen.calls[0]; assert call['url'].startswith('https://openlibrary.org/search.json?') and 'title=Brave+New+World' in call['url'] and 'author=Aldous+Huxley' in call['url'] and 'strata-listory' in call['headers']['user-agent']
    assert p.search('Nothing Here') == [] and p.search('x') == [] and openlibrary.to_candidate({})['cover_url'] == ''
    assert 'openlibrary' not in providers.configured() and providers.get('openlibrary').unavailable() is None and 'openlibrary' in [s['name'] for s in providers.status()]
