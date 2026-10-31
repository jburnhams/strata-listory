"""Audnexus (https://api.audnex.us): an open-source community API in front of Audible's catalogue. Exact ASIN lookup only (no search). Tries the configured region, then the US one."""
from app import settings
from app.providers.base import Http, Provider, candidate

API = 'https://api.audnex.us'


class Audnexus(Provider):
    name = 'audnexus'; label = 'Audnexus'; site = 'https://api.audnex.us'
    min_interval = 0.3; cache_days = 14          # a free community service: be gentle with it; book data changes rarely

    def __init__(self): self.http = Http('Audnexus', self.min_interval)

    def by_asin(self, asin: str) -> dict | None:
        for region in dict.fromkeys([settings.audible_region(), 'us']):
            raw = self.cache(f'{region}:{asin}', lambda: self.http.request(f'{API}/books/{asin}?region={region}'))
            if raw:
                sp = raw.get('seriesPrimary') or {}
                try: pos = float(sp['position']) if sp.get('position') else None
                except ValueError: pos = None
                return candidate('audnexus', id=raw.get('asin', asin), asin=raw.get('asin', asin), title=raw.get('title', ''), subtitle=raw.get('subtitle') or '', authors=[a['name'] for a in raw.get('authors') or []], narrators=[n['name'] for n in raw.get('narrators') or []],
                                 series=sp.get('name', ''), series_position=pos, runtime_minutes=raw.get('runtimeLengthMin'), cover_url=raw.get('image') or '', language=raw.get('language', ''), release_date=(raw.get('releaseDate') or '')[:10], publisher=raw.get('publisherName') or '', isbns=[raw['isbn']] if raw.get('isbn') else [], description=raw.get('summary') or raw.get('description') or '', genres=[g['name'] for g in raw.get('genres') or [] if g.get('type') == 'genre'])
        return None
