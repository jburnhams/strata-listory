# Broader identification fixes — 2026-11-03

The next pass targets shared lookup failures rather than individual title aliases.
A fresh read-only snapshot contains **144 pending original-work reviews**, of
which **120 are classified as books**. Of those 120, **82 have blank author
fields** in the recorded identity; many are chapter fragments rather than
distinct books. Another contains an explicit narration label instead of an
author assertion.

## Shared causes and changes

1. **The second catalogue is searched without the first catalogue's useful
   evidence.** A strong, unambiguous title/author hit now supplies a provisional
   query for later work catalogues. This changes discovery only: no identity or
   approval is inferred from one source. Ambiguous author sets, incompatible
   local authors, other works and numeric chapter labels do not supply a hint.
2. **Author inference compares each provider in isolation.** A local tag may
   concatenate a full title/subtitle while Open Library stores only the main
   title. The first provider's complete title now supplies context across the
   candidate cohort. Inferring an author still requires agreement from at least
   two independent work sources.
3. **Artist tags mix writer and reader credits.** Slash-separated multiword
   personal names are parsed as separate retained credits. Provider agreement
   determines primary work authors; no narrator role is invented from the slash.
   Explicit `read by` / `narrated by` labels are recording credits, not author
   assertions. URLs, `AC/DC` and `A/B Testing` are left intact.

Resolver version 15 projects existing saved evidence through these rules. The
two-source threshold, title/volume checks, original-year conflict checks and
separate audiobook/quality consent remain in force. Purely numeric unknown-author
titles such as `(14)` cannot become an unrelated book called 14 through coincident
catalogue hits. Original tags and provider archives remain available.

## Queue replay and verification

The read-only replay reuses archived Hardcover candidates, reads the persistent
Open Library index and cached work responses, and unions new Open Library hits
with the saved hits. It does **not discard contradictory saved candidates**, make
network lookups, write to the catalogue or approve runs. Uncached network-only
lookups are recorded as unavailable in this replay, rather than invented.

**Six distinct works pass all nine book checks** with two independent source
families:

| Book | Original year in saved evidence | Required action for eligibility |
| --- | --- | --- |
| #975 A Far Cry from Kensington — Muriel Spark | 1988 | Existing evidence suffices once the writer/reader slash tag is parsed. |
| #991 Flying Blind — Peter Robison | 2021 | Existing evidence suffices once the writer/reader slash tag is parsed. |
| #642 Build — Tony Fadell | 2022 | Repeat the second-source lookup with the first source's canonical title and author; confirm author across the full cohort. |
| #400 Losing the Signal — Jacquie McNish / Sean Silcoff | 2015 | Same discovery/inference fix. The shared primary anchor is McNish; Silcoff remains in Hardcover's additional-contributor evidence. |
| #1359 Now You See Me — Sharon J. Bolton | 2011 | Parse the slash credits, then search Open Library using the canonical writer name. |
| #1865 The Pyramid of Lies — Duncan Mavin | 2022 | Treat `Narrated by: Duncan Mavin` as a recording credit; obtain independent work-author corroboration. |

The identities were checked against primary publisher/author sources:
[Build](https://www.buildc.com/the-book),
[A Far Cry from Kensington](https://www.ndbooks.com/book/a-far-cry-from-kensington/),
[Flying Blind](https://www.penguin.co.uk/books/317849/flying-blind-by-robison-peter/9780241994795),
[Losing the Signal](https://us.macmillan.com/books/9781250060181/losingthesignal/),
[Now You See Me](https://www.penguin.co.uk/books/407128/now-you-see-me-by-sharon-bolton/9781409030638),
[The Pyramid of Lies](https://www.panmacmillan.com/authors/duncan-mavin/pyramid-of-lies/9781529088922).
The [National Library of Scotland](https://www.nls.uk/collections/stories/literature-and-poetry/muriel-spark-people-watcher-traveller-novelist/)
also confirms Kensington's 1988 publication.

Only two become eligible from the existing provider events alone. Four need a
metadata refresh to save the improved second-source lookup; a resolver-version
upgrade cannot fabricate missing provider events. These are eligibility results,
not live approvals. Original publishers remain unknown for these six in this
bounded replay; the changes do not invent missing publication metadata.

## Remaining broader issue

The 16 Summer's Lease parts, 24 Reborn on the Run entries and five Ego Is the Enemy
parts represent **45 review entries for approximately three underlying works**.
They need source grouping/inventory repair before being counted as separate
automatic-book-approval successes. They were deliberately excluded from the six
new successes. Numeric-label inference tests protect the Reborn fragments from
being mistaken for unrelated numbered novels. Radio/episode identification and
unsupported source types also retain their independent review requirements.

Snapshots, before-code copies and replay outputs are retained in the gitignored
`data/broad-identification-audit-20261103/` directory. No catalogue approval or
processing-queue records were written by this audit.

Validation: **637 backend unit tests passed, 95.42% coverage**. Ruff and whitespace
checks passed. New regressions exercise real collector sequencing through mocked
provider boundaries, refusal to approve from a single source, reader-only tags,
ambiguous query hints, slash-credit preservation and numeric-fragment rejection.
