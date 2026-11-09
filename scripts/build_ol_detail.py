#!/usr/bin/env python3
"""Build data/openlibrary/detail.sqlite (see app.identify.oldetail): every Open Library author, work and edition record as a zstd blob keyed by OL id, plus the edition -> work link. Atomic; about 40 minutes.

scripts/build_ol_detail.py [--dumps DIR] [--limit N] [--output PATH]"""

import argparse
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "src"))
from app.identify import oldetail

if __name__ == "__main__":
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--dumps")
    ap.add_argument("--limit", type=int, default=0)
    ap.add_argument("--output")
    a = ap.parse_args()
    print(oldetail.build(a.dumps, a.output, a.limit, log=lambda m: print(m, flush=True)))
