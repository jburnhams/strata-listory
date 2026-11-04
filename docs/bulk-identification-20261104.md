# Bulk book identification and learned matching — 2026-11-04

The initial read-only audit found 355 current book-identification reviews: 329
classified as books, 24 radio and two episodes. None passed every existing book
predicate. Failed-check counts overlap: 348 lacked two independent families,
310 lacked the required work score, 284 lacked an original-work link, 172 had
work conflicts, 166 lacked an author and 12 had local identity conflicts.
These are review entries, not necessarily distinct works: chapter fragments and
collections substantially inflate the queue. Do not count approving their parent
as dozens of independently identified books.

## Changes

Written-work title comparison now takes competing subtitles from the written-work
catalogues. An audiobook storefront's marketing subtitle cannot invent another
written work. A recording title that explicitly matches the full local title can
still explain concatenated main-title/subtitle tags. Recording selection retains
its independent runtime, narrator, language, ambiguity and abridgement checks.
Contradictory subtitles from book catalogues still veto an underspecified title.
Resolver version 19 projects older evidence through this correction.

Book collection also retries a missing Open Library hit using a canonical main
title plus author, preserving the original candidates. When subtitle ambiguity
blocks acceptance, bounded edition discovery can fetch explicit attached English
editions before acceptance: at most three work keys and 12 distinct title aliases.
Only valid work/edition links with compatible main titles can supply aliases;
missing languages, different volumes and different work links cannot bridge them.
A network error leaves the original candidates intact. This is discovery, not a
reduction in approval thresholds.

A cached edition-only experiment added no eligible matches. It did not justify
relaxing subtitle checks. Separating recording marketing from written-work
ambiguity recovered four existing reviews. A bounded 15-book discovery pass
using the local Open Library index, cache and missing API responses recovered
three further distinct works. A final dump-only pass using unambiguous catalogue
hints recovered two entries with missing local authors, bringing the total to nine
replay-passing works. Contrary saved candidates were retained throughout.

| Catalogue ID | Work | Author | Original year in evidence |
| --- | --- | --- | --- |
| 1049 | The North Water | Ian McGuire | 2016 |
| 1584 | The Apollo Murders | Chris Hadfield | 2021 |
| 454 | A Promised Land | Barack Obama | 2020 |
| 358 | Better than Sex | Hunter S. Thompson | 1994 |
| 949 | The Moth and the Mountain | Ed Caesar | 2020 |
| 859 | A Bit of a Stretch | Chris Atkins | 2020 |
| 1359 | Now You See Me | Sharon J. Bolton | 2011 |
| 400 | Losing the Signal | Jacquie McNish (shared primary credit) | 2015 |
| 642 | Build | Tony Fadell | 2022 |

These results concern original-work identification. They do not establish the
recording's identity, completeness, quality or permission to process it. All nine
have compatible Hardcover and Open Library assertions; their source-integrity
checks must still be repeated against live inputs before approval.

## Features and training data

Run this from the repository root:

```bash
PYTHONPATH=src .venv/bin/python -m app.ingest.match_audit data/catalogue.db --out data/candidates.jsonl
```

The exporter opens SQLite in read-only mode inside one snapshot transaction. It
never opens audio, invokes a provider, migrates the database, recomputes stored
projections or changes approvals. It refuses to overwrite its output.

The first export contains 6,415 candidate pairs from 677 current runs: 380 with no
book approval, 289 with automatic approval and eight with manual approval. Across
history there are nine manual book approvals and 303 automatic approvals. None
of those events alone establishes the correctness of a particular candidate.
Every exported `label` and `work_group` therefore starts unknown. Duplicated
candidate records are collapsed; competing candidates remain in the export.

The 24 features include normalised Levenshtein and sequence similarity, title-token
Jaccard overlap, exact title, relative title length, separate subtitle presence,
title/volume number agreement, existing title-rule score and conflicts, author
edit similarity, exact author sets, set overlap, author counts and missingness,
current compatibility/work score, exact ASIN, attached-edition evidence, exact
cross-family title/author support and the margin over other source work IDs.
The support feature is exact title/author agreement, not an independence proof.
Metadata providers can share upstream data, and repeated editions are not votes.

The follow-up experiment uses regularised logistic classification as the baseline,
then a shallow boosted-tree classifier trained on user-trusted automatic approvals. The target is *this candidate identifies
the local original work*, not similarity or the fact that a candidate ranked first.
An XGBoost ranker can improve candidate ordering, but its output is not an
approval probability. See the [XGBoost learning-to-rank documentation](https://xgboost.readthedocs.io/en/stable/tutorials/learning_to_rank.html).

Independent labels must include verified matches, verified mismatches, same-title
books by different authors, narrator/author swaps, volumes, translations,
collections, summaries and no-correct-candidate queries. Do not mark every
unselected candidate negative: several editions can represent the correct work.
Review uncertain pairs as unknown rather than forcing a binary label.

Assign one `work_group` to all editions, fragments, duplicate catalogue entries
and reruns of the same underlying work. Keep those groups together across
training, threshold calibration and a final untouched test split. Fit transforms
and feature selection on training data only; choose the threshold on calibration
data, then report held-out per-work precision, false approvals, recall, coverage
and abstention. Also test unfamiliar authors separately. These controls follow
[scikit-learn's guidance on grouped validation and leakage](https://scikit-learn.org/stable/modules/cross_validation.html).

Optimise recall subject to a precision constraint; overall accuracy is misleading
here. Report uncertainty as well as observed errors. With zero errors in 100
independent reviewed work groups, the exact one-sided 95% precision lower bound
is only 97.05%. Approximately 2,995 independently audited zero-error groups are
needed for a 99.9% lower bound. `match_audit.precision_bound` exposes this calculation.
At the time of that initial export, no model was trained from those unknown
labels. The user subsequently designated existing automatic approvals as trusted
ground truth; the resulting model experiment is documented in
[match-model-20261104.md](match-model-20261104.md).

A future model should initially rank reviews in shadow mode. Its threshold must
never bypass source hashes, manual locks, incompatible authors, volume conflicts,
observed title collisions, independent corroboration or separate recording and
quality consent. Retain model/version/features and abstention reasons for each
proposed automatic decision.

Audit artifacts and scripts are in the gitignored
`data/bulk-identification-audit-20261104/` directory. The local dump index was used
before network lookups. The user explicitly approved sending the bounded
shortlist's titles/authors to Open Library for missing evidence.

## Applied result and validation

After live source hash, catalogue signature, current membership, grouping and
provider-manifest checks, eight new automatic book approvals were recorded:
#1049, #1584, #454, #949, #859, #1359, #400 and #642. They are queued before recording
analysis. The last five use fresh metadata-refresh runs with frozen local-dump
replay evidence and verified manifests; the original runs remain intact.

#358 Better than Sex already had a legacy automatic combined identification
approval. The corrected projection makes that existing consent valid again;
its current state is Quality review. No replacement approval was written for
that run. Losing the Signal retains Sean Silcoff in additional-contributor
evidence; the existing resolver projects McNish as the shared primary author. No new recording or quality approval was granted by this audit, and
no processing was started. The eight new approvals all have scope `book` and
resolver version 19. A SQLite backup taken before these writes is retained at
`data/bulk-identification-audit-20261104/before-approvals.sqlite`; `applied.json`
records the precise outcomes.

Validation: 796 backend unit tests passed, 95.71% coverage against the unchanged
95% floor; nine ingest integration tests passed; Ruff and whitespace checks
passed. The exporter tests were rerun after explicitly closing its read-only
connection. No frontend changes were required.
