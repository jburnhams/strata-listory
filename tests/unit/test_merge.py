import pytest
from app import merge as M


def test_names_titles_and_people():
    assert M.norm('The  Great Gatsby (Unabridged)!') == 'the great gatsby' and M.norm('Café & Co') == 'cafe and co' and M.norm(None) == ''
    assert M.main_title('Banking on It: How I Disrupted an Industry') == 'banking on it' and M.main_title('Dune [B0X]') == 'dune' and M.main_title('Title - Subtitle') == 'title'
    f, s, b = M.person_key('F. Scott Fitzgerald'), M.person_key('Scott Fitzgerald'), M.person_key('Frank Herbert')
    assert M.same_person(f, s) and not M.same_person(M.person_key('Frank Herbert'), M.person_key('Brian Herbert')) and M.same_person(M.person_key('Herbert'), b) and not M.same_person(b, M.person_key('Frank Smith'))


def test_vote_picks_the_heaviest_agreement_and_keeps_what_lost():
    r = M.vote([('A', 'audnexus', 2.5), ('B', 'audiobookdb', 1.0), ('A', 'audible', 2.0)])
    assert r['value'] == 'A' and r['sources'] == ['audnexus', 'audible'] and r['alternatives'] == [dict(value='B', sources=['audiobookdb'])] and r['confidence'] == 0.82
    assert M.vote([]) is None and M.vote([('', 'x', 1), (None, 'y', 1), ([], 'z', 1)]) is None
    assert M.vote([('x', 'a', 1)])['confidence'] == 1.0


def test_titles_agree_on_the_main_part_and_the_cleanest_wording_wins():
    r = M.vote_title([('Banking on It: How I Disrupted an Industry (Unabridged)', 'audible_file', 3.0), ('Banking on It', 'openlibrary', 2.5), ('Banking on It: A Memoir', 'hardcover', 2.0), ('Something Else', 'audiobookdb', 1.0)])
    assert r['value'] == 'Banking on It' and r['sources'] == ['audible_file', 'openlibrary', 'hardcover'] and r['alternatives'][0]['value'] == 'Something Else'


def test_people_are_kept_by_agreement_and_one_source_cannot_add_a_stranger():
    r = M.vote_people([(['CBS Radio Workshop', 'Aldous Huxley'], 'audiobookdb', 1.0), (['Aldous Huxley'], 'audible_file', 3.0), (['Aldous Huxley'], 'openlibrary', 2.5), (['Aldous Huxley'], 'hardcover', 2.0)])
    assert r['value'] == ['Aldous Huxley'] and r['alternatives'] == [dict(value=['CBS Radio Workshop'], sources=['audiobookdb'])]
    both = M.vote_people([(['Neil Gaiman', 'Terry Pratchett'], 'audible_file', 3.0), (['Terry Pratchett', 'Neil Gaiman'], 'hardcover', 2.0)])
    assert both['value'] == ['Neil Gaiman', 'Terry Pratchett']                                                         # both agreed on: both kept, the heaviest source's order
    assert M.vote_people([(['Only Source'], 'audible', 2.0)])['value'] == ['Only Source'] and M.vote_people([([], 'x', 1)]) is None
    full = M.vote_people([(['F. Scott Fitzgerald'], 'openlibrary', 2.5), (['Scott Fitzgerald'], 'audible', 2.0)]); assert full['value'] == ['F. Scott Fitzgerald'] and len(full['sources']) == 2     # one person, two spellings
    split = M.vote_people([(['A One', 'B Two'], 'a', 1.5), (['C Three'], 'b', 1.0), (['D Four'], 'c', 1.0), (['E Five'], 'd', 1.0), (['F Six'], 'e', 1.0)])
    assert split['value'] == ['A One', 'B Two'] and len(split['alternatives']) == 4                                       # nobody is named by enough sources: the heaviest source's list is trusted
    assert M.vote_people([(['A One'], 'a', 1.0), (['B Two'], 'b', 5.0)])['value'] == ['B Two']


def test_lists_are_ranked_by_how_many_sources_name_each_item():
    r = M.vote_list([(['Utopias', 'Brainwashing'], 'openlibrary', 2.5), (['dystopian', 'utopias'], 'hardcover', 2.0), ([], 'x', 1)], 3)
    assert r['value'] == ['Utopias', 'Brainwashing', 'dystopian'] and r['sources'] == ['openlibrary', 'hardcover'] and M.vote_list([([], 's', 1)]) is None


RECS = {
    'catalogue': dict(title='Brave New World', authors=['CBS Radio Workshop', 'Aldous Huxley']),
    'audible_file': dict(title='Brave New World', authors=['Aldous Huxley'], narrators=['Michael York'], publisher='Blackstone Audio, Inc.', release_year=2008, runtime_minutes=480, asin='B004EVP6BY', genres=['Classics']),
    'audnexus': dict(title='Brave New World', authors=['Aldous Huxley'], narrators=['Michael York'], publisher='Blackstone Audio, Inc.', release_year=2008, runtime_minutes=481, isbns=['9781602833821'], asin='B004EVP6BY'),
    'audiobookdb': dict(title='Brave New World', authors=['CBS Radio Workshop', 'Aldous Huxley'], narrators=['Someone Else'], publisher='Elsewhere'),
    'openlibrary': dict(title='Brave New World', authors=['Aldous Huxley'], original_year=1932, genres=['Utopias'], rating=3.97, ratings_count=483),
    'hardcover': dict(title='Brave New World', authors=['Aldous Huxley'], original_year=1932, genres=['Dystopian', 'Utopias'], rating=4.0, ratings_count=4000, description='A novel.'),
}


def test_merge_chooses_the_best_of_each_source():
    f = M.merge(RECS)['fields']
    assert f['authors']['value'] == ['Aldous Huxley'] and f['authors']['alternatives'][0]['sources'] == ['catalogue', 'audiobookdb']
    assert f['narrators']['value'] == ['Michael York'] and f['narrators']['sources'] == ['audible_file', 'audnexus'] and f['narrators']['alternatives'][0]['value'] == ['Someone Else']
    assert f['publisher']['value'] == 'Blackstone Audio, Inc.' and f['original_year']['value'] == 1932 and f['release_year']['value'] == 2008 and f['asin']['value'] == 'B004EVP6BY'
    assert f['runtime_minutes']['value'] == 480 and f['runtime_minutes']['sources'] == ['audible_file', 'audnexus']                      # 480 and 481 minutes are the same recording
    assert f['genres']['value'][:2] == ['Utopias', 'Classics'] or f['genres']['value'][0] == 'Utopias'
    assert f['isbns']['value'] == ['9781602833821'] and f['description']['value'] == 'A novel.'
    assert [r['source'] for r in M.merge(RECS)['ratings']] == ['openlibrary', 'hardcover'] and 'audiobookdb' in M.merge(RECS)['sources']


def test_only_the_right_layer_speaks_for_a_field():
    recs = {'audible_file': dict(original_year=1999, narrators=['N N'], release_year=2008), 'openlibrary': dict(original_year=1932, narrators=['Wrong Person'], release_year=1950)}
    f = M.merge(recs)['fields']
    assert f['original_year']['value'] == 1932 and f['original_year']['sources'] == ['openlibrary']                         # the audiobook file cannot say when the book was first published
    assert f['narrators']['value'] == ['N N'] and f['release_year']['value'] == 2008                                       # and a book catalogue cannot say who narrated


def test_values_you_set_by_hand_always_win_and_show_what_they_replaced():
    f = M.merge(RECS, locked={'authors': ['A. Huxley'], 'original_year': 1931, 'title': '', 'narrators': None})['fields']
    assert f['authors']['value'] == ['A. Huxley'] and f['authors']['sources'] == ['set by hand'] and f['authors']['alternatives'][0]['value'] == ['Aldous Huxley']
    assert f['original_year']['value'] == 1931 and f['title']['value'] == 'Brave New World' and f['narrators']['value'] == ['Michael York']              # empty locks are ignored


def test_merging_nothing_or_one_source_is_safe():
    assert M.merge({}) == dict(fields={}, ratings=[], sources=[])
    only = M.merge({'catalogue': dict(title='T', authors=['A B'])})['fields']; assert only['title']['value'] == 'T' and only['authors']['confidence'] == 1.0 and 'narrators' not in only
