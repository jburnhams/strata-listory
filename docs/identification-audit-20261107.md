# Book identification audit and unified design — 2026-11-07

Read-only audit of how strata-listory decides which written work (and which recording) a folder of audio files is. Nothing was changed. Line references are to `src/app/` at commit `7140a66`. Numbers from the catalogue are from a read-only open of `data/catalogue.db` on omarchy.

## Findings (short version)

1. **There is no LLM and no embedding model anywhere.** Identification is rules plus string similarity (`difflib`). The only "model" is an offline XGBoost/logistic experiment (`ingest/match_model.py`, `requirements-model.txt`) that is not wired into any decision, and whose labels are the app's own earlier automatic approvals (see below).
2. **Two generations of matcher are both live.** The original `matcher.py` (0.7×title + 0.3×author, thresholds 0.6/0.85/0.95) still backs the CLI `match`, the server `match` task, `enrich.py` and `sources.py`, *and* its `norm`/`title_score`/`author_score`/`score` are called from inside the newer ingest resolver. The "legacy" code is load-bearing.
3. **Six title normalisers, three "same person" definitions, and about a dozen unrelated thresholds** that disagree about case, accents, articles, `&`, volumes and ordinals (tables below). They are applied to the same strings in sequence, so a title can pass one test and fail the next for purely mechanical reasons.
4. **The local Open Library index uses a seventh normaliser and keeps only a fraction of the dump.** Subtitles, alternate titles, series, identifiers (Goodreads/Amazon), narrator contributions, edition counts and work redirects are all dropped at import, which is why the resolver compensates with HTTP lookups, a subtitle-grouping state machine and retry heuristics.
5. **The bottleneck is not "OL does not know the book".** In the bulk audit (`bulk-identification-20261104.md`) of 355 reviews, 348 lacked two independent source families, 310 lacked the work score, 284 lacked an original-work link. That is mostly a gate-design and candidate-ranking problem, which a complete local work/edition graph can address.

## What exists today

### Title/name normalisation

| # | Where | Behaviour | Gotchas |
|---|---|---|---|
| 1 | `matcher.norm` (`matcher.py:22`) | lower; drops the/a/an/(un)abridged; ordinals to `1st`; `&`→and; **keeps only `[a-z0-9]`** | No accent folding: non-ASCII letters become spaces (`Brontë`→`bront`). Used by `inventory`, `grouping`, `enrich`, scoring. |
| 2 | `merge.norm` (`merge.py:20`) | NFKD accent strip; drops bracketed (un)abridged; `&`→and; keeps articles | Used by `contributors`, `evidence`, `sources`, voting. |
| 3 | `book_titles.normal`/`compact` (`book_titles.py:7`) | `merge.norm` + drop `vol N` + drop articles and abridged words | A third article policy. |
| 4 | `openlibrary_local.normalise` (`:21`) | casefold, NFKD, `\w` tokens | Keeps articles, does **not** map `&`→and, so `Pride & Prejudice` never hits the exact title index for `Pride and Prejudice` and falls through to FTS. This is the key stored in the index. |
| 5 | `metadata.work_title` (`ingest/metadata.py:64`) | strips "Nth anniversary/edition", empty brackets, tidies colons | Applied before most comparisons, but not before `matcher.score`. |
| 6 | `merge.main_title` / `_clean_title`, `scan._norm` (letters only), `scan.parse_name`, `grouping.title_hint/album_parts`, `inventory.stem_key`, `evidence._key` | each its own cleanup | `scan._norm` removes digits entirely. |

Similarity: `difflib.SequenceMatcher` everywhere except `match_audit.edit_similarity`, a hand-rolled Levenshtein used for the ML features only.

### Person matching (three definitions)

* `contributors.same_person/same_author/omitted_middle_initial` (edit-distance one on surname ≥6 chars, a hard-coded nickname list of 7 groups, initial rules, a looser first-name rule when the title match is strong).
* `matcher.author_score` (surname containment, SequenceMatcher > 0.6, returns 1/0.95/0.5 unknown/0.3/0).
* `merge.person_key` (surname + set of initials) for voting.

Open Library already supplies author aliases (indexed in `names` FTS, `authors.aliases`); the nickname list and the surname-edit rules duplicate what that data does better.

### Matching and decision rules

* **Legacy:** `matcher.match_book`: ASIN first, then per-provider search, `0.7*title+0.3*author`, `MIN_SCORE 0.6`, `GOOD_SCORE 0.85`, `EARLY_STOP 0.95`, `AGREE_BONUS 0.05` per provider.
* **Ingest title analysis:** `book_titles.analyse` (rule engine on main title, subtitle, volume numbers, author-prefix collections). Outputs 0, 0.96, 0.99, 1 or a fuzzy ratio. `similar()` is true at SequenceMatcher ≥ 0.93, `fuzzy main title` accepted at ≥ 0.85.
* **Compatibility:** `metadata.compatible_work` needs title score ≥ 0.85 plus author overlap plus an initials-ambiguity check. The Open Library provider's own `accepted()` needs `analyse ≥ 0.85` **and** `author_score ≥ 0.9`; local search ranks with `matcher.score`.
* **Work score:** `0.7*analyse + 0.3*author_score`, gate `work.score ≥ 0.9` (`ingest/policy.py`). Other literals: 0.9 (anchor/measured recording), 0.95 (strong title), 0.96 (linked editions), 0.8 (grouping), 0.6 (negative-label rule in `match_model.distinct_work`).
* **Gates** (`ingest/policy.py` `GATES`): `metadata.work_families ≥ 2`, `metadata.original_work_link ≥ 1`, `work.conflicts = 0`, `recording.runtime_error ≤ 0.02`, narrator/language/abridgement agreement, and audio alignment/quality gates.
* **Source independence:** `family()` collapses `audnexus` and `audible_file` into `audible`; multiple Open Library records are one family.
* **Recording identity** is a separate, genuinely different problem (ASIN, runtime within 2%, narrator set, language, abridged). The work-versus-recording split is sound and should stay.

### ISBN and ASIN

* ISBNs: `openlibrary_local.isbn_keys` (checksummed, ISBN-10↔13) is the only real validator. `details.py` has a separate unchecked regex for ISBN-in-ASIN-field cover lookups. **Zero of 1,930 identity events in the catalogue contain an ISBN**, so "ISBN-led matching" is in practice a second hop: provider candidates → ISBNs → OL editions → works. Files never seed it.
* ASINs: three extractors (`scan.parse_name` bracketed `[A-Z0-9]{10}`, which also matches an ISBN-10 and any 10-char token; `sources.ASIN`; `inventory.embedded_asins`). Only 150 of 5,859 catalogue books carry an ASIN.
* Series: carried through provider fields and `merge` but there is no series-level identity or normalisation, only 6 books have a series.

### Models

* No LLM, embedding or vector search.
* `match_model.py`: 48 features, depth-2 XGBoost, thresholds 0.90 (19-feature) and 0.98 (expanded). Labels are *positive = candidate that the app's own automatic approval chose* (507 of the 550 approvals are automatic), negatives are mined. The expanded set feeds `compatible`, `work_score` and `title_rule_score` back in as features, so it learns the rules. It is a good regression harness; it is not independent evidence that the rules are right. The expanded model still accepted one wrong held-out candidate.

## Where it is duplicated or inconsistent

* Normalisation: seven keys, five article policies, two `&` policies, accent handling in 3 of 7.
* Person identity: three definitions, plus a hand-written nickname table that Open Library alias data supersedes.
* Thresholds: 0.6/0.8/0.85/0.9/0.93/0.95/0.96/0.98 with no single definition of "same title".
* Two matchers sharing helper functions; improving one silently shifts the other (`rank()` and `candidate_score()` call `matcher.score`).
* Local OL index key (`normalise`) differs from every query-side key; query side compensates with four spelling variants.
* Subtitle handling is reimplemented three times: `book_titles.subtitle_groups`, `openlibrary.search` hydration (HTTP `work.json` and `editions.json`), `metadata.discover_work_editions`.
* Online fallback to OL search duplicates the local index and makes results depend on network and on a 0.5 s rate limiter.

## What the full Open Library data can replace

Verified on the first 400k records of each local dump file: editions carry `title`, `subtitle`, `full_title` (33%), `other_titles` (8%), `series` (17%), `contributions` (18%), `identifiers` with `goodreads` (60% of those present), `librarything`, `amazon`, plus ISBNs, `physical_format` and `edition_name`. Works carry `subtitle` and `first_publish_date` only sparsely in that sample (about 2% and 12%), so original year must keep coming from earliest edition year, which the code already does. The published index (`index.sqlite`, 34 GB; 41.7M works, 39.8M English-or-unknown editions, 15.5M authors) retains none of `full_title`, `other_titles`, `series`, `contributions`, `identifiers`, works' subtitle, or redirects/merges, and has no edition count per work.

Proposed uses, none of which I have yet validated on the library:

| Today | Replace with |
|---|---|
| Subtitle groups, `discover_work_editions`, HTTP edition hydration, title-only retries | A `work_titles(work, norm_title, kind)` table: every edition title, subtitle, full_title and other_title mapped to its work. Subtitle variants of one work are then aliases, and a different-subtitle book (a different work) simply is not an alias. |
| Nickname table, surname-edit rules | `author_names(norm_name, author)` over names and `alternate_names`; compare person by author key first, fall back to rules only for unmatched strings. |
| Online `search.json` fallback | Local only, with online used solely for "not found locally" (dump is monthly). |
| `edition_count` / provider ratings as popularity | Edition and ISBN counts per work from the dump, one comparable prior instead of per-provider rating counts. |
| Hardcover/OL series strings | `series` from editions, plus Audible/Audnexus series for recordings. |
| Duplicate/empty OL works (`publication_editions` search) | Collapse using redirects plus work clusters from shared ISBNs. |
| ASIN-only linking | Amazon identifiers from `identifiers.amazon` as an extra edge, with the caveat that coverage is small (about 3% of editions in the sample). |
| Narrator for audio editions | `contributions` on audio-format editions as a weak corroboration, not a gate. |

Honest limits: OL still contains duplicate and junk works, sparse dates, and is silent on new releases and on many audiobook-only titles. It remains **one** source family for corroboration purposes. A bigger local graph improves *candidate generation and disambiguation*; it does not by itself justify loosening the two-family approval gate. That needs a measurement (step 0 below).

## Design principle from Jonathan (2026-11-07): the work is the identity

Goal: identify every book with no false positives. Manually approved and confirmed books are the highest-weight ground truth (automatic approvals are weaker, being rule output).

* A resolved book is a **work cluster**: OL work key plus all edition titles/subtitles/ISBNs/authors, with redirects and duplicate works merged. Recordings link to a cluster, never to one edition.
* **Confidence comes from agreement across all editions of the work** (title, subtitle, author set, ISBNs), not from one best-matching record. Counting agreeing editions is a stronger signal than any single fuzzy score.
* **Approve only when unambiguous**: one cluster clearly ahead of the runner-up, title and author agreeing across several editions; otherwise send to review. Any resolver change must reproduce every manually confirmed approval before it ships, and false-positive rate on that set is the headline metric.
* Manual approvals also supply local naming conventions (how files spell titles/authors) for tuning.

## Revised direction (Jonathan, 2026-11-07): model-first, local-first, skip radio

Decisions: delete freely and re-approach; local OpenLibrary data first, remote only when local finds nothing (covers and reviews become a later stage); scope is the initial identification stage only; radio shows are detected and skipped, not matched; a learned model (XGBoost) scores candidates from many normalisation features instead of piling up deterministic rules.

Design:

1. **Stage 0, kind detection.** Classify each item as book / radio / other before any matching (today only the regexes in `scan.RADIO` and `kind_of`). Radio is skipped with a recorded reason. Treat this as its own small classifier or rule set evaluated on its own labels.
2. **Stage 1, candidate generation (recall only, no decisions).** From the local index: ISBN to edition to work; every normalised variant of the title (several keys at once: with/without articles, subtitle split, `&`, accents, compact) against `work_titles`; author key plus aliases; FTS fallback. Return a cluster per work (all editions), capped at a few dozen. Many normalisers are fine here, since each is just another way to find candidates.
3. **Stage 2, features per cluster.** Compute the normalisation variants as features, not rules: best/mean similarity across all edition titles and subtitles, share of editions agreeing, exact-match flags per normaliser, author exact/alias/fuzzy, author-set agreement, year, edition count and ISBN count (popularity), duplicate-cluster margin to the runner-up, series and volume agreement, missing-field flags. No hard vetoes inside the features.
4. **Stage 3, model.** Gradient-boosted trees (XGBoost, already a dependency of the experiment) giving P(correct work). Train per query with the cluster as the unit, plus a "none of these" outcome so unknown books abstain. Calibrate probabilities on held-out works and choose the auto-accept threshold for zero observed false positives; everything below goes to review; the margin to the second cluster is an extra requirement.
5. **Labels.** Weighted: manual approvals/rejections highest, automatic approvals lower (they were produced by the old rules). Current counts: 550 approvals of which only 43 manual, plus 38 approve and 26 reject match-review decisions. That is thin; expect to need active learning: score the pending queue, show the model's least certain and highest-scoring items for quick confirm/reject, retrain. Hard negatives come from sibling works by the same author and near-title clusters.
6. **Removed**: legacy `matcher.match_book` path, `merge`/`book_titles` rule engine for acceptance, per-provider scoring and the HTTP edition hydration, the two-family gate for the book stage (replaced by calibrated probability plus margin). Recording matching (ASIN, runtime, narrator) is kept separate and later.

Risks to watch: the model can only be as good as the labels, so false-positive claims must be tested on held-out manually confirmed works only; calibration needs enough negatives; index v5 (aliases, `work_titles`, counts) is a prerequisite for the strongest features and takes a multi-hour rebuild.

## Earlier proposal (superseded in part by the direction above)

Three layers with one definition each.

1. **`textkeys` module (one place).** `title_key(s)` (NFKD, casefold, `&`→and, edition/anniversary/(un)abridged labels, bracket removal, article policy, ordinals, volume normalisation) and `person_key(s)`; both used at index build time and query time. Plus `isbn` (checksum + 10↔13) and `asin` (strict) helpers, replacing the three ASIN extractors and the `details.py` regex. `difflib` stays as the only fuzzy primitive.
2. **Local work resolver (`identity/resolver`).** Input: tags, folder hints, ISBNs, ASIN. Candidate generation in precedence order: ISBN→edition→work; exact `work_titles` key plus `author_names` key; FTS fallback. Output: ranked works with a small, documented feature set (title evidence kind, author evidence kind, edition count prior, year, series agreement). One score, one threshold table in `policy.py`.
3. **Recording matcher (unchanged in spirit).** ASIN, runtime, narrators, language, abridgement. It consumes the resolved work and never re-identifies it.

Decision policy stays explicit (gates), but the gates should be defined over the resolver's outputs. Keep the ML harness as an evaluation tool, not a decision maker, until it has independent labels (64 manual decisions exist today, too few). A small transparent score with a few weights is preferable to 48 features that restate the rules.

## Migration plan (each step shippable, behaviour-preserving until stated)

0. **Baseline harness, no behaviour change.** Freeze the current resolver outputs for the 550 approvals, 64 manual decisions and the 355 bulk-audit reviews (the retained `data/match-*` datasets are a start). Report precision/recall and the current failed-gate histogram. This is the yardstick for everything below.
1. **Consolidate normalisers** into `textkeys` with characterisation tests; replace `matcher.norm`, `merge.norm`, `book_titles.normal`, `openlibrary_local.normalise` call sites one module at a time. Expected, documented diffs: accent handling, `&`, articles. Bump the resolver `VERSION` (existing mechanism projects older evidence).
2. **Unify person comparison** behind `person_key` + one `same_person`; delete `matcher.author_score` after moving its callers.
3. **Retire the legacy matcher path.** Route CLI `match`, the server task, `enrich.py` and `sources.py:80` through the ingest resolver, or remove them; move the remaining scoring helpers out of `matcher.py`.
4. **Index v5** (rebuild from the retained dumps; the last full build took about 5.2 h, run as the existing background unit, atomic publish and lock unchanged): add `work_titles`, `author_names`, `series`, `identifiers`, edition counts, redirects, using the shared `textkeys`. Existing readers keep working against v4 until switched.
5. **Shadow-mode local resolver.** Run it beside the current path on the baseline set; compare candidates and gate outcomes; only then replace the HTTP edition/work hydration and the online search fallback.
6. **Re-derive gates and thresholds** from the baseline: one table, measured. Decide the two-family rule and the model question on that evidence.

Rough size: steps 1–3 are code-only and low risk; 4 is mostly waiting on a rebuild; 5–6 are where the real behaviour change and the review effort sit. Steps 0–3 could be done without touching the 34 GB index.

## Open questions for Jonathan

* Is it acceptable that online lookups (Hardcover, OL search) become optional corroboration rather than the default path?
* Is the CLI `match` / old catalogue workflow still used, or can it be deleted in step 3?
* Do you want the two-independent-families approval gate to stay as is until step 0 shows what it costs?
