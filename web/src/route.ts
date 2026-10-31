// Hash routing: #/ (library), #/book/12 (one book), #/process (approval queue), #/settings (settings).
import { useEffect, useState } from 'react'

export type Route =
  | { page: 'library' }
  | { page: 'book'; id: number }
  | { page: 'process' }
  | { page: 'ingest' }
  | { page: 'settings' }

export function parse(hash: string): Route {
  const parts = hash.replace(/^#\/?/, '').split('/').filter(Boolean).map(decodeURIComponent)
  if (parts[0] === 'settings') return { page: 'settings' }
  if (parts[0] === 'ingest') return { page: 'ingest' }
  if (parts[0] === 'process') return { page: 'process' }
  if (parts[0] === 'book' && /^\d+$/.test(parts[1] || '')) return { page: 'book', id: Number(parts[1]) }
  return { page: 'library' }
}

export const href = {
  library: () => '#/',
  book: (id: number) => `#/book/${id}`,
  process: () => '#/process',
  ingest: () => '#/ingest',
  settings: () => '#/settings',
}

export function useRoute(): Route {
  const [r, setR] = useState(() => parse(window.location.hash))
  useEffect(() => { const f = () => setR(parse(window.location.hash)); window.addEventListener('hashchange', f); return () => window.removeEventListener('hashchange', f) }, [])
  return r
}

/** Run an async loader when `deps` change; gives {data, error, reload}. A newer call wins over an older one still in flight. */
export function useLoad<T>(load: () => Promise<T>, deps: unknown[]) {
  const [data, setData] = useState<T | null>(null), [error, setError] = useState<string | null>(null), [n, setN] = useState(0)
  useEffect(() => {
    let live = true; setError(null)
    load().then(d => { if (live) setData(d) }, e => { if (live) setError(String(e.message || e)) })
    return () => { live = false }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [...deps, n])
  return { data, error, reload: () => setN(x => x + 1) }
}
