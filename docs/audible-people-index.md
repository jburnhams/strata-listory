# Local Audible index built from people

Goal: a local, private catalogue of Audible audiobooks (ASIN, title, authors, narrators, series, runtime, language, release date) for identification, in the way `data/openlibrary/index.sqlite` serves books. Decision record: audnexus is only a lazy per-ASIN cache over Audible's own API, so it was not self-hosted.

## Why people, not categories or years
Audible's unauthenticated `api.audible.<tld>/1.0/catalog/products` has no date filter (unknown parameters are silently ignored), returns at most about 500 unique products per sorted query, and its `total_results` is not reliable (category totals summed to millions). Filters that do work: `author`, `narrator`, `publisher`, `title`, `keywords`, `category_id`, and sorts `ReleaseDate`, `-ReleaseDate`, `Title`, `-Title`. Listing every person's books is the route to near-exhaustive coverage.

## Pipeline
1. `scripts/crawl_audiobookdb_people.py` clones AudiobookDB's `/api/people` list (cursor paged, 20 per page, id/name/roles/credit count) into `data/audiobookdb/people.sqlite`. It runs at 4 requests a second, stops at a daily budget reserve (default 1,000 of about 20,000) and sleeps, resuming from a saved cursor. User unit `listory-adb-people`. AudiobookDB's terms restrict bulk harvesting and derived indexes; this was a deliberate decision by the owner for private use, and carries a risk of key revocation.
2. `scripts/fetch_audible_people.py` queries Audible with `author=` / `narrator=` for each person (highest credit count first), keeps products whose credited name matches exactly, and stores them in `data/audible/audible.sqlite` (`product`, `credit`, `query`; keyed by ASIN and region). People above the 500-result cap are re-listed in other sort orders; `query.capped` marks any still full. User unit `listory-audible-people` (`--follow` waits for the crawl to add people).
3. Detail (description, chapters) is to be fetched on demand by ASIN.

Check progress: `journalctl --user -u listory-adb-people -u listory-audible-people`.

## Storage and matching design (proposal)

### Measured (160k products, 400 sampled)
Raw JSON averages 4.2 KB (zlib: 1.5 KB). About half is noise for our purposes: `social_media_images` (1.5 KB), `available_codecs` (0.7 KB) and the rating histogram (0.8 KB); the identification-relevant fields (asin, title, subtitle, authors/narrators with ASINs, series, runtime, language, format, dates, publisher) are under 0.4 KB. Projected at ~500k products: raw 2 GB, zlib 0.7 GB, relational index ~150 MB.

### Layout: archive + derived index (as with Open Library)
1. **Archive** `data/audible/audible.sqlite` (what the fetcher writes today): `product.raw` is the complete response and nothing is dropped. Move it to a separate `product_raw(asin, region, fetched, body BLOB zlib)` table, keeping the fields below as columns, so the hot tables stay small. Append/upsert only; a re-fetch overwrites the row and keeps `fetched`. It can always rebuild the index.
2. **Index** `data/audible/index.sqlite`, built from the archive into `index.sqlite.building` and atomically renamed, with a status JSON (snapshot time, row counts). Queries never touch the archive. Tables: `book(asin, region, title, subtitle, series_id, series_pos, runtime_min, lang, abridged, release, publisher, isbn, work_id)`, `person(person_asin|name_key)`, `credit(asin, role, person)`, `series(series_asin, title)`, FTS5 over title+subtitle+series, and exact-lookup indexes on `title_key`, `(author_key,title_key)`, `credit(person)`.
3. **Normalised keys** use the one shared normaliser (accent fold, `&`/and, articles, abridged words, volume numbers) rather than another private one; the audit lists seven. Store both the key and the original text.

### Fetch more per call now (same request count)
Add response groups `product_details` (adds ISBN, copyright, platinum keywords), `product_extended_attrs` (publisher summary, editorial reviews), `category_ladders` (genres) and `relationships` (links between editions/parts). These cost roughly 1-2 KB more per product and no extra calls, but retrofitting means re-querying everything, so decide before the bulk run gets far. `customer_rights`/`product_state` (delisted) is useful for filtering stale entries.

### Work versus recording
Audible lists many recordings of one work (abridged, dramatised, other narrators, re-releases; 2,729 title+author pairs already repeat). Keep the ASIN as the recording key and derive `work_id` by grouping on (author person ASIN, base title key) with the series position as tie-breaker; never merge across different narrators/runtimes at the ASIN level. This matches the app's existing split between work identity (Open Library) and recording identity (ASIN, runtime, narrator, language, abridged).

### Using it for matching
- **Exact:** an ASIN found in a folder name, tag or Audible sidecar is a direct `book` lookup.
- **Candidates for a folder (title/author unknown ASIN):** (a) exact `(author_key,title_key)`; (b) FTS5 on title with an author-credit join, using author person ASINs where the Audible sidecar provides them; (c) series title + position.
- **Recording features:** runtime within 2% of the audio total, narrator-set overlap, language, abridged flag, release year. These are the features Open Library cannot supply, and they make recording selection mostly deterministic when a candidate exists.
- **Provenance:** results are one source family, `audible` (shared with Audnexus and the sidecar), and so never count as independent corroboration of the work; Open Library/Hardcover stay the independent family. Return the snapshot id with candidates so saved evidence is reproducible.
- **Cross-check:** Audible ISBN, where present, seeds the ISBN-led Open Library hop and gives an independent audio-to-print link.
- **Freshness:** weekly incremental by `-ReleaseDate` per author already seen, monthly full refresh of rows older than 90 days, delisted detection via `product_state`.
