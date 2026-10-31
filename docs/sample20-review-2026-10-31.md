# Investigator review of the original 20 entries — 2026-10-31

**Superseded by the [approved multi-source review](sample20-multisource-review-2026-10-31.md).** The wider query was subsequently approved and completed; this document preserves the earlier Open Library-only findings.

## Result and scope

I reviewed all 20 original entries against their filenames, tags, measured audio, sidecars, neighbouring files and fresh Open Library results. The first unchanged-catalogue refresh linked only 2/20. Recovering identities from local evidence produced 9 exact unique Open Library links, 4 ambiguous sets of duplicate work records, and one good Skunk Works candidate rejected by the strict author-list rule. These are evidence-supported investigator decisions, not automatic processing approvals.

**13 strong book-work identifications, 1 probable book-work identification, and 6 other audio items.** The 14 book-related entries represent 13 distinct works because two are chapters of The Way of the Runner. Six entries are fragments, not complete audiobook candidates. No catalogue records or queue approvals were changed.

Recording evidence is much narrower: Great Expectations and Summer Farmer have mutually consistent local Audible sidecars, ASINs, performer tags and measured durations. Two Audible episodes have ASINs and informative tags but no episode sidecar in the original snapshot. None of this is a claim that every chapter is present or that a newly fetched external source independently verifies the recording. The currently implemented automatic policy remains unmet.

The broader multi-provider comparison was blocked by automatic approval review and is pending explicit approval of the destinations and account-backed lookups. Results below use Open Library only for fresh external corroboration; they do not claim fresh Audible, Goodreads, Apple, Google, Hardcover or AudiobookDB verification.

## Decisions for all 20 entries

| ID | Recovered identity | My judgement | Evidence and remaining issue |
|---|---|---|---|
| 1 | Frankie Boyle — Prometheus Vol. 1 | Stand-up recording, not a book | Album/title tags agree with filename; artist tag misspells Frankie as Frabkie. No Open Library result. Performer/catalogue corroboration still needed. |
| 171 | Charles Dickens — Great Expectations | Strong work; strong local recording identification | [Main work](https://openlibrary.org/works/OL8721462W) has 1,489 editions and 1861 first publication; duplicate work records do not make the novel uncertain. Local ASIN B004FT7MUS, narrator Martin Jarvis and 1,052-minute sidecar agree with measured 1,052.7 minutes. Sidecar explicitly says unabridged. |
| 196 | Kevin Morris — Summer Farmer, from White Man's Problems | Identified short story, not the whole collection | Local Audible sidecar and tags identify ASIN B00YYC7GWU, Matthew McConaughey, unabridged, 18 minutes; measured 18.3 minutes. Catalogue title FREE is wrong. Do not attach collection runtime or treat this as the whole book. |
| 230 | M. J. Arlidge — Six Degrees of Assassination, episode 3: III | Identified audio-drama episode | Embedded ASIN B08DCFH4Z6, author, series and cast identify a 29.4-minute episode. Catalogue author is the episode title. No Open Library book result is expected. |
| 273 | Stephen Fry's Victorian Secrets — episode 11: Séance, Science and Messiahs | Identified nonfiction audio-series episode | Embedded ASIN B08DFLWTFZ, Stephen Fry and series agree with path; 33.6 minutes. Catalogue author is wrong. A series/episode catalogue is needed. |
| 306 | Blagger's Guide To — S01E03, Popular Music | Radio-comedy episode | Tags explicitly say Radio Comedy; series, season/episode and 26.6-minute duration fit. Do not search for a print work. BBC Programme Index is a suitable next source. |
| 331 | Blagger's Guide To — S05E04, Country Music | Radio-comedy episode | Same reasoning as 306; 28.9 minutes, different season/episode. Programme metadata still needs external corroboration. |
| 363 | Carrie Sun — Private Equity | Strong work; recording not yet verified | [Work](https://openlibrary.org/works/OL26772702W), author and memoir description agree with tags. 524.8 minutes. Open Library reports 2022; local promotional text refers to anticipated books of 2024. Publication year is unresolved, not blindly accepted. |
| 388 | Jerry Seinfeld — Is This Anything? | Strong work; recording not yet verified | Author recovered from duplicated artist tag Jerry Seinfeld/Jerry Seinfeld. [Work](https://openlibrary.org/works/OL21896399W), 2020; duplicate Open Library record is the same title/author. 379.3 minutes; verify recording/abridgement rather than assuming author narration from a slash-delimited artist value. |
| 413 | Malcolm Gladwell — Outliers: The Story of Success | Strong work; recording not yet verified | [Work](https://openlibrary.org/works/OL5749847W), 2008, author tag. Store is a filename/tag typo for Story. Reject the separate Outliers in 30 Minutes summary result. 437.7 minutes. |
| 438 | Probably Julian Barnes — The Noise of Time | Probable work only | [Work](https://openlibrary.org/works/OL17764501W), 2016, matches the compressed filename TheNoiseofTime_mp332. File has no tags; 341.4 minutes alone cannot establish author, narrator or abridgement. Needs publisher/runtime corroboration or checking opening credits. |
| 463 | Adharanand Finn — The Way of the Runner, chapter 1 | Strong work; fragment, not a whole book | [Work](https://openlibrary.org/works/OL20032260W), 2015. Author, album and composer Derek Perkins in tags. This 4.9-minute item is track 1/27 in a 27-file folder totalling 546.1 minutes. |
| 488 | Adharanand Finn — The Way of the Runner, chapter 26 | Strong work; fragment of the same book as 463 | Same work and recording tags; track 26/27, 20.0 minutes. Must not become another book or another processing job. |
| 593 | Arthur Conan Doyle — The Valley of Fear, Part II: The Scowrers | Strong work; partial book within an omnibus | [Work](https://openlibrary.org/works/OL262505W). Parent folders establish The Valley of Fear; tags name The Complete Sherlock Holmes: The Heirloom Collection. Full Valley folder has 14 files totalling 306.1 minutes; selected Part II is 160.1 minutes. Do not label the part as the entire collection. Publication year needs serialization/book-edition distinction. |
| 621 | Ben R. Rich and Leo Janos — Skunk Works | Strong work; chapter fragment | [Work](https://openlibrary.org/works/OL3466236W), 1994, has both actual authors plus a malformed combined-author entry. Tags name Pete Larkin. Selected track 10/17 is 38.6 minutes; all 17 files total 728.5 minutes. Strict list equality falsely rejected this good work candidate. |
| 646 | Cade Metz — Genius Makers | Strong work; recording not yet verified | [Work](https://openlibrary.org/works/OL24233014W), 2021; author/title/subtitle/description agree with local tags. Folder totals 599.8 minutes. Need recording narrator/identifier. |
| 869 | Christopher Hitchens — Hitch-22, part 5 | Strong work; fragment plus duplicate-format hazard | [One work record](https://openlibrary.org/works/OL24487782W); several Open Library duplicates describe the memoir. Folder contains one 1,050-minute M4B AND 15 MP3 parts totalling another 1,050 minutes. Summing the folder would double runtime. Choose one representation after comparison; do not delete either automatically. |
| 903 | David Ricketts — When the Fund Stops, chapter 4 | Strong work; chapter fragment | [Work](https://openlibrary.org/works/OL26338029W), 2021; tags identify Deborah Balm and track 4/16. Selected track 29.0 minutes; all 16 files total 263.0 minutes. Catalogue title Chapter 004 and author When the Fund Stops need replacing at book-group level. |
| 935 | Beverly Donofrio — Riding in Cars with Boys | Strong work; plausible local recording, external edition verification needed | [Main work](https://openlibrary.org/works/OL4811725W), 1990, matches author after reversing surname-first catalogue text. Multiple duplicate works and a movie tie-in must not be mistaken for different original works. Comment says Read by Christina Moore; album says Unabridged 06:16; measured 375.9 minutes. Strong internal consistency, but recording ID/publisher still needed. |
| 973 | Kashmir Hill — Your Face Belongs to Us | Strong work; recording not yet verified | [Work](https://openlibrary.org/works/OL34278920W), 2023, matches author/title and the detailed Clearview AI description in tags. Catalogue folder name Facial Recognition is only a topic. 606.7 minutes. |

## Sources worth adding next

- **Apple Books/iTunes Search API:** documented audiobook search and identifier lookups. Useful as an additional storefront catalogue, subject to actual recording-level data returned. No sample queries were run yet. [Official documentation](https://performance-partners.apple.com/search-api).
- **Publisher product pages / Penguin Random House metadata:** several files explicitly identify Books on Tape, Penguin Random House or Simon & Schuster. Publisher records are useful for narrator, audio ISBN, edition date and duration. PRH has a title/author metadata API; its access requirements need checking before integration. [PRH developer portal](https://developer.penguinrandomhouse.com/).
- **BBC Programme Index / programme pages:** better fit for the two Blagger's Guide entries than any book catalogue. [British Library guidance](https://bl.libguides.com/reference-services/sound-and-vision/how-to-access-bbc-recordings).
- **MusicBrainz:** supports audiobook, spoken-word and audio-drama releases; promising for stand-up, CDs and compilations. Actual coverage of this sample has not been tested. [Release types](https://musicbrainz.org/doc/Release_Group/Type).
- **Google Books:** additional work/edition metadata and available aggregate ratings; it cannot establish a particular audio recording. Public API requests require project identification/API key according to the current docs; availability without a configured key has not been tested. [Official API guide](https://developers.google.com/books/docs/v1/using).
- **Hardcover:** already integrated; candidate-specific details/reviews are worthwhile once identities are recovered. The wider fresh comparison remains pending approval.
- **OverDrive:** particularly relevant because several files retain OverDrive tags; its metadata API exposes audio duration. API access is restricted to library customers, so this is not a ready personal-use integration. [Metadata](https://developer.overdrive.com/api-docs/discovery-apis/metadata), [access requirements](https://developer.overdrive.com/getting-started/api-overview).
- **WorldCat:** useful edition/ISBN bibliography, but API access requires eligible library subscriptions; not a low-friction next integration. [Access requirements](https://www.oclc.org/developer/api/oclc-apis/worldcat-search-api.en.html).
- **LibriVox:** documented catalogue API for public-domain volunteer recordings; use when local evidence points to that origin, not as proof for commercial narrations. [API](https://librivox.org/api/info).

## What the current assessor gets wrong

1. It searches poor catalogue fields before recovering identity from tags and parent groups.
2. It treats duplicate Open Library work records as identity ambiguity even when the underlying work is clear.
3. It compares author lists literally, missing reversed names, duplicated artist values and malformed extra contributor entries.
4. It reads is_abridged but ignores the explicit format_type=unabridged in local Audible sidecars. Thus its reported unknown abridgement is wrong for 171 and 196.
5. It applies a whole-book model to stand-up, short stories, series episodes, chapter fragments and omnibus parts.
6. It does not detect alternative complete-versus-chaptered representations of the same audio.
7. First publication years can be wrong or describe serialization, editions or prepublication records. Work identity confidence is not field-value confidence.

## Evidence artifacts

- Original snapshot: `data/evidence-report-2026-10-31.json`.
- Fresh unchanged-catalogue report: `data/evidence-sample20-fresh-20261031T095617.json`.
- Corrected-identity Open Library lookups and archived raw responses: `data/sample20-openlibrary-corrected-20261031T095707/` (20 entries; 9 linked, 4 ambiguous, 5 not_found, 2 missing_identity).
- Local neighbouring-file probes: `data/sample20-fragment-inventories-20261031.json`.
- Prepared broader comparison: `data/research_sample20.py`; not executed because expanded destinations/account lookups require approval.

Artifacts are local and gitignored. No source responses were discarded or prior snapshots overwritten. This was an investigation, not a product-code change; no new application test run was needed.
