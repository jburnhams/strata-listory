# Just William source grouping investigation — 3 November 2026

The saved inventory for catalogue entry 1172 (`/home/jburnhams/audio-local/Books/Just William Collection`) contains 395 MP3 files in 28 numbered subfolders. Parent run `aaca865e0f534c509ba500fbfd352196` split that inventory into 207 work groups. All 207 are currently separate metadata reviews, including individual stories and story fragments. The bulk metadata refresh reused those source groups, so it could not correct their boundaries.

Embedded album/disc labels and continuous per-folder track numbers show that the numbered subfolders are discs of approximately ten BBC audiobook volumes. Individual story names belong in the chapter/story list. They do not establish independent source-book boundaries in this collection.

| Audiobook volume | Numbered folders | Files | Measured duration, minutes |
| --- | --- | ---: | ---: |
| 1 | 01–02 | 30 | 152.3 |
| 2 | 03–04 | 30 | 137.5 |
| 3 | 05–07 | 30 | 150.4 |
| 4 | 08–11 | 61 | 176.3 |
| 5 | 12–14 | 61 | 179.4 |
| 6 | 15–17 | 60 | 161.4 |
| 7 | 18–20 | 60 | 177.6 |
| 8 | 21–23 | 12 | 170.9 |
| 9 | 24–26 | 12 | 168.4 |
| 10 | 27–28 | 39 | 139.2 |

Examples: folders 03 and 04 have albums `Just William 2 [Disc 1]` and `Just William 2 [Disc 2]`; folders 08–11 have spelling variants of `JUST WILLIAM VOL4 CD1` through `CD4`. Volume five is written as both FIVE and VOLUME FIVE. Folders 21–23 mix `Just William 8` and `Just William 08 [Disc 3]`. Folder 26 has Unknown Album, so its membership in volume nine is inferred from its location, track sequence and the matching story sequence in the publisher's later compilation; that inference should remain visible for review.

Publisher evidence:

- [Penguin/BBC volume 1](https://www.penguin.co.uk/books/423050/just-william-by-richmal-crompton/9780563478218) identifies Richmal Crompton as author and Martin Jarvis as reader, lists ten stories matching folders 01–02, and describes a two-CD release of approximately 150 minutes.
- [Penguin/BBC first radio collection](https://www.penguin.co.uk/books/435113/just-william-a-bbc-radio-collection-by-richmal-crompton/9781785296406) has a story sequence corresponding to volumes 1–5, with extra interview material advertised. It is a later compilation release, published in 2017.
- [Penguin/BBC second radio collection](https://www.penguin.co.uk/books/437922/just-william-a-second-bbc-radio-collection-by-richmal-crompton/9781787530768) lists the stories in volumes 6–10 in matching order, including the four stories in folder 26. It is a later compilation release, published in 2018.

Other Just William folders exist, including catalogue entries 1115 (first BBC collection) and 1173 (second BBC collection). Their overlapping publisher contents are evidence of related releases; audio identity and completeness have not been established by fingerprint comparison.

Tag quality is inconsistent. The collection includes Unknown Artist, W. Brown, Just William, Martin Jarvis, and even Vikas Swarup as artist values. These cannot be accepted blindly as the literary author. The publisher evidence distinguishes Richmal Crompton from narrator Martin Jarvis.

The current splitter counts repeated filename story stems globally, then lets those story names override otherwise shared album metadata when several stems occur. It also compares raw album labels before normalising disc/volume labels, and requires author agreement even where artist tags are unreliable. These decisions explain the excessive split.

Recommended regrouping model: release/volume → ordered discs → named stories → split tracks. Use album/volume/disc identity, track continuity, contributor evidence and publisher contents jointly. Preserve ambiguity where tags conflict or a disc label is absent. The original printed-book identity is separate: these BBC selections and later compilations need not have the same contents as the original printed book titled Just William. No source audio or live catalogue grouping was changed during this investigation.

Implementation: grouping version 3 recognises ordered per-album track runs, normalises explicit volume/disc spelling and joins corroborated sibling discs before considering individual story stems. Raw tags are preserved. Ambiguous W Brown/Unknown Album discs require explicit publisher-backed layout annotations bound to each file's SHA-256; annotations survive scoped rediscovery and are invalidated by source changes. The general rule produces 12 conservative groups; verified corrections produce the ten volumes above. Source summaries display the grouping rationale. Numeric volume differences cannot be erased by fuzzy title matching.

Regressions cover the complete 395-file/28-disc variant set, single-track discs, missing first tracks, missing whole discs, distinct volumes and authors, mixed flat collections, alternative performances, inconsistent artists, changed source files, and publisher correction persistence through splitting. A real-audio integration test verifies ingest and API evidence for multiple anthology volumes.

Live result: parent run `53d4195d44f54b548f4067349321c824` now has ten current volume reviews. All 207 previous story-level catalogue entries map to these ten new runs; all 395 source files are assigned exactly once. Each volume has one ordered representation. Metadata lookups completed for all ten; each remains awaiting metadata approval. The separate first/second BBC collection releases remain available for later comparison.
