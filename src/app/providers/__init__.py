"""The metadata providers and which of them to use. Add one by writing a `Provider` subclass (see base.py) and listing it in ALL.

The order matters: ASIN lookups and searches try the providers in the configured order (`match_providers` setting or APP_PROVIDERS, comma separated; default: all, in the order below). A provider that cannot run (for example no API key) is skipped and listed with the reason by `./app providers`."""
import os

from app import settings
from app.providers.audible import AudibleSearch
from app.providers.audiobookdb import AudiobookDB
from app.providers.audnexus import Audnexus
from app.providers.base import MatchError, Provider
from app.providers.hardcover import Hardcover
from app.providers.openlibrary import OpenLibrary

from app.providers.applebooks import AppleBooks
from app.providers.googlebooks import GoogleBooks

ALL = (Audnexus, AudibleSearch, Hardcover, AudiobookDB, OpenLibrary, AppleBooks, GoogleBooks)
_instances: dict = {}


def get(name: str) -> Provider:
    for cls in ALL:
        if cls.name == name:
            if name not in _instances: _instances[name] = cls()
            return _instances[name]
    raise KeyError(f'unknown provider {name!r} (known: {", ".join(c.name for c in ALL)})')


def configured() -> list[str]:
    raw = os.environ.get('APP_PROVIDERS') or settings.load().get('match_providers') or ''
    names = [n.strip() for n in raw.split(',') if n.strip()] or [c.name for c in ALL if c.matching]
    for n in names: get(n)                                    # unknown names fail loudly
    return names


def enabled(names: list[str] | None = None) -> list[Provider]:
    """The configured providers that can run now, in order."""
    ps = [get(n) for n in names or configured()]
    return [p for p in ps if p.unavailable() is None]


def status() -> list[dict]:
    on = configured()
    return [dict(min_interval=c.min_interval, cache_days=c.cache_days, name=c.name, label=c.label, site=c.site, selected=c.name in on, available=(why := get(c.name).unavailable()) is None, reason=why or '', order=on.index(c.name) + 1 if c.name in on else None) for c in ALL]


__all__ = ['ALL', 'MatchError', 'Provider', 'configured', 'enabled', 'get', 'status']
