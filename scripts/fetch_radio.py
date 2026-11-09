#!/usr/bin/env python3
"""Fetch (or resume) the radio programme catalogues into data/radio/raw and rebuild data/radio/index.sqlite. BBC Programmes A-Z is about 3,500 small JSON pages at one request per 0.6 s (40 minutes the first time, seconds when pages are cached); Wikidata is two small SPARQL queries.

    python scripts/fetch_radio.py [--refresh] [--no-fetch] [--only bbc|wikidata]

`--no-fetch` just rebuilds the index from the saved pages. Nothing in the app needs the index: it only adds evidence to the kind classifier."""
import argparse, os, sys

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), '..', 'src'))
from app.identify import radioindex  # noqa: E402
from app.providers.base import Http  # noqa: E402

if __name__ == '__main__':
    ap = argparse.ArgumentParser(); ap.add_argument('--refresh', action='store_true', help='fetch pages again even if saved'); ap.add_argument('--no-fetch', action='store_true'); ap.add_argument('--only', choices=('bbc', 'wikidata'))
    a = ap.parse_args(); raw = radioindex.folder() / 'raw'
    if not a.no_fetch:
        if a.only in (None, 'wikidata'): radioindex.fetch_wikidata(Http('wikidata', 2.0), raw, refresh=a.refresh)
        if a.only in (None, 'bbc'): radioindex.fetch_bbc(Http('bbc programmes', 0.6), raw, refresh=a.refresh)
    print(radioindex.build())
