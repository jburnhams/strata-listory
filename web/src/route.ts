// Hash routing: #/ (library), #/book/12 (one book), #/process (approval queue), #/settings (settings).
import { useEffect, useState } from 'react'

export type Route =
  | { page: 'library' }
  | { page: 'book'; id: number }
  | { page: 'process' }
  | { page: 'proposed' }
  | { page: 'finished' }
  | { page: 'metadata' | 'audiobook' | 'quality' }
  | { page: 'settings' }
  | { page: 'automatic' | 'shortlist' }

export function parse(hash: string): Route {
  const parts = hash.replace(/^#\/?/, '').split('/').filter(Boolean).map(decodeURIComponent)
  if (parts[0] === 'automatic-approvals') return { page: 'automatic' }
  if (parts[0] === 'match-shortlist') return { page: 'shortlist' }
  if (parts[0] === 'settings') return { page: 'settings' }
  if (parts[0] === 'audiobook-identification') return { page: 'audiobook' }
  if (parts[0] === 'ingest' || parts[0] === 'metadata' || parts[0] === 'book-identification') return { page: 'metadata' }
  if (parts[0] === 'quality') return { page: 'quality' }
  if (parts[0] === 'process') return { page: 'process' }
  if (parts[0] === 'proposed') return { page: 'proposed' }
  if (parts[0] === 'finished') return { page: 'finished' }
  if (parts[0] === 'book' && /^\d+$/.test(parts[1] || '')) return { page: 'book', id: Number(parts[1]) }
  return { page: 'library' }
}

export const href = {
  library: () => '#/',
  book: (id: number) => `#/book/${id}`,
  process: () => '#/process',
  proposed: () => '#/proposed',
  finished: () => '#/finished',
  ingest: () => '#/metadata',
  metadata: () => '#/book-identification',
  audiobook: () => '#/audiobook-identification',
  quality: () => '#/quality',
  settings: () => '#/settings',
  automatic: () => '#/automatic-approvals',
  shortlist: () => '#/match-shortlist',
}

export function useRoute(): Route {
  const [r, setR] = useState(() => parse(window.location.hash))
  useEffect(() => { const f = () => setR(parse(window.location.hash)); window.addEventListener('hashchange', f); return () => window.removeEventListener('hashchange', f) }, [])
  return r
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
