#!/usr/bin/env python3
"""Rebuild data/openlibrary/identify.sqlite from the dumps (atomic; the old index serves until the new one passes its integrity check). About 2 hours. `--dumps DIR` builds from another snapshot directory."""
import argparse, os, sys

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), '..', 'src'))
from app.identify import olindex  # noqa: E402

if __name__ == '__main__':
    ap = argparse.ArgumentParser(description=__doc__.split('\n')[0]); ap.add_argument('--dumps')
    print(olindex.build(dumps=ap.parse_args().dumps))
