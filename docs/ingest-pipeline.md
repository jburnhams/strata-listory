# Staged ingest, evidence and threshold policies

The ingest pipeline consumes scanned catalogue entries before final-library processing. It groups source files, recovers identities from local evidence, queries providers, compares audio representations, measures quality and creates a plan using only the selected files. Every run and stage attempt is retained. Automatic approval and processing both default to off.

The current rollout is books only. Radio programmes, comedy performances, plays and podcasts are deferred; a print-work match alone must never establish the identity of a broadcast or performance. The existing output planner remains responsible for loudness tolerance, lossless gain, copy-versus-encode decisions and final peak verification. Clear identity does not bypass audio or completeness checks. Selected-source plans retain both the complete inventory manifest (to detect changed alternatives) and the selected input manifest (to decide what is processed).

## Use it

Run `./app doctor --audio` to check the audio toolchain. Optional fingerprint and waveform-analysis tools and local verification are documented in [audio-tools.md](audio-tools.md).

Open **Ingest** in the web app. Enter catalogue IDs or leave the field blank to analyse the next five unanalysed book entries. Online collection uses the configured ingest providers; turn it off for an offline review. A single background worker handles expensive jobs, and CLI jobs use the same file lock so two ingest jobs cannot analyse/propose the same group concurrently.

```bash
./app scan
./app ingest-run 869 --limit 1
./app ingest-status
./app ingest-show RUN_ID
./app ingest-policy
./app ingest-evaluate RUN_ID
./app ingest-enqueue RUN_ID       # proposes the saved plan for manual approval
./app process-approve BOOK_ID
./app process-run
```

`ingest-run --offline` makes no metadata-provider requests. `ingest-resume RUN_ID` resumes an interrupted/failed run, preserving failed attempts and reusing completed stages only if its source files and catalogue metadata are unchanged. Explicitly running the same book again creates a new auditable run. Default batches skip entries that already belong to a run; supply their IDs to retry.

## Stages and principal metrics

Each stage records running/done/failed events, timestamps, elapsed seconds, numeric metrics (or unknown), result data and errors. UI timings update while a stage is running. Stages continue through reviewable evidence gaps to collect calibration data; actual exceptions stop the run and preserve the failure.

| Stage | Measurements and retained evidence |
|---|---|
| Inventory | Every audio file, complete ffprobe JSON, SHA-256, size, mtime, local Audible sidecars, grouping/member IDs, alternative representations, probe errors and observed missing/duplicate track numbers |
| Identity | Title/author/narrator recovery, embedded IDs, language/abridgement, content kind, conflicting local assertions, manual locks and the underlying tag/sidecar evidence |
| Providers | Provider attempts/errors, availability, source response count, raw request/response/cache observations with original retrieval time, every candidate and detail response |
| Metadata | Selected work/recording assertions, field provenance/alternatives, rejected candidates and reasons, source-family count, original-work link, critical conflicts, title/author similarity, runtime error, narrator/language corroboration, abridgement and recording ambiguity |
| Alignment | Per-representation and per-file window maps, correlated coverage, gaps, unmatched fraction, missing/duplicate tracks and unresolved alternatives; failed matches never prove another performance |
| Quality | Full loudness, true peak, loudness range, peak statistics, silence measurements, channel-cancellation loss, decode diagnostics and three spectral-retention samples per representation |
| Selection | Complete-source preference, decode/peak safety and sampled spectral-retention ranking; explicit selected file list, quality metrics for that selection and an ambiguity flag |
| Planning | Existing chapter/tier/loudness plan, warnings, selected inputs and frozen source/evidence manifests; no audio is written |
| Eligibility | Every actual value against its threshold, missing measurements, content-kind gate, passed/failed counts and the exact policy snapshot |
| Queue / processing | Proposal/approval audit, processing elapsed time, output size/duration, loudness, true peak, retries and output-verification result, including manual processing through the existing queue |

Measurements called `score` are similarity measurements, not probabilities. `eligibility.passed/failed` are predicate counts, not confidence percentages. Unknown/nonfinite measurements cannot pass a numeric gate. Missing narrator/language evidence stays unknown rather than being counted as agreement.

The alignment prototype uses local energy-change sketches and window correlation. Its globally anchored per-file refinement handles encoder timing differences without assuming that concatenated parts have one fixed offset. It is not a calibrated general-purpose fingerprint service; speed changes, silence, repeated material and different performances can remain unresolved. No audio or fingerprints are uploaded.

Quality selection is a documented heuristic, not a perceptual-quality score. Spectral energy can include noise/artifacts. Noise removal, speech-to-text matching, perceptual ranking and automatic assembly from incompatible performances are not implemented. Non-book items are classified where local evidence supports it, but require a separate processing recipe; changing `allowed_kinds` alone cannot turn an episode into a book plan.

## Providers and preservation

Default ingest providers are Audible, Audnexus, AudiobookDB, Hardcover, Open Library and Apple Books. Existing API keys are reused through their normal adapters. Google Books is optional and requires `APP_GOOGLE_BOOKS_KEY`; the key is sent in a header, never an archived URL. Goodreads, publisher and BBC adapters have not been implemented.

Audible detail requests include recording attributes, descriptions, ratings and public reviews. Overall/story/performance distributions remain separate. Hardcover work details retain reviews, moods and tags. Apple search is followed by a compatible public product-page lookup for narrator, language and runtime. Candidate selection considers complete author lists, language, narrator and duration; first search results are not blindly accepted. Numeric Audible product IDs are retained.

Provider HTTP observations are archived before use, including unknown fields, failures and cache hits. Authentication headers/API keys are never observed. Cached evidence retains the original fetch time and is labelled cached; it is not presented as a new network response. Evidence lives under `<data_dir>/ingest/<run_id>/sources/`. SQLite schema version 8 adds the run/event/member/policy tables, using the existing verified pre-migration backup path.

Runs retain the original source assertions as well as derived metadata. Selecting a field does not delete the alternatives. Final output `metadata.json` contains the chosen fields, normalized source records, rejected candidates, work/community data, recording assertions, selected-source hashes and ingest reference. The raw provider evidence used by the plan is copied into the final output's `evidence/` directory. Feature streams and full stage history remain in the data directory and its backups. No original audio is deleted or moved by ingest.

## Grouping and source selection

A numbered/tagged chapter entry expands to its parent group. Other catalogue entries that point into the same discovered set are attached to one primary group; one proposal covers that group's selected representation. The inventory can represent a whole M4B and an MP3 sequence separately, rather than adding their durations together.

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

Alignment defaults are 99.5% correlated coverage, no more than 0.5 seconds for a reported gap, at most 0.5% unmatched windows and no observed missing tracks. Runtime defaults to 2% relative error. These values are starting predicates, not a validated safety claim. Keep automation off while comparing labelled good/bad recordings and examining false positives/negatives.

`auto_approve=true` permits qualifying new runs to approve their specific plan; `auto_process=true` additionally executes it and requires automatic approval to be enabled. The gates still fail closed on unknown measurements, incomplete runs, ignored entries and unsupported processing kinds. Previously approved automatic plans are refused if the policy revision changes before execution. Existing manually approved plans retain their explicit human approval, subject to source/metadata integrity checks.

## Validation and live example

Synthetic end-to-end tests run generated audio through the stages, manually approve the selected manifest, process it with FFmpeg and verify final provenance/evidence plus processing metrics. Unit tests cover interrupted retries, policy replay, tampering, provider failures/caching, grouping, duplicate claims, negative fingerprint controls, quality unknowns and automatic-job isolation. Web tests exercise stage/metric presentation, policy editing, polling, errors and manual proposals.

Live Hitch-22 validation run `19e1dc09b3d04882a86fb1420567bf45` completed all nine ingest stages and selected the 15 MP3 files. It measured 99.9849% aligned coverage, zero unmatched windows, a largest reported gap of 0.3 seconds and selected-source true peak -1.1 dBTP. Complete unchanged quality measurements were reused by hash. Its initial policy passed 21 of 25 predicates and retained four review reasons: conflicting local title assertions, missing local narrator/language corroboration and plan warnings. Automation was off and no live audio was processed. Earlier validation runs remain in history.

Final validation: 293 backend unit tests and 11 integration tests passed. Backend coverage was 95.48%; 42 web tests passed with the web coverage gate, TypeScript check, lint and production build.

After installing this version, restart the running application server to load the new Ingest endpoints. The frontend build includes the Ingest navigation and page.
