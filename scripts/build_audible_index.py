#!/usr/bin/env python3
"""Rebuild data/audible/index.sqlite from the archive written by scripts/fetch_audible_people.py (atomic; the old index keeps serving until the new one passes its integrity check). Safe to run while the fetch is running: the archive is read-only here. About 20 seconds per 160,000 books."""
import os, sys

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), '..', 'src'))
from app.identify import audibleindex  # noqa: E402

if __name__ == '__main__':
    print(audibleindex.build())
