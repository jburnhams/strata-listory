# Listen kinds: book, radio series, radio programme, play

Is a listen a book, or something broadcast (radio series, one programme, a play)? Code: `src/app/store/kinds.py` (the scoring, pure), `kinds_db.py` (gathering evidence, writing verdicts), `src/app/identify/radioindex.py` (the local radio programme index).

## Why a score

The structure step (`store/structure.py`) decides book or programme from file names alone (numbered episodes, dated broadcasts, `S01E02`). It cannot see a series whose files are called `01.mp3`, a play in one file, or a collection of radio adaptations filed under a book title. Those were missed (a series credited to "BBC Radio", a play named with a bracketed channel and year, a ten-book collection by "BBC Radio Adaptation"). Rules that would catch them would also catch real books, because the opposite is just as common: an unabridged reading narrated by a BBC actor, or published by BBC Audio, is a book.

So each piece of evidence is a *signal* with a weight in log-odds, added to a prior (about 17% broadcast); a logistic gives `p_broadcast`. At 85% or more the listen is classified as broadcast, at 25% or less as a book, in between it stays as it was and is flagged **review** with the kind it would be. Every verdict keeps its signals, so a card can say why.

## Signals (weights in log-odds; positive points to broadcast)

| Signal | Weight | Fires on |
|---|---|---|
| structure | +2.0 | the structure step made a `programme` group (a prior, not a verdict: other evidence can overturn it) |
| channel | +3.0 | BBC Radio 1-7, R4, Radio 4 Extra, World Service, iPlayer, get_iplayer, RTE radio in the title, author, narrator, publisher, artist, composer, folder or tags |
| radio credit | +2.2 | an author/artist that is just "Radio" or "BBC Radio" |
| strand | +2.2 | a named strand: Drama on 3, Afternoon Play, Saturday Play, Woman's Hour, Classic Serial (Book at Bedtime only +1.0: it is a reading) |
| bbc credit | +1.0 | "BBC" as the author/artist (never as a publisher) |
| radio wording | +3.3 / +1.6 | "radio adaptation", "radio drama", "radio play"; the weaker "radio collection", "radio show", "broadcast" |
| dramatised | +1.8 | dramatised/dramatisation, adapted by, full-cast production/dramatisation, audio drama |
| series number / episode number / broadcast date | +1.8 / +1.1 / +1.3 | "Series 2", `S01E03`, "Episode 4" (not "Episodes in ..."), air dates |
| radio genre | +2.5 | genre tag Radio Drama, Radio Comedy, Wireless, Podcast, BBC R4 Drama, ... |
| programme genre | +0.8 | Drama, Comedy, Factual, Documentary, Standup, Humour ... |
| slot-length parts | +1.2 | three or more files, 80% of them about 14, 28, 44 or 58 minutes (books are cut by size, broadcasts by the schedule) |
| play length | +0.6 | one to three files, under 140 minutes |
| cast list | +0.8 | three or more names credited as narrator |
| audible content type | +2.5 / +2.0 / +1.5 | the closest matching Audible recording (within 6% of the runtime, from a candidate with P of 0.5 or more) is a "Radio/TV Program" / "Show" / "Performance". When Audible lists the same length both ways the signal is "audible disagrees" and counts for nothing. |
| audible keywords | +1.4 | keywords such as dramatizations on that recording |
| programme catalogue | +1.0 to +1.6 | the title is in the radio programme index (below) |
| unabridged | -2.2 | "unabridged" / "abridged" in the title, album or folder |
| reading | -2.0 | "read by", "narrated by", "classic readings", "audiobook" |
| audible audiobook | -3.0 | the closest matching Audible recording is an ordinary audiobook ("Product") |
| audible id | -1.2 | the tags carry an Audible ASIN |
| book genre | -0.8 | Audiobook, Fiction, Biography ... |
| long | -1.8 / -3.0 | 6 hours / 10 hours, unless something marks it as an archive of broadcasts (channel, strand, radio genre, radio wording, slot-length parts) |

BBC Audio files readings under Audible's "Radio/TV Program" too, which is why that is evidence and not a rule, and why "unabridged" and "read by" outweigh it.

Kinds: `book`; `radio_series` (series/episode numbers, or several broadcast-sized parts); `radio_show` (one programme: documentary, stand-up, one-off); `play` (a drama or dramatisation). The listen's `kind` is set to the verdict (`listen.kind`, event `reclassify` with the reason); the full verdict is in `listen_kind` (kind, `guess`, `p_broadcast`, `band`, `signals`, `decided_by`).

A person's choice (`decided_by = user`, from the "Why?" buttons or `POST /api/listens/{id}/kind`) is never recomputed over.

## Using it

```sh
./app listens-kinds [--dry-run] [--report out.json] [ids...]   # classify every live listen (the report has real titles: keep it outside the repo)
./app listens-build ...      # classifies after the metadata step, before copies are merged (only books are merged)
./app listens-identify ...   # classifies again afterwards: matched Audible recordings add evidence (--no-kinds to skip)
```

Web: the Matches page has a **Kind check** filter (radio, plays and programmes / kind is a close call), its cards show the kind, how sure, the reasons behind "Why?" and buttons to overrule; the Listens page has the same check filter and column. `GET /api/matches?check=review|broadcast`, `GET /api/listens?check=review|broadcast`.

## Radio catalogues

`python scripts/fetch_radio.py` builds `data/radio/index.sqlite` (offline lookups through `radioindex.find`, never a hard dependency: without the file the other signals decide). Raw pages are cached under `data/radio/raw/` so a refresh resumes.

* **BBC Programmes A-Z** (`bbc.co.uk/programmes/a-z/by/<letter>/all.json?page=N`, 30 titles a page, about 100,000 titles, a polite 0.6 s apart): every top-level brand and series with the service that owns it; only radio services are kept. Usable, no key.
* **Wikidata** (SPARQL, English labels): about 4,200 direct instances of radio programme and 2,600 of radio drama. The subclass walk (`P279*`) times out; direct instances answer in a second.
* **BBC Genome** (Radio Times listings 1923-2009): behind a human-verification wall, no bulk download or API. Not used.
* **BBC programme JSON** (`/programmes/<pid>.json`): works, gives service, genre and episode counts per programme, but needs a pid; the A-Z already provides the title list, so per-programme pages are not fetched.
* **MusicBrainz** and **Open Library** subjects were considered: MusicBrainz is music, and the Open Library dumps are already used for books; neither identifies a broadcast.

Titles collide with books ("Middlemarch", "Just William"), so a catalogue hit is moderate evidence (+1.0, +1.6 when it is on the BBC list and also a Wikidata drama or listed twice), and keys shorter than six letters are ignored.

## Known limits

* The embedded-tag summary stores no comment tag, so "dramatised by ..." in a comment is not seen.
* Plays and dramatisations of novels that a publisher lists as ordinary audiobooks (Audible "Product") can only be caught by their tags and lengths.
* Weights are hand-set and explainable rather than fitted; the review band is the safety valve.
