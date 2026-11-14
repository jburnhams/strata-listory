# Listen identification (probabilistic record linkage)

Which written work is each listen, and which recording of it? Code: `src/app/linkage/`. It replaces, for the Postgres catalogue, the SQLite-era `src/app/identify` model (XGBoost on 48 features, [identification.md](identification.md)), whose indexes and text keys it reuses. Lessons taken from that work ([identification-audit-20261107.md](identification-audit-20261107.md)): one normaliser (`identify.textkeys`), the work cluster as the unit of identity, no vetoes, a "none of these" outcome, and labels from old automatic approvals are weaker than manual ones.

## Pipeline

1. **Readings** (`query.py`). A listen's evidence is read from its records (embedded-tag summary, parsed folder name), its chosen fields, its runtime per copy, narrator and ASIN. Names are ambiguous, so it becomes several readings (title, author): chosen, album tags, folder, folder with title and author **swapped** (`The Title - The Author` parses the wrong way round), the whole folder name split at a dash, each with disc/part numbers stripped and brackets dropped. Bracketed words (`Title (Narrator Name)`) are kept as narrator hints.
2. **Candidates** (`candidates.py`, recall only). A third source, radio programmes, is described in [listen-kinds.md](listen-kinds.md#radio-programmes-as-an-identification-source). Audible: ASIN, any title key of any reading, the credited authors' books with a similar title, full-text words as a fallback, from `data/audible/index.sqlite` (now both UK and US stores, English and unlabelled only: 1.2M recordings). Open Library: `identify.candidates.find` per reading over `identify.sqlite`, plus the works holding the ISBN or Amazon id of a found recording.
3. **Work clusters.** Open Library works and Audible recordings that are one book are joined: by ISBN, by an edition's Amazon id, or by a shared title key (or main title before a subtitle, brackets removed first) with a shared author (initials, `Jr.` and reordered names tolerated; records without a real author join only each other). Duplicate Open Library works, abridged and unabridged recordings and different narrators of one book are then one candidate, so they never compete.
4. **Comparison vectors** (`compare.py`). Per reading and cluster, one coarse level per field, or none when it cannot be compared (a missing author is no evidence; a different author is):

   | field | levels |
   |---|---|
   | title | exact (rare / common / generic, by how many works share the title), main title, close, partial, weak |
   | author | all, some, surname, conflict |
   | narrator | match, conflict (also from bracket hints) |
   | runtime | closest recording within 1.5%, 4%, 12%, 30%, further |
   | asin | match, other |
   | year | same, close, later, earlier (the listen's year vs the work's first) |
   | numbers | numbers in the titles agree, only local, only theirs, differ |
   | sources | Open Library and Audible, only one |
   | popularity | editions and Audible ratings: high, medium, low, none |
5. **Model** (`model.py`): Fellegi-Sunter. For each level, m = P(level | right work) and u = P(level | other candidate); a candidate's evidence is the product of m/u over its fields, using the reading that suits it best. One listen has at most one right work, so the posterior is over hypotheses (candidate i, or none of them): `P(i) = (p/n)·L_i^t / ((1-p) + (p/n)·Σ L_j^t)`, with p the share of listens whose work is among the candidates, n the number of candidates (a generic title spreads its prior thinner) and t a temperature. **EM** fits m, u and p over all listens; labelled listens are clamped to their answer (semi-supervised). t is then chosen to maximise the likelihood of the labelled answers, because the naive product of correlated fields is overconfident. Pure Python, seconds to fit.
6. **Decision and writing** (`apply.py`). P ≥ 0.95: the work is linked as a `match` listen-part, accepted by `auto_policy` with P, runner-up, P(none), the comparison vector and the closest recording's ASIN in `detail`; status `identified`. Otherwise the top three works (P ≥ 0.02) are linked as `candidate` parts and the status is `review`. Works are `work` rows keyed by identifier `work` (the cluster's most-published Open Library work, or `asin:…` for Audible-only books) with the other Open Library works, ASINs and ISBNs as further identifiers, so every listen of one book links to one row. Reruns change nothing unless the answer changed; a listen a person has decided on is never touched; `--rebuild` clears the model's own output.
7. **Copies** (`apply.merge_copies`, `--merge-copies`): listens accepted as the same work whose runtimes agree within 2% and whose narrators do not disagree are merged (preferred copy survives, event with the evidence; `split` undoes it). Different recordings of one work (another narrator, an abridgement) stay separate listens linked to the same work.

8. **Collections kept as folders of books** (`linkage/containers.py`, `--merge-containers`): per-listen matching cannot see that a folder of numbered books (`Author; The Complete X/01 Book A`, `02 Book B`...) is one audiobook of the collection, because each piece rightly matches its own book. The folder is read as a pseudo-listen (its name and segments, the title and author the pieces share, the **sum** of their runtimes) and matched by the same model. It is joined into one listen of ordered `part` inputs only when the work is accepted at the threshold, the sum is one of its recordings within 2%, and every piece runs under 80% of that recording (a piece as long as the book is a copy). That sum test is what keeps a folder of separate books with "Complete" in its name apart (their sum is no recording). Already matched pieces, even approved by a person, take part: the collection match supersedes the sub-book matches (events keep them), so a missing piece added later joins the approved ones. Sums within 12% are listed as proposals, never joined. `overlong` lists listens far longer than the longest recording of their matched work (a collection matched to one of its books: review it).

## Match queue (home page, `#/`)

The way to confirm matches quickly and teach the model. `GET /api/matches` lists every book listen whose work the model has proposed and nobody has decided, **most certain first** (the model's own acceptances, P ≥ 0.95, come first, so a run of one-key confirmations builds a large confirmed set fast), paged by a `probability:listen` cursor so answers given meanwhile cannot skip or repeat cards. Each card shows, side by side, what the files say (chosen title, author, narrator, year, every copy's length, folder, tags that differ, publisher, ASIN, file count, a link to the files) and the proposed work (title, authors, year, editions, Open Library and Audible links, an Open Library cover by ISBN, the Audible recordings with narrator, length and its difference from yours) with the evidence the model used as coloured words (title exact, narrator matches, runtime within 1.5%, author differs...). Other candidates sit below with **This one**.

Keys: `A` approve, `R` not this book, `N` none of these, `J`/`K` next/previous, `U` undo (answers are saved when the five-second undo window closes). `POST /api/matches/decide` (`apply.decide`):
* **approve** a candidate: it is accepted by the user (an automatic acceptance gets a user `accept` event, i.e. it is confirmed), the other candidates are unlinked, status `identified`.
* **reject** (not this book): the candidate is marked rejected and stays linked as the record; status `review`. The next model run leaves that work out and decides among the rest.
* **none** of these: all candidates rejected, status `not_found`; the model leaves the listen alone (a search UI is the next step).

Every answer is a label (`dataset.catalogue_labels`: approved work, rejected works, or "none"), merged over the old catalogue's approvals. Every 25 answers (or **Learn from my answers**) `service.retrain_and_apply` refits the model in the background with the labels clamped and re-decides every undecided listen, so confirmed easy matches let it accept more on its own; the page polls while it runs and reloads the list when it finishes. A listen a person approved or marked "none" is never changed by the model. The older SQLite Review moved to **Pipeline → Old review**.

## Commands

```sh
./app listens-identify-prepare [--old-catalogue data/catalogue.db]   # candidates and vectors -> data/linkage/vectors.json (cached per listen); old approvals -> old-labels.json
./app listens-identify-train                                         # fit, cross-validate, save data/linkage/model.json and report.json
./app listens-identify [--dry-run] [--threshold 0.95] [--merge-copies] [--rebuild] [ids...]
python scripts/build_audible_index.py                                 # rebuild the Audible index as the fetchers add recordings (about 6 minutes, niced)
```

## Labels and measurement (2026-11-08)

Labels: the old SQLite catalogue's approvals mapped to listens by library-relative file paths (370 listens, 84 manual, 286 automatic), plus any decision a person makes on a listen (`catalogue_labels`: accepting or rejecting a candidate). The right work was among the candidates for 361 of 370 (the misses are labels with no Open Library key and titles that are not the book's, e.g. a track title as album tag).

5-fold cross-validation (each fold's labels hidden while fitting; its listens still take part unlabelled), 361 listens:

| P ≥ | accepted | share | disagree with label |
|---|---|---|---|
| 0.5 | 351 | 97% | 6 |
| 0.9 | 331 | 92% | 3 |
| 0.95 | 316 | 88% | 3 |
| 0.99 | 256 | 71% | 2 |

Top candidate right 97.5%; expected calibration error 0.019 (the 0.9–1.0 bin: mean P 0.991, right 0.991). All three disagreements at 0.95 are the same book under a second Open Library record (a subtitle written without a colon, `10½` against `10 1/2`, a `Lib/E` suffix), so the identification is right and the label points at a duplicate; no wrong book was accepted. Manual labels only: 81 listens, 65 accepted at 0.95, the one disagreement being one of those duplicates.

Learned evidence (log2 m/u, bits): exact rare title +4.3, generic exact title −0.6, author conflict −5.2, narrator match +4.8, runtime within 1.5% +5.2, ASIN match +7.6, only one source knows the work −2.7 to −3.8. Fitting with **no** labels gives the same ranking (top-1 97.5%); the labels mostly set the temperature (0.5).

Whole library (780 book listens after merging): 538 identified automatically, 246 to review (of which 16 with no candidate at all: misspelt names, radio material classed as a book, compilations), 4 copies merged, 9 works with two or more distinct recordings linked.

## Known limits and next steps

* Search for listens with no right candidate (`not_found`, and the ~17 with no candidates at all, which the queue does not show).
* Recall: misspelt titles or authors find nothing (no fuzzy full-text fallback in Open Library); collections and omnibus editions are weakly handled.
* Radio listens are identified against the BBC/Wikidata programme catalogue (title, channel, year), not individual episodes; which listens are radio is decided by [listen-kinds.md](listen-kinds.md).
* Recording identity: the closest Audible recording is recorded in `detail.recording`, not yet as its own `audiobook` work.
