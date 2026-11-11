# Progress log

## 2026-11-09: radio programmes as an identification source

The radio catalogue (BBC Programmes A-Z, Wikidata) is now a third candidate source beside Open Library and Audible: broadcast and close-call listens get radio programme candidates scored by the same model (new `channel` and `form` comparisons), shown on the Matches card with the BBC's own record, and a radio match accepted by a person is a broadcast label for the kind classifier. See [listen-kinds.md](listen-kinds.md).

## 2026-11-09: book or radio? a scored classification

Listens are now classified as book, radio series, radio programme or play from many weak signals (channel names, series/episode wording, genre tags, slot-length parts, Audible content type, a local BBC/Wikidata programme index) with the reasons kept, instead of file-name rules alone; an unabridged reading by a BBC actor stays a book. A person's choice is never overwritten; close calls are flagged for review. See [listen-kinds.md](listen-kinds.md).

## 2026-11-09: Learning failed after a rejected top candidate

"Learn from my answers" failed with a unique violation on `listen_part_active_ordinal` (listen, role, ordinal). Cause: rejecting a review candidate keeps it linked (state `rejected`, its ordinal kept, as the record of the decision), and the next run numbered its new candidates from 1 again, so whenever the rejected one had been #1 and the listen stayed in review the insert collided and the whole run rolled back. Every listen whose top candidate was rejected hit it, so one rejection stopped all learning. `apply` now numbers new parts after the highest ordinal a rejection holds in that role (rank order kept), and a run takes a transaction-scoped Postgres advisory lock, so a CLI run and the web's Learning cannot interleave (the web's thread lock only covers one process). No data was left bad: the failed run rolled back; listens whose only parts are rejections drop out of the queue until the next successful run re-decides them.

## 2026-11-09: Matches queue order and filters

The Matches page (home) can now be worked in other orders and on part of the queue: most certain first (default), least certain first, most recently added first (newest listen id first, since one build adds many listens in the same transaction) or oldest first; and filters to skip or show only the matches the model accepted on its own, by certainty word (the bands the cards show), kind of listen, listen status, with or without an Audible recording, and words searched across the listen's title, author, narrator and folders and every candidate's title and authors. All of it lives in the URL (`?sort=uncertain&auto=skip`), each option says how many cards it holds over the whole queue (`facets` in the response), and `total` says how many fit. The ordering and filtering are pure functions in `src/app/linkage/queue.py`; the cursor is now the sort key itself (`key:listen_id`) so paging stays exact in every order. Validation: unit tests for the new module, a Postgres store test for the API, web integration tests for URL state, keyboard answers within a filtered list, search and clearing.

## 2026-11-07: frontend redesign on strata-react, Review as home

The web app now runs on [strata-react](https://github.com/jburnhams/strata-react) (AppShell, DataView, DataTable, Field, AsyncButton, Tabs, UndoBar, Done, TanStack Query, React Router with hash routing) with a theme generated from a book-cloth brown. Home is **Review**: one endless list of every doubtful identify decision, most impactful first, with Accept and Not this book on each card, keyboard shortcuts, an undo window and retrain. Page map, decisions and what is left: [frontend.md](frontend.md). The identify queue API now pages with `after=<last group>` and decisions look any group up in the cached queue (they were limited to the first 50). Validation: web typecheck, 134 tests, coverage gate and build; unit tests for the paged queue.

## 2026-11-04: PR #12 CI and lifecycle review fixes

Resolved two review findings: a failed job superseded by explicit retry or newer completed work no longer blocks future submissions; unresolved retry failures still require another explicit retry. Isolated workers watch a private pipe owned by the supervisor and kill their process group/tools on EOF, including abrupt server death. Completed supervisors close their pipe handles. macOS CI exposed inherited negative niceness: set an absolute nice target of 19 instead of adding 19 to the inherited value, while preserving an already lower priority.

Validation: focused queue/OS/worker tests and all 24 integration tests passed. New regressions cover successful and failed retry chains, changed retry options, supervisor pipe cleanup, platform priority boundaries, and a real server crash that terminates both its detached worker and tool.

## 2026-11-04: searchable failure details

Processing exposes labelled, multiline failure reasons and the last step for stopped work. Missing error text falls back to the last failed activity entry, or explicitly states that no reason was recorded. The Failed tab can be combined with search across titles, IDs, stages, errors and activity messages. Status tabs wrap on smaller screens. Search filters the existing saved API response without adding backend work. Rebuilt and verified the live frontend without restarting active jobs.

Validation: full web suite and coverage gate passed, along with typecheck/build and whitespace checks. The new UI regression covers mixed job/plan failures, timeout search, failed activity fallback, absent reasons, exclusion of queued work and clearing search.

## 2026-11-04: background job scheduling priority

Isolated workers lower their CPU priority before importing processing code (POSIX nice 19, Windows idle priority class). Linux workers request idle I/O priority through ionice when available; an absent/unsupported tool does not fail the job. Child tools inherit those settings. Applied CPU nice 19 and idle I/O to the current job, FFmpeg and their existing threads without restarting or interrupting the job. Verified the web-server process remains nice 0.

Validation: 23 focused OS/worker unit tests and two real-process integration tests passed. The new real-process test verifies a tool inherits nice 19/idle I/O while the supervisor's priority stays unchanged. Ruff and whitespace checks passed.

## 2026-11-04: terminal job failures, hard deadlines and review follow-ups

Background slots now supervise isolated child processes, with per-job persisted deadlines (default 30 minutes via `APP_JOB_TIMEOUT_SECONDS`). A deadline, abnormal child exit or shutdown kills the child/process group and records a terminal failed state. Restart no longer requeues interrupted jobs. Duplicate automatic submissions return the existing failure; Processing exposes errors and an explicit Retry job action. Work ownership propagates failure to unfinished ingest runs, process plans and preview samples, including legacy interrupted regroup/processing work. Finished reviews remain intact; unattempted collection children and remaining batch books are queued in the same transaction as failure. Explicit retry selects failed work rather than repeating completed siblings or the remaining batch. Process retries reset only their own failed reservations, retaining normal source/approval validation.

Addressed both PR #11 review findings: scanning now queues inspection/artwork jobs for uncached books, and Settings prevents database changes while queued/running work exists. Submissions/claims and settings changes share a configuration lock; isolated workers pin their database. Added schema 11 for deadlines/ownership and schema 12 for indexed failure/history lookups. The live service configuration is set to one worker with its existing 4 GiB cap. Test defaults remain in temporary directories even when APP_DATA is unset.

Validation: full backend suite 770 passed at 95.68% coverage, followed by 42 supervisor/OS checks and two final environment/deadline checks. All 22 integration tests and 94 web tests passed; web coverage gate, typecheck/build, Ruff and whitespace checks passed. A real process is killed at its deadline and the next real isolated job completes. An 800-launch regression keeps PYTHONPATH fixed rather than growing it on every book. The running service shows one isolated child and a persisted 1800-second deadline, retaining the 4 GiB cap. Before/after audit found no missing queue records (`data/job-safety-audit-20261104/`).

## 2026-11-04: FFmpeg 6 decoded-duration compatibility

PR CI exposed a Linux-only split-MP3 repair assertion: FFmpeg 6's final progress timestamp reports the start of the last packet, undercounting about one MP3 frame per part. Reproduced the identical 179 ms discrepancy with isolated FFmpeg 6.0. Counting decoded PCM samples confirmed the repaired output preserved the sum of the input samples; the planned duration was short.

Decoded duration now uses the final `astats` sample count at a fixed 48 kHz analysis rate, with no PCM buffered or written. Only the sample-count statistic is enabled. Progress still verifies completion; level-prefixed warnings/errors remain diagnostics, while informational metadata/statistics do not falsely mark an input damaged. The existing 150 ms output assertion is unchanged. Unit doubles explicitly give a misleading progress timestamp to verify that the sample count wins, and missing/zero counts or incomplete decoding still fail closed.

Validation: 302 process/ingest unit tests passed; all 21 integration tests passed with isolated FFmpeg 6.0 and again with installed FFmpeg 9. Ruff and whitespace checks passed.

## 2026-11-04: fix repeated OOM restarts and unresponsive HTTP

The service had repeatedly been killed by systemd-oomd/the kernel overnight. A loose MP3, The Road to Kalamata, has a malformed `track=1/4294967295` tag. Its track total expanded discovery recursively into the entire Books directory (16,830 files). On completing inventory, missing-track calculation materialised `set(range(1,total+1))`, attempting billions of integers. After limiting discovery, the same allocation reproduced immediately: Python RSS reached 3.3 GiB, MemoryHigh throttling held the GIL, and even health requests timed out. The second ingest worker waited for the active ingest lock, so durable recovery repeatedly retried the same failing work.

Loose-file expansion now requires coherent direct audio siblings and never recursively enters unrelated subdirectories. Sidecar discovery uses the same bounded sibling scope; explicit folder/collection selection remains recursive. Missing track/disc counts are calculated by subtracting observed, in-range unique numbers from the declared total, with no allocation proportional to untrusted totals. Raw malformed counts remain visible and completeness gates still fail rather than silently treating a corrupt total as complete.

Deployed with the existing two-worker/4 GiB limits. The original durable Road job passed inventory, work/recording identification and reached alignment; progress now reports one file rather than 16,830. Live validation during processing: health 14 ms, Library workflow 31 ms, Operations 162 ms, books 212 ms; Python RSS about 360 MiB (service cgroup including filesystem cache about 1.2 GiB). No source audio or queue entries were discarded.

The Road run subsequently reached quality review, safely retaining the malformed missing-track count for review. Validation: 290 ingest/grouping/HTTP unit tests passed, all 21 integration tests passed, and 28 final focused inventory tests passed. A real byte-split MP3 integration regression ensures recognised numbered sibling families still reunite after tightening discovery. Ruff and whitespace checks passed. The final fix is loaded in the running service.

## 2026-11-04: prefer English work editions

English now wins among strongly compatible work entries before popularity/edition-count tie breakers; incompatible titles, authors and volumes remain excluded. Open Library retains explicit edition language during subtitle hydration and chooses the English duplicate when equivalent subtitle editions are encountered. Unknown or multilingual edition language remains unknown. Saved, verified work-to-edition links can recover the same language evidence during resolver projection (version 18).

A consistent full title tag, matching an explicitly English linked work edition and compatible with the existing author/main title, can disambiguate a shortened album title. Mixed chapter titles, missing language, invalid links and manually locked titles cannot use this rule. No language is inferred from the words in a subtitle. Raw tags/provider archives remain unchanged; HTTP reads continue to use saved views, with projection/approval performed by queued workers.

Read-only cached replay of 142 reviews finds one additional eligible work: Homo Deus (#383), supported by Hardcover and the English Open Library edition. Yuval Noah Harari and original publication year 2015 remain resolved; the 2017 English-edition date does not replace that original year. No eligibility regressions. Results are in gitignored `data/audio-work-identification-audit-20261103/english-editions-{replay,summary}.json`.

Validation: 295 provider, identification, title and HTTP ingest unit tests passed, including 13 new language regressions. Ruff and whitespace checks passed. Deployed with the existing two-worker/4 GiB limits; saved-evidence automatic approval is queued under the existing policy.

## 2026-11-03: distinctive full titles can correct unverified artist credits

Resolver version 17 allows title-led discovery when a long, distinctive full title agrees despite the local author tag. A single unambiguous work-catalogue hit can supply search terms; replacing an unlocked local author requires agreement from two independent families, including a work catalogue. Every returned full-title author set must agree, including complete coauthors and pairwise initial variants. An observed collision vetoes the override. Short/generic titles, omitted subtitles, different volumes and manually locked authors cannot use it. A compatible exact audiobook ASIN can supply the title-led discovery evidence; recording consent remains separate.

The original artist credit remains in archived identity evidence and `unverified_authors` in the projected catalogue record, rather than being merged into the inferred work's authors. New tests cover successful inference, discovery before consensus, single-source refusal, collisions, manual locks, generic/partial/volume mismatches, coauthor conflicts and competing middle initials. Cached replay of the same 142 reviews adds no further eligible works beyond the preceding audiobook fix, with no eligibility regressions. Results: gitignored `data/audio-work-identification-audit-20261103/title-consensus-replay.json`.

Validation: all 313 identification, contributor, title and HTTP ingest unit tests passed; Ruff and whitespace checks passed. Deployed with the two-worker/4 GiB limits intact; Sherlock's existing durable metadata-refresh job remains queued.

## 2026-11-03: audiobook discovery before original-work identification

The work-provider stage now queries configured audiobook providers first. A compatible exact ASIN, or an unambiguous title/author match with local narrator and runtime agreement, supplies canonical work-search terms. Conflicting authors/volumes and ambiguous recordings cannot supply the hint. Accepted evidence remains archived in the provider stage; work approval still requires an original-work catalogue link and independent source families. Audible/Audnexus remain one family, and recording/quality consent remain separate. An unknown writer requires independent work-catalogue corroboration. Audiobook release dates do not populate original publication years.

Contributor roles now recognise plural introductions/forewords/afterwords. Joined title/subtitle matching works in both directions, including when a recording supplies punctuation for two previously joined work titles. Resolver version 16 refreshes saved projections; collecting missing audiobook evidence still requires a queued metadata refresh, which reuses verified inventory.

A read-only replay of 142 current metadata reviews combines previously saved work evidence with cached audiobook lookups, omitting uncached requests and preserving existing candidate contradictions. Sherlock Holmes: The Definitive Collection (#1945) is the single additional eligible work: an exact B06X1BRZYC audiobook plus Hardcover corroborate the collection and Arthur Conan Doyle. Stephen Fry is retained as an introduction contributor/narrator. This identifies the complete collection, not a single constituent novel; Hardcover's 2017 date describes that collection. Replay made no live approvals. Audit script/results are in gitignored `data/audio-work-identification-audit-20261103/`. Deployed with two shared workers and the existing 4 GiB service cap; Sherlock refresh job `f8c6e5459e474420abc2d35a3c2c6c78` is durable and waits behind existing work.

Validation: 700 full backend unit tests passed, 284 identification/contributor/title tests passed, final focused checks passed (101 tests plus 12 checks covering the last title/refresh adjustments), and all 21 integration tests passed. Ruff and whitespace checks passed. Coverage was not recalculated in this pass.

## 2026-11-03: all server analysis behind a shared durable queue

All source scanning, provider matching/enrichment, source inspection, approval validation, recording selection, evaluation, rating, proposal/build execution and listening-preview generation now run through one SQLite job queue. Default concurrency is one worker total (`APP_WORKERS`, 1–8); process jobs use one internal book worker and previews no longer have a separate pool. HTTP processing actions return 202/job ID and the browser polls saved job outcomes. Identical pending submissions coalesce, transactional claims enforce the shared cap, known book resources exclude overlapping work, dead-owner jobs recover, interrupted batches resume their saved active run, and remaining selected IDs stay durable. Catalogue mirror writes are deferred to the queue. Existing consent/source validation remains in the queued actions.

Reviews are materialized by workers instead of loading full evidence on GET. Saved provider status, covers/index, source details/sidecars, listening status/locations, plan summaries and output metadata live in read models. JSON reads use database records; static/media routes serve existing assets. Configuration uses its bootstrap settings file with the displayed settings saved in SQLite. Run evidence views return at most 100 small event headers. Policy updates queue a saved-view refresh.

Cutover captured four interrupted runs, two pending regrouping entries and metadata automation. All six book jobs and automation are restored in the durable queue. A local intermediate schema-nine queue lacked the later resource column; schema ten explicitly migrates it without losing jobs, with an exact regression. Live validation: one active worker, no failed jobs, duplicate evaluation requests share one job ID; processing POSTs return in 21 ms / 2 ms. Reads: cover index 2 ms, Library workflow 34 ms, Operations 204 ms, identification page 123 ms. Python RSS about 221 MiB during startup/recovery. Audit snapshots: gitignored `data/shared-queue-audit-20261103/`.

Validation: full backend suite 687 passed, 95.67% coverage; subsequent migration/queue/server subset 69 passed, including the additional schema-nine regression; 21 integration tests and 93 web tests passed. Web coverage gate, typecheck/build, Ruff and whitespace checks passed. Existing source grouping/identification fixes remain included; source audio is not modified by this queue migration.

## 2026-11-03: lookup-only reads and bounded server memory

Library workflow and Operations previously loaded every current run's full evidence and re-evaluated approvals, including legacy metadata projection. Book details could probe sources and update durations; cover GETs could extract artwork and fetch providers. These reads now return compact saved SQLite summaries and existing local assets. HTTP GET/HEAD cannot project old identification evidence. Resolver projections are stored in SQLite instead of a global in-memory cache. Run iteration and review formatting load one run at a time, and startup reconciliation runs on the background worker.

The old service repeatedly hit OOM limits (about 25 GB memory and 47 GB swap peak). Captured eight queued entries plus the active run and verified all nine durable records before restart. Every entry remains queued, active or advanced to review after recovery. Live timings after deployment: health 6 ms, books 144 ms, Library workflow 30 ms, cover index 162 ms, Operations 152 ms, identification queue 279 ms, source summary 9 ms and book details 11 ms. Python resident memory measured about 164 MiB after these reads; total service memory including background audio work was about 710 MiB. Audit snapshots are under gitignored `data/server-read-audit-20261103/`.

Validation: full unit suite 668 passed at 95.52% coverage; 48 focused tests also cover two subsequently added saved-probe/bounded-log cases. All 21 integration tests and 91 web tests passed, with web coverage gate, production build, Ruff and whitespace checks. File SHA-256 values already exist in SQLite inventory JSON. A normalized content/location index for reuse after moves is documented as future work, not implemented by this timeout fix.

## 2026-11-03: 45 fragments, three works and stale duration headers

Read-only inventory and decoding confirm 16 Summer's Lease parts, 23 Reborn on the Run parts plus a five-minute preview, and five Ego Is the Enemy byte chunks. Grouping version 4 unites them into three work groups, preserving the preview and all source hashes. Ego's decoded chunks total 6:56:18 instead of the erroneous 12:19:57 header-based sum. Diagnosed header/decoder faults now force encoding regardless of mono/bitrate/loudness, with original evidence and corrected timing retained in plans. No live regrouping or consent writes were made. Bret Easton Ellis (723 files / six groups), Adrian Mole (473 / nine) and Just William (395 / ten) retain identical work boundaries. Details: [fragment audit](fragment-grouping-review-20261103.md).

Validation: 666 backend unit tests passed, 95.46% coverage; 21 integration tests passed, including real split-VBR encoding with a clean output header. Ruff and whitespace checks passed.

## 2026-11-03: broader lookup and contributor fixes

Resolver version 15 separates slash-joined personal credits, recognises explicit reader labels, guides second-source discovery with an unambiguous first-source hit, and compares inferred authors against the complete title cohort. One source still cannot approve identity. Numeric chapter labels cannot infer unrelated work authors. Read-only replay of 144 current pending reviews finds six distinct eligible works: Build, Losing the Signal, A Far Cry from Kensington, Flying Blind, Now You See Me and The Pyramid of Lies. Two need only current evidence projection; four require improved second-source lookups to be saved by a refresh. Archived Hardcover results and read-only Open Library/cache lookups support the replay; contradictory old candidates were retained. No catalogue/queue writes were made. See [broad identification audit](broad-identification-review-20261103.md).

Validation: 637 backend unit tests passed, 95.42% coverage; Ruff and whitespace checks passed.

## 2026-11-03: revised editions identify the original work

Resolver version 14 uses saved, explicitly attached publication-edition titles as work evidence and ignores numbered edition labels in work comparison. The Pragmatic Programmer (#1864) now passes all nine book checks with two source families, original year 1999 and Addison-Wesley; its complete 20th Anniversary / 2nd Edition title remains the recording clue for the next stage. Source aliases are deduplicated and do not mutate archived provider evidence or count as independent providers. Replay of the 155-review snapshot adds only this eligible book (three to four). Endurance gains an original publisher; The Impossible First remains blocked on contradictory original years, and four other entries gain metadata without becoming eligible. See the [revised-edition audit](identification-review-20261103.md#revised-edition-follow-up-the-pragmatic-programmer).

Validation completed after the earlier approval-review usage-limit interruption: 621 backend unit tests passed, 95.37% coverage; Ruff and whitespace checks passed.

## 2026-11-03: anniversary wording belongs to edition evidence

Expanded the existing numbered-anniversary normalization to plain Anniversary Edition and hyphenated forms, removing empty surrounding brackets for work searches. Original recording titles remain unchanged. Regression tests cover search/provider agreement, original publication year, preserved recording titles, genuine Anniversary titles and retained volume/revision evidence. Resolver version 13 ensures saved evidence uses the current rules. The 155-review replay has no additional work/recording measurement changes or newly eligible books from this adjustment.

Validation: 613 backend unit tests passed, 95.35% coverage; Ruff and whitespace checks passed.

## 2026-11-03: conservative omitted-initial book identification

Follow-up after pulling main: the first stage targets the original work rather than requiring an exact edition. Resolver version 12 treats explicitly attached Open Library editions as evidence that alternate/translated subtitles belong to one work, preserving distinct unlinked works and volume numbers. Endurance (#371) newly passes all nine book checks with original year 1959; Hitch-22 gains source agreement but remains blocked by its local identity conflict. The 155-review replay changes only those two work results, taking eligibility from two to three. Automatic audiobook/quality approval remains separate and no live approvals were changed. Details: [attached-edition follow-up](identification-review-20261103.md#follow-up-attached-editions-identify-the-original-work).

Follow-up validation: 603 backend unit tests passed, 95.35% coverage; Ruff and whitespace checks passed.

Replayed 155 current pending book reviews (including seven legacy reviews) from saved evidence with read-only SQLite access. Allowing one omitted middle initial with a strong title and exact full first/surname makes only The Player of Games (#429) and Apollo (#851) eligible: both gain Open Library corroboration alongside Hardcover and pass all nine book checks. Official author/NASA/Smithsonian references support these identities. No other pending work result changed. Added competing-initial ambiguity protection, negative name tests, stage-automation regressions and a version-10 saved-review projection test; resolver version is now 11. No live approval records were changed. Details and remaining candidates: [identification-review-20261103.md](identification-review-20261103.md).

Validation: 589 backend unit tests passed, 95.32% coverage; Ruff and whitespace checks passed. The complete suite ran outside the sandbox after reproducing and isolating a sandbox-only stall in an existing HTTP test.

## 2026-11-01: Library workflow filters and metadata-driven source groups

- Library now hides current/queued workflow entries by default, with counted inclusion checkboxes for Processing, Book identification, Audiobook identification, Quality, Proposed and Finished. Membership covers linked catalogue entries and selected-but-not-started batch items. Hidden workflow selections are cleared, and ingest submission reloads membership.
- Work agreement accepts shared authors despite additional credits. Additional names remain in evidence while canonical authors use matching primary credits. Known-name variants and shared local-author tags remain compatible; missing local authors require corroboration from two independent book catalogues. American Psycho’s saved evidence now passes 9/9 book checks and retains Mariano Antolín Rato as an additional credit.
- Book searches remove anniversary wording while recording searches retain the local edition clue. American Gods’ failed Open Library lookup used the full edition label; new work searches use American Gods, retaining 10th/Tenth equivalence for performance matching.
- Source inventory splits mixed collections by metadata/filename evidence before identification. Separate books may share a folder, while one work may span directories/discs. Each child run has an exact scoped manifest and independent consent. Collection history, existing plans/outputs and source files are retained. Queued children resume after restart; old pending mixed collections are regrouped with fresh consent. Adrian Mole’s saved 473-file inventory separates into nine source groups, including episodic recordings and a standalone interview.

## 2026-11-01: sequential book and audiobook identification

- Split metadata into independent Book identification and Audiobook identification review pages/stages, followed by Quality. Book catalogue search uses configured Hardcover/Open Library providers; after book consent, recording providers are searched afresh with the approved title/authors and local performance clues. Recording resolution freezes the approved work, including its publication metadata, while refining narrator, edition, runtime, language, release and abridgement.
- Book/source predicates and recording predicates have independent approval/automation settings. Changing book consent invalidates downstream recording and quality consent. Both provider manifests are verified before advancement and frozen in new plans. Previous combined metadata approvals continue to cover both identifications with their original signatures; old unapproved work begins at book identification. Startup reconciles existing pending reviews through enabled identification automation.
- Navigation now follows Library, Processing, Book identification, Audiobook identification, Quality, Proposed, Finished, Settings. Old metadata/ingest URLs remain aliases. Quality retains its five asynchronous cached listening excerpts.
- Validation: 451 backend unit tests passed (95.39% coverage), 78 web tests passed with the coverage gate, and 5 real-audio integration tests passed. TypeScript, production build, lint and diff checks passed; Chromium verified all three screens with isolated saved fixtures and no live approvals. Build is staged in `/tmp/strata-identification-static` pending completion of the ongoing audio analysis, so the old server and its matching frontend remain together until idle.

## 2026-11-01: earliest work publication and Charles/Chuck

- Original publication now chooses the earliest supported year across compatible work catalogues, retaining later dates as visible alternatives. Audiobook dates cannot participate, and manual year locks still win. Less Than Zero resolves to Hardcover 1985 rather than Open Library 2025. The separate 1985 Open Library candidate contains a narrator in its author list and remains excluded by complete-author checks.
- Charles/Chuck is an explicit same-surname name variant. Title-only discovery retries author-qualified searches with no compatible results while preserving full acceptance checks. Rechecked But What If We’re Wrong in run `1d012c1fe8d64eb68e649672548a4d7a`: book identification passes 6/6 and year resolves to 2016; recording evidence is still incomplete, so it remains in metadata review.
- Metadata resolution version 3 projects saved provider evidence into current review views without modifying original events; frozen proposals keep their saved assertions.
- Strong matching book titles now provide context for similar author given names with the same surname and matching middle names, without requiring a nickname dictionary entry. Narrator matching remains independent. Validation: 445 backend unit tests passed with 95.42% coverage; 216 focused regressions passed. Deployment waits for the ongoing Less Than Zero quality analysis to finish; a one-off idle-check job performs the authorized restart without interrupting it.

## 2026-11-01: person names and quality listening previews

- Person comparison removes leading honorifics while preserving raw credits. Complete author and narrator lists allow unambiguous initials, a small explicit nickname vocabulary and one spelling edit in a sufficiently long name component; given-name and surname differences cannot both be fuzzed. Approximate author matches retain a lower score than exact normalized names. Coauthors and ambiguous names remain review cases.
- Quality review starts background preparation of five stable, jittered excerpts across the selected recording on page open. Each clip is up to 30 seconds, normalized to -20 LUFS with a -1.5 dBTP ceiling, and cached under the run/selected-source fingerprint. Two workers bound decoding concurrency; atomic files and cross-process locks prevent partial/cache duplication. Status polling enables individual samples as they finish; failures can be retried. Source size/mtime changes invalidate cached playback, and source audio is never rewritten.
- Validation: 431 backend unit tests passed (95.37% coverage), 76 web tests passed with the coverage gate, and 5 real-audio ingest/finished integration tests passed. TypeScript, production build, lint and diff checks passed. Refreshed All That Remains as `03164c5bcb7846458bc3e331e76d40cc`: correct Apple/Sue Black edition now corroborates English, all 16 metadata checks pass, automatic metadata approval recorded, and quality remains manual. Restarted the idle server to deploy.

## 2026-11-01: Beastie Boys full-cast matching

- Fixed provider author arrays containing joint credits (`Michael Diamond & Adam Horovitz`) so complete author matching can fetch Apple recording details. List-form comma-inverted names remain intact; explicit contribution roles and real coauthors remain preserved.
- Generic ensemble descriptions such as `full cast` are no longer treated as people's names. Without local named performers, the narrator gate still requires distinct provider-family corroboration. Partial rosters agree only with multiple matching names plus compatible work/runtime/language and release date or recording ID. Unambiguous initials are supported without equating different full names; raw source spellings and rosters remain intact.
- Verified Beastie Boys Book: Audible/Audnexus list 15 credits; Apple lists 44 including `B. Midler` versus `Bette Midler`. Both describe the English 30 October 2018 release at 762 minutes, within 15.2 seconds of the 98 source files. Fresh six-provider evidence is in `data/metadata-check-606-20261101T143427/`. Refreshed run `ca7feee385034dd68bef5b173d58674b` preserves original `1013c3e2b9334e698900fc64ba874c0a`, passes 16/16 metadata checks and records automatic metadata approval. Audio analysis completed and it now waits in Quality review, with no quality approval or processing proposal.
- Validation: 408 backend unit tests passed with 95.43% coverage, 4 real-audio ingest/finished integration tests passed, and lint/diff checks passed. Negative controls cover solo narration versus an ensemble, unrelated rosters, missing/different dates, runtimes and languages, and ambiguous initials. Restarted the idle server and verified the live approval boundary; quality and final processing remain manual.

## 2026-11-01: independent book and performance metadata, source filenames

- Separated written-work identity from recording identity in resolution, checks and field provenance. Book title/author/year/publisher have their own records and cannot acquire an audiobook publisher or release date. Recording title/edition, narrator/cast, publisher, release date, language, abridgement and runtime remain in their own namespace. Sources can identify the book while describing an incompatible performance; those recording fields are retained as an unapplied candidate, with local narration credits preserved.
- Metadata review shows independent book/performance check groups plus shared source-integrity checks. Automatic approval requires all groups to pass, and old automatic approvals cannot bypass the new predicates. Policy aliases migrate to `work.score` and `recording.conflicts`, with separate work-conflict and recording-match checks. Historical evidence is projected without modifying its saved events or making provider requests. Explicit manual stage approval still records overrides.
- Capitalist Realism `fb6d91f1055a4199a5feb67a57ec3cf8` now shows book 6/6, performance 2/7 and source 3/3. Russell Brand remains its local narrator; Tom Lawrence/2024/250 minutes is an unmatched catalogue candidate. Original work year 2009 remains distinct from the local 2021 date tag. The book publisher remains unknown. A Man for All Markets and American Gods retain passing work and performance checks.
- Proposed plans and new final evidence retain both metadata namespaces. Existing proposals use only frozen data; the five oldest proposals saved just title/author, so publishers, dates and a separate recording title are marked unknown rather than inferred. Manual saved titles remain intact.
- Added source folder/filename summaries to entries across Library, Processing, Metadata, Quality, Proposed and Finished. Three filenames plus remaining/total counts provide context without rendering huge lists. The lightweight source endpoint uses inventory or directory listings without audio probes; book details already retain full filenames. Source unavailability does not block the rest of the page.
- Validation: 396 backend unit tests passed with 95.41% coverage, 74 web tests passed with the coverage gate, and 4 real-audio ingest/finished integration tests passed. TypeScript, production build, lint and diff checks passed. Chromium verified separate publication/performance fields, the unmatched narrator comparison, source filename and frozen proposal sections. Restarted the idle server and verified live responses; Capitalist Realism remains unapproved.

## 2026-11-01: separate review pages, operations and finished identity

- Library is now the source picker with search, individual/page/all-match selection, persistent selections across filters and 25-book pages. Ingest candidate picking no longer duplicates the catalogue on another page. Metadata and Quality have their own routes and approval controls; their queues filter before 50-run pagination and exclude running, failed and superseded runs. The old ingest URL opens Metadata.
- Processing defaults to all current operations across both workers, including selected books not yet reached inside a batch, active analyses, pending approvals, failures and final plans. Failures support retrying analysis. Completed work is hidden by default and available through Show completed/Done. Both workers expose their active and queued tasks; stage pages link to Processing for execution progress.
- Planning and execution now require a real title and author before producing a final book, including execution of older approved plans. Final summaries retain their resolved identity and Finished reads those fields. Approved ingest metadata can supply a provider cover, and fresh metadata approval invalidates an earlier no-cover cache entry.
- At the user's request, removed finished copies of Is This Anything (388), Outliers (413) and Private Equity (363, Carrie Sun; requested because its cover was missing). Source files were verified and preserved. Removed their processed records/plans and queued online re-ingest of exactly those three. Old run histories remain intact; removal audit and output metadata are in `data/reingest-removals-20261101T125809/records.json`. Great Expectations remains in Finished. Metadata automation remains on; quality/final approval and execution remain manual.
- Added a separate Proposed page for post-quality final-plan approval, at the user's request. Processing retains queued/running/failed work and completed history; proposal approval controls appear only on Proposed. Navigation follows Library, Processing, Metadata, Quality, Proposed, Finished, Settings.
- Validation: 383 backend unit tests passed with 95.29% coverage; 64 web tests passed with the coverage gate, 4 ingest/finished integration tests passed, plus TypeScript, build, lint and diff checks. An isolated Chromium preview verified selection and navigation through all four pages, failures and the completed toggle. Restarted the idle server and verified separate live queues and the three re-ingest operations.

## 2026-11-01: American Gods chapter and edition matching

- Diagnosed run `52de5dff9f74422d928bcadd095e7d44`: its 115 chapter files were treated as 115 recordings, comparing a 14.59-minute chapter against the 1,176-minute provider recording. The `10th`/`Tenth` spelling difference also reduced the detailed Audible edition's score, favouring a sparse AudiobookDB record. These caused the missing work/runtime/cast/language/abridgement checks. The displayed running state was left by an earlier restart during quality analysis.
- Added leading-number chapter grouping for siblings with the same title stem and a sequence starting at 1; observed gaps still count as missing tracks, distinct titles and whole-file alternatives remain separate. Edition ordinals normalize for comparison while provider titles remain intact. Verified recordings supply their main title for later searches, and release dates guide Apple detail selection. Short storefront titles need matching recording fields/runtime to corroborate a verified edition; conflicting casts still require review.
- Apple Books' visible narrator card named only Daniel Oreskes, but its embedded product response contained all five performers. The adapter now retains that full product-specific cast. Comparison accepts an unambiguous adjacent surname transposition with the rest of the name identical (`Dennis Boutsikaris` versus `Dennis Boutskiaris`); original source spellings are preserved.
- The recheck passes 14/14 metadata predicates: one 1,182.27-minute recording, 0.53% runtime difference, title/author similarity 0.93, four source families, English, full cast, unabridged and original publication year 2001. Evidence is under `data/metadata-check-495-20261101T120823/`. Restarted the idle server, recorded the old attempt's interruption, and started replacement run `fa9ee377329544b18c2f09489c4c6a10`; its metadata was automatically approved and quality analysis began. Quality approval and final processing remain manual.
- Validation: 370 backend unit tests passed with 95.21% coverage; all 3 real-audio ingest integration tests passed, plus lint and diff checks.

## 2026-11-01: automatic metadata approval

- Added independent metadata and quality automation settings, retaining the existing final-plan approval and execution settings. The Ingest checkbox enables metadata approval only when every metadata predicate passes; saving also queues existing latest pending reviews. Approval revalidates source, catalogue and provider evidence. Superseded, incomplete, ignored and failed reviews are not promoted. Older policies retain their previous automation behaviour.
- Restarted the idle server and enabled metadata automation in live policy revision 1. Quality approval, final processing approval and execution remain manual. A Man for All Markets (`bb454487748f457987919f5bd012cec3`) passed 14/14 metadata checks, recorded an automatic metadata approval and advanced to Quality review with no quality approval or processing proposal. Other pending books with failed metadata checks remained pending.
- American Gods' saved Audible, AudiobookDB and Hardcover results explicitly identify the tenth anniversary edition, so that information should be retained. Its interrupted run passes 8/14 metadata checks; inventory currently treats the 115 numbered files as separate representations, creating an incorrect runtime comparison. That grouping issue remains unresolved by this automation change.
- Validation: 354 backend unit tests passed with 95.13% coverage; 3 real-audio ingest integration tests and 66 web tests passed with the coverage gate, TypeScript check, production build and lint.

## 2026-11-01: metadata recovery and separate ingest approvals

- Fixed Audible identifier recovery from embedded product URLs, explicit contributor-role matching, regional identifier lookup, Audnexus abridgement retention, work-title linking and cross-family narrator/language corroboration. Missing provider fields remain unknown rather than becoming invented conflicts. Original contributor strings and provider responses remain in evidence.
- Rechecked A Man for All Markets using the configured providers. Original run `b0513fa901f447a5bbc4d673bbd0faad` is preserved; refreshed run `bb454487748f457987919f5bd012cec3` passes 25/25 predicates, resolves Edward O. Thorp as author/narrator and gets original publication year 2017 from Open Library. The publisher also confirms 24 January 2017: https://www.penguinrandomhouse.com/books/178551/a-man-for-all-markets-by-edward-o-thorp/9780812998740/. Hardcover's generic 2016 date is retained as an advisory, distinct from the explicit first-publication year. A lossless lift finishing inside the existing loudness tolerance no longer creates a blocking shortfall warning.
- Ingest now stops for Metadata review before alignment/quality, then Quality review before planning. Both approvals persist with their evidence signatures and policy revision; quality ratings bind to quality approval. Neither proposal nor processing approval can bypass these stages. Existing completed runs require the new approvals and can reuse verified audio evidence. Superseded runs remain in history. Global automation settings remain off; the user authorizes routine checks with the configured metadata providers without repeated confirmation.
- Added stage queues, resolved metadata/source rows, per-phase checks and actions, and collapsed technical history/policy details. The candidate picker remains available in All stages; dedicated review queues focus on their books. History pages keep runs older than the newest 50 accessible, with page-specific queue counts and superseded-run detection across pages. New runs bind their selected entry immediately so an older run cannot remain current during inventory. CLI `ingest-approve RUN_ID metadata|quality` advances the same workflow; `ingest-refresh RUN_ID` starts a fresh metadata review while retaining unchanged audio evidence.
- Validation: 348 backend unit tests passed, coverage 95.09%; 3 ingest integration tests and 65 web tests passed with the coverage gate, TypeScript check, production build and lint. Chromium verified the focused metadata/quality queues, two approvals and processing handoff with isolated data; no real stage approvals or audio processing were performed during validation.

## 2026-11-01: searchable ingest batches

- Agreed frontend direction: a per-book processing journey plus queues for reviewing each stage. First step is a candidate picker in Ingest, linked from Library, replacing manual catalogue-ID entry.
- Search source entries by title, author, narrator, series, path or ID; select individual entries, pages or all matches, and review selected entries across searches. Requests set their limit to the selected count so batches larger than five are not truncated. Failed submissions retain selection; pending requests disable repeat submission.
- Fixed selection being disabled when the separate ingest dashboard was loading or failed. Selection now depends only on candidate availability and submission state, with its count beside the bulk buttons. Removed the 200-entry cap for explicit selections through the UI, API and pipeline; automatic discovery remains capped. Regression tests cover unavailable/loading dashboards and selecting/submitting 1,681 entries with group deduplication.
- Validation: 59 web tests passed with the coverage gate, plus 45 backend ingest tests, lint, TypeScript check and production build. Chromium verified individual, page and all 1,681 selections against a simulated dashboard HTTP 503; no live ingest was started.

## 2026-10-31: books-only ingest-to-output pilot

- Deferred non-books at the user's request. Processed three reviewed book matches with direct Audible product checks and explicit manual review events, retaining unknown corroboration and unchanged global automation settings.
- Private Equity: 12 chapters, -18.1 LUFS/-1.6 dBTP; Is This Anything?: 9 chapters, -18.9/-1.1; Outliers: 16 chapters, -19.2/-1.6. Durations, original source hashes and portable evidence verified. Previous Seinfeld pilot output preserved separately.
- Fixed selected-source handoff validation to include preserved alternatives and fixed the stereo-to-mono gain decision to use measured mono loudness. Existing encoding, loudness tolerance and peak thresholds retained. 296 unit tests passed, 95.52% coverage; targeted real-audio integration, lint and diff checks passed.
- Full outcomes and review limitations: [book-output-pilot-2026-10-31.md](book-output-pilot-2026-10-31.md).

## 2026-10-31: optional audio analysis dependencies installed

- Installed signed Arch Chromaprint 1.6.2 user-locally and NumPy 2.5.4/SciPy 1.18.1 in the project virtual environment. Added optional requirements and `./app doctor --audio` checks, with unit coverage.
- Verified real local fingerprints and waveform alignment against matching Hitch-22 excerpts and an unrelated recording. Dependency consistency passed. These smoke tests do not change ingest thresholds or its current alignment implementation. Setup and evidence: [audio-tools.md](audio-tools.md).

## 2026-10-31: integrated staged ingest and metric policies

- Built the nine-stage ingest pipeline with append-only stage attempts, timings, raw response archives, immutable source manifests and policy snapshots. Added CLI/API and Ingest UI for background jobs, saved metric evaluation, resumable failures and manual proposal. SQLite migration 8 uses existing pre-migration backups.
- Integrated work/recording evidence, selected candidate details/reviews, numeric Audible IDs, Apple public page details, optional keyed Google Books, per-file alignment, quality and source selection. Verified partials remain alternatives; differing/unresolved performances require review. Existing processor consumes only selected files and reused measurements. Final metadata carries selected values/provenance, rejected alternatives and copies of raw source evidence.
- Added atomic group proposals/plan claims, full-hash/sidecar/catalogue/evidence revalidation, auto-job isolation, downstream processing metrics and content-hash quality reuse. Automation defaults off. Metrics/thresholds are predicates, not confidence percentages; unknowns fail closed.
- Live run `19e1dc09b3d04882a86fb1420567bf45` selected Hitch-22 MP3s: 99.9849% coverage, zero unmatched windows, -1.1 dBTP. All stages completed; 21/25 initial gates passed and four reasons kept it in review. No live processing. Per-part matching was corrected during validation so encoder timing differences do not suppress full-source coverage. Earlier run retained.
- Validation: 293 backend unit tests, 11 integration tests and 42 web tests passed; backend coverage 95.48%, web coverage gate/typecheck/build and lint passed.
- Usage, stages, thresholds, preservation model, calibration limits and validation: [ingest-pipeline.md](ingest-pipeline.md).


## 2026-10-31: Hitch-22 audio alignment and quality pilot

- Added an offline experimental comparison module (`python -m app.audio_compare`): local 100 ms energy fingerprints, whole-window correlation, conservative anchored refinement, unresolved/ambiguous states, source hashes, per-file timing maps, coverage and sampled quality diagnostics. No external audio service or new dependency.
- All 15 MP3 parts align to the whole M4B; 2108/2108 windows matched, covering 99.9849% of the reference by correlated intervals. Small boundary differences remain below the feature resolution; neither source has embedded chapter markers. This identifies the same performance, not bit-identical audio or semantic chapters.
- Additional whole-book quality scans and matched spectral samples support preferring the MP3 set: mono 80 kbps/22.05 kHz vs AAC 16 kbps/12 kHz, more high-frequency energy, -1.1 vs +0.8 dBTP. Both decode successfully. All 16 originals rehashed unchanged; no processing approved.
- Full report and provenance: [hitch22-audio-comparison-2026-10-31.md](hitch22-audio-comparison-2026-10-31.md). Unit/adversarial and real FFmpeg tests cover excerpts, reordered parts, gain/encoding changes, edits, repeated content and negative controls.


## 2026-10-31: approved seven-source comparison completed

- User explicitly approved all listed services; ran fresh Audible UK/US, Audnexus, Hardcover, AudiobookDB, Open Library, Apple Books and five Google Books attempts against the original sample. Saved 179 response/page snapshots without credentials or audio. Google returned 429 throughout and contributed no corroboration.
- Investigator outcome: 18/20 identities externally supported; 14 book-related entries represent 13 original works, plus stand-up, one short story and two Audible episodes. The two radio episodes remain externally unresolved. Four entries have embedded IDs corroborated by fresh Audible; another 12 have recording candidates, not automatic approvals.
- Apple product pages supplied narrator/language/duration missing from search results. Collected 45 selected Hardcover review excerpts and 41 Audible product review excerpts. Rejected Thai Outliers and Spanish Hitch-22 false positives; preserved regional/reissue conflicts. Full Sherlock collection measures 3484.5 minutes vs Audible 3484.
- Findings, source assessment and per-entry judgements: [sample20-multisource-review-2026-10-31.md](sample20-multisource-review-2026-10-31.md). Raw and structured evidence under `data/sample20-investigation-20261031T100258/`. No product-code, catalogue or queue changes.


## 2026-10-31: investigator review of all 20 samples

- Fresh Open Library on original catalogue fields: 2 unique links. Recovering titles/authors from tags and parent groups: 9 unique links, 4 duplicate-work ambiguities, plus a convincing Skunk Works candidate rejected by literal author comparison. Investigator assessment: 13 strong book-work identifications, 1 probable, 6 other audio items; 6 of the book entries are fragments, including two from the same book.
- Found duplicated complete M4B + MP3 parts for Hitch-22, explicit unabridged sidecar values ignored by the assessor, and a suspicious publication year for Private Equity. Full per-entry judgements, evidence paths and additional-source assessment: [sample20-review-2026-10-31.md](sample20-review-2026-10-31.md).
- Expanded provider investigation was blocked by automatic approval review pending explicit user approval of the destinations/account-backed API calls. Fresh external findings here are Open Library only; no catalogue or queue changes.


## 2026-10-31: fresh Open Library evidence

- `evidence-report --fresh-openlibrary` searches the candidate title and authors, fetches a uniquely matching work and its ratings, and includes the work description, subjects, original publication year and attributed rating/count. Conflicting recording identities, ambiguous works and contradictory work details are not linked. Work metadata cannot establish recording identity.
- Every search, details or ratings response (including failed requests) is archived before use in a unique, exclusively created JSON file under `data/evidence/openlibrary/`, with book ID, query, URL and retrieval timestamp. Raw unknown fields are retained. Refreshes never overwrite earlier responses or catalogue metadata; default reports remain offline. API reference: https://openlibrary.org/dev/docs/api/books and https://openlibrary.org/dev/docs/api/search.


## 2026-10-31: read-only identity evidence baseline

- Added `evidence-report [IDS] --limit N --stride N`: SQLite read-only snapshot, no normal DB connection/migrations/backups, provider requests, cache writes or queue changes. Every audio file is probed without the book page's 60-file cap. The JSON retains original catalogue/suggestion/extras, all root Audible sidecars, full ffprobe JSON, filename parsing, timestamps and file signatures. Selection retains field provenance, alternatives and manual locks.
- Separate work and recording checks: full title/author agreement, book-catalogue linkage, narrator/identifier, explicit abridgement, measured duration (2% or 120 seconds), album tags and conflicting source fields. Audible/Audnexus/sidecar count as one family. Assessment is deliberately conservative and not a probability. Even candidates cannot automatically process.
- Live stored-evidence sample: 20 books, every 25th eligible item by ID; all 20 require review. All lack a qualifying independent source family, linked work metadata and explicit abridgement; 18 lack recording identity/runtime evidence. One catalogue/filename title conflicts with recording metadata. Artifact: `data/evidence-report-2026-10-31.json` (local, gitignored). No conclusion about fresh provider hit rate follows from this stored-only sample.
- Next: fresh multi-source evidence collection with append-only history, edition-aware candidate association and explicit abridgement/recording fields before calibrating automatic approvals. Existing expiring provider caches are not an archive. File signatures are size/mtime, not content hashes; file completeness beyond what is present cannot yet be proven.


## 2026-10-30: the processor

- Metadata is now merged across sources instead of falling back down a list (user's direction: audiobooks have narrators and producers, the original book has its own metadata, match as many sources as possible and choose the best of each). Probes first: ISBN as the link from audiobook to book rarely works (Audnexus ISBN for 11 of 30 Audible books, Open Library found 0 of those 11, Hardcover recognised 0 of 30 ASINs), so the link is title + author with the audiobook sources agreeing first. New `merge.py` (weighted agreement per field, layers, locks, alternatives kept) and `sources.py` (normalised records, two-step gather, resolve, rows). Added a `locked` column: fields set by hand win; confirming a match locks nothing. Real result: Brave New World now resolves to Aldous Huxley / Michael York / Blackstone Audio / 1932, with "CBS Radio Workshop" shown as outvoted; Right to Kill gains its narrator (Dick Hill) and ASIN from the Audible catalogue search although the folder had none.

- The folder year is now the original publication year (user's decision). Open Library was chosen over Goodreads: Goodreads' terms exclude robots and data extraction (and it has had no API since 2020), so nothing was scraped and the supplied login was not used (stored at `~/.config/strata-listory/goodreads.cred`, mode 600, offered for deletion). On 12 test titles Open Library gave the exact first-published year for 9; misses were a missing work, an off edition-year (The Secret Barrister 2020) and a bad record (Oliver Twist 1822). Hardcover's release date is a weaker second source (it said 1986 for Born a Crime, which is 2016), hence the disagreement note. On the confirmed books: Brave New World 1932 (via Hardcover; Open Library did not match because the catalogue has the author as "CBS Radio Workshop, Aldous Huxley"), Banking on It 2020, Right to Kill 2016, Secret Diary of Adrian Mole 1982, Adrian Mole and the Weapons of Mass Destruction 2004.

- Output naming set by the user: `Author/[year] title/00n - author - title.mp3` (book title in every file name, chapter titles in the tags and playlist; no series folder). Added `year` and `year_source` to books (migration 6) with the priority described in the README, and a year tag (`date`). The Processing page now lists the metadata that will be written with each value's source and flags disagreements with the Audible metadata file; the first real example caught Brave New World's author as "CBS Radio Workshop, Aldous Huxley" (an AudiobookDB match) while the Audible file says "Aldous Huxley".

- Chapter sizes set by the user: split only above 60 minutes (parts of about 45), combine only when one is under 10 minutes and the pair stays within 30. Added one exception, flagged to the user: credits-length pieces (under 2 minutes) join a neighbour up to an hour. Real books: a 150-file title went to 26 chapters (12 to 51 minutes), the others stayed at 16 to 30.

- Limiter budget measured from the book's peaks instead of a fixed 3 dB: peak levels per short window are collected in the same pass as loudness; the lift is allowed until the 99th-percentile peak would lose more than 2 dB (loudest peak: 7 dB). On three real books this moved the end level from -20.9/-21.1/-20.6 to -18.7/-18.2/-18.0 LUFS; the one book re-encoded as a test kept its loudness range (6.6 to 6.1 LU) and came out at -1.2 dBTP.

- Added the manual approval queue (`process/queue.py`, Processing page, `process_plan` table): proposals carry the exact plan, a person approves or rejects, only approved plans run; a plan is refused if the inbox files changed since it was proposed. The server runs proposals and approved plans on one background worker thread.
- The first full copy to `~/audio-local` finished (428 GB, 29,086 files). rsync exit 23 came from two files whose names exceed ext4's 255 bytes (curly quotes in long titles); `audio-sync` now lists such files and still fails on any other error. The app's library was switched to the copy with `use-library` (5,128 paths moved, DB backed up first).

- Decided with the user (later the same day): process only confirmed books with a title and author; loudness has a tolerance band (3 dB quiet / 2 dB loud around -18 LUFS) outside which a book is normalised even if it would otherwise be copied; quieter is better than clipping or compensating, so the earlier idea of ReplayGain tags for what gain could not do was dropped, and the limiter allowance was cut from 6 dB to 3 dB.

- Built `./app process` after agreeing the design with the user: mono, LAME VBR tiers by content, -18 LUFS per book, copy-as-is for mono mp3 up to 96 kbps (lossless cut and `mp3gain`), chapters from embedded/Audible/file/pause sources.
- Timings on beelink: LAME V5 about 550x realtime per core; `ebur128` about 1000x (the `loudnorm` filter is 16x slower, so it is not used for measuring); a whole book takes 1.5 to 2 minutes. Measuring the spectrum of whole chapters was the slow part, so it samples three 90 s stretches per chapter.
- Calibration (30 speech and 30 music-heavy excerpts): silence fraction separates them (speech median 19%, music 0%); energy above 10 kHz does not (speech median -25 dB with p90 -21, music median -23, p90 -19). First guess thresholds on the HF ratio put every speech chapter in the top tier, which is why tiers are now chosen on pauses first.
- Bugs caught by testing on real books: a short credits track gave the merged chapter the wrong name; the spectrum pause fraction was computed against a zero duration when a segment ran to the end of its file; after encoding, true peaks overshot the limiter by about 0.8 dB, hence LIMITER_MARGIN.

## 2026-10-30: local copy of the audio share

- beelink has ~850 GB free and the share is 428 GB, so `audio-sync` mirrors it to `~/audio-local` (rsync, one way, nightly) and the app is switched to that copy with `./app use-library`. First full copy ran in the background (about 30 MB/s observed at the start). The copy is excluded from restic (it can be rebuilt from the share, which has its own backup story).
- Safeguards in the script after thinking about "sync" deleting things: never writes to the share; `--delete-delay` plus `--max-delete=300`; aborts if the share lists empty or below half of its previous top-level count; one run at a time (flock); failures raise the usual ntfy alert.

## 2026-10-30: resilience to the share being down

- Added `fs.py`: reachability probe with timeout, short memory and no pile-up, used before touching the audio share or a share-based backup folder; scans abort on a vanished share instead of storing half a library; the book page, covers, mirror and backups degrade as described in the README. The Linux mount table is read from `/proc/self/mountinfo` (`autofs` counts as a network mount because touching an unmounted automount mounts it, which can take a while); other systems skip the probe and rely on error handling.
- Not changed: the data folder must stay on a local disk (SQLite). The systemd service has no dependency on the mounts.

## 2026-10-30: Hardcover

- Hardcover's GraphQL API (token in `~/.config/strata-listory/hardcover.key`, not tracked) works as a search provider (4 of 4 test titles right) and gives ratings, counts, genres, moods, content warnings, tags, series, audiobook editions and public reviews. Its documentation pages returned 403 to my fetch tool, so the exact rate limit and terms were not read; the client keeps to about one request a second.
- First live run: 11 of 12 confirmed books enriched. Hardcover has several records for some classics, so a rating can come from a small duplicate record (Adrian Mole #1: 5.0 from 2 ratings); the book page shows the counts so this is visible.
- Bug found while testing: the backup folder pointed at the SMB share and the `server` PC was down, so opening the database failed with "Host is down". Backups now fall back to `<data_dir>/backups` when the configured folder cannot be written, and a failing automatic backup only prints a warning.

## 2026-10-30: mirror and provider covers

- Covers can now come from any provider (suggestion cover, then each provider by ASIN, then AudiobookDB by id) and are saved with the book's final data in a mirror of the audio tree under `<data_dir>/library/` (the share is read-only). Covers cached earlier under `covers/` move into the mirror on first use; only the "no cover found" markers stay in `covers/`.
- Rate limit and cache lifetime are properties of each provider class, not global constants.

## 2026-10-30: modular providers

- Audnexus (the project AudiobookDB succeeds) found 53 of 53 real ASINs from the `Audible/` folders (47 uk, 6 only in the us catalogue); AudiobookDB found 1 of 12. Only ~150 catalogue items (117 books) have an ASIN in the name, so title search matters more: Audible's own catalogue search returned the right book in the top 2 for 8 of 10 titles.
- Matching is now provider based (`providers/`). Sample of 60 scanned books (`match-test`, 2026-10-30, after the scanner fix below): Audnexus 5% (it is ASIN only), Audible 30% good / 33% any, AudiobookDB 30% / 50%, all three 37% / 55%. Misses are mostly unclean titles, not provider gaps.
- Scanner fix found through that test: chapter files whose names differ only in numbers (`AB_022`, `ch0`, `Part06`) were each becoming a "book". They now fold into one book, and a rescan removes untouched `new` fragments (1,104 on the live library; books 2,280 -> 1,681).

## 2026-10-30: book page and covers

- Found that 118 of the `Audible/` books have a `<title> [ASIN].metadata.json` next to the audio (Audible's own product data: narrators, series, publisher, release date, runtime, ratings, categories, summary, chapters, cover URL). The book page reads it as the richest local source; no API terms involved.
- Covers come from the user's own files (folder image, embedded art, then the cover URL in that metadata file), then, by the user's decision, from the book's AudiobookDB match as a last resort (its terms say the API licence does not cover cover art, i.e. users must sort out rights themselves; judged fine for a private internal catalogue). ffmpeg writes embedded MP3 art as PNG, so the cache accepts jpg/png/webp.
- Mistake worth recording: while testing the backup folder I ran `./app remove 1` on the live database, meaning to remove a test book I had just added, and deleted scanned item #1 instead (an unreviewed, unmatched loose mp3). Restored it from the backup taken just before; nothing else was affected. Use the id the add command prints.

## 2026-10-29: scanning and matching

- Surveyed `~/audio` (read-only SMB mount): 428 GB, 28,000 mp3 + 355 m4b + 94 m4a in 1,183 folders. Layouts vary: author folders with book subfolders, `Title (Unabridged)` folders of chapter files, CD1/CD2 and "(Disc N)" splits, loose files, an `Audible/` folder named `Author Year Title [ASIN]`, and lots of radio (Desert Island Discs, comedy, BBC dramas).
- Scanner on that library: ~5,700 candidates, ~2,300 books, ~3,400 radio episodes. First version split chapter files into thousands of "books" (10,900 candidates); fixed by treating numbered/chapter-named files, disc folders and `Title 02-10` siblings as one book.
- AudiobookDB (beta, ~218K books, successor to Audnexus): ASIN lookup found only 1 of 12 of the `Audible/` ASINs, but title search found most of the same books. On 40 Audible folders title search matched 65% with a naive scorer (some misses were parsing artefacts). Later in the day the service returned 503 for a while, so matching retries 502/503/504 and reports failures plainly.
- Matching never changes a book directly: it records a suggestion with a score and the user confirms or ignores it. Radio is never looked up (saves budget, avoids noise).

## 2026-10-29: start of the audiobook app

- Turned the template into Strata Listory. Replaced `greet.py` with `catalogue.py` and the greeting UI with a library page; the `greeting` setting became `library_dir`. Kept the template structure, tooling, test layout and coverage gates unchanged.
- Catalogue moved to SQLite after the first draft used JSON, because past SQLite corruption makes backups a requirement: corruption check on first open, daily verified online backups with pruning, `./app backup|backups|restore`. Backups share the data disk, so the restic job covers disk loss.
- Assumptions (single user, no file access yet, manual entry) are listed in the README; no feature list was given.

## 2026-10-29: template extraction

- Extracted the shared Python + React full-stack structure from `strata-360` and `strata-stills` into `python-app-template`.
- Included cross-platform `oslib.py`, atomic JSON `settings.py`, FastAPI server with `landing.html` fallback and static asset mounting from `src/app/server/static/`, React 19 + Vite 7 + Tailwind v4 web app with `withBase()` reverse-proxy path resolution, hermetic pytest and Vitest suites with MSW v3, ratcheting coverage floors, and multi-OS GitHub Actions workflows (`unit.yml` and `integration.yml`).

Book identification loading: review queues now select a page using small consent/event headers before reading full evidence. Legacy metadata projections use a bounded process cache keyed by source event IDs and resolver version, with isolated copies per request. Review polling waits for the current request to finish. Collection splitting indexes source ancestors once and can reuse durable children after interruption; startup restores interrupted grouping and approval continuations. Regression tests cover page-bounded evidence reads, cache invalidation/isolation, interrupted splitting and slow initial requests.

Book identification sorting: added Highest identification (default), Most metadata, Newest, Oldest and Title A–Z. The default ranks by passing book checks, title/author similarity, independent source families and known book fields. Metadata-first ranks known title/author/year/publisher fields before source agreement and identification. Compact SQL summaries support global sorting before pagination, while full evidence still loads only for the chosen page. Changing sort resets the page; pagination labels now work for every order.

Open Library transport failures: GET requests retry transient resets/timeouts/incomplete responses up to three attempts with bounded backoff; failures are not cached. Outer Dark was refreshed successfully, passed all nine book checks, and advanced to audiobook review. Book review cards now explain confirming independent sources, saved lookup failures, and missing metadata fields separately.

Recording resolution version 7 groups equivalent storefront entries, prefers representatives with known abridgement, applies a single compatible recording within 2% runtime without fabricating missing values, and preserves review requirements for missing/contradictory evidence. Competing performances require an explicit saved-candidate selection; the selection retains approved book identity and does not approve quality. Library now has a lazy folder tree that combines recursive folder filtering with existing search, workflow visibility and bulk selection.

Offline Open Library: downloading works/authors/ratings for 2026-10-31, importing into a separate persistent SQLite/FTS5 database. Initial import and later updates happen once per snapshot, not per lookup; online lookups remain the fallback. See openlibrary-offline.md for commands and storage details.


Library filtering now includes locally available cover images, combined with folder/search/workflow filters. Known covers are enumerated from local mirrors and caches without touching source audio or metadata providers. BBC R4 spelling and punctuation variants classify as radio during scanning, catalogue edits and ingest; 21 existing catalogue entries were corrected. Chapter-code sequences such as APS000 and APS301–APS360 stay together and retain missing-number checks, with each audio format/code family kept as an alternative representation. The Bret Easton Ellis collection was re-ingested with the corrected grouping; American Psycho is now one audiobook review with its chapter sets intact.

Completed local dump setup: 41,731,803 works, 15,472,329 authors and 1,069,316 ratings. The persistent index is 17,946,062,848 bytes; compressed inputs total 4,872,462,594 bytes. Expanded works/authors/ratings sizes are 23,715,935,364 / 5,972,375,548 / 45,901,320 bytes. Real network-disabled queries took approximately 1–47 ms. Common-surname lookup now tries the full author name before bounded surname fallback; rated canonical works rank ahead of empty duplicate records.

Rechecked 52 current book identifications with the local source: all refreshed successfully, two automatically approved. Book identification is independent of radio/processing eligibility. Original publisher enrichment uses bounded cached per-work edition lookups and same-year publisher selection; no editions dump was downloaded. Author inference from agreeing work catalogues now precedes detail lookups, so The Prostrate Years gained its 2009 Penguin Group UK publisher and Census gained its 2018 Text Publishing Company publisher.

Processing now exposes elapsed current steps and persistent activity logs, including source-file inventory/hashing, provider lookup, audio comparison and quality measurement progress. Polling waits for in-flight requests. Explicit ingest batches yield between books and skip aliases in the completed group so approval continuations can run promptly. Complete single-file books do not inventory unrelated formats in mixed folders; related alternatives in a dedicated book folder remain discoverable. Interrupted current runs resume on startup; remaining original selections were preserved and restored during deployment.

Validation: 525 backend unit tests passed, coverage 95.31%; 88 web tests passed with coverage gate; five real-audio ingest integration tests passed; lint, typecheck/build and whitespace checks passed. Live browser verified Library folder/cover controls and Processing current-step/activity logs.

Book title matching now prefers complete title/subtitle agreement, accepts a missing subtitle only without conflicting or ambiguous known subtitles, and checks ambiguity across provider families. Local Open Library discovery unions individual coauthor matches, recognises joined title words (ChiRunning), and prioritises numbered author-named collections. Cached work/attached-edition title evidence links subtitle-only tags to Chuck Klosterman IV while preserving IV/V distinctions. Small work/edition requests enrich the existing index without rebuilding it or downloading an editions dump. Review source records expose the match basis and subtitle edition reference.

Title matching validation: 539 backend unit tests passed at 95.26% coverage, 88 web tests passed with the coverage gate, and five real-audio ingest integration tests passed. Lint, web typecheck/build and whitespace checks passed. Deployment preserved and requeued 871 unstarted selections; interrupted current runs resume automatically.

Live refresh confirmed automatic 9/9 book approvals for A Very Stable Genius (2020, Penguin Press; 0f033630b9574c8ba2861041baced01c), Chi Running (2004, Simon & Schuster; c88b3a324a1348bebea7d0f84729741a), and Chuck Klosterman IV (2006, Scribner; 5ffd26d273b44b05b6ac73eb5b44de47). Each has two independent work-source families and its audiobook-identification continuation queued. Live health and the new frontend bundle were verified.

Refreshed all 321 current pre-fix book-identification reviews using existing inventories: 16 automatically passed all nine book checks and were queued for audiobook identification; 305 still require review. All refreshes succeeded without provider errors. Easy Money separately passed book and recording identification (2023, Abrams Press); alignment flags two missing tracks. Its quality scan exposed invalid UTF-8 in FFmpeg diagnostic metadata, so tool output decoding now preserves numeric measurements while replacing malformed log characters.

FFmpeg diagnostic-decoding regression validated: 63 processing tests and the full 540-test backend suite passed, coverage 95.26%. Fix deployed with the remaining 868 ingest selections preserved; Easy Money's failed quality scan was explicitly requeued while retaining both valid identification approvals and the two-missing-tracks flag.

Common multi-disc grouping: recognise ordered chapter/story runs under a coherent album, normalise volume/disc labels and join corroborated sibling discs. Preserve distinct numbered volumes and alternative recordings, retain track/disc gaps and contributor conflicts, and show grouping evidence in source details. Publisher-backed repairs for malformed album tags are stored separately and bound to file hashes. A hermetic 395-file Just William regression verifies all 28 disc variants; numeric title matching now preserves missing/different volume numbers.

Dry-run checks against the existing source inventories retained Adrian Mole's nine work groups (473 files) and Bret Easton Ellis's six work groups (723 files), including American Psycho's 174 source files. Just William produces ten groups after explicit publisher-backed repairs; all 395 files are assigned once. Additional regressions cover Windows path spelling and named BBC compilations.

Grouping validation passed: 563 backend unit tests, 95.30% coverage; six real-audio ingest integration tests; 89 web tests with the coverage gate; lint, typecheck/build and whitespace checks. Deployment preserved and requeued 822 unstarted selections, with interrupted runs restored on startup.

Live Just William regroup complete: parent 53d4195d44f54b548f4067349321c824, ten metadata-review children covering 395 files once each. All 207 old story entries are linked to the new volume runs; source evidence and publisher-backed tag repairs are visible through the live API. Each volume re-partitions into one work and has one ordered audio representation. All ten book-identification lookups completed and await review; no quality/final approvals were granted.

PR #10 review follow-up: existing runs now use the current policy at each new stage-approval gate while preserving their original execution/provider policy. Replanning queues only selected replacement proposals and supports failed ingest-backed plans with valid approvals and unchanged inputs. Review pagination clamps when approvals shrink the queue. Regression tests reproduce these cases. Local validation: 567 backend tests, 95.32% coverage; 91 web tests with coverage gate and production build; 18 integration tests in parallel; lint and whitespace checks passed.

## 2026-11-04 — Bulk identification and match features

Audited 355 current book-identification reviews against saved evidence. Separated
recording marketing subtitles from competing written-work assertions, preserving
explicit full-local-title context and all book-catalogue conflicts. Added bounded
English edition-link discovery and canonical main-title retries. Four existing
reviews pass after the scope fix; a 15-book Open Library discovery pass identified
three more candidates. The local dump and cache were used first.

Added a read-only candidate-feature exporter: 6,415 pairs, with unknown labels,
source margins, author/title edit distances and explicit negative/missing evidence.
Eight current manual approvals are insufficient independent training labels for
high-precision model deployment; automatic approvals were not recycled as truth.
See `bulk-identification-20261104.md` for replay results, grouped validation and
logistic/boosted-tree experiment requirements.

Applied eight new book-only automatic approvals after live invariant checks; the
other replay-passing work, Better than Sex, retains its existing legacy consent
and is at Quality review. Five approvals use fresh runs with archived local-dump
replay evidence. All earlier runs and a pre-write SQLite backup are retained.
No new recording/quality consent or processing was granted. Validation: 796 unit
tests, 95.71% coverage; nine ingest integration tests; Ruff and whitespace checks.

A final dump-only pass over missing-author entries also recovered Losing the
Signal and Build through unambiguous catalogue hints and independent work
corroboration. Total: nine replay-passing works, eight newly recorded book
approvals and one existing legacy consent restored by projection.

## 2026-11-04 — Train from trusted automatic approvals

The user explicitly designated existing automatic approvals as ground truth.
Implemented an offline supervised matching experiment using accepted catalogue
candidates frozen at approval time, with uncertain editions left unknown and
same-author/wrong-work negatives mined within disjoint work-group partitions.
The models exclude the five features that directly encode the current resolver.

295 approved queries supplied 2,784 positive candidate pairs across 240 work
groups, including 2,782 automatic-approval positives. Compared three logistic
regressions and three shallow XGBoost classifiers using calibration-only selection.
The selected depth-two XGBoost model recovered 48/52 held-out work groups, with
zero observed false positives on 203 labelled negative test pairs. It shortlisted
52 current book-identification reviews at a calibrated cutoff of 0.90; no
model-generated approvals were written. These metrics measure agreement with the
trusted teacher and derived wrong-work labels, not independent field accuracy.

Optional packages remain in `requirements-model.txt`; artifacts are retained in
`data/match-model-20261104/`. See `match-model-20261104.md` for the method,
reproduction commands and results. Validation: 804 unit tests, 95.75% coverage,
focused final pending-filter tests, Ruff and whitespace checks.

## 2026-11-04 — Quick review pages for automatic and model matches

Added Automatic approvals and Match shortlist pages with source/match comparisons,
search, pagination, check warnings, publication notes and lazy full evidence.
Indexed 314 automatic approvals and imported 52 current model recommendations.
Explicit accepts freeze the displayed candidate, not an unrelated current resolver
choice, and continue to separate recording identification. Unapproval appends a
revocation, invalidates dependent consent, rejects pending processing approval
and holds the run against automatic reconciliation; metadata refresh retains the
hold. Existing completed outputs remain intact. Stale/superseded, changed-source,
locked-field and active-work safeguards run in the existing durable job queue.

All page reads use persisted cards; no source inspection, provider lookup or
resolver projection runs on GET. Verified the real layout in headless Chromium
and started the built app on its usual localhost port. Validation: 816 backend
unit tests, 95.60% coverage; nine ingest integration tests; web tests and coverage
gate, typecheck/build, Ruff and whitespace checks passed.

## 2026-11-04 — Firm approval and rejection on both review pages

Added Approve and Reject to each quick-review card. Firm approval of an automatic
match records explicit human book consent and suppresses the entry on subsequent
page loads and reindexing. It binds the existing approval and work signature
without rewriting the approval event, preserving downstream consent and frozen
plan signatures. Replaced/changed matches require new confirmation. Human book
consent survives changes to automatic threshold policy.

Reject on either page dismisses the entry and holds the book for manual review;
reimporting the identical rejected candidate does not restore it. All decisions
remain in append-only events, and compact workflow headers retain the decision
needed to classify consent correctly.

Validation: 821 backend unit tests passed at 95.61% coverage; 105 web tests and the
unchanged coverage gate passed, along with typecheck/build, Ruff and whitespace
checks. The running app was updated after draining its current analysis job;
queued work was retained and normal workers resumed after restart.


## 2026-11-04 — Ten-card review batches with preloading

Both quick-review queues now use fixed batches of ten. Completed decisions remove
only their own card; they do not refill or refetch the current batch. The next
page and available covers preload, and finishing a batch swaps in those prepared
cards immediately and scrolls to the top. Stable created/id cursors survive queue
removals while requests are in flight, avoiding skipped entries. Search/refresh
invalidate old preloads, failed preloads retry on demand, and completed decisions
remain hidden even when the next load fails. Manual navigation and the final
partial batch retain earlier unreviewed entries.

Validation: 822 backend unit tests passed with 95.61% coverage; 112 web tests and
the unchanged coverage gate passed. Typecheck/build, Ruff and whitespace checks
passed. The running app was upgraded after its current background job completed,
with queued work preserved.

## 2026-11-04 — Correct the managed-service deployment target

The user's actual site is `http://100.78.82.80/strata-listory/`, routed by Caddy to
`strata-listory.service` on `127.0.0.1:8364`. Earlier verification incorrectly used
a temporary `./app serve` instance on default port 8360; that did not update the
managed backend, which continued returning 404 for both new review APIs.

Drained its current job, stopped and started the actual user service, and retained
queued work. Verified both review endpoints return HTTP 200 with ten cards per
page on port 8364. The Caddy snippet correctly strips `/strata-listory/` and targets
8364; unauthenticated requests through the public URL correctly redirect to the
existing login service. Future deployments on this machine must update the
managed service, not launch a separate default-port development server.

## 2026-11-04 — Keep narrator credits out of chapter grouping

Lords of Finance had 30 numbered chapter files sharing the same album and artist
(Liaquat Ahamed), but an album-artist credit of “Narrated by Stephen Hoye”. The
partitioner treated that auxiliary credit as a writer assertion and failed to
match even identical credits, producing 30 separate books. Grouping and identity
recovery now prefer a contributor field containing an actual author; explicit
reader credits supply narrator identity instead. Plain album-artist author
assertions and conflicting actual writers retain their existing boundaries.
Grouping version 5 also reconsiders old fragment scopes against their siblings.

Repaired the live book by verifying all 30 original file hashes, taking a SQLite
backup, and linking the parent and 30 historical chapter aliases to one current
run, with one representation. Reused only verified archived work-provider
responses; recomputed identity and work approval, carrying over no fragment
recording or quality consent. Original runs, the existing human decision, and
all source audio remain intact. The repaired identity is Liaquat Ahamed, narrated
by Stephen Hoye. The existing managed service remains the sole server.

PR validation also adds NumPy to the test dependencies: the offline model tests
exercise real array operations with deterministic estimator doubles, while
scikit-learn and XGBoost remain optional research-only dependencies. This keeps
those tests collectable in CI's clean test environment.

Final combined validation: 829 backend unit tests passed at 95.58% coverage;
all nine real-audio ingest integration tests passed. All 112 web tests and the
unchanged coverage gate passed, along with typecheck/production build, Ruff and
whitespace checks. Both review endpoints on the managed backend returned HTTP
200 with ten cards. The automatic review search for Lords of Finance returns
exactly one current entry containing 30 files; follow-up recording analysis is
queued on the existing worker.

## 2026-11-04 — Bind shortlist decisions and training labels to current consent

Addressed PR review findings by recording each validated shortlist import as an
append-only revision included in the card's review token. Importing a replacement
candidate invalidates both Approve and Reject on an already open card, preserving
the exact candidate the user reviewed. Trusted training rows now exclude an
approval followed by revocation or rejection; a later explicit approval can
provide fresh supervision. Added regressions for both stale decision actions and
both forms of withdrawn consent.

## 2026-11-04 — Stop the old service and prepare the Omarchy migration

Stopped and disabled the single managed `strata-listory.service`. Created a
consistent SQLite backup with no running jobs, verified its full integrity,
and copied it to `~/data/strata-listory/migration-20261104/` on `//server/Omarchy`.
The snapshot retains 5,706 entries, 1,745 ingest runs and all review history;
one unattempted 113-entry ingest batch remains queued. Automatic output
processing remains off. All 15,563 referenced provider archive paths exist and
are preserved in a separate support archive, together with model/audit results.
Private app settings, provider key files, the three configured app environment
variables and service/proxy reference configuration are in a private archive,
never committed to Git. Transfer files are verified after copying with SHA-256.

The user will recreate `~/audio-local` from the audio mount on Omarchy. Retaining
the original home/repository/source paths preserves signed inventory and evidence
references. SQLite must be restored to local disk, rather than run on the SMB
transfer share. Downloaded dumps, tool environments/caches, listening previews
and output audio are omitted; generated state stays in the database as history.
See `docs/omarchy-migration-20261104.md` for complete restore commands, required
configuration, queued-work behavior, output rebuilding and the proxy/auth boundary.

## 2026-11-04 — Responsive concurrent match approvals

- Replaced the page-wide saving flag with per-run pending state and independent book-specific errors. Other cards, search, refresh and pagination stay interactive while jobs complete; stale overlapping list responses cannot resurrect a completed revision.
- Reserved one bounded durable worker slot for `ingest/match_review`, prioritized ahead of queued bulk work while retaining resource locks across both kinds of jobs. `APP_WORKERS` still caps bulk tasks; at most one review decision runs alongside them.
- On omarchy, recent automatic confirmations spent 42 s and 158 s waiting, versus 0.18–0.21 s executing. The bottleneck was the shared ingest queue rather than the decision itself.
- Added queued/out-of-order UI regressions for both review modes, failure recovery, stale refresh handling, and scheduling tests for both caps and shared book locks. No live review decisions were submitted as tests.
- Validation: 835 backend unit tests passed at 96.25% coverage; six job/API integration tests passed; 115 web tests passed the unchanged coverage gate; lint and production build passed. Deployed on omarchy, verified health and both review APIs, and explicitly requeued the ingest job interrupted by the service restart.

## 2026-11-04 — Stable review batches and reversible decisions

- Automatic approvals and Match shortlist retain saved cards in place. Only the selected saved action is disabled; the opposite action submits the current server token. Next retains undecided/pending/failed cards and adds only enough new reviews to fill ten slots.
- Shortlist recording continuation is queued on Next without waiting for analysis to finish, so acceptance remains reversible before advancing. Queue failures retain the affected card for retry. Restore validates the same candidate/evidence and renews book consent without reusing downstream consent.
- Removed duplicate full audio validation within shortlist acceptance. Recent share-backed accepts started promptly but took 4–8 seconds; one full hash check per decision remains mandatory.
- Validation: 114 frontend tests passed the unchanged coverage gate and production build; 268 ingest/approval tests plus 72 queue/API/reversal tests and three job integrations passed. No live decisions were submitted by validation.

## 2026-11-04 — General anthology grouping and local editions

- Group named anthologies from a shared album identity, a complete ordered run of at least three tracks, and a matching container title. Different per-story artist credits no longer split that work. Explicit container editor credits can name the anthology; unlabelled credits remain unknown instead of assigning the first story author. Original track tags remain in evidence. No book-title exception or source-layout override is used.
- Grouping version 6 invalidates older source descriptors on fresh inventory. General regressions cover multiple unrelated anthology titles and preserve splits for resets, gaps, competing albums, unrelated folders and whole-book files. A scan of 39 archived split parents identified London After Midnight (23 tracks) as the applicable existing case; normal re-ingest was queued after backup, with old fragment history retained and no approvals copied.
- Added optional streaming Open Library editions import with indexed ISBN/work links and bounded local publisher queries. Preserve edition publication dates separately from work first-publication dates; support multiple linked works and audio-format filtering. Atomic failed-import behavior remains unchanged.
- The transfer excluded Open Library snapshots, so started resumable, checksum-verified authors/works/ratings/editions downloads and a waiting, bounded importer for the official 2026-10-31 snapshot on omarchy.
- Final backend validation: 851 unit tests passed at 96.24% coverage; lint passed. The anthology re-ingest is queued behind existing work; four-dump download and waiting import are active.

## 2026-11-04 — Compact multi-CD labels

- Recognize compact disc totals such as `CD3o5` and `CD3of5` alongside `Disc 3 of 5` and `CD3/5`. Grouping version 7 rebuilds older descriptors on fresh inventory. This is a general parser change, with no title-specific exceptions.
- Me Talk Pretty One Day's archived five-CD layout now groups as one 87-track recording, in disc/track order with zero missing/duplicate tracks. Tags and source audio remain unchanged. The earlier split came from combining ordinary `Disc 1/2` labels with unrecognised `(CD3o5)`–`(CD5o5)` labels.
- Added positive/negative parser tests, an unrelated-title reconstruction of the mixed 87-track layout, and missing-disc detection. All 72 focused grouping tests and lint passed. Backed up the catalogue and queued normal full-container re-ingest (book 917), preserving old fragment history and requiring fresh combined-work consent.
- Re-ingest has now linked all five catalogue members into one 87-track inventory and one recording representation (run `7972c9f5f3b0495390ca678dff9ba3fc`); later analysis remains in progress. Broader verification passed 231 ingest unit tests and nine pipeline integration tests.


## 2026-11-04 — Incremental training evidence and richer match features

- Added learned resolver outputs, subtitle/type evidence, publication/linked-edition availability and five ratings/popularity features (48 total). Edition evidence remains linked to the precise work/edition. Ratings are a prior within a provider; they do not create approval labels.
- Persist candidate vectors, frozen consent labels and per-query synthetic examples in `data/training/features.sqlite`. Feature additions fill missing columns without overwriting existing values or rerunning title comparisons. Changed review decisions update supervision separately; revoked/superseded consent and changed foreign evidence invalidate affected examples.
- Run publication records durable maintenance markers, serviced by background jobs and recovered on bootstrap. Maintenance holds no book lock and stays out of the operations view, preserving responsive review decisions.
- Reused 5,718 candidate vectors and 1,404 synthetic examples. Actual retraining with old feature computation disabled completed in 15.76 seconds. Warm feature-family comparison runs took 11–13 seconds. CPU model fitting is small; GPU acceleration would not address the former repeated preparation cost.
- Expanded-model held-out results: 536 correct candidate accepts and one incorrect, versus 527 correct and one incorrect without ratings (75 groups each). Great Expectations' biography proposal fell below threshold without an exception. Updated 32 manual shortlist suggestions and withdrew 14 covered below-threshold suggestions, protected by immutable input and current-review revision checks. No automatic approval policy changed.
- Validation: 876 backend unit tests passed at 96.26% coverage; 41 focused cache/job tests and three real job integrations passed. Ruff and patch whitespace checks passed. Service restarted, interrupted work resumed through the normal retry endpoint, and bootstrap is recovering outstanding evidence markers.

## 2026-11-04 — Preserve folder authors and expand spaced chapter codes

- Fixed recovery replacing a known same-work catalogue author with an empty grouping hint. Explicit `Author - Title` folder credits now survive untagged CD tracks and are retained as folder evidence. Contradictory embedded/folder author credits increase the identity conflict gate. Anthology credits and scoped unrelated-work safeguards remain explicit.
- Recognize spaced sequence names such as `AB - 001 - Opening.mp3`. Selecting one member of a coherent coded sibling run inventories the full recording and no longer narrows back to a single file because chapter titles differ. Unrelated siblings still prevent directory expansion. Grouping version 8 refreshes stale source descriptors.
- Archived House of Cards evidence now recovers William D. Cohan instead of an empty author; its earlier title-only lookup had approved Michael Dobbs. Archived The Push reconstructs as one Tommy Caldwell work, 25 files and one complete representation with no missing/duplicate tracks. Withdrew the incorrect work approval and all 25 fragment approvals through normal review jobs, preserving their history and revoking their training consent.
- Queued a normal metadata refresh for House of Cards and a complete sibling re-ingest for The Push. Training-cache maintenance now yields to ordinary ingest jobs, while the reserved human review lane and worker cap remain in force.
- Validation: 882 backend unit tests passed at 96.26% coverage, including 308 focused grouping/ingest tests; all 12 ingest/job integration tests passed. Ruff and whitespace checks passed. Fixes are deployed; corrective jobs are queued behind existing ingest work.

## 2026-11-05 — Smaller English/unknown-language editions index

- Edition imports now retain English, multilingual-with-English and unspecified-language records, excluding explicitly non-English editions before storing their ISBN/work links. Work/author/ratings evidence and original work years remain intact.
- Added `openlibrary-compact` for the initial all-language snapshot: wait for the importer lock, prepare a separate filtered database, reclaim pages with VACUUM, verify and atomically publish. Readers retain the previous index until publication; failure preserves it, and already filtered snapshots are not rebuilt.
- The initial import is allowed to finish without restarting. A low-priority compaction service will apply the same filter to its published result.
- Full unit coverage reached 96.28% (891 passed; one existing backup-retention fixture failed because its two hourly examples crossed midnight). Made that fixture use a fixed noon clock and rechecked it independently with the edition/CLI tests; backup implementation is unchanged. Edition tests cover language placeholders, multilingual editions, link cleanup, repeat-safe compaction and preservation of the published file on failure.
- Initial import has read 56,896,821 editions and is building indexes. The low-priority `listory-openlibrary-compact` unit is active and waiting on the shared import lock; no import restart or expanded dump copy was needed.
- Final focused verification: all 45 edition, CLI and backup tests passed; Ruff and patch whitespace checks passed.

## 2026-11-06 — ISBN-led edition matching

- Wire the completed local editions index into matching: discover valid ISBNs from local tags/identity and compatible provider/work-edition evidence, match equivalent ISBN-10/13 values, traverse duplicate edition records to exact linked works, and retain alternate edition title/subtitle evidence. Bounded local queries archive their snapshot and records for review/training.
- Keep conflicting authors/titles, work dates and edition dates separate. ISBNs broaden discovery; they do not bypass identity checks or turn duplicate Open Library records into independent providers. Existing consent versions remain unchanged.
- Add the missing reverse edition/work index to future builds and the current live index (65.9-second low-priority maintenance, no dump re-import). Confirmed covering-index traversal and live read-only matches for William D. Cohan's House of Cards and Tommy Caldwell's The Push in under 0.2 seconds each.
- Queued an ISBN-enabled House of Cards refresh through the normal worker; its explicit manual-review hold is retained. New matching jobs use the ISBN path automatically.
- The live refresh completed as run `8e458236f88e4966a34a4b58a35bbd3f`: two ISBN queries produced four linked edition records, retained William D. Cohan, and kept the explicit manual-review hold. Original year remains 2009 from independent Hardcover evidence; edition publication years are retained separately.
- Validation: 901 unit tests passed at 96.21% coverage, plus all nine ingest integration tests. Focused discovery/audit/index tests (267) and Ruff/whitespace checks passed.

## 2026-11-06 — PR #14 follow-up

- Retain pending review decisions when Next is pressed on a full batch with no completed decisions, and protect pending cards throughout refill. Use the live in-flight set so a deferred shortlist acceptance remains visible until a later Next can queue its continuation.
- Add automatic/shortlist regressions exercising Next before the first decision completes and confirming accepted shortlist work is subsequently resumed. Normalize the folder-evidence assertion with the platform's Path representation for Windows.
- Validation: 116 frontend tests passed the coverage gate; typecheck/production build and all 76 grouping unit tests passed. Replacement cross-platform CI is monitored on PR #14.

## 2026-11-07 — Replay jobs interrupted by a server stop

- Diagnosis of the "stalled" Processing page on omarchy: the queue was empty, not stuck. The 10:35 restart had failed the in-flight `bootstrap` and `reconcile` jobs with "job interrupted by server shutdown" / "exit -15" and nothing re-queued them; the rest of the backlog is review-state ingest runs waiting on a person.
- `JobQueue.recover_interrupted` (called from `start()`) now retries jobs whose error says the server was stopped (shutdown message or SIGTERM exit). Timeouts, crashes (dead owner) and application errors still need an explicit retry. Bounded to 2 automatic retries per chain and to failures from the last 24 hours, so a job that really takes the server down cannot loop.

## 2026-11-07: Postgres catalogue design (listen schema)
New `src/app/store/` (models, `listens.py`) and `migrations/` on the shared omarchy Postgres, schema `listory` (created with `dbtool new-app listory`, migrated to the initial revision). **Nothing reads or writes it yet**: the app still runs on `data/catalogue.db`; an importer and cutover are separate steps.
Model: immutable parts (`part_file` by sha256, `part_record` metadata claims as received, reference `work`/`person`/identifiers) and one mutable `listen` (a self-contained thing you listen to in one go) made of `listen_part` link rows (file, record or work; `ordinal` = listening order or priority; unlinked, never deleted). Merge/split only move link rows. Every change to a listen or listen-part writes a `listen_event` in the same transaction (auto acceptances carry score, margin, model version, policy revision). File locations come from filecat by sha256. Run tests with `pytest tests/unit/store --pg` (needs a Postgres; CI: `store.yml`). Design discussion and the real-data numbers it came from: the project thread.

Step 1 of the new pipeline, built on that schema: `./app filegroups [folder]` scans the library into `part_file` (sha256; only changed files are rehashed, `part_file_location` remembers path/size/mtime) and groups it by folder into `part_filegroup` / `part_filegroup_member` (a group holds files and/or child groups with an `ordinal`, so book > disc > track works; kind is a fixed list in `GROUP_KINDS`; every grouping is a new run and groups are immutable). A `listen_part` can link to a filegroup. The **Files** page (`#/files?group=`) browses it through `GET /api/filegroups[/{id}]`, which exists only when omarchy-db is installed and the database answers. Next: infer disc/book structure inside the folder groups, then identification on top.

Step 3, structure inference (`./app filegroups-structure`, `src/app/store/structure.py`): turns the folder grouping into a new grouping run of kinds `book`, `disc`, `series`, `programme` (numbered radio episodes), `collection`, `loose` (`collection` = a container holding a mix, e.g. books plus a radio series; `folder` = the rules could not call it, `detail.reason` says why), deterministic and with evidence on each group. Rules: sibling folders that are numbered variants of one name (CD1/CD2, "Goldfinch 01-26", "one"/"two") are discs of a book; folders of different titles are a series; leaf folders are a book when `scan.one_work` or a 1..n numbered run says so, otherwise `loose`; whole-book files (m4b/m4a/aac) are books of their own unless numbered like chapters; files next to sub-folders, or an mp3 set next to its m4b, become clusters judged separately (the "same book twice" case is left for a listen merge). First run on the real library: see the PR description.

Regression check for the structure rules: `./app filegroups-structure --update` saves the classification of every group as `data/structure-golden.json` (real file names, so it stays in the untracked data folder; the repo is public); `--check` classifies again without writing and prints what a rule change moved (`~ path: loose -> book`), exit 1 if anything differs. Run it before and after touching `structure.py`, review the diff, then `--update`. Each bug found on real data also gets an invented, anonymous case in `tests/unit/store/test_structure.py`.

Step 4, listens (`./app listens-build [--rebuild]`, `src/app/store/build.py`): one listen per `book` group (kind `book`) and per `programme` group (kind `radio_series`) of the newest structure grouping, each with one `listen_part` (role `input`) linking to its filegroup (discs and tracks stay below it) and status `grouped`; series, collections and folders only contain listens, `loose` groups are left for a person. Every listen, link and status change has its `listen_event` (actor `rule`, naming the structure grouping). Rerun with `--rebuild` (the listen tables are derived data). The **Listens** page (`#/listens`, `GET /api/listens`, `GET /api/listens/{id}`) lists them with search and a kind filter. First run on the real library: 947 books and 133 radio series (1,080 listens; 118 loose files left). Next: link the copies of one title (mp3 set, m4b, another mp3 set) into one listen, choose metadata and the copy to use.

Copies (`build.link_copies`, run by `listens-build` after the metadata step unless `--no-copies`): book listens are merged only when they are practically the same thing: the same chosen title (brackets, "unabridged"-style words, a leading index and years ignored; digits kept so Vol. 2 and Vol. 4 differ), authors that do not disagree (equal, or unknown joining a single known author; different authors never merge) and runtimes within 4%. Anything less certain waits until the works are identified (Jonathan: far safer to merge once we know which works they are). The preferred copy (a single m4b/m4a first, then the larger) survives, the others become its later `input` parts (`ordinal` 2..); each merge has its event with the evidence and `split` undoes it. A different runtime under the same title is another recording of the same work and stays its own listen. Radio series are not merged. Real library: 48 copies merged into 42 listens.

Metadata (`src/app/store/metadata.py`, run by `listens-build` after copies): per listen two records are linked as `record` listen-parts (ordinal 1 `embedded_tags`: the most common value of each tag and how many files say it; ordinal 2 `folder_name`: `scan.parse_name`), then `listen_choice` rows pick title, author, narrator, year, asin, language and publisher (tags that at least half the tagged files agree on, generic tags like "Track 01" ignored, then the folder name; a composer counts as narrator only with an Audible ASIN; "A/A" duplicates collapse) each pointing at its record and logged as an event; `listen.label` caches the chosen title. The Listens page shows author, narrator and year and searches them. Real library: 931 listens, title 931, author 891, year 628, narrator 147, asin 60.

Step 6, identification (`./app listens-identify-prepare`, `listens-identify-train`, `listens-identify`, `src/app/linkage/`): each book listen is linked to a written work by a Fellegi-Sunter record-linkage model fitted by EM (semi-supervised, with a "none of these" hypothesis and a fitted temperature), over work clusters that join Open Library works with the Audible recordings of them. Accepted at P >= 0.95, otherwise the top candidates go to review; listens of the same work link to one `work` row, and copies of one recording (same work, runtime within 2%) can be merged. The Audible index is now built from the archive's columns for both the UK and US stores (English and unlabelled only, 1.2M recordings). Cross-validated on 361 listens labelled from the old catalogue: right work first for 97.5%, calibration error 0.019, 88% accepted at 0.95 with no wrong book accepted (the three disagreements are duplicate Open Library records of the same book). Real library: 538 of 784 identified, 4 copies merged. Design and numbers: [listen-identification.md](listen-identification.md).

Match queue (Jonathan: an endless queue showing everything, easy matches first, each answer feeding the model): **Matches** is the new home page (`MatchQueuePage`, `/api/matches`). Cards are ordered most certain first, show the files against the proposed work and its Audible recordings with the model's evidence, and take A / R / N (approve, not this, none of these) with undo. Answers are labels; every 25 answers the model is refitted and re-decides the undecided listens in the background; rejected works drop out of a listen's candidates. The SQLite Review moved under Pipeline.

## 2026-11-09: stand-alone files named by their container folder
A whole-book file lying in a container folder (or next to sub-folders) got its listen title from the *container's* folder name (`Books`, an author's name) because the input part's path is the container's; the file's own name (the filegroup label) was never read, so identification searched for the wrong title. `metadata.name_parts` now reads the file's name for stand-alone-file groups (`from_group` -1, one file), with the container as author fallback. Existing listens keep their old metadata until `listens-build --rebuild`. Anonymous regression tests in `tests/unit/store/test_metadata.py`.
Follow-up: a single-file listen's file name is now also an identification reading (`Query` readings source `file`, loaded from the file's current location), read even when tags or the chosen title say something else; deduplicated against tag/folder readings, generic names ignored. `dataset.VERSION` 7 so cached vectors recompute. This needs no metadata rebuild: it rescues listens such as a book titled by its container folder on the next `listens-identify-prepare`.

## 2026-11-09: tag-editor placeholders
`Album`, `Album Artist`, `Artist`, `Title`, `Untitled`, `Various Artists` (what tag editors write for an empty field) are generic like `Track 01`, in `store/metadata.py` (chosen title/author fall back to the folder name) and `linkage/query.py` (an already-chosen placeholder is not a reading). Affected listens are re-read with `listens-metadata-redo <ids>`.

## 2026-11-10: parts are not copies
`link_copies` merged `X 01` and `X 02` (two whole-book files of one book, same album tag, runtimes within 4%) as copies of one recording, so the listen looked like a duplicate and matched as a whole book with half the runtime. Group names numbered differently (`build.numbering`) are now kept apart (`numbered_apart`); an unnumbered copy may still join one of them. Already-merged cases need `split` (listen 10576 into 10577). Whether parts should become one listen with ordered input parts (and their summed runtime) is an open design question.
## 2026-11-10: a full-length file beside its own split chapters
A folder holding one book twice, as a full-length `.mp3` and the same book split into chapter `.mp3`s, was one "chapter files of one work" group with double the runtime (two Flashman books: 1584 minutes for a 792-minute book). `structure.full_beside_its_parts` takes out the file whose length is the sum of all the others (within 3%, at least three others) as a book of its own, so the two become two groups that `link_copies` then joins as copies. `filegroups-structure --check` on the real library moves four folders (three Flashman books, one chapterized thriller part); the live groups change only when structure and listens are rebuilt.

## 2026-11-11: parts of one recording are one listen
`./app listens-identify --merge-parts` (`apply.merge_parts`) joins listens that are the numbered parts of one recording ("For X 01" and "For X 02", "Y Part 1" and "Y Part 2") into one listen whose input parts are the parts in listening order, each marked `detail.relation = "part"` (copies stay the default: alternative copies ranked by preference). Evidence, once the work is known: the listens' best candidate (P >= 0.5) is the same work, their group names differ only by a number (`build.part_key`, `build.numbering`), each runs well short of the book, and their runtimes add up to one of the work's recordings within 12%. That sum is what identification then sees as the listen's runtime (`query.minutes`), so the joined listen is decided again with the whole runtime: run `listens-identify-prepare` and `listens-identify` after joining (the survivor is set back to `review`). `merge_copies` also reads a listen of parts as one runtime. Separate numbered books of a series are different part keys, so are never joined. Copies in the API carry `relation`.
Also: a leading index without a separator ("01 Title Part 1") no longer separates part keys, and a single listen whose several inputs were merged as copies earlier ("Inversions Part 1/2") is converted in place when the same evidence holds. On the real library `--merge-parts --dry-run` finds exactly three: the two-file "For Special Services", the two-file "Excession" and the two-copy "Inversions".
