# Book identification (model-first, local Open Library)

Replaces the rule-engine route for deciding *which written work* a local item is. Design rationale and the audit it came from: [identification-audit-20261107.md](identification-audit-20261107.md). Code is in `src/app/identify/`; the older `matcher`/`ingest/metadata` path is still in place until the migration steps below are done.

## Pipeline

1. **Index** (`olindex.py`): builds `data/openlibrary/identify.sqlite` from the Open Library dumps. A work is a cluster: every title, subtitle, full title and alternate title ever used by its English or unlabelled-language editions (with how many editions use each), author names and aliases, ISBNs, Amazon IDs, series, edition and audio-edition counts, ratings. All text keys come from `textkeys.py`, the single definition of title/person/ISBN/ASIN normalisation. First build: 38 GB, about 2 hours; a compact format is planned (see below).
2. **Readings** (`local.py`): file and folder names pack author, title and part numbers into one string, so each plausible reading (as tagged, part numbers stripped, `Author - Title` split) is searched. The model sees which reading matched.
3. **Candidates** (`candidates.py`): recall only. ISBN, any title-key variant (with an author filter when known; surname-only credits narrow by title first), and the credited authors' fuzzily close works.
4. **Features** (`features.py`): every normalisation is a feature, never a veto: title match against all aliases and the share of editions agreeing, author name-part matches, popularity, genericness of the title (homonym count), reading used, folder context (`context.py`: siblings sharing a stripped title, grouped member count) and within-query context (margin to the best *different* book; duplicate catalogue works with the same title and a shared author name are one cluster).
5. **Model** (`model.py`): XGBoost probability per candidate. A query is auto-accepted only when its best cluster scores at least 0.98 and beats the runner-up by 0.3 (`score.py`); everything else is reviewed.
6. **Review queue** (`queue.py`, `service.py`): uncertain groups ordered by impact (files settled by one answer, model uncertainty, other unresolved groups by the same author). Decisions are stored in `data/identify/labels.sqlite` as manual, highest-weight labels (rejects are hard negatives) and used by the next training run.

## Commands

```sh
python -m pip install -r requirements-model.txt         # optional; training and scoring need it
./app identify-train      # fit from approvals + stored decisions, saves data/identify/model.ubj
./app identify-refresh    # score the library, cache the queue
./app identify-queue --limit 10
./app identify-decide GROUP approve|reject [--work /works/OL…W]
```

API: `GET /api/identify/queue?limit=&after=<last group held>` (pages without offsets, so answers given meanwhile cannot shift the next page), `POST /api/identify/decisions` (`retrain: true` queues a retrain and rescore), `POST /api/identify/refresh`.

Downloading the dumps: `python scripts/fetch_openlibrary.py latest` fetches authors, works, editions, ratings, redirects and deletes for the newest monthly snapshot; build with `olindex.build()`.

## Measured so far (small data: treat as a first estimate)

* 524 approved runs (about two thirds are automatic approvals produced by the old rules, so this is partly circular; 31 are manual).
* The right book is among the candidates for 508 of 524.
* Held-out test queries (244, including copies with the author blanked): 206 auto-accepted at a score of 0.95 and margin 0.3, with 0 false positives; the calibration split still contains one disputed case, so no threshold is auto-selected yet and 0.98/0.3 is fixed by hand.
* Library-wide (1,450 items classed as book, which are 913 distinct titles): 49% of items are auto-accepted. The remainder is mostly ungrouped disc/chapter files and episodes awaiting the grouping step, plus blank-author or generic titles that need the review queue or the audiobook-side match.

## Not done yet

* The queue UI is the Review page (see [frontend.md](frontend.md)); a decision there is saved as a training label **and** carried into the ingest pipeline (`service.apply`, run in the ingest worker): Accept approves the run's book as the model's Open Library work through the ordinary `match_review.accept` path (so its checks still apply) and the run continues to audiobook identification; Not this book withdraws an approval if there is one. A group whose files are still separate catalogue entries (no grouped run, about a quarter of the queue on 2026-11-07) gets the label only, and Review says why.
* Compact index (hashed aliases, main-title keys, redirects and deletes applied, filtered to about 20M works) and the monthly refresh timer.
* Kind detection (radio versus book) and better disc/chapter grouping feeding this stage.
* The old matcher and ingest rule engine are not removed yet.

## Audible-first matching (wired in; limited by index coverage so far)

Once the local Audible index (ASIN, ISBN, title, authors, narrators, series, runtime) exists, audiobook matching runs first because it is more accurate and gives fewer false positives; Open Library becomes the fallback and the source of the original work. The pipeline is built to take that without a redesign:

* An Audible hit is a candidate source in `candidates.find` (ASIN, ISBN, title and author against the Audible index) and contributes features per candidate: exact ASIN match, runtime agreement, narrator agreement, title/author match against the audiobook record, and the Audible record's ISBN linking to an Open Library work.
* The Open Library work cluster stays the unit of identity, so an Audible match that carries an ISBN resolves straight to a work through `work_isbns`. Items with no ISBN link are scored on title and author as today.
* The model is retrained with those features; strong Audible evidence should raise the share auto-accepted at the same zero-false-positive setting, which is then re-measured before any threshold changes.

Strictness rule for the Audible stage: some audiobooks are older tape or CD recordings that are not on Audible, so an Audible match is never accepted on title alone. It needs corroboration from at least one independent signal (author, narrator, runtime within the usual tolerance, or an exact ASIN/ISBN); a title-only Audible hit counts for nothing and the item falls back to Open Library. This is enforced twice: the Audible candidate's features carry no "title only" shortcut (a title-only hit has all corroboration features at zero), and auto-accept still requires the model's calibrated score and margin, with the zero-false-positive measurement repeated on items known not to be on Audible.

Status (2026-11-07): `audible.py` looks every item up in `audibleindex` first. The index returns title-only candidates with evidence flags and the model decides; only a corroborated hit (exact ASIN, strong grade, or full title with agreeing narrator or runtime and nothing conflicting) adds a reading that searches Open Library with the audiobook's author, title and ISBN, which is how a one-word title like *Census* gets resolved. Local runtime and language come from the ingest inventory. With the current 162k-book index, 58 of 1,450 library items have a corroborated hit (10 by ASIN), so the model has seen few examples and the accepted share barely moved (516 to 520 groups); retrain with `./app identify-train` as the index grows.
