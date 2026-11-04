# Repairing the 45 fragmented work reviews

Read-only inspection and decoding confirm three candidate original works:

| Work | Old reviews / files | Correct representations | Duration after repair |
| --- | ---: | --- | --- |
| Summer's Lease | 16 | One ordered 16-part run | About 8:00:09 |
| Reborn on the Run | 24 | One 23-part run plus a separate preview | About 7:33:14, plus a 0:05:00 preview |
| Ego Is the Enemy | 5 | One five-part byte-split MP3 run | 6:56:18 instead of the erroneous 12:19:57 sum |

All 45 original SHA-256 manifests still match. Files are assigned once to their
work groups; no source audio is moved, renamed or rewritten. These results do not
grant work, performance or quality consent.

## Shared causes and fixes

`John Mortimer - Summer's Lease 01 of 16.mp3` lacked the literal word Part;
`1 (1).mp3` became a title `(1)`; and bare names such as `Ego Is the Enemy 1.mp3`
were conservatively kept separate. Grouping version 4 recognises these validated
families across discovery, work grouping and representation ordering:

- Named N-of-total runs retain leading and trailing missing-part checks.
  Duplicate indices and contradictory totals do not establish a run.
- Numeric copy counters need a coherent folder and a run starting at one.
  Conflicting tags, whole-book formats and identical downloads are not
  concatenated on this evidence.
- Bare trailing numbers require a fixed-byte MP3 chunk signature and agreement
  with the folder title: equal middle byte counts, a nearly equal first file and
  a shorter final file. Ordinary numbered volumes stay distinct.
- A descriptive preview can belong to the same work but remains a separate
  representation. Two Reborn preview windows match the opening track at about
  100 and 250 seconds (energy correlations 0.974 and 0.980); this partial match
  does not certify completeness or performer identity.

On fresh ingest, obsolete version-3 fragment scopes are reconsidered. Selecting
an old fragment discovers the containing work, links its parent and all aliases,
and analyses it once even if every alias was selected. History remains intact;
old fragment approvals are not reused. Fresh ingestion would replace 45 reviews
with three work reviews, a reduction of 42. No live regrouping or consent writes
were performed by this audit.

## Stale headers and mandatory re-encoding

Ego's first chunk retains the whole-book VBR header: it reports 24,977.867755
seconds but decodes to 5,322.954 seconds. All five decoded chunks total
24,977.842676 seconds, essentially the original whole-book length. The apparent
long first file is not a complete alternative recording. Other chunks' reported
lengths are also wrong. The other two works decode within normal padding
differences of their reported lengths.

Bad-header detection applies beyond these filename patterns. Explicit probe
header warnings and unreliable bitrate-derived lengths trigger decoded-clock
measurement. A diagnosed header mismatch or decoder error sets
`requires_reencode`; bitrate, mono format and acceptable loudness cannot override
it. An estimate agreeing with decoded samples does not by itself diagnose damage.

Original durations and diagnostics remain alongside the measured values. Plans
reuse verified selected-source facts and retain encoding reasons. Every
alternative remains in the change-detection manifest. Execution refuses copying
that contradicts a recorded re-encoding requirement.

All five Ego chunks are diagnosed for encoding. Warnings at boundaries of chunks
2, 3 and 4 remain visible for quality review. Encoding creates clean output
headers; it does not erase source-error evidence or certify that no samples were
lost at the splits.

## Validation

666 backend unit tests passed with 95.46% coverage; 21 integration tests passed.
Ruff and whitespace checks passed. Real-audio tests create byte-split VBR MP3s,
measure the pieces, force encoding and verify clean output headers and durations.
Other tests cover gaps, duplicates, alternatives, legacy scopes, alias linking,
fresh consent and changed input during measurement.

Dry-run boundaries remain identical for Bret Easton Ellis (723 files, six groups),
Adrian Mole (473 files, nine groups) and Just William (395 files, ten groups).
Snapshots, duration measurements, preview comparisons and repeatable scripts are
retained in the gitignored `data/fragment-grouping-audit-20261103/` directory.
