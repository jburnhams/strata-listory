# Pending book identification audit — 2026-11-03

Replayed the current book-identification queue against the existing and proposed
resolvers, using saved source inventories and provider responses. The SQLite
connection used `mode=ro`; no lookups, approval writes, refreshes or processing
were performed. Only current runs were included, excluding runs superseded for
any linked member. Checking consent also included seven legacy reviews beyond
the 148 runs explicitly marked `metadata_review`.

Of **155 pending book reviews**, the existing rules passed none; the new rules
pass **two**. These are also the only two reviews whose resolved work results
changed. The other 153 retain their previous work results and eligibility.

| Book | Previously blocked by | Result with the new rules |
| --- | --- | --- |
| #429 The Player of Games (`68581762587643b7ba813a6eefe785fe`) | Hardcover credited Iain M. Banks; Open Library credited Iain Banks. Only one independent family counted. | Both families support the same title; all nine book checks pass. Saved publication year: 1988. |
| #851 Apollo (`d183f3438b8044d0b1b7766c803c475d`) | Hardcover credited Charles Murray; Open Library credited Charles A. Murray. Only one independent family counted. | Both families support Apollo / The Race to the Moon; all nine book checks pass. Saved publication year: 1989. |

External verification supports both identifications: the [author's official
Player of Games page](https://iainbanks.co.uk/the-works/the-player-of-games/)
identifies the novel and its 1988 publication; [NASA's Apollo
bibliography](https://www.nasa.gov/wp-content/uploads/static/history/ap11ann/booksbiblio.htm)
credits Charles A. Murray and Catherine Bly Cox for Apollo, the Race to the Moon.
The [Smithsonian's catalogue](https://www.si.edu/object/siris_sil_843139) uses
Charles Murray and records the original 1989 publication. These checks verify
the work, not the local recording or its completeness.

The saved resolver still reports unknown original publishers for these two
books. Apollo's additional Catherine Bly Cox credit remains in provider evidence;
this change does not repair the pre-existing primary-author projection.

## Change and safety boundaries

Allow one omitted middle initial only with a strong title match and exact full
first name and surname. Do not combine it with a nickname/spelling correction,
omit a full middle name, or treat conflicting initials as equal. An abbreviated
credit cannot corroborate a candidate when available strong-title evidence names
two different middle initials. Exact, unambiguous credits remain usable.

The independent-family threshold remains two. Existing subtitle, volume, original
work, source-inventory and conflict gates remain in force. Resolver version 11
causes saved version-10 evidence to be projected through the current rules, so
existing waiting reviews benefit without refetching providers. Regression tests
exercise automatic book consent from a saved older review and verify that
audiobook consent, quality consent and processing are not granted by this change.

## Other strong-looking candidates kept for review

| Book | Reason to retain review / next evidence needed |
| --- | --- |
| The Coming Wave | Multiple known subtitles, plus incomplete contributor information; require an explicit edition/work link before treating them as equivalent. |
| Endurance | Resolved by the attached-edition follow-up below. |
| Homo Deus | English and translated subtitles currently create ambiguity across the provider cohort. Preserve edition languages/work links before considering equivalence. |
| The Impossible First | Different known subtitles, plus 2019/2020 publication assertions. Resolve the work/edition links and dates first. |
| The Power Law (two entries) | The local and Hardcover subtitles differ; a regional-subtitle relationship needs explicit evidence. |
| The Pragmatic Programmer, 20th Anniversary / 2nd Edition | Resolved at original-work scope in the revised-edition follow-up below; the second-edition recording title remains intact. |
| Sherlock Holmes The Definitive Collection; Just William 3 | Only one independent work family supports the current identification. Collection identity still needs corroboration. |
| Exciting Times: A Novel | Hardcover's saved matching-title hits have no authors; only Open Library currently supplies identity-compatible evidence. |
| Suffragette: My Own Story | The Hardcover hit is titled My Own Story; the local title and Open Library title differ. Require a verified alternate-title/work link. |
| Hitch-22 | An independent local identity conflict remains, in addition to the subtitle variants. |

Full local evidence is retained in the gitignored
`data/identification-audit-20261103/snapshot.json` and `comparison.json`. The
comparison records both sets of checks and work results for every pending
review, plus the policy used. These results describe the saved evidence at the
time of the audit; newly ingested/refreshed books may change the queue.

## Validation

All 589 backend unit tests passed with 95.32% coverage, above the unchanged 95%
gate. Ruff and `git diff --check` passed. The complete suite ran outside the
sandbox after an existing HTTP test stalled inside it and passed in 0.52 seconds
outside it. No frontend files changed. The implementation is local to
`main`; live approvals have not been applied.

## Follow-up: attached editions identify the original work

The identification target is the underlying/original work. An exact edition is
useful evidence, but edition subtitle differences need not imply different works;
the next stage independently identifies the audiobook performance.

**Endurance (#371)** is the next safe automatic-book-approval candidate. The saved
Open Library candidates explicitly attach English, Spanish, French and other
edition subtitle variants to `/works/OL874159W`, with individual
`/books/OL…M` edition references. Its English subtitle matches Hardcover's
Endurance: Shackleton's Incredible Voyage. Previously each subtitle counted as a
different possible work, so the whole provider cohort was rejected. The
[publisher's description](https://www.tyndale.com/p/endurance/9780842308243)
confirms Alfred Lansing's work and its original 1959 publication.

Resolver version 12 groups alternate subtitles through these explicit attached
edition links. Similar subtitle assertions in independent catalogues can then
corroborate the work. Source records expose the linked-edition match basis; the
edition references remain available in the saved provider evidence. Different
work identifiers without a bridge remain ambiguous. Malformed identifiers,
unverified edition references, different authors, and explicit/missing volume
numbers cannot create a bridge. The volume parser now checks labels before the
comparison normalizer removes them.

Replaying the same 155-review snapshot against version 11 versus version 12:

| Review | Result |
| --- | --- |
| Endurance (#371) | Newly passes all nine book checks, with Hardcover and Open Library agreement and original year 1959. Original publisher remains unknown. |
| Hitch-22 (#869) | Subtitle ambiguity is resolved and two sources agree, but a separate local identity conflict still prevents approval. |
| The other 153 reviews | Work results and eligibility unchanged; the two omitted-initial matches from the first pass remain eligible. |

This raises the snapshot's eligible count from two to three. All 148 currently
explicit `metadata_review` runs were checked to be present in that snapshot.
Homo Deus and The Coming Wave remain unchanged: their saved alternate subtitles
do not provide the explicit same-work bridge used by this change. Additional
work-level evidence may identify them without requiring a particular audiobook
edition.

The new regression tests cover translated/alternate subtitles of one work,
different works, missing/invalid links, different authors, volume distinctions,
transitive catalogue links and automatic book approval stopping at audiobook
review. Follow-up replay artifacts are in the gitignored
`data/identification-audit-20261103/edition-links/` folder. No live approvals were
changed.

Follow-up validation: **603 backend unit tests passed, 95.35% coverage**; Ruff
and `git diff --check` passed. The full suite ran outside the sandbox as above.

## Anniversary wording

Anniversary wording describes an edition of the original work and is ignored
when constructing work searches/comparisons. Numbered forms were already
supported; resolver version 13 also handles bare `Anniversary Edition`, hyphenated
forms and empty brackets left by their removal. The unchanged original wording
is preserved in `recording_title` for audiobook identification. Genuine titles
such as The Anniversary and volume numbers remain intact. At this point second/
revised-edition labels were retained; the subsequent revised-edition follow-up
handles numbered edition labels for work matching. The full-cast regression checks
the recording's ASIN separately from the original 2001 work year.

Replaying all 155 saved reviews produces no additional work or recording
measurement changes. This closes a search/matching gap for future inputs without
adding approvals to the current snapshot. The comparison is saved in
`data/identification-audit-20261103/edition-links/anniversary-comparison.json`.

Validation: 613 backend unit tests passed, 95.35% coverage; Ruff and whitespace
checks passed.

## Revised-edition follow-up: The Pragmatic Programmer

**The Pragmatic Programmer (#1864) should pass original-work identification.**
Its second edition is a version of the original work, and the exact recording is
identified at the next stage. [Pearson's edition
page](https://www.pearson.com/en-us/subject-catalog/p/pragmatic-programmer-the-your-journey-to-mastery-20th-anniversary-edition/P200000000337/9780135957059)
explicitly connects the 2019 revision to the authors' 1999 first edition.

The saved Open Library publication editions already attached both From Journeyman
to Master (1999, `/books/OL7408140M`) and Your Journey to Mastery / 20th Anniversary
(including `/books/OL34787490M`) to `/works/OL5748544W`. These were previously used
only for publication metadata, so the subtitle bridge was unavailable to title
matching. Hardcover also supplies the original title and 1999 year.

Resolver version 14 includes valid, attached publication-edition titles in the
existing provider's work evidence. References must bind to the selected work and
an explicit edition, with a compatible main title. Aliases are deduplicated;
archived provider data is preserved and extra editions do not count as additional
independent sources. Numbered edition labels are ignored for work comparison,
including the nested `20th Anniversary Edition, 2nd Edition: Your Journey to
Mastery` wording. Substantive subtitle differences still need the explicit work
bridge. The full local title, ASIN, narrator and other recording clues remain
available to the next stage.

The Pragmatic Programmer now passes all nine book checks, with two source
families, original year **1999** and original publisher **Addison-Wesley**. Its
2019 edition metadata is retained in the attached-edition evidence; the original
work year does not assert that the local audiobook reads the first edition.

The 155-review snapshot gains only this additional eligible book (three to four).
Other changed work results:

- Endurance retains approval eligibility and gains an original publisher from
  its saved attached editions.
- The Impossible First gains title agreement, but stays in review because its
  saved 2019 original year conflicts with Hardcover's 2020 assertion. The
  [publisher dates the original release to January 14,
  2020](https://www.simonandschuster.net/books/The-Impossible-First/Colin-OBrady/9781982133139).
  A linked-subtitle match now retains original-year disagreements as a work
  conflict rather than automatically accepting the earliest year.
- The two Power Law entries, Suffragette and Exciting Times gain saved edition
  metadata, but still lack a second compatible independent family and remain in
  review.

Regression tests cover the original/revised subtitle bridge, retained raw
recording title, original-versus-revised publication metadata, invalid/mismatched
references, title/volume boundaries, deduplication, preserved archives, conflicting
years and automatic book approval stopping at audiobook identification. Replay
artifacts are in `data/identification-audit-20261103/edition-links/revision-comparison.json`.
No approval records were written by this audit.

Validation: 621 backend unit tests passed, 95.37% coverage; Ruff and whitespace
checks passed. The full suite completed after the earlier usage-limit interruption.
