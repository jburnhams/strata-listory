"""Audnexus (https://api.audnex.us): an open-source community API in front of Audible's catalogue. Exact ASIN lookup only (no search). Tries the configured region, then the US one."""
from app import settings
from app.providers.base import Http, Provider, cached, candidate

API = 'https://api.audnex.us'


class Audnexus(Provider):
    name = 'audnexus'; label = 'Audnexus'; site = 'https://api.audnex.us'

    def __init__(self): self.http = Http('Audnexus')

    def by_asin(self, asin: str) -> dict | None:
        for region in dict.fromkeys([settings.audible_region(), 'us']):
            raw = cached(f'audnexus:{region}:{asin}', lambda: self.http.request(f'{API}/books/{asin}?region={region}'))
            if raw:
                sp = raw.get('seriesPrimary') or {}
                try: pos = float(sp['position']) if sp.get('position') else None
                except ValueError: pos = None
                return candidate('audnexus', id=raw.get('asin', asin), asin=raw.get('asin', asin), title=raw.get('title', ''), subtitle=raw.get('subtitle') or '', authors=[a['name'] for a in raw.get('authors') or []], narrators=[n['name'] for n in raw.get('narrators') or []],
                                 series=sp.get('name', ''), series_position=pos, runtime_minutes=raw.get('runtimeLengthMin'), cover_url=raw.get('image') or '', language=raw.get('language', ''), release_date=(raw.get('releaseDate') or '')[:10])
        return None
