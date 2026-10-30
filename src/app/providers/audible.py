"""Audible's public catalogue search (`api.audible.<region>/1.0/catalog/products`): title and author search that returns ASIN, authors, narrators, series, runtime and cover. No key needed. It is an undocumented endpoint (the one Audnexus and the Audible apps use), so it can change without notice; failures are handled per provider."""
import urllib.parse

from app.providers.base import Http, Provider, candidate
from app import settings

HOSTS = dict(uk='api.audible.co.uk', us='api.audible.com', de='api.audible.de', fr='api.audible.fr', ca='api.audible.ca', au='api.audible.com.au')
GROUPS = 'contributors,series,media,product_attrs'


def to_candidate(p: dict) -> dict:
    s = (p.get('series') or [{}])[0]
    try: pos = float(s['sequence']) if s.get('sequence') else None
    except ValueError: pos = None
    imgs = p.get('product_images') or {}
    return candidate('audible', id=p.get('asin', ''), asin=p.get('asin', ''), title=p.get('title', ''), subtitle=p.get('subtitle') or '', authors=[a['name'] for a in p.get('authors') or []], narrators=[n['name'] for n in p.get('narrators') or []],
                     series=s.get('title', ''), series_position=pos, runtime_minutes=p.get('runtime_length_min'), cover_url=imgs.get('500') or next(iter(imgs.values()), ''), language=p.get('language', ''), release_date=p.get('release_date', ''))


class AudibleSearch(Provider):
    name = 'audible'; label = 'Audible catalogue'; site = 'https://www.audible.co.uk'
    min_interval = 0.3; cache_days = 14          # an unofficial endpoint: be gentle with it; book data changes rarely

    def __init__(self): self.http = Http('Audible', self.min_interval)

    def _url(self, path: str, **q) -> str:
        return f"https://{HOSTS.get(settings.audible_region(), HOSTS['uk'])}/1.0/catalog/{path}?" + urllib.parse.urlencode(dict(q, response_groups=GROUPS))

    def search(self, title: str, author: str = '') -> list[dict]:
        t = title.strip()[:100]
        if len(t) < 2: return []
        q = dict(title=t, num_results=8, products_sort_by='Relevance', **({'author': author.strip()[:60]} if author.strip() else {}))
        raw = self.cache(f'{settings.audible_region()}:search:{t.lower()}:{author.strip().lower()}', lambda: self.http.request(self._url('products', **q)))
        return [to_candidate(p) for p in (raw or {}).get('products', [])]

    def by_asin(self, asin: str) -> dict | None:
        raw = self.cache(f'{settings.audible_region()}:asin:{asin}', lambda: self.http.request(self._url(f'products/{asin}')))
        return to_candidate(raw['product']) if raw and raw.get('product') else None
