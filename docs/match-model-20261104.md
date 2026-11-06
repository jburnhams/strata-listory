# Learning work matches from trusted approvals — 2026-11-04

The user explicitly trusts existing automatic approvals as ground truth. The
training pipeline now uses those decisions as positive supervision, alongside
manual approvals where the accepted catalogue candidate can be recovered.
Independent manual relabelling is not a prerequisite for this experiment.

`src/app/ingest/match_model.py` reads the catalogue in a single read-only SQLite
snapshot. For each approved current run it freezes the identity, provider
candidates and accepted work records immediately before the approval event.
Later refreshes or recording refinements cannot change that training example.
A candidate is positive when its URL/work identity or exact retained source
record identifies the approved work. Alternative editions that cannot be tied
to the decision remain unknown. Book approval is supervision for original-work
identity; it supplies no recording/completeness/quality label.

Negative supervision has two recorded origins: candidates contradicting the
approved identity, and different approved works deliberately paired with the
local query. Hard-negative mining prioritises other works by the same author,
then nearby titles, choosing distinct work groups. It preserves realistic
cross-catalogue support on wrong-work examples so missing source agreement
cannot act as an artificial shortcut. Subtitle/edition spelling variants alone
do not create negative labels. These negatives are derived from trusted
identities; they are not separately hand-labelled decisions.

A union of catalogue members, accepted source work IDs and canonical work
identities keeps duplicated runs, linked editions and fragments together.
Stable hashes allocate work groups to training, calibration and test partitions.
Cross-work negatives are generated only within their partition. Each work-group
and class receives equal total training weight, so many edition aliases do not
make one work dominate training.

The initial version used 19 raw features from the earlier 24-feature export. It excluded
`compatible`, `work_score`, `title_rule_score`, `title_rule_conflict` and
`source_margin`, which directly encode the existing resolver. Title/author edit
similarity, token overlap, missing fields, exact identifiers, subtitle presence,
number agreement and raw source support remain available.

The comparison fits three regularised logistic regressions and three shallow
XGBoost classifiers. Model and threshold selection use only the calibration
partition, maximising recovered work groups subject to zero observed false
positives and at least ten accepted groups. The selected configuration is then
evaluated on held-out work groups. The test set does not choose the threshold
or the winning model. Failed calibration produces an abstention threshold above
one, rather than a permissive default.

## Reproduce

The packages are optional research dependencies, separate from the app's runtime:

```bash
python -m venv data/match-model-env
data/match-model-env/bin/pip install -r requirements-model.txt
PYTHONPATH=src data/match-model-env/bin/python -m app.ingest.match_model data/catalogue.db --out data/new-model-experiment
```

On Windows use the environment's `Scripts/python` and `Scripts/pip`. Output must
be a new directory. For further experiments, `--dataset PATH/dataset.jsonl`
reuses frozen features, rechecks derived negative labels and regenerates hard
negatives within each split.

Artifacts include the full dataset with label origins and partitions, model
weights, a report with dependency versions and feature importance, and pending
candidate scores. The experiment makes no provider requests, reads no audio,
and does not write approvals or change application policy. Scores on currently
unrecognised books are recommendations for the next matching pass.

## Interpretation

The evaluation measures generalisation from trusted approvals to other held-out
approved works and derived wrong-work pairs. It does not independently audit the
trusted labels or establish precision on every kind of messy unrecognised input.
Source hashing, collection boundaries, conflicting local identities, volume
checks and separate recording/quality consent still apply. A high model score
cannot establish that a chapter fragment is a complete anthology or settle a
conflicting original publication year.

The implementation follows the official
[scikit-learn logistic regression API](https://scikit-learn.org/stable/modules/generated/sklearn.linear_model.LogisticRegression.html)
and [XGBoost estimator API](https://xgboost.readthedocs.io/en/stable/python/python_intro.html).
Actual result counts and the selected model are recorded below after the run.

## Measured result

The frozen dataset contains 295 approved queries grouped into 240 underlying-work
groups. It contains 2,782 positive pairs from automatic approvals, two recoverable
positive pairs from manual approvals, 317 contradictory-candidate negatives and
885 mined wrong-work negatives; 151 alternatives remain unknown. There are 150
training groups, 38 calibration groups and 52 test groups. Candidate aliases do
not count as independent work groups.

| Configuration | Calibration accepted correct pairs | Calibration false positives | Calibration groups recovered |
| --- | ---: | ---: | ---: |
| Logistic regression, C=0.1 | 362 / 410 | 0 | 33 / 38 |
| Logistic regression, C=1 | 290 / 410 | 0 | 25 / 38 |
| Logistic regression, C=10 | 277 / 410 | 0 | 25 / 38 |
| XGBoost, depth 2 | 369 / 410 | 0 | 33 / 38 |
| XGBoost, depth 3 | 369 / 410 | 0 | 33 / 38 |
| XGBoost, depth 4 | 369 / 410 | 0 | 33 / 38 |

The selected model is the depth-two XGBoost classifier, at score threshold 0.90.
It recovered 275 of 356 positive candidate pairs and at least one match for 48
of 52 held-out work groups (92.3% work-group recovery). None of the 203 labelled
negative test pairs passed the threshold. These are zero *observed* false
positives within this labelled experiment, not a guarantee on all unknown inputs.
A score of 0.90 is an empirically chosen cutoff, not a claim of 90% real-world
correctness.

Prioritising same-author negatives made title-token overlap, title sequence
similarity and relative title length useful alongside author similarity. The
initial easier-negative experiment leaned much more heavily on author matches;
it is retained as a development artifact rather than the selected model.

Scoring the pending queue produced a shortlist of 52 current book-identification
reviews above the selected threshold. Examples include Seven Years in Tibet,
The Hitchhiker's Guide to the Galaxy, David Copperfield, Ugly Americans, Running
with the Kenyans and Death in Ten Minutes. This is a shortlist for additional
work-resolution checks: year conflicts, alternate subtitles and source grouping
can still independently block approval. No model-generated approvals were written.

Artifacts live under the gitignored `data/match-model-20261104/` directory. The
`final` experiment contains the fitted model, report, dataset and the filtered
`shortlist.json`; `final-verified` repeats the same training with pending-query
filters that exclude processed entries and unsupported content kinds. Actual
libraries used: scikit-learn 1.9.1, XGBoost 3.4.1 and NumPy 2.5.4. Training and
prediction were performed locally without provider requests.

Validation: 804 backend unit tests passed with 95.75% coverage; focused tests also
verify the final pending-state/content filters. Ruff and whitespace checks passed.
The actual model fits and predictions were also run using the isolated research
environment, in addition to deterministic unit tests of the orchestration.

The verification run produced an identical XGBoost model hash. After excluding
processed entries and unsupported content types, it scored 307 pending queries
and shortlisted 52. The canonical final artifacts are
`data/match-model-20261104/final-verified/{report.json,model.ubj,dataset.jsonl,pending.jsonl,shortlist.json}`.
`shortlist.json` contains one top recommendation per qualifying review rather
than counting multiple editions as separate books.

## Version 2: evidence and popularity features

The expanded feature set includes the resolver’s compatibility, work/title scores, conflict flag and source margin as learned inputs. They are not hard vetoes, approval rules, or target labels. New raw evidence includes subtitle similarity, biography/annotation/adaptation markers and their differences from the local title, publication-year missingness/value, ISBN/publisher availability, and archived linked-edition facts. Edition evidence is used only when its work and edition IDs match the candidate.

Ratings contribute five features: count presence, `log1p(count)`, count relative to the largest count among candidates from the same provider (in log space), average rating on a 0–1 scale, and average presence. Missing values, real zeroes and invalid values are distinct. Raw counts from different rating communities are not directly used as relative popularity. Popularity is a learned prior; it cannot establish book identity by itself or distinguish all editions of the same work.

Frozen datasets can be extended with missing columns from their retained candidate, local identity and linked-edition evidence, without querying live providers or borrowing later metadata. Synthetic cross-work negatives get popularity calculated in the query’s candidate context, rather than an artificial zero value. Historical unknown candidates remain unknown; adding features does not manufacture rejection labels.

Use `--feature-set legacy`, `--feature-set evidence` (everything except ratings) and `--feature-set expanded` to compare on one immutable database snapshot and frozen dataset. Each configuration chooses its model/threshold on calibration groups and is assessed on separate held-out groups. `data/match-features-20261104-221647/` retains this comparison’s source snapshot, datasets, reports and candidate predictions. No model experiment writes approvals.


## Incremental training evidence

The application maintains `data/training/features.sqlite` as a persistent SQLite sidecar. Candidate features are keyed by immutable inventory, identity and provider event IDs. Review decisions have a separate key: approval or revocation updates supervision without recomputing candidate comparisons. Publishing a run records a durable dirty marker; background maintenance caches evidence and training examples outside the review worker lane. Bootstrap recovers unfinished markers.

Adding feature columns backfills only missing values from retained raw candidate, local identity and linked-edition evidence. Existing feature values and synthetic pairs are preserved. New queries are mined individually; pairs referencing withdrawn consent, changed foreign evidence or invalid group/split membership are regenerated. Training always checks current decision provenance. Intentionally changing an existing feature definition requires an explicit migration, rather than silently overwriting cached values.

The completed comparison is in `data/match-features-cached-20261104-223752/`. Warm evidence and legacy experiments, including fitting and pending predictions, took 13.08 and 11.34 seconds. The expanded model accepted 536 correct and one incorrect held-out candidate at its calibration-selected threshold of 0.98. Without ratings it accepted 527 correct and one incorrect candidate; both covered 75 distinct held-out groups. These are candidate counts, not independent books. The older 19-feature model accepted 347 correct candidates and zero incorrect candidates across 41 groups. The biography suggestion for Great Expectations fell from 0.985 to 0.836, below threshold, without a title-specific exception. Recommendations remain subject to human review.

The installed CPU XGBoost library fits these small models quickly (a representative 5,000-row fit took 0.34 seconds). The NVIDIA GPU would not address evidence preparation; retaining and incrementally updating evidence removes that repeated cost.

A final no-dataset retrain completed in 15.76 seconds with the original feature-computation function replaced by a failure guard. All 48 features came from the persistent cache; it did not recompute existing comparisons.
