# Staged ingest, evidence and threshold policies

Grouping version 4 recognises N-of-total parts, numeric copy-counter chapters and fixed-byte MP3 chunks. Fresh ingestion reconsiders obsolete fragment scopes and links their aliases to the resulting work; old approvals remain separate. Previews retain separate representations. See the [45-fragment audit](fragment-grouping-review-20261103.md). Diagnosed header/decoder faults always require encoding, including otherwise copy-eligible mono MP3s. Plans preserve reported and decoded durations and the encoding reason.

The ingest pipeline consumes scanned catalogue entries before final-library processing. It identifies work boundaries from embedded metadata and filenames, using folders as fallback clues. A selected collection can produce several independent runs, including multiple books in a single folder or one book across subfolders. It then groups source files, recovers identities from local evidence, queries providers, compares audio representations, measures quality and creates a plan using only the selected files. Every run and stage attempt is retained. Automatic approval and processing both default to off.

The current rollout is books only. Radio programmes, comedy performances, plays and podcasts are deferred; a print-work match alone must never establish the identity of a broadcast or performance. The existing output planner remains responsible for loudness tolerance, lossless gain, copy-versus-encode decisions and final peak verification. Clear identity does not bypass audio or completeness checks. Selected-source plans retain both the complete inventory manifest (to detect changed alternatives) and the selected input manifest (to decide what is processed).

## Use it

Run `./app doctor --audio` to check the audio toolchain. Optional fingerprint and waveform-analysis tools and local verification are documented in [audio-tools.md](audio-tools.md).

Open **Library** to choose books for ingest. Search by title, author, narrator, series, source path or catalogue ID; select individual books, the current page, or all matching entries. Library hides entries already in Processing, Book identification, Audiobook identification, Quality, Proposed or Finished by default. Each stage has an inclusion checkbox and count. Workflow membership includes linked group entries and queued batch IDs. Hidden workflow entries are removed from selection, and submission refreshes membership immediately. Reading/listening status remains a separate filter. Selection otherwise persists across searches, filters and pages; **Show selected only** lets you review the batch before **Start ingest**. Ignored entries, radio and books without a source path remain unselectable. Explicit selection can start a fresh run for an already analysed entry; related entries are grouped by the worker. Explicit batches can cover the whole matching library; the 200-entry limit applies only to automatic discovery without a selected ID list. Online collection uses the configured providers. A single background worker handles expensive jobs, and CLI jobs use the same file lock so two jobs cannot analyse/propose the same group concurrently.

**Book identification**, **Audiobook identification** and **Quality** are sequential, separate review pages. Each shows only its current awaiting-approval runs. **Processing** monitors both workers and shows selected books still waiting inside an ingest batch, running analyses, pending approvals, failed operations and approved processing plans. It includes failures by default; **Show completed** includes finished operations and plans. **Proposed** is a separate page after Quality for reviewing and approving final processing plans. A proposed plan is waiting for final processing approval; once approved, it appears in Processing ready to run. Running means execution has started. **Finished** contains completed playable outputs.

```bash
./app scan
./app ingest-run 869 --limit 1
./app ingest-status
./app ingest-show RUN_ID
./app ingest-policy
./app ingest-evaluate RUN_ID
./app ingest-approve RUN_ID metadata  # approve the book and search for its recording
./app ingest-approve RUN_ID audiobook # approve the specific recording and start quality analysis
./app ingest-approve RUN_ID quality   # create a processing proposal after reviewing quality
./app ingest-refresh RUN_ID       # refresh metadata online, reusing unchanged audio evidence in a new run
./app process-approve BOOK_ID
./app process-run
```

`ingest-run --offline` makes no metadata-provider requests. `ingest-resume RUN_ID` resumes an interrupted/failed run, preserving failed attempts and reusing completed stages only if its source files and catalogue metadata are unchanged. Explicitly running the same book again creates a new auditable run. Default batches skip entries that already belong to a run; supply their IDs to retry.

In manual mode, new runs stop after Inventory → Identity → Book catalogue search → Book identification. Review title, complete authors, original publication year, book publisher and source integrity, then choose **Approve book and identify audiobook**. The approved book identity is retained while a new recording-provider search uses its canonical title and authors, together with local runtime, narration and edition clues. The worker stops at **Audiobook identification**, where recording title/edition, narrators, publisher, release, language, runtime and abridgement have independent checks and sources. The approved book is shown for context; a recording match cannot replace it.

Choose **Approve audiobook and analyse quality** to run Alignment → Quality → Selection, then review the measurements and five cached, normalized listening excerpts in **Quality**. **Approve quality and propose processing** creates the final proposal in **Proposed**. Each approval is independent, records failed/unknown checks explicitly overridden by a person, and requires complete analysis and unchanged inputs. Actual errors must be corrected or resumed before approval.

Book automation checks written-work and source-integrity predicates. Audiobook automation checks only the recording predicates and requires valid book consent. Quality and final processing remain separate. Older combined metadata approvals cover both identifications, preserving their original signatures and audit records; older unapproved work starts at book identification and performs recording lookup after book consent. Saved evidence is projected without rewriting history. Existing automatic approvals are rechecked against current predicates.

Proposed plans and newly saved output evidence retain the separate work and performance namespaces. Older proposals show only their frozen assertions; where older plans saved only title/author, other fields and the separate recording title remain explicitly unknown. This view does not silently refresh a proposal from newer run data or alter its output decisions.

All audiobook list entries in Library, Processing, Book identification, Audiobook identification, Quality, Proposed and Finished show a source-folder summary. Up to three filenames are shown, followed by the remaining and total file counts. The source endpoint reuses the latest inventory or lists audio filenames without probing/decoding them. Book details retain their full file listing.

All three stage approvals retain evidence event IDs, policy revision and any quality rating. Changing book consent invalidates dependent audiobook and quality consent. Both provider-search manifests are verified before advancement. Processing plans require every approval and retain frozen work/performance metadata.

Each identification page has its own automatic-approval setting. The legacy metadata automation setting migrates to both identification settings; subsequent changes can control them independently. Saving enabled automation checks the latest pending reviews and verifies unchanged source/catalogue/provider evidence. Passing book identification can advance to recording search even if that recording is still unknown or incompatible. Automatic identification alone stops at Quality review.

Review queues are filtered before pagination in groups of 50, so older pending books remain accessible even after newer runs enter another stage. **Older reviews** and **Newer reviews** navigate each queue. Running and failed operations belong in Processing. Superseded runs remain in the append-only store and can be inspected with `ingest-show`; they do not reappear as current review work. Legacy `#/ingest` and `#/metadata` links open Book identification; the new routes are `#/book-identification` and `#/audiobook-identification`.

`ingest-refresh RUN_ID` rechecks a completed metadata phase with the configured providers using the current metadata code and policy. It creates a new run, verifies source hashes and catalogue signatures, retains the old run, and reuses inventory plus any completed alignment, quality and selection evidence with explicit links to its origin. Identity, provider collection and metadata are recomputed first; no approval is carried forward. After all three review approvals, planning and eligibility are rebuilt. It refuses incomplete metadata evidence, changed inputs, active groups and groups with processing plans or finished outputs. This lets a completed book be rechecked while a batch analyses other books; global automation settings still apply.

Identity recovery accepts validated Audible product links in embedded URL tags and keeps conflicting identifiers visible. Explicit foreword/introduction and other contributor roles are separated from primary authors for searching, matching and output metadata; raw credits remain in the identity and provider evidence. Matching a work's shorter title follows verification of the recording's complete title/author identity. Regional Audible identifier lookup tries the US catalogue when the configured region returns no usable product. Audnexus abridgement assertions and Apple product-page details are retained.

Unknown local narrator/language assertions can be corroborated by at least two accepted provider families describing the selected recording. Audible, Audnexus and an Audible sidecar still count as one family. Missing fields alone are not critical conflicts; actual disagreements and unresolved recording alternatives still prevent eligibility. Language aliases are normalized for comparison while original assertions are preserved.

Generic credits such as `full cast` describe an ensemble and are excluded from named-person comparison. Without local named narrators, automatic approval still needs corroborating recording evidence from distinct provider families; the descriptor alone is insufficient. Provider author arrays may contain combined credits such as `Michael Diamond & Adam Horovitz`, which are split for comparison while their original strings remain in the saved responses.

Person comparisons ignore leading honorifics and allow unambiguous initials, bounded spelling errors and explicit nickname pairs such as Charles/Chuck with the same surname. For book authors, a strong matching title also permits similar given names with an exact surname and matching middle names; this contextual relaxation does not apply to narrators. A matching author in common corroborates the work even when a source credits additional people; genuinely unrelated and ambiguous author names remain incompatible. Canonical authors retain the matching primary names, while additional credited names stay visible in source evidence and original responses. Local author-tag variations use the same shared-author rule. Missing local authors may be inferred only when two independent book catalogues agree on a strongly matching title and compatible author set, with ambiguous same-title books left unresolved. When an author-qualified provider search finds no compatible candidate, discovery retries the title alone; shared-author, title and recording checks still govern acceptance.

Partial cast rosters can agree only when multiple names form an unambiguous subset and the work, runtime, language and release date (or exact recording identifier) support the same edition. Initial abbreviations such as `B. Midler` can match a single full given name with the same surname; different full names or ambiguous initials stay distinct. Original rosters are retained, including shorter provider lists. Conflicting casts, dates, runtimes and languages still require review.


For a confidently matched written work, original publication uses the earliest supported year from the compatible book catalogues. Later catalogue dates remain visible as alternatives and do not displace an earlier date or block review merely because they describe a later edition. Audiobook release dates stay in the performance namespace, and manually locked years retain precedence. Existing review evidence is projected through the current resolution rules without rewriting archived events or frozen processing proposals. Loudness plans that finish within the existing quiet tolerance do not create a blocking shortfall warning. Neither change relaxes the audio peak, decode or integrity checks.

## Source collections and work boundaries

Inventory detects separate works before provider searches or audio alignment. Embedded album/author tags and repeated filename stems supply work identities; generic chapter labels fall back to nearby folder names. Conflicting strong filename sequences remain visible as a source-identity conflict rather than being silently combined. Disc labels/numbers can join a continuous representation across directories without treating each disc as a complete alternative. Numeric book titles such as 1984 remain titles.

A mixed collection is retained as a completed split operation with child-run references. Each work receives its own catalogue anchor, exact file inventory, source scope, providers, approvals and quality measurements. Existing proposals/outputs are retained; unrelated works never become alternative recordings of each other. Bonus interviews and episodic recordings remain separate when their evidence identifies them separately. Unsupported content kinds retain their existing recipe gate.

Scoped inventories retain the surrounding file-name snapshot, relevant sidecars, selected file hashes and an explicit list of inputs. Plans freeze the same scope for validation. Files are never moved or edited. Re-ingesting a scoped work can discover new chapters; changed or ambiguous boundaries require regrouping the collection. Queued child runs survive restarts. Pending old mixed-folder runs are regrouped into fresh child runs with new consent, while their original history remains intact.

Book catalogue searches omit anniversary decorations from the work query and retain the original recording title as an edition clue for the subsequent recording search. Tenth/10th normalization remains active. This lets American Gods match a work catalogue while the performance phase still distinguishes the anniversary recording.

## Stages and principal metrics

Each stage records running/done/failed events, timestamps, elapsed seconds, numeric metrics (or unknown), result data and errors. UI timings update while a stage is running. Each review phase collects its evidence before waiting for approval; actual exceptions stop the run and preserve the failure.

| Stage | Measurements and retained evidence |
|---|---|
| Inventory | Every audio file, complete ffprobe JSON, SHA-256, size, mtime, local Audible sidecars, grouping/member IDs, alternative representations, probe errors and observed missing/duplicate track numbers |
| Identity | Title/author/narrator recovery, embedded IDs, language/abridgement, content kind, conflicting local assertions, manual locks and the underlying tag/sidecar evidence |
| Book catalogue search (`providers`) | Configured book providers, raw responses, candidates/details, source manifests and availability/errors |
| Book identification (`metadata`) | Written-work title/authors, original publication year/publisher, independent provenance, similarity, source families and conflicts |
| Recording catalogue search (`recording_providers`) | Approved book identity, recording-provider responses/candidates/details and a separate immutable manifest |
| Audiobook identification (`recording`) | Frozen approved work plus recording runtime, narrator/language corroboration, abridgement, edition ambiguity and recording-specific provenance |
| Alignment | Per-representation and per-file window maps, correlated coverage, gaps, unmatched fraction, missing/duplicate tracks and unresolved alternatives; failed matches never prove another performance |
| Quality | Full loudness, true peak, loudness range, peak statistics, silence measurements, channel-cancellation loss, decode diagnostics and three spectral-retention samples per representation |
| Selection | Complete-source preference, decode/peak safety and sampled spectral-retention ranking; explicit selected file list, quality metrics for that selection and an ambiguity flag |
| Planning | Existing chapter/tier/loudness plan, warnings, selected inputs and frozen source/evidence manifests; no audio is written |
| Eligibility | Every actual value against its threshold, missing measurements, content-kind gate, passed/failed counts and the exact policy snapshot |
| Queue / processing | Proposal/approval audit, processing elapsed time, output size/duration, loudness, true peak, retries and output-verification result, including manual processing through the existing queue |

Measurements called `score` are similarity measurements, not probabilities. `eligibility.passed/failed` are predicate counts, not confidence percentages. Unknown/nonfinite measurements cannot pass a numeric gate. Missing narrator/language evidence stays unknown rather than being counted as agreement.

Quality version 2 also records up to three half-second RMS samples from quiet passages distributed across each representation. Passages are first detected below -38 dBFS for at least 1.2 seconds; their sample positions, levels, digital silence and unavailable results are retained, with the median of finite samples shown on Ingest. This selection is biased towards quiet passages and may include room tone, music or speech tails, so it is neither an unbiased noise floor nor a signal-to-noise ratio. Recordings with no qualifying passages report an unknown level, not zero noise. The measurement is informational and does not enable denoising, alter source selection or add an approval gate. Existing version 1 quality results remain in their original runs; new runs recompute them before caching version 2 results.

The alignment prototype uses local energy-change sketches and window correlation. Its globally anchored per-file refinement handles encoder timing differences without assuming that concatenated parts have one fixed offset. It is not a calibrated general-purpose fingerprint service; speed changes, silence, repeated material and different performances can remain unresolved. No audio or fingerprints are uploaded.

Quality selection is a documented heuristic, not a perceptual-quality score. Spectral energy can include noise/artifacts. Noise removal, speech-to-text matching, perceptual ranking and automatic assembly from incompatible performances are not implemented. Non-book items are classified where local evidence supports it, but require a separate processing recipe; changing `allowed_kinds` alone cannot turn an episode into a book plan.

## Providers and preservation

Default ingest providers are Audible, Audnexus, AudiobookDB, Hardcover, Open Library and Apple Books. Existing API keys are reused through their normal adapters. Google Books is optional and requires `APP_GOOGLE_BOOKS_KEY`; the key is sent in a header, never an archived URL. Goodreads, publisher and BBC adapters have not been implemented.

Audible detail requests include recording attributes, descriptions, ratings and public reviews. Overall/story/performance distributions remain separate. Hardcover work details retain reviews, moods and tags. Apple search is followed by a compatible public product-page lookup for narrator, language and runtime. Candidate selection considers complete author lists, language, narrator and duration; first search results are not blindly accepted. Numeric Audible product IDs are retained.

Provider HTTP observations are archived before use, including unknown fields, failures and cache hits. Authentication headers/API keys are never observed. Cached evidence retains the original fetch time and is labelled cached; it is not presented as a new network response. Evidence lives under `<data_dir>/ingest/<run_id>/sources/`. SQLite schema version 8 adds the run/event/member/policy tables, using the existing verified pre-migration backup path.

Runs retain the original source assertions as well as derived metadata. Selecting a field does not delete the alternatives. Final output `metadata.json` contains the chosen fields, normalized source records, rejected candidates, work/community data, recording assertions, selected-source hashes and ingest reference. The raw provider evidence used by the plan is copied into the final output's `evidence/` directory. Feature streams and full stage history remain in the data directory and its backups. No original audio is deleted or moved by ingest.

## Grouping and source selection

A numbered/tagged chapter entry expands to its parent group. Other catalogue entries that point into the same discovered set are attached to one primary group; one proposal covers that group's selected representation. The inventory can represent a whole M4B and an MP3 sequence separately, rather than adding their durations together.

Leading chapter numbers such as `001 American Gods …`, `002 American Gods …` also form a sequence when sibling filenames share a title and start at 1, even without track tags. Their durations are summed into one recording and observed numeric gaps remain visible. Different title stems and complete-file alternatives stay separate.

Full-file and part representations are aligned against a reference timeline. Verified partial alternatives may be retained without blocking selection of a complete source. Unresolved/different-performance alternatives trigger review; the pipeline never joins their audio automatically. Observed missing track numbers and alternative representation completeness are retained as separate evidence. Natural filename ordering is used and the existing planner reports disagreement with track tags.

Selection prefers completeness, then successful decoding, peak safety and measured high-band retention. Near-ties require review. The existing processor receives **only the selected paths**, plus their already measured loudness data; it does not rediscover the inbox folder. Chapter segments and output tags are built from those inputs and the merged metadata.

The manifest freezes every discovered source file, local sidecar, catalogue member and archived provider response. Proposal and execution revalidate hashes and catalogue signatures; same-size source edits, new audio/sidecars or changed metadata invalidate the plan. Group proposal reservation and approved-plan claiming are atomic so concurrent workers cannot overwrite a human decision or execute the same approval twice. Automatic execution is restricted to the current group's approved plan.

Complete quality measurements may be reused across runs only when all source paths/hashes and representation layouts match the saved versioned measurement and the new decode has no diagnostics. The reuse points back to its originating run. A failed/incomplete measurement is recomputed.

## Threshold calibration

The web policy editor displays the complete policy. Saving creates a revision. Existing run decisions retain their original policy; **Evaluate thresholds** applies the current policy to the saved metrics without fetching providers or decoding audio again. Stage evidence and failed decisions are not overwritten.

```bash
./app ingest-policy > data/ingest-policy.json
# Edit the complete JSON policy; retain all known gate keys.
./app ingest-evaluate RUN_ID --policy data/ingest-policy.json
./app ingest-policy data/ingest-policy.json
```

Initial gates require readable sources, known title/author, an original-work link, multiple source families, no critical metadata conflict, high title/author similarity, close runtime, narrator/language corroboration, known abridgement, resolved source alignment, complete quality measurements, an unambiguous selection and no plan warnings. Audible, Audnexus and the local Audible sidecar count as one family. Storefront corroboration is not a guarantee of independent upstream data.

Edition ordinals such as `10th` and `Tenth` are normalized for comparison, with original titles retained. Once a recording's full identity is verified, subsequent searches use its main work title, and matching release dates guide the Apple product-detail lookup. A shorter storefront title can support that edition only with matching runtime, compatible cast evidence and language; a competing performance still requires review. Apple Books' embedded product data supplies the full cast when its visible narrator card names only one performer. Cast comparison tolerates one adjacent-letter transposition in a surname of at least six letters when the rest of the name matches and the mapping is unambiguous; original credits remain in the provider records.

Alignment defaults are 99.5% correlated coverage, no more than 0.5 seconds for a reported gap, at most 0.5% unmatched windows and no observed missing tracks. Runtime defaults to 2% relative error. These values are starting predicates, not a validated safety claim. Compare labelled good/bad recordings and examine false positives/negatives before enabling quality or processing automation.

`auto_approve_metadata` and `auto_approve_quality` independently permit automatic stage approval when every check for that phase passes. `auto_approve=true` permits qualifying runs to approve their final processing plan; `auto_process=true` additionally executes it and requires final automatic approval to be enabled. Policies saved before the separate phase settings inherit their previous `auto_approve` value for both phases. The gates still fail closed on unknown measurements, incomplete runs, ignored entries and unsupported processing kinds. Automatic approvals are bound to the policy revision. Existing manually approved plans retain their explicit human approval, subject to source/metadata integrity checks.

## Validation and live example

Synthetic end-to-end tests run generated audio through the stages, manually approve the selected manifest, process it with FFmpeg and verify final provenance/evidence plus processing metrics. Unit tests cover interrupted retries, policy replay, tampering, provider failures/caching, grouping, duplicate claims, negative fingerprint controls, quality unknowns and automatic-job isolation. Web tests exercise stage/metric presentation, policy editing, polling, errors and manual proposals.

Live Hitch-22 validation run `19e1dc09b3d04882a86fb1420567bf45` completed all nine ingest stages and selected the 15 MP3 files. It measured 99.9849% aligned coverage, zero unmatched windows, a largest reported gap of 0.3 seconds and selected-source true peak -1.1 dBTP. Complete unchanged quality measurements were reused by hash. Its initial policy passed 21 of 25 predicates and retained four review reasons: conflicting local title assertions, missing local narrator/language corroboration and plan warnings. Automation was off and no live audio was processed. Earlier validation runs remain in history.

Final validation: 293 backend unit tests and 11 integration tests passed. Backend coverage was 95.48%; 42 web tests passed with the web coverage gate, TypeScript check, lint and production build.

After installing this version, restart the running application server to load the new Ingest endpoints. The frontend build includes the Ingest navigation and page.

## Listening review indicators

Quality version 3 measures three 12-second passages at 10%, 50% and 90% of each representation. Each is decoded to mono at 16 kHz, divided into quarter-second windows, and the quietest and loudest fifth are used as **relative** quiet and active groups. The retained measurements include quiet high-band RMS (approximately 3–7 kHz), quiet-to-active level difference, active low-to-presence energy ratio (approximately 0–1 kHz versus 1–3 kHz), and all sample positions. Quiet high-band is unknown when the original sample rate is missing or below 14 kHz. The Ingest page also shows the existing whole-recording loudness range.

These are experimental indicators for choosing listening samples, not measurements of speech-specific SNR, hiss, muddiness or perceptual quality. Quiet windows can include speech, music, gates or digital silence; spectral bands depend on the narrator, microphone, production and bandwidth. They do not rank sources, alter processing or automatically recommend denoising. Version 1 and 2 cached results are retained but are not reused as version 3 results. Compare representative source excerpts by ear, including a normal speech passage and pauses, before testing any filter.

For existing and new runs, the selected-source view also displays probed formats, bitrates and sample rates, alongside release dates attributed to recording providers, ambiguous embedded date tags, and medium-related wording from folder names, file names and tags. These are clues rather than verified provenance: a CD label may describe a track sequence, a date may refer to a release or work, and higher bitrate cannot restore information already lost. The original recording date and medium remain unknown without supporting evidence. These clues do not influence ranking, approval or processing.

A continuous-sound review cue flags a selected recording when at least two sampled passages have quiet-to-active differences of -8 dB or higher. It is calculated from stored samples, including historical runs, without changing processing eligibility. This provisional heuristic can flag compressed or continuous narration and can miss music or effects; it is not speech, music, noise, or content-type classification. Listen before deciding whether background sound is intentional. Missing samples do not count as evidence.

### Confirmed medium-quality enhancement

The ingest view suggests listening candidates when encoded bitrate is at most 48 kbps or sampled quiet-to-active differences are at least -12 dB. These weak clues are not ratings: low bitrate or compression does not necessarily sound bad. A person can mark an ingest good, medium, poor, or unrated. Only a **confirmed medium** standard audiobook receives the Diamond and gradual levelling recipe when its plan is proposed; the queue displays this before approval. Ratings are tied to the ingest run and cannot be changed while a processing plan exists; repropose the plan after changing it. Good, poor, and unrated recordings retain the normal recipe. A continuous-sound cue warns that Diamond can remove intentional music or effects. Generative processing can change, omit or repeat words and results require listening review.

The optional worker expects an installed Diamond inference checkout and its checkpoint, a separate Python environment containing Diamond dependencies, FFmpeg, and the model's cached codec dependencies. By default it looks under repository `data/diamond-inference` and `data/torch-rocm-env/run-amd`; override with `STRATA_DIAMOND_CODE`, `STRATA_DIAMOND_MODEL`, and `STRATA_DIAMOND_PYTHON`. `STRATA_DIAMOND_DEVICE` defaults to `cpu`; `STRATA_DIAMOND_THREADS` defaults to 4. Model assets are not included in this repository. Consult Diamond and Descript Audio Codec licensing separately. CPU processing can take far longer than the book's duration and requires substantial disk space for temporary decoded chapters. Inputs are not overwritten.

The worker locates -35 dBFS pauses at least 0.2 seconds long, then restores short segments with a second of prior context and a brief blend to the original around each cut. It refuses to process if it cannot find safe-sized pause intervals. It applies the tested gated, smoothed local RMS gain, capped at ±3 dB, and the normal pipeline remeasures the result and encodes it at 44.1 kHz with peak checks. This is not a speech detector: level estimation may react to background and a detected pause can still split speech. The complete book has not been validated for word accuracy; the pipeline records enhancement in output metadata and failure leaves no final book.

Navigation follows **Library → Processing → Metadata → Quality → Proposed → Finished → Settings**.

## Required output identity

Planning and execution both reject blank or placeholder titles/authors, including old approved plans. A failed identity check writes no final output. Final summaries retain the resolved title and author so Finished does not fall back to an incomplete source catalogue entry. An approved ingest recording may also supply its provider cover; a new metadata approval clears an earlier negative cover cache entry.


## Regression cases from catalogue reviews

Each reported case is represented by isolated fixtures or a parametrised behaviour test; provider boundaries are mocked and tests never depend on this machine's audiobook library.

| Encountered case | Regression location | Behaviour retained |
| --- | --- | --- |
| A Man for All Markets | `tests/unit/test_ingest.py` | Recording-linked shorter work title, contributor roles and original publication metadata |
| American Gods anniversary / American Psycho credits | `tests/unit/test_ingest.py`, `tests/unit/test_matcher.py` | Edition wording and additional contributors retain the shared work identity |
| American Psycho coded chapters | `tests/unit/test_grouping.py` | Sequential code families and alternative formats, with gaps retained |
| Adrian Mole nested books / radio readings | `tests/unit/test_ingest.py`, `tests/unit/test_grouping.py` | Independent books within a source tree; book checks independent of radio processing eligibility |
| Less Than Zero publication year | `tests/unit/test_sources.py`, `tests/unit/test_ingest.py` | Earliest corroborated original year and matching same-year publisher |
| Charles/Chuck Klosterman | `tests/unit/test_contributors.py`, `tests/unit/test_book_titles.py` | Contextual name variants and author-prefixed subtitle matching preserve volume distinctions |
| A Very Stable Genius / ChiRunning | `tests/unit/test_openlibrary_local.py` | Coauthor union, full/main-title discovery and joined-word spelling |
| Easy Money quality scan | `tests/unit/test_process.py` | Invalid UTF-8 in diagnostic tags cannot discard successful measurements |
| Just William | `tests/unit/test_grouping.py`, `tests/unit/test_ingest.py`, `tests/integration/test_ingest_pipeline.py` | Full 395-file/28-disc structure, verified repairs, persisted grouping and real audio/API boundaries |

Grouping evidence is shown alongside source folders and filenames. Explicit tag repairs are stored separately, include provenance, and only apply while the file hash matches.

Review gates in existing runs use the current stage-automation settings and thresholds, while the original execution/provider policy remains in the run's audit record. Replanning a failed processing plan queues replacement proposals only for the selected entries, reusing valid ingest approvals and refusing changed inputs. Review pagination returns to an available page when approvals or background updates shrink the queue. These cases have backend/API and component regressions.


## Saved reads and bounded memory

Library workflow and Operations read compact SQLite run/consent headers, current membership, plan states and at most twelve recent activity messages per run. They do not load provider candidates, audio inventories or complete plans, and never re-evaluate identification to classify a row. Consent signatures and policy revisions classify the saved workflow; approval and execution still perform full validation.

HTTP GET/HEAD requests use archived evidence without projecting older resolver versions. Book source summaries, details and covers return saved facts and existing local assets; opening a page does not traverse the audio share, run probes, update catalogue durations, extract artwork or contact providers. Missing saved facts remain unknown. Startup reconciliation runs on the ingest worker. Run iteration and review formatting load one full evidence record at a time. Resolver projections are versioned in SQLite rather than a process-wide cache of full evidence.

SQLite inventories already retain per-file SHA-256, size, modification time and path, but those fields currently live inside inventory event JSON. They are not yet an indexed identity/location registry. For reuse after moves, a future normalized index should separate content identity (SHA-256 plus analysis version) from observed locations (path and stat), and store directory hashes for both relative-name manifests and path-independent content multisets. Filename and directory-name hashes are discovery hints, not authority to reuse technical facts or approval. Content hashes survive renames; verifying a new location still requires reading its bytes once, but need not repeat decoding or quality analysis. Any changed grouping, ordering, source selection or catalogue identity needs fresh consent even when individual file facts are reusable.


## Shared durable job queue

Server processing uses the SQLite `jobs` table (schemas 9–12), shared by ingest, proposal/build execution, source inspection, scan/match/enrich, approval validation, recording selection, evaluation, quality rating and listening previews. `APP_WORKERS` sets one total worker cap (default 1, range 1–8). Each background slot supervises an isolated `app.job_worker` process; process jobs use one internal book worker. Claims are transactional, identical pending submissions coalesce, and jobs with the same known book resource cannot overlap across domains. Jobs record queued/running/done/failed state, result, error and a persisted timeout (`APP_JOB_TIMEOUT_SECONDS`, default 1800 seconds). Timeout/shutdown force-kills the child and its tools before releasing the slot.

Interrupted, crashed or timed-out jobs fail permanently rather than replaying at startup. Ownership records mark their unfinished ingest runs/plans and previews failed too, so reconciliation cannot retry the same work under another run. Finished reviews remain intact, while unattempted collection children and remaining batch books are queued atomically. Explicit retry revisits the failed work. Processing displays recent failures with **Retry job** (`POST /api/jobs/{id}/retry`); an explicit process retry resets only that job's failed reservations and still performs normal input/consent validation. Failed-job history remains saved after a retry.

Source inspection/artwork discovery is enqueued after scanning uncached books. Data-folder changes return HTTP 409 while jobs are queued or running, serialized with parent-side submissions and claims. Isolated children pin `APP_DATA` to the claimed database, and batch failure publishes remaining work in the same transaction, preventing a settings-change gap.

Processing requests return HTTP 202 with a job ID. `GET /api/jobs/{id}` reads its saved state/result; the web client polls these lightweight records and reports job errors. Continuations are queued rather than run inside approval requests. Catalogue edits remain small database mutations; their mirror writes are queued. Startup indexes legacy cached artifacts and materializes review views on the same queue. Policy changes queue a saved-view refresh. Configuration remains in the small bootstrap settings file, with the settings response persisted in SQLite.

Review pages read materialized `read_models` rows, paginated using compact saved headers. Unmaterialized rows remain incomplete and cannot enable approval. Provider status, cover index, cover locations, source inspection, Audible sidecars, listening sample status/locations, plan summaries and verified output metadata are persisted in SQLite. JSON reads never walk the source or mirror filesystem. Audio, image and static responses serve existing files and may check the asset path; they do not generate or retrieve assets. `POST /api/books/{id}/inspect` explicitly queues source inspection/artwork retrieval and saves the resulting facts, including any discovered duration. Full run evidence remains archived; the interactive run endpoint returns at most 100 compact event headers.
