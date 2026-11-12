# Frontend (strata-react)

> **Retired 2026-11-12.** The code this document describes (the old per-book identification model and review queue, `match_review`, `training_cache`, `evidence-report`) was removed; identification now lives in `src/app/linkage/` and the Matches page ([listen-identification.md](listen-identification.md)). Kept as history.

The web app (`web/`) is built on [strata-react](https://github.com/jburnhams/strata-react): its components, token contract and TanStack Query bridge, with a product theme (`web/src/theme.generated.css` from `palette.mjs` with the seed `#8a4b2a`, a book-cloth brown; `web/src/theme.css` for the status roles). Design rules come from [ux-research](https://github.com/jburnhams/ux-research): one question and one finding per screen, states in the types, one filled button per view, filters and tabs in the URL, reversible actions with Undo.

## The user journey

The identify model (Audible and Open Library together) settles what it is sure of. What is left is a ranked list of doubtful matches. So the app opens on that list and the rest of the app supports it:

| Page | Route | One question |
|---|---|---|
| **Matches** (home) | `#/` | Is this the right book? The identification model's proposals for every listen, easiest first, one key to confirm; see [listen-identification.md](listen-identification.md#match-queue-home-page-). |
| Review (Pipeline → Old review) | `#/pipeline?tab=review` | The SQLite-era queue, kept until that pipeline is retired. |
| Library | `#/library` | Which books do I have, and which should go into the pipeline next? Search, match, listening and workflow filters are in the URL. |
| Book | `#/book/:id` | Everything known about one book, with the player when it is finished. |
| Listens | `#/listens?q=&kind=` | What is there to listen to? The listens made from the book and programme groups (`./app listens-build`), searchable, filtered by kind, each linking to its files. |
| Files | `#/files?group=` | How were my audio files grouped? Browses the folder tree grouped into the listory Postgres schema (`./app filegroups` makes it); groups and files, breadcrumb, size. The search box at the top (`?q=`) finds groups and files whose name or folder path contains every word, and each result jumps to its folder. |
| Pipeline | `#/pipeline?tab=` | The older per-book approval stages (book, audiobook, quality, automatic approvals, match shortlist). |
| Processing | `#/processing?tab=` | What is being turned into finished audiobooks: queue, plans to approve, finished. |
| Settings | `#/settings` | Where does the app read and write? |

### Review

* One endless list over `GET /api/identify/queue`, impact order from the server. Each card shows what the files say, what the model matched them to, how sure it is (a near tie with the runner-up reads as a close call whatever the score) and how many files one answer settles.
* **Accept** records an `approve` label and approves the book in the ingest pipeline as the model's work; **Not this book** records a `reject` (a hard negative) and withdraws an approval if there is one. When the pipeline cannot take the answer (files not grouped into one book yet, the run is mid-stage) it is still saved for training and a note says why. Both fold the card away at once with a five second Undo; the answer is saved when the window closes, on the next answer or on leaving the page, and `flush()` saves it before a retrain. A failed save puts the card back and says so in the bar.
* Built from strata-react's `useInfiniteLoad` / `InfiniteList`, `useUndoable`, `useActiveItem`, `useHotkeys` / `KeysLegend`. Pages are requested `after` the last group held, never by offset, so answers saved meanwhile cannot skip or repeat cards; the list refetches when you come back to the page.
* Keys: `A` accept, `R` not this book, `J`/`K` next or previous card, `U` undo. Ignored while typing in a field.
* **Retrain with my answers** runs `identify-refresh --train` as a job and reloads the list.

### What was kept, merged or replaced

* Replaced: the "Book identification", "Automatic approvals", "Match shortlist" and "Audiobook identification" top-level pages as a way to *decide what a book is*. Review does that for every book in one list. Old addresses (`#/book-identification`, `#/automatic-approvals`, ...) redirect.
* Kept, as tabs under **Pipeline**: those same pages, because they are still what moves a book through the ingest pipeline. Review now approves the book stage, so the Book tab only holds what Review cannot take (ungrouped files); Audiobook, Quality and the two match tabs remain until those stages are driven the same way, after which Pipeline can be merged into Processing.
* Merged: Processing, Proposed and Finished into one Processing page with tabs; Quality into Pipeline.
* Dropped from Library: the folder tree, the cover-only filter and paging (the table virtualises; search matches paths).

### Migration status

Rewritten on Strata components: shell, Review, Library, Settings, Finished, Book (frame). Moved to Strata *tokens* but still on their original markup: Pipeline pages, Processing queue, the Book sections. Moving those onto `DataView`/`DataTable`/`Field` is the next step, with the Pipeline tabs reduced as above.

## Running it

`cd web && npm run dev` (proxies `/api` to `./app serve` on 8360). Tests: `npm run test:integration`; gate: `npm run test:coverage:gate`. `renderWithStrata` (via `tests/utils/render.tsx`) supplies the router and query client; every endpoint the app calls needs an msw handler in `tests/utils/server.ts`, including `/api/identify/*`.

## strata-react 1.0 (2026-11-12)

The app now depends on the published `strata-react@^1.0.20` from npm instead of `github:...#main`. What changed for this app:

* React Aria is the primitives layer, so `DataTable` and `InfiniteList` are a `grid` of `row`s (tests query `getByRole('grid', { name })`, a sortable header is the `columnheader` itself) and `tests/utils/setup.ts` calls `installLayoutShims()`.
* Raw `<details>` became `Disclosure` (the body mounts when opened, so tests open it first); raw `<button>`, `<input>`, `<select>`, `<textarea>` and checkboxes became `Button`, `Input`, `NativeSelect`, `Textarea` and `Checkbox`; the `btn`/`input` class strings are gone.
* Polling is `useJob`: the Pipeline and Processing pages poll every 3 s only while the worker is busy or has work queued, the match queue summary only while the model relearns. A failed refresh keeps the page under a `StaleNotice`. The old `setInterval` loader in `route.ts` is gone (`readOf` bridges a `Load` to the `{ data, error }` the older pages read).
* Audio is `MediaPlayer` (strata-react 1.0.20, from our issue #134): the book player passes the chapters as `sources` with their durations (one seek bar over the book, chapter marks, keeps playing from chapter to chapter, speech speeds, lock-screen metadata) and the quality samples use it for one file (a failed load shows its own Try again). It uses the browser's own bar on touch devices.
* Not adopted: `Workspace`, `Timeline`, `FileIntake` and the rest have no screen here yet.
