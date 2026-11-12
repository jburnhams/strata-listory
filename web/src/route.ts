// Links between pages (the app uses hash routing so it works behind a reverse proxy at any path), and the bridge from strata-react's `Load` to the {data, error, loading} the older pages read.
import type { Load } from 'strata-react'

export const href = {
  library: () => '#/library',
  book: (id: number) => `#/book/${id}`,
  process: () => '#/processing',
  proposed: () => '#/processing?tab=plans',
  finished: () => '#/processing?tab=finished',
  ingest: () => '#/pipeline?tab=book',
  metadata: () => '#/pipeline?tab=book',
  audiobook: () => '#/pipeline?tab=audiobook',
  quality: () => '#/pipeline?tab=quality',
  automatic: () => '#/pipeline?tab=automatic',
  shortlist: () => '#/pipeline?tab=shortlist',
  files: (group?: number) => (group ? `#/files?group=${group}` : '#/files'),
  settings: () => '#/settings',
  review: () => '#/',
}

/** What a page that shows `data`, `error` and a `loading` flag (and no stale notice or skeleton of its own) reads from a strata-react `Load`. */
export function readOf<T>(load: Load<T>) {
  return { data: load.status === 'ready' ? load.data : null, error: load.status === 'error' ? load.message : null, loading: load.status === 'loading' }
}
