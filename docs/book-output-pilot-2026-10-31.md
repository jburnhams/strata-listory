# Books-only output pilot

Non-books are deferred at the user's request. Private Equity (363), Is This Anything? (388) and Outliers (413) were selected from the previously reviewed matches for an initial output pilot. Each completed staged ingest with raw provider evidence and full audio checks. Their previously identified Audible product IDs were fetched directly before a manual processing decision: B0CV1BN2KL, 1471198588 and B004EW5DB6 respectively.

Global automatic approval/processing and numeric thresholds remain unchanged. Explicit review events retain missing local narrator/language corroboration and planner warnings. These were manually reviewed matches, not unattended threshold successes. Originals remain in place. Raw responses and review records are copied into final output evidence directories.

The pilot found and corrected two handoff issues:

- Plans selecting one representation now validate the full source inventory, including alternatives, while processing only selected files. Previously a preserved alternative could incorrectly prevent processing.
- Average stereo-to-mono output now uses measured mono loudness for the existing gain/tolerance decision. The first Is This Anything? output measured -23.5 LUFS because the planner used stereo loudness (-20.5 LUFS). Its original output is retained under `<final>/.pilot-history/388-before-mono-loudness-fix`; the corrected build uses the original source. No tolerance or peak threshold changed.

Local reproduction/review artifacts are under `data/book-output-pilot-20261031/`; completed results are recorded in the catalogue and ingest processing events. A synthetic stereo integration test checks that this gain decision produces mono output within the existing quiet tolerance and below the peak ceiling.

## Verified final results

| Book | Chapters | Final LUFS | True peak dBTP |
|---|---:|---:|---:|
| Private Equity | 12 | -18.1 | -1.6 |
| Is This Anything? (corrected build) | 9 | -18.9 | -1.1 |
| Outliers | 16 | -19.2 | -1.6 |

All three final outputs passed duration checks (within 0.1 seconds of source), chapter counts and portable evidence hash verification. All original source hashes were rechecked unchanged. Final files are under the configured `/home/jburnhams/audiobooks` directory. All three use the existing V5 encoding decisions; the `reencoded` result flag specifically describes forced conversion of otherwise copyable chapters, not every encoding operation. Seinfeld's stereo input was never eligible for the mono-MP3 copy path.

Validation after both fixes: 296 backend unit tests passed with 95.52% coverage, targeted real-audio ingest integration tests passed, and lint/diff checks passed. This is an initial three-book pilot; remaining reviewed books have not yet been processed through this rollout.
