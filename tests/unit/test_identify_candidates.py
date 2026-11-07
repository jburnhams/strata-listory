from identify_dumps import built
from app.identify import candidates as cand, features as ft


def test_find_by_title_author_isbn_and_redirect(tmp_path):
    with_ = cand.connect(built(tmp_path))
    assert cand.find(with_, 'The Hobbit', 'Tolkien, J.R.R.')['/works/OL1W'] >= {'title+author'}
    assert 'title+author' in cand.find(with_, 'Hobbit (Unabridged)', 'John Ronald Reuel Tolkien')['/works/OL1W']
    assert cand.find(with_, 'Unrelated', '', isbns=['0261102214'])['/works/OL1W'] == {'isbn'}
    assert '/works/OL1W' in cand.find(with_, 'The Hobbit')  # no author: best-attested title hits
    assert cand.find(with_, 'The Hobbit', 'Nobody Else')['/works/OL1W'] == {'title'}
    assert 'author-fuzzy' in cand.find(with_, 'Hobbit Der', 'J.R.R. Tolkien')['/works/OL1W']
    assert cand.find(with_, 'The Hobbit', 'Tolkien')['/works/OL1W'] >= {'title+surname'}
    assert cand.homonyms(with_, 'The Hobbit') == 1 and cand.homonyms(with_, '') == 0 and cand.homonyms(with_, 'nothing here') == 0
    assert cand.is_person_name(with_, 'J.R.R. Tolkien') and not cand.is_person_name(with_, 'The Hobbit') and not cand.is_person_name(with_, 'Tolkien') and not cand.is_person_name(with_, '')
    assert cand.canonical(with_, ['/works/OL2W', '/works/OL1W']) == {'/works/OL2W': '/works/OL1W', '/works/OL1W': '/works/OL1W'}


def test_describe_and_features(tmp_path):
    db = cand.connect(built(tmp_path)); d = cand.describe(db, '/works/OL1W')
    assert cand.describe(db, '/works/OL9W') is None
    assert d['editions'] == 2 and d['authors'][0]['name'] == 'J. R. R. Tolkien' and d['series'] == ['middle earth']
    good = ft.item_features(dict(title='The Hobbit', author='J.R.R. Tolkien', isbns=['9780261102217'], series='Middle-earth'), d, {'title+author', 'isbn'})
    bad = ft.item_features(dict(title='Something Else', author='Jane Roe'), d, {'title'})
    assert good['title_exact'] == 1 and good['author_exact_all'] == 1 and good['isbn_match'] == 1 and good['series_match'] == 1
    assert bad['title_exact'] == 0 and bad['author_match_share'] == 0 and bad['via_title'] == 1
    surname = ft.item_features(dict(title='The Hobbit', author='Tolkien'), d, {'title+surname'})
    assert surname['author_single_token'] == 1 and surname['author_surname_share'] == 1 and surname['author_exact_all'] == 0
    assert ft.item_features(dict(title='x', author='John Tolkien'), d, set())['author_first_name_share'] == 0 and ft.item_features(dict(title='x', author='J. Tolkien'), d, set())['author_initial_share'] == 1
    generic = ft.item_features(dict(title='Census'), dict(d, homonyms=400), set())
    assert generic['title_generic_no_author'] == 1 and generic['title_homonyms_log'] > 5 and good['title_generic_no_author'] == 0
    rows = ft.add_context([(d, good), (dict(d, key='/works/OL5W', title='Other', titles={('other', 0): 1}), bad)])
    assert rows[0][1]['is_top_group'] == 1 and rows[0][1]['score_margin'] > 0 > rows[1][1]['score_margin']
    assert ft.add_context([]) == [] and 'title_best_sim' in ft.feature_names() and ft.feature_names() == sorted(ft.feature_names())


def test_cluster_ids_merge_same_title_shared_author_only():
    def work(key, title, *authors): return dict(key=key, title=title, authors=[dict(key=a + key, name=a) for a in authors])
    ids = ft.cluster_ids([work('1', 'North', 'a'), work('2', 'north', 'a', 'b'), work('3', 'North', 'c'), work('4', 'North'), work('5', 'North')])
    assert ids[0] == ids[1] and len({ids[0], ids[2], ids[3], ids[4]}) == 4


def test_readings_cover_filename_styles():
    from app.identify import local
    assert [r['reading'] for r in local.readings(dict(title='The Red Dragon CD01 07', author=''))] == ['raw', 'stripped']
    got = {(r['reading'], r['title'], r['author']) for r in local.readings(dict(title="Jonathan Coe - The Rotters' Club 09", author=''))}
    assert {('stripped', "Jonathan Coe - The Rotters' Club", ''), ('author_title', "The Rotters' Club", 'Jonathan Coe'), ('title_author', 'Jonathan Coe', "The Rotters' Club")} <= got
    only = local.readings(dict(title='Dune', author='Unknown Artist'))
    assert [r['reading'] for r in only] == ['raw'] and only[0]['author'] == ''
    assert local.strip_numbers("Summer's Lease 13 of 16") == "Summer's Lease"
    assert local.strip_numbers('1984') == '' and local.strip_numbers('Apollo 13') == 'Apollo'
    known = local.readings(dict(title='Author One - Title', author='Author One'))
    assert {r['reading'] for r in known} == {'raw', 'author_title'}


def test_sibling_context_counts_numbered_parts():
    from app.identify import context
    rows = [(f'/a/Red Dragon/The Red Dragon CD01 {n:02}.mp3', f'The Red Dragon CD01 {n:02}') for n in range(1, 5)] + [('/a/Red Dragon/Other.mp3', 'Other')]
    index = context.Index(rows)
    got = index.annotate(dict(title='The Red Dragon CD01 03', path=rows[2][0]), members=1)
    assert got['siblings'] == 4 and got['folder_files'] == 5 and got['numbered'] == 1
    single = index.annotate(dict(title='Other', path=rows[4][0]), members=3)
    assert single['siblings'] == 1 and single['members'] == 3 and single['numbered'] == 0
    f = ft.item_features(got, dict(key='k', title='t', subtitle='', year=1, work_year=1, editions=1, audio_editions=0, ratings=0, rating_average=None, authors=[], titles={}, series=[], isbns=[], subjects=[]), set())
    assert f['local_numbered'] == 1 and f['local_siblings_log'] > f['local_members_log']
