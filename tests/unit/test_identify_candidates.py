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
    rows = ft.add_context([(d, good), (dict(d, key='/works/OL5W', title='Other', titles={('other', 0): 1}), bad)])
    assert rows[0][1]['is_top_group'] == 1 and rows[0][1]['score_margin'] > 0 > rows[1][1]['score_margin']
    assert ft.add_context([]) == [] and 'title_best_sim' in ft.feature_names() and ft.feature_names() == sorted(ft.feature_names())


def test_cluster_ids_merge_same_title_shared_author_only():
    def work(key, title, *authors): return dict(key=key, title=title, authors=[dict(key=a, name=a) for a in authors])
    ids = ft.cluster_ids([work('1', 'North', 'a'), work('2', 'north', 'a', 'b'), work('3', 'North', 'c'), work('4', 'North'), work('5', 'North')])
    assert ids[0] == ids[1] and len({ids[0], ids[2], ids[3], ids[4]}) == 4
