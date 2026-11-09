"""Filtering and ordering the match queue (`app.linkage.queue`). Every title and name here is invented."""
import pytest

from app.linkage import queue as Q


def item(lid, p, *, auto=False, second=None, kind='book', status='review', added=1000., recordings=True, title='Glass Orchard', authors=('Mara Quill',), text='', others=()):
    top = dict(p=p, auto=auto, second=second, title=title, authors=list(authors), recordings=[dict(asin='B0AAAAAAA1')] if recordings else [])
    return dict(listen_id=lid, p=p, kind=kind, status=status, added=added, text=text, candidates=[top, *others])


ITEMS = [
    item(1, 0.99, auto=True, added=100.),
    item(2, 0.6, second=0.55, kind='radio_series', added=300., title='Salt Roads', authors=('Ben Ash',), text='Salt Roads Ben Ash /x/Radio/Salt Roads'),
    item(3, 0.4, recordings=False, status='identified', added=300., title='Lune', text='Lune folder', others=(dict(p=0.2, title='Moonlight Orchard', authors=['Ida Fenn']),)),
    item(4, 0.8, added=200., title=None, authors=()),
]
ids = lambda items: [it['listen_id'] for it in items]


@pytest.mark.parametrize('sort, expected', [
    ('certain', [1, 4, 2, 3]),        # highest probability first
    ('uncertain', [3, 2, 4, 1]),      # the hardest cards first
    ('recent', [3, 2, 4, 1]),         # newest first; listens added together, newest id first
    ('oldest', [1, 4, 2, 3]),
])
def test_select_orders_by_the_chosen_sort(sort, expected):
    assert ids(Q.select(ITEMS, Q.Filters(sort=sort))) == expected


@pytest.mark.parametrize('filters, expected', [
    (dict(auto='skip'), [4, 2, 3]),
    (dict(auto='only'), [1]),
    (dict(kind='radio_series'), [2]),
    (dict(status='identified'), [3]),
    (dict(certainty='sure'), [1]),
    (dict(certainty='close'), [2]),
    (dict(certainty='likely'), [4]),
    (dict(certainty='unsure'), [3]),
    (dict(audible='no'), [3]),
    (dict(audible='yes'), [1, 4, 2]),
    (dict(q='salt ash'), [2]),                   # every word, from the listen's own text
    (dict(q='  GLASS   mara '), [1]),           # or the proposed work's title and authors, any case and spacing
    (dict(q='fenn'), [3]),                       # or another candidate's
    (dict(q='salt quill'), []),
    (dict(auto='skip', sort='uncertain', audible='yes'), [2, 4]),
])
def test_select_keeps_only_items_that_pass_every_filter(filters, expected):
    assert ids(Q.select(ITEMS, Q.Filters(**filters))) == expected


@pytest.mark.parametrize('bad', [dict(sort='random'), dict(auto='maybe'), dict(certainty='certain'), dict(audible='1')])
def test_filters_refuse_values_they_do_not_know(bad):
    with pytest.raises(ValueError, match=f"{next(iter(bad))} must be one of"):
        Q.Filters(**bad)


@pytest.mark.parametrize('sort', Q.SORTS)
def test_paging_by_cursor_neither_skips_nor_repeats(sort):
    ordered = Q.select(ITEMS, Q.Filters(sort=sort)); seen = []
    page = ordered[:1]
    while page:
        seen += ids(page); page = Q.after(ordered, Q.cursor(page[-1], sort), sort)[:1]
    assert seen == ids(ordered)
    assert Q.after(ordered, Q.cursor(ordered[1], sort), sort) == ordered[2:]


def test_paging_holds_when_the_item_at_the_cursor_was_answered():
    ordered = Q.select(ITEMS, Q.Filters(sort='recent')); cur = Q.cursor(ordered[1], 'recent')        # the card the page last held is answered and leaves the queue
    assert ids(Q.after([it for it in ordered if it is not ordered[1]], cur, 'recent')) == ids(ordered[2:])


@pytest.mark.parametrize('bad', ['nonsense', '0.5', 'x:1', '0.5:y', ''])
def test_a_malformed_cursor_is_refused(bad):
    with pytest.raises(ValueError):
        Q.after(ITEMS, bad, 'certain')


def test_cursor_round_trips_the_exact_key():
    it = item(9, 0.1 + 0.2, added=1762696800.123456)
    assert Q.parse_cursor(Q.cursor(it, 'uncertain'), 'uncertain') == (0.1 + 0.2, 9)
    assert Q.parse_cursor(Q.cursor(it, 'recent'), 'recent') == (-1762696800.123456, -9)


def test_an_item_with_no_added_time_sorts_as_oldest():
    items = [item(1, 0.5, added=None), item(2, 0.5, added=5.)]
    assert ids(Q.select(items, Q.Filters(sort='recent'))) == [2, 1]


@pytest.mark.parametrize('p, second, word', [(0.99, 0.9, 'sure'), (0.95, None, 'sure'), (0.6, 0.5, 'close'), (0.8, 0.7, 'close'), (0.8, 0.04, 'likely'), (0.7, None, 'likely'), (0.69, 0.1, 'unsure'), (0.3, 0.2, 'close')])
def test_band_matches_the_words_on_the_cards(p, second, word):
    assert Q.band(p, second) == word


def test_facets_count_each_value_over_the_whole_queue():
    assert Q.facets(ITEMS) == dict(kind={'book': 3, 'radio_series': 1}, status={'review': 3, 'identified': 1}, certainty={'sure': 1, 'close': 1, 'unsure': 1, 'likely': 1}, auto={'skip': 3, 'only': 1}, audible={'yes': 3, 'no': 1})
    assert Q.facets([]) == dict(kind={}, status={}, certainty={}, auto={'skip': 0, 'only': 0}, audible={'yes': 0, 'no': 0})
