# Covers

A stage between Listens and Processing: for every confirmed or accepted book listen, find its cover at every source that knows the identified book and audiobook editions, keep the pictures at the highest size on offer, and let a person choose the preferred one. The choice is saved as the listen's `cover` choice (`listen_choice`), ready for the next stage.

## The job (`./app covers`, or "Search for covers" on the Covers page)
- **Which listens:** kind `book`, a work accepted or confirmed, no active `cover` choice, and no finished cover search newer than the listen's newest change (`covers.run.pending`). Choosing a cover is not a change. `--force` or explicit ids search again.
- **What is known:** the accepted work's identifiers (all Audible ASINs, ISBNs, Open Library works) plus the folder and files the listen was made from (`covers.run.context`).
- **Sources** (`covers/sources.py`; each returns pictures and a status, never raises): `audible` (product image of every ASIN from the local archive, size suffix stripped), `audnexus`, `openlibrary` (by ISBN, and the covers listed on the work and its editions, large), `apple` (title-matched, asked at 3000 px), `google` (by ISBN, largest link), `local` (an image in the folder, art embedded in the first audio file). Not used: Hardcover (needs a token), AudiobookDB (its terms exclude its images). Adding a source is one function and a line in `SOURCES`.
- **Storage:** pictures once by sha256 under `<data_dir>/covers/` (`cover_image`: size, bytes, a 64-bit difference hash from ffmpeg); `cover_run` per search (every source's status and reason); `cover_candidate` per picture a source offered. Near-duplicates (equal or close hash, same shape) are shown as one cover with every source and the largest standing for it.
- **Politeness:** one request every 0.4 s per host, https only, 40 MB cap. A 429 is retried up to four times, waiting as long as `Retry-After` says (at most 60 s); a host that still says 429 is left alone for ten minutes and the source records `rate limited` (the search can be repeated from the page). Google Books allows few anonymous requests: an API key in `GOOGLE_BOOKS_API_KEY` or `~/.config/strata-listory/googlebooks.key` raises that.

## The page
Tabs: To choose, Chosen, None found, All searched; search by title; per listen thumbnails (size, bytes, sources) to click, Clear choice, Search again, and what each source said (found / nothing / failed / not tried, with the reason).

## Not done yet
Cover choice per filegroup (copy) of a listen, radio and plays, a source for BBC programme images, and reading the chosen cover in Processing (the old path still uses `details.cover`).
