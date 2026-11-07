from app.identify import model


def rows(*spec):
    return [dict(run_id=r, cgroup=g, work=g, label=int(l), origin=o, features={'title_exact': 1}, split='train') for r, g, l, o in spec]


def test_by_query_groups_duplicates_and_ranks():
    r = rows(('q', 'a', 1, 'manual'), ('q', 'a', 1, 'manual'), ('q', 'b', 0, 'manual'))
    queries = model.by_query(r, [.4, .9, .95])
    assert queries == {'q': [(.95, False, 'manual'), (.9, True, 'manual')]}


def test_decide_threshold_and_margin():
    ranked = [(.95, True, 'automatic'), (.9, False, 'automatic')]
    assert model.decide(ranked, .9, .0) == ('accept', True)
    assert model.decide(ranked, .9, .1) == ('review', True)
    assert model.decide(ranked, .99, 0.) == ('review', True)
    assert model.decide([], .5, 0.) == ('review', False)
    assert model.decide([(.8, False, 'manual')], .5, .5) == ('accept', False)


def test_evaluate_and_choose_requires_zero_false_positives():
    queries = {'1': [(.99, True, 'manual'), (.1, False, 'manual')], '2': [(.7, False, 'automatic'), (.65, True, 'automatic')], '3': [(.97, True, 'automatic')]}
    r = model.evaluate(queries, .5, 0.)
    assert (r['accepted'], r['false_positives'], r['manual_accepted'], r['manual_false_positives']) == (3, 1, 1, 0)
    t, m, result = model.choose(queries)
    assert result['false_positives'] == 0 and result['accepted'] == 2 and m >= .1
    assert model.choose({'x': [(.99, False, 'manual')]}) is None


def test_matrix_uses_stable_names_and_fills_missing():
    X, names = model.matrix(rows(('q', 'a', 1, 'manual')))
    assert len(X[0]) == len(names) and X[0][names.index('title_exact')] == 1.0 and sum(X[0]) == 1.0
    assert set(model.split_rows(rows(('q', 'a', 1, 'manual')))) == {'train', 'calibration', 'test'}
