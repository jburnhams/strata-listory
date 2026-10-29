import sqlite3, time
import pytest
from fakes import http_error
from app import catalogue, db, matcher


def raw(title, authors=('Frank Herbert',), series=None, ordinal='1', subtitle='', id='x1'):
    return dict(id=id, title=title, subtitle=subtitle, people=[dict(person=dict(name=a), role=dict(name='Author')) for a in authors] + [dict(person=dict(name='Some Narrator'), role=dict(name='Narrator'))],
                **(dict(seriesWithId=[dict(title=series, ordinal=ordinal)]) if series else {}))


class FakeSource:
    def __init__(self, by_title=None, by_asin=None): self.by_title, self.asins, self.queries = by_title or {}, by_asin or {}, []
    def search(self, title): self.queries.append(title); return [matcher.candidate(r) for r in self.by_title.get(title, [])]
    def by_asin(self, asin): return matcher.candidate(self.asins[asin]) if asin in self.asins else None


@pytest.fixture(autouse=True)
def fast(monkeypatch):
    monkeypatch.setattr(matcher, 'MIN_INTERVAL', 0); monkeypatch.setenv('APP_ADB_KEY', 'adb_test')
    monkeypatch.setattr(time, 'sleep', lambda s: None)


def test_candidate_picks_authors_and_series():
    c = matcher.candidate(raw('Flashman', ('George MacDonald Fraser',), series='The Flashman Papers', ordinal='2'))
    assert (c['author'], c['series'], c['series_position']) == ('George MacDonald Fraser', 'The Flashman Papers', 2.0)
    assert matcher.candidate(raw('Standalone'))['series'] == '' and matcher.candidate(raw('X', series='S', ordinal='?'))['series_position'] is None


def test_scoring_title_and_author():
    c = matcher.candidate(raw('Dune', ('Frank Herbert',)))
    assert matcher.score(dict(title='Dune', author='Frank Herbert'), c) == 1.0
    assert matcher.score(dict(title='Dune', author=''), c) == 0.85                       # unknown author: neutral
    assert matcher.score(dict(title='Dune', author='Someone Else'), c) == 0.7             # right title, wrong author
    assert matcher.score(dict(title='Dune', author='Herbert, Frank'), c) == 1.0
    sub = matcher.candidate(raw('The Great Gatsby', ('F. Scott Fitzgerald',), subtitle='A Novel'))
    assert matcher.title_score('Great Gatsby: A Novel (Unabridged)', sub) > 0.9
    assert matcher.title_score('Completely Different', c) < 0.4
    assert matcher.author_score('Frank Herbert and Brian Herbert', matcher.candidate(raw('x', ('Frank Herbert', 'Brian Herbert')))) == 1.0


def test_match_book_prefers_asin_then_best_title():
    src = FakeSource({'Dune': [raw('Dune Messiah', id='b'), raw('Dune', id='a')], 'Nothing Like It': [raw('Totally Unrelated', ('X Y',))]}, {'B000000001': raw('Dune', id='asin-hit')})
    sug, sc = matcher.match_book(dict(title='Dune', author='Frank Herbert', asin=''), src)
    assert (sug['id'], sug['matched_by'], sc) == ('a', 'title', 1.0)
    sug, sc = matcher.match_book(dict(title='whatever', author='', asin='B000000001'), src)
    assert (sug['id'], sug['matched_by'], sc) == ('asin-hit', 'asin', 1.0)
    sug, sc = matcher.match_book(dict(title='Dune', author='', asin='B999999999'), src)       # unknown ASIN falls back to the title search
    assert sug['id'] == 'a'
    assert matcher.match_book(dict(title='Nothing Like It', author='', asin=''), src) == ({}, None)


def test_match_pending_records_suggestions_and_skips_radio_and_already_tried():
    a = catalogue.add_scanned([dict(path='/a', title='Dune', author='Frank Herbert', narrator='', series_position=None, asin='', kind='book', file_count=3),
                               dict(path='/b', title='Nope', author='', narrator='', series_position=None, asin='', kind='book', file_count=1),
                               dict(path='/c', title='Episode 1', author='', narrator='', series_position=None, asin='', kind='radio', file_count=1)])
    assert a == dict(added=3, existing=0)
    src = FakeSource({'Dune': [raw('Dune', series='Dune Chronicles')]})
    assert matcher.match_pending(10, src) == dict(tried=2, suggested=1, none=1, remaining=0)
    by = {b['title']: b for b in catalogue.list_books()}
    assert by['Dune']['review'] == 'suggested' and by['Dune']['suggestion']['series'] == 'Dune Chronicles' and by['Dune']['match_score'] == 1.0
    assert by['Nope']['review'] == 'new' and by['Nope']['suggestion'] == {}                 # tried, nothing found
    assert by['Episode 1']['suggestion'] is None                                             # radio is never looked up
    assert matcher.match_pending(10, src)['tried'] == 0


def test_confirm_applies_the_suggestion_and_ignore_marks_unwanted():
    catalogue.add_scanned([dict(path='/a', title='dune', author='', narrator='', series_position=None, asin='', kind='book', file_count=1)])
    b = catalogue.list_books()[0]
    catalogue.set_suggestion(b['id'], dict(title='Dune', author='Frank Herbert', series='Dune Chronicles', series_position=1.0), 0.9)
    got = catalogue.confirm(b['id'])
    assert (got['title'], got['author'], got['series'], got['series_position'], got['review']) == ('Dune', 'Frank Herbert', 'Dune Chronicles', 1.0, 'confirmed')
    assert catalogue.ignore(b['id'])['review'] == 'ignored'
    assert catalogue.confirm(catalogue.add_book(title='Hand entered')['id'])['title'] == 'Hand entered'      # nothing to apply


def test_add_scanned_is_idempotent_and_refreshes_file_counts():
    k = dict(path='/a', title='', author='', narrator='', series_position=None, asin='', kind='book', file_count=1)
    catalogue.add_scanned([k]); assert catalogue.add_scanned([dict(k, file_count=5)]) == dict(added=0, existing=1)
    b = catalogue.list_books()[0]
    assert b['file_count'] == 5 and b['title'] == 'a' and b['review'] == 'new'         # falls back to the file name when nothing could be parsed


def test_list_filters_by_review_and_kind():
    catalogue.add_book(title='Mine')
    catalogue.add_scanned([dict(path='/r', title='Ep', author='', narrator='', series_position=None, asin='', kind='radio', file_count=1)])
    assert [b['title'] for b in catalogue.list_books(review='new')] == ['Ep']
    assert [b['title'] for b in catalogue.list_books(kind='book')] == ['Mine']
    with pytest.raises(catalogue.CatalogueError, match='review must be one of'): catalogue.add_book(title='x', review='maybe')


def test_search_is_cached_and_sends_key_and_user_agent(fake_urlopen):
    fake_urlopen.reply([raw('Dune')])
    src = matcher.AudiobookDB()
    assert [c['title'] for c in src.search('Dune')] == ['Dune'] and [c['title'] for c in src.search('dune')] == ['Dune']      # second call from the cache
    [call] = fake_urlopen.calls
    assert call['url'].endswith('/api/search') and call['body'] == dict(q='Dune', type='books') and call['headers']['x-api-key'] == 'adb_test' and 'strata-listory' in call['headers']['user-agent']
    assert src.search('ab') == []                                                            # too short for the service: not even asked


def test_cache_expires_after_a_week(fake_urlopen):
    fake_urlopen.reply([raw('Dune')], [raw('Dune Messiah')])
    src = matcher.AudiobookDB(); src.search('Dune')
    c = db.connect()
    with c: c.execute("UPDATE match_cache SET fetched='2000-01-01T00:00:00+00:00'")
    c.close()
    assert src.search('Dune')[0]['title'] == 'Dune Messiah'


def test_asin_lookup_and_not_found(fake_urlopen):
    fake_urlopen.reply(raw('Dune'), http_error(404))
    src = matcher.AudiobookDB()
    assert src.by_asin('B000000001')['title'] == 'Dune' and src.by_asin('B000000002') is None
    assert fake_urlopen.calls[0]['url'].endswith('/audiobooks/external/audible/B000000001') and fake_urlopen.calls[0]['method'] == 'GET'


def test_errors_are_reported_clearly(fake_urlopen, monkeypatch, tmp_path):
    fake_urlopen.reply(http_error(500), OSError('down'), http_error(429), http_error(429))
    src = matcher.AudiobookDB()
    for q, msg in (('aaa', 'answered 500'), ('bbb', 'unreachable'), ('ccc', 'answered 429')):
        with pytest.raises(matcher.MatchError, match=msg): src.search(q)
    fake_urlopen.reply([raw('Dune')]).reply(http_error(429), [raw('Dune')])               # a 429 is retried once
    assert src.search('ddd')[0]['title'] == 'Dune' and src.search('eee')[0]['title'] == 'Dune'
    fake_urlopen.reply(http_error(503), http_error(503), [raw('Dune')], http_error(503), http_error(503), http_error(503))      # an outage is retried twice, then reported
    assert src.search('fff')[0]['title'] == 'Dune'
    with pytest.raises(matcher.MatchError, match='answered 503'): src.search('ggg')
    monkeypatch.delenv('APP_ADB_KEY'); monkeypatch.setenv('HOME', str(tmp_path))
    with pytest.raises(matcher.MatchError, match='no AudiobookDB API key'): matcher.key()
    (tmp_path / '.config' / 'strata-listory').mkdir(parents=True); (tmp_path / '.config' / 'strata-listory' / 'audiobookdb.key').write_text(' adb_file \n')
    assert matcher.key() == 'adb_file'


def test_migration_from_the_first_schema_keeps_data_and_backs_up():
    from app import backup
    c = sqlite3.connect(db.path()); c.execute(db.SCHEMA[0]); c.execute("INSERT INTO books (title, added) VALUES ('Old', 'x')"); c.execute('PRAGMA user_version=1'); c.commit(); c.close()
    b = catalogue.list_books()[0]
    assert b['title'] == 'Old' and b['review'] == 'confirmed' and b['kind'] == 'book' and b['suggestion'] is None
    assert [x['reason'] for x in backup.list_backups() if x['reason']] == ['pre-migration']
