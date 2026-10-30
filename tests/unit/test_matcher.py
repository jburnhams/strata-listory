import sqlite3
import pytest
from app import backup, catalogue, db, matcher
from app.providers.base import MatchError, candidate


def cand(title, authors=('Frank Herbert',), source='fake', asin='', **kw):
    return candidate(source, id=kw.pop('id', title), asin=asin, title=title, authors=list(authors), **kw)


class Fake:
    """A scripted provider: `titles` maps a searched title to candidates, `asins` maps an ASIN to one."""
    def __init__(self, name='fake', titles=None, asins=None, fail=None):
        self.name, self.titles, self.asins, self.fail, self.searched = name, titles or {}, asins or {}, fail, []
    def unavailable(self): return None
    def by_asin(self, asin):
        if self.fail: raise MatchError(self.fail)
        return self.asins.get(asin)
    def search(self, title, author=''):
        if self.fail: raise MatchError(self.fail)
        self.searched.append((title, author)); return self.titles.get(title, [])


def test_scoring_title_and_author():
    c = cand('Dune')
    assert matcher.score(dict(title='Dune', author='Frank Herbert'), c) == 1.0
    assert matcher.score(dict(title='Dune', author=''), c) == 0.85                       # unknown author: neutral
    assert matcher.score(dict(title='Dune', author='Someone Else'), c) == 0.7             # right title, wrong author
    assert matcher.score(dict(title='Dune', author='Herbert, Frank'), c) == 1.0
    sub = cand('The Great Gatsby', ('F. Scott Fitzgerald',), subtitle='A Novel')
    assert matcher.title_score('Great Gatsby: A Novel (Unabridged)', sub) > 0.9
    assert matcher.title_score('Completely Different', c) < 0.4
    assert matcher.author_score('Frank Herbert and Brian Herbert', cand('x', ('Frank Herbert', 'Brian Herbert'))) == 1.0
    assert matcher.author_score('Frank Herbart', c) == 0.3


def test_asin_wins_in_provider_order_then_falls_back_to_search():
    a = Fake('a', asins={'B000000001': cand('Dune', source='a', asin='B000000001')}); b = Fake('b', asins={'B000000001': cand('Dune (other)', source='b')}, titles={'Dune': [cand('Dune', source='b', id='found')]})
    sug, sc = matcher.match_book(dict(title='x', author='', asin='B000000001'), [a, b])
    assert (sug['source'], sug['matched_by'], sug['sources'], sc) == ('a', 'asin', ['a'], 1.0) and not a.searched
    sug, sc = matcher.match_book(dict(title='Dune', author='Frank Herbert', asin='B999999999'), [a, b])         # unknown ASIN: search instead
    assert sug['id'] == 'found' and sug['matched_by'] == 'title'


def test_search_asks_providers_in_order_and_stops_early_on_a_certain_answer():
    a = Fake('a', titles={'Dune': [cand('Dune', source='a')]}); b = Fake('b', titles={'Dune': [cand('Dune', source='b')]})
    sug, sc = matcher.match_book(dict(title='Dune', author='Frank Herbert', asin=''), [a, b])
    assert sc == 1.0 and sug['sources'] == ['a'] and b.searched == []                                 # a perfect first answer: no need to ask b
    a = Fake('a', titles={'Dune': [cand('Dune', source='a')]}); b = Fake('b', titles={'Dune': [cand('Dune', source='b', narrators=['Scott Brick'], runtime_minutes=1260)]})
    sug, sc = matcher.match_book(dict(title='Dune', author='', asin=''), [a, b])                       # author unknown: 0.85, so ask b too
    assert b.searched and sug['sources'] == ['a', 'b'] and sc == 0.9                                  # agreement earns a bonus
    assert sug['narrators'] == ['Scott Brick'] and sug['runtime_minutes'] == 1260                    # and the missing fields are filled in from b


def test_best_candidate_wins_alternatives_are_kept_and_weak_ones_dropped():
    p = Fake(titles={'Dune': [cand('Dune Messiah', id='m'), cand('Dune', id='d'), cand('Children of Dune', id='c'), cand('Zebra', ('Z Z',), id='z')]})
    sug, sc = matcher.match_book(dict(title='Dune', author='Frank Herbert', asin=''), [p])
    assert sug['id'] == 'd' and [a['title'] for a in sug['alternatives']] == ['Dune Messiah']          # Children of Dune is too weak to show
    assert matcher.match_book(dict(title='Dune', author='', asin=''), [Fake(titles={'Dune': [cand('Totally Unrelated', ('X Y',))]})]) == ({}, None)
    assert matcher.match_book(dict(title='Nothing', author='', asin=''), [Fake()]) == ({}, None)


def test_failures_are_skipped_unless_every_provider_fails():
    down = Fake('down', fail='Down answered 503'); ok = Fake('ok', titles={'Dune': [cand('Dune')]})
    assert matcher.match_book(dict(title='Dune', author='Frank Herbert', asin='B000000001'), [down, ok])[1] == 1.0
    with pytest.raises(MatchError, match='Down answered 503'): matcher.match_book(dict(title='Dune', author='', asin=''), [down])
    with pytest.raises(MatchError, match='no metadata provider'): matcher.match_book(dict(title='Dune', author='', asin=''), [])


def test_match_pending_records_suggestions_and_skips_radio_and_already_tried():
    k = dict(narrator='', series_position=None, asin='', file_count=1)
    assert catalogue.add_scanned([dict(k, path='/a', title='Dune', author='Frank Herbert', kind='book'), dict(k, path='/b', title='Nope', author='', kind='book'), dict(k, path='/c', title='Episode 1', author='', kind='radio')])['added'] == 3
    p = Fake(titles={'Dune': [cand('Dune', series='Dune Chronicles')]})
    assert matcher.match_pending(10, [p]) == dict(tried=2, suggested=1, none=1, remaining=0, providers=['fake'])
    by = {b['title']: b for b in catalogue.list_books()}
    assert by['Dune']['review'] == 'suggested' and by['Dune']['suggestion']['series'] == 'Dune Chronicles' and by['Dune']['match_score'] == 1.0
    assert by['Nope']['review'] == 'new' and by['Nope']['suggestion'] == {} and by['Episode 1']['suggestion'] is None
    assert matcher.match_pending(10, [p])['tried'] == 0


def test_evaluate_compares_providers_alone_and_combined():
    books = [dict(title='Dune', author='Frank Herbert', asin=''), dict(title='Emma', author='Jane Austen', asin=''), dict(title='Gone', author='', asin='')]
    a = Fake('a', titles={'Dune': [cand('Dune')]}); b = Fake('b', titles={'Emma': [cand('Emma', ('Jane Austen',))], 'Gone': [cand('Gone Girl', ('X Y',))]}); down = Fake('d', fail='503')
    r = matcher.evaluate(books, [a, b])
    assert r['a'] == dict(books=3, good=1, some=1, errors=0) and r['b'] == dict(books=3, good=1, some=1, errors=0) and r['combined'] == dict(books=3, good=2, some=2, errors=0)
    assert matcher.evaluate(books, [down])['d']['errors'] == 3 and 'combined' not in matcher.evaluate(books, [a])


def test_confirm_applies_the_suggestion_and_ignore_marks_unwanted():
    catalogue.add_scanned([dict(path='/a', title='dune', author='', narrator='', series_position=None, asin='', kind='book', file_count=1)])
    b = catalogue.list_books()[0]
    catalogue.set_suggestion(b['id'], dict(title='Dune', author='Frank Herbert', series='Dune Chronicles', series_position=1.0, asin='B0TEST0001', narrators=['Scott Brick', 'Orlagh Cassidy'], runtime_minutes=1260), 0.9)
    got = catalogue.confirm(b['id'])
    assert (got['title'], got['author'], got['series'], got['series_position'], got['review']) == ('Dune', 'Frank Herbert', 'Dune Chronicles', 1.0, 'confirmed')
    assert (got['asin'], got['narrator'], got['duration_seconds']) == ('B0TEST0001', 'Scott Brick, Orlagh Cassidy', 75600)
    assert catalogue.ignore(b['id'])['review'] == 'ignored'
    assert catalogue.confirm(catalogue.add_book(title='Hand entered')['id'])['title'] == 'Hand entered'      # nothing to apply
    c = catalogue.add_book(title='Timed', duration_seconds=100); catalogue.set_suggestion(c['id'], dict(title='Timed', runtime_minutes=5), 1)
    assert catalogue.confirm(c['id'])['duration_seconds'] == 100                                          # a known duration is kept


def test_add_scanned_is_idempotent_refreshes_file_counts_and_replaces_fragments():
    k = dict(path='/a', title='', author='', narrator='', series_position=None, asin='', kind='book', file_count=1)
    catalogue.add_scanned([k]); assert catalogue.add_scanned([dict(k, file_count=5)]) == dict(added=0, existing=1, replaced=0)
    b = catalogue.list_books()[0]
    assert b['file_count'] == 5 and b['title'] == 'a' and b['review'] == 'new'         # falls back to the file name when nothing could be parsed


def test_rescan_replaces_untouched_chapter_fragments_with_the_folder(tmp_path):
    folder = tmp_path / 'My_Book'; folder.mkdir()
    k = dict(title='', author='', narrator='', series_position=None, asin='', kind='book', file_count=1)
    catalogue.add_scanned([dict(k, path=str(folder / f'{i}.mp3')) for i in range(3)] + [dict(k, path=str(tmp_path / 'My_Book2' / 'x.mp3'))])
    keep = catalogue.list_books(query='1')[0]; catalogue.confirm(keep['id'])            # the user already confirmed one fragment
    res = catalogue.add_scanned([dict(k, path=str(folder), file_count=3, title='My Book')])
    assert res == dict(added=1, existing=0, replaced=2)
    assert sorted(b['title'] for b in catalogue.list_books()) == ['1.mp3', 'My Book', 'x.mp3']       # confirmed work and unrelated look-alike paths are untouched


def test_list_filters_by_review_and_kind():
    catalogue.add_book(title='Mine')
    catalogue.add_scanned([dict(path='/r', title='Ep', author='', narrator='', series_position=None, asin='', kind='radio', file_count=1)])
    assert [b['title'] for b in catalogue.list_books(review='new')] == ['Ep']
    assert [b['title'] for b in catalogue.list_books(kind='book')] == ['Mine']
    with pytest.raises(catalogue.CatalogueError, match='review must be one of'): catalogue.add_book(title='x', review='maybe')


def test_migration_from_the_first_schema_keeps_data_and_backs_up():
    c = sqlite3.connect(db.path()); c.execute(db.SCHEMA[0]); c.execute("INSERT INTO books (title, added) VALUES ('Old', 'x')"); c.execute('PRAGMA user_version=1'); c.commit(); c.close()
    b = catalogue.list_books()[0]
    assert b['title'] == 'Old' and b['review'] == 'confirmed' and b['kind'] == 'book' and b['suggestion'] is None
    assert [x['reason'] for x in backup.list_backups() if x['reason']] == ['pre-migration']
