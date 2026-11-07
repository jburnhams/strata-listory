# Local Audible index built from people

Goal: a local, private catalogue of Audible audiobooks (ASIN, title, authors, narrators, series, runtime, language, release date) for identification, in the way `data/openlibrary/index.sqlite` serves books. Decision record: audnexus is only a lazy per-ASIN cache over Audible's own API, so it was not self-hosted.

## Why people, not categories or years
Audible's unauthenticated `api.audible.<tld>/1.0/catalog/products` has no date filter (unknown parameters are silently ignored), returns at most about 500 unique products per sorted query, and its `total_results` is not reliable (category totals summed to millions). Filters that do work: `author`, `narrator`, `publisher`, `title`, `keywords`, `category_id`, and sorts `ReleaseDate`, `-ReleaseDate`, `Title`, `-Title`. Listing every person's books is the route to near-exhaustive coverage.

## Pipeline
1. `scripts/crawl_audiobookdb_people.py` clones AudiobookDB's `/api/people` list (cursor paged, 20 per page, id/name/roles/credit count) into `data/audiobookdb/people.sqlite`. It runs at 4 requests a second, stops at a daily budget reserve (default 1,000 of about 20,000) and sleeps, resuming from a saved cursor. User unit `listory-adb-people`. AudiobookDB's terms restrict bulk harvesting and derived indexes; this was a deliberate decision by the owner for private use, and carries a risk of key revocation.
2. `scripts/fetch_audible_people.py` queries Audible with `author=` / `narrator=` for each person (highest credit count first), keeps products whose credited name matches exactly, and stores them in `data/audible/audible.sqlite` (`product`, `credit`, `query`; keyed by ASIN and region). People above the 500-result cap are re-listed in other sort orders; `query.capped` marks any still full. User unit `listory-audible-people` (`--follow` waits for the crawl to add people).
3. Detail (description, chapters) is to be fetched on demand by ASIN.

Check progress: `journalctl --user -u listory-adb-people -u listory-audible-people`.
