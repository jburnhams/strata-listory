// Links between pages (the app uses hash routing so it works behind a reverse proxy at any path) and the small loader the older pages still use.
// New screens use strata-react's `useLoad` (TanStack Query) instead; this one stays until the Pipeline pages move over.
import { useEffect, useState } from 'react'

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

/** Run an async loader when `deps` change; gives {data, error, reload}. A newer call wins over an older one still in flight. */
export function useLoad<T>(load: () => Promise<T>, deps: unknown[]) {
  const [loading, setLoading] = useState(true)
  const [data, setData] = useState<T | null>(null), [error, setError] = useState<string | null>(null), [n, setN] = useState(0)
  useEffect(() => {
    let live = true; setError(null); setLoading(true)
    load().then(d => { if (live) { setData(d); setLoading(false) } }, e => { if (live) { setError(String(e.message || e)); setLoading(false) } })
    return () => { live = false }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [...deps, n])
  return { data, error, loading, reload: () => setN(x => x + 1) }
}
