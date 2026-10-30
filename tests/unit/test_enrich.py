import pytest
from app import catalogue, enrich, mirror, providers
from app.providers.base import MatchError, candidate

DETAILS = dict(source='hardcover', id='42', rating=4.2, ratings_count=10, reviews_count=2, reviews=[])


class HC:
    name = 'hardcover'
    def __init__(self, hits=None, asins=None, details=DETAILS, fail=False): self.hits, self.asins, self.d, self.fail, self.calls = hits or [], asins or {}, details, fail, []
    def unavailable(self): return None
    def search(self, t, a=''): self.calls.append(('search', t)); return self.hits
    def by_asin(self, asin): self.calls.append(('asin', asin)); return self.asins.get(asin)
    def details(self, i):
        self.calls.append(('details', i))
        if self.fail: raise MatchError('Hardcover answered 503')
        return self.d


def confirmed(title='Dune', author='Frank Herbert', **kw):
    b = catalogue.add_book(title=title, author=author, **kw); return catalogue.get_book(b['id'])


def test_hardcover_id_prefers_the_suggestion_then_asin_then_a_likely_search_hit():
    hc = HC(hits=[candidate('hardcover', id='7', title='Dune', authors=['Frank Herbert'])], asins={'B0X': candidate('hardcover', id='9', title='By Asin')})
    b = confirmed(); catalogue.set_suggestion(b['id'], dict(source='hardcover', id=11, title='Dune'), 1.0)
    assert enrich.hardcover_id(catalogue.get_book(b['id']), hc) == '11' and hc.calls == []
    assert enrich.hardcover_id(confirmed('Other', asin='B0X'), hc) == '9'
    assert enrich.hardcover_id(confirmed(), hc) == '7'
    weak = HC(hits=[candidate('hardcover', id='8', title='Dune Messiah', authors=['Frank Herbert'])])
    assert enrich.hardcover_id(confirmed(), weak) is None                                      # not a likely match: do not attach someone else's reviews
    assert enrich.hardcover_id(confirmed('Nothing'), HC()) is None


def test_enrich_book_stores_the_data_and_writes_the_mirror():
    hc = HC(hits=[candidate('hardcover', id='42', title='Dune', authors=['Frank Herbert'])]); b = confirmed()
    assert enrich.enrich_book(b, [hc])['rating'] == 4.2
    ex = catalogue.get_extras(b['id'])['hardcover']
    assert ex['data']['ratings_count'] == 10 and ex['fetched'] and mirror.read_meta(b)['extras']['hardcover']['data']['id'] == '42'
    hc.calls.clear(); enrich.enrich_book(catalogue.get_book(b['id']), [hc])
    assert hc.calls == [('details', '42')]                                                      # a refresh goes straight to the known id
    nf = confirmed('Nothing'); assert enrich.enrich_book(nf, [HC()]) is None and catalogue.get_extras(nf['id'])['hardcover']['data'] == dict(not_found=True)
    with pytest.raises(MatchError, match='Hardcover is not available'): enrich.enrich_book(b, [])
    with pytest.raises(MatchError, match='503'): enrich.enrich_book(confirmed('Z'), [HC(hits=[candidate('hardcover', id='1', title='Z', authors=['Frank Herbert'])], fail=True)])


def test_enrich_pending_skips_fresh_and_unconfirmed_books_and_refreshes_stale_ones(monkeypatch):
    hc = HC(hits=[candidate('hardcover', id='42', title='Alpha', authors=['Frank Herbert']), candidate('hardcover', id='43', title='Beta', authors=['Frank Herbert'])])
    a, b = confirmed('Alpha'), confirmed('Beta')
    catalogue.add_scanned([dict(path='/n', title='New', author='', narrator='', series_position=None, asin='', kind='book', file_count=1)])
    catalogue.add_book(title='Radio ep', kind='radio')
    monkeypatch.setattr(providers, 'enabled', lambda names=None: [hc])
    assert enrich.enrich_pending(1) == dict(tried=1, enriched=1, not_found=0, remaining=1)
    assert enrich.enrich_pending(10) == dict(tried=1, enriched=1, not_found=0, remaining=0)
    assert enrich.enrich_pending(10)['tried'] == 0
    from app import db
    c = db.connect()
    with c: c.execute("UPDATE book_extra SET fetched='2000-01-01T00:00:00+00:00' WHERE book_id=?", (a['id'],))
    c.close()
    assert enrich.enrich_pending(10)['tried'] == 1                                             # data older than 30 days is fetched again


def test_removing_a_book_removes_its_extras():
    b = confirmed(); catalogue.set_extra(b['id'], 'hardcover', DETAILS); catalogue.remove_book(b['id'])
    assert catalogue.get_extras(b['id']) == {}
    with pytest.raises(catalogue.NotFound): catalogue.set_extra(b['id'], 'hardcover', DETAILS)
