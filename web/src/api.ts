// Typed client for the FastAPI server (src/app/server/app.py). Same-origin.
export interface Health { ok: boolean; version: string }
export interface Settings { data_dir: string; library_dir: string; backup_dir: string }
export type Status = 'unlistened' | 'listening' | 'finished'
export const STATUSES: Status[] = ['unlistened', 'listening', 'finished']
export type Review = 'new' | 'suggested' | 'confirmed' | 'ignored'
export const REVIEWS: Review[] = ['new', 'suggested', 'confirmed', 'ignored']
export type Kind = 'book' | 'radio'
export interface Alternative { source: string; title: string; author: string; asin: string; score: number }
export interface Suggestion { id?: string; source?: string; title?: string; author?: string; narrators?: string[]; series?: string; series_position?: number | null; runtime_minutes?: number | null; asin?: string; matched_by?: string; sources?: string[]; alternatives?: Alternative[] }
export interface Book { id: number; title: string; author: string; narrator: string; series: string; series_position: number | null; duration_seconds: number | null; path: string; status: Status; added: string; asin: string; kind: Kind; review: Review; file_count: number | null; suggestion: Suggestion | null; match_score: number | null }
export type NewBook = { title: string; author?: string }
export interface Filters { q?: string; status?: Status; review?: Review; kind?: Kind }
export interface ScanResult { found: number; added: number; existing: number }
export interface MatchResult { tried: number; suggested: number; none: number; remaining: number; providers?: string[] }

// Where the app is served from: '' at the server's own root, '/subpath' behind a reverse proxy at that path. Taken from the page's own URL (index.html has relative asset paths: vite.config.ts base './'), so one build works in both places.
export const BASE = typeof document === 'undefined' ? '' : new URL('.', document.baseURI).pathname.replace(/\/$/, '')      // no page (the unit tests run in node): the root
export const withBase = (u: string) => (u.startsWith('/') ? BASE + u : u)

export function booksUrl(f: Filters) {
  const params = new URLSearchParams()
  if (f.q?.trim()) params.set('q', f.q.trim())
  for (const k of ['status', 'review', 'kind'] as const) if (f[k]) params.set(k, f[k]!)
  const qs = params.toString()
  return withBase('/api/books' + (qs ? '?' + qs : ''))
}

async function req<T>(url: string, init?: RequestInit): Promise<T> {
  const r = await fetch(withBase(url), init)
  const d = await r.json().catch(() => ({}))
  if (!r.ok) throw new Error((d as { detail?: string }).detail || r.statusText)
  return d as T
}
const send = (method: string, body: unknown): RequestInit => ({ method, headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(body) })

export const api = {
  health: () => req<Health>('/api/health'),
  settings: () => req<Settings>('/api/settings'),
  saveSettings: (s: Partial<Settings>) => req<Settings>('/api/settings', send('PUT', s)),
  books: (f: Filters = {}) => req<Book[]>(booksUrl(f).slice(BASE.length)),
  addBook: (b: NewBook) => req<Book>('/api/books', send('POST', b)),
  setStatus: (id: number, status: Status) => req<Book>(`/api/books/${id}`, send('PATCH', { status })),
  removeBook: (id: number) => req<Book>(`/api/books/${id}`, { method: 'DELETE' }),
  confirm: (id: number) => req<Book>(`/api/books/${id}/confirm`, { method: 'POST' }),
  ignore: (id: number) => req<Book>(`/api/books/${id}/ignore`, { method: 'POST' }),
  details: (id: number) => req<BookDetails>(`/api/books/${id}/details`),
  enrichBook: (id: number) => req<{ found: boolean; extras: Extras }>(`/api/books/${id}/enrich`, { method: 'POST' }),
  enrich: (limit = 25) => req<{ tried: number; enriched: number; not_found: number; remaining: number }>(`/api/enrich?limit=${limit}`, { method: 'POST' }),
  scan: () => req<ScanResult>('/api/scan', send('POST', {})),
  match: (limit = 25) => req<MatchResult>(`/api/match?limit=${limit}`, { method: 'POST' }),
}

export interface ProbeInfo { available: boolean; files_probed?: number; complete?: boolean; duration_seconds?: number; tags?: Record<string, string>; codec?: string; bitrate_kbps?: number | null; chapters?: number; has_cover_art?: boolean }
export interface AudibleInfo { asin: string; title: string; subtitle: string; authors: string[]; narrators: string[]; series: { title: string; sequence: string }[]; publisher: string; release_date: string; language: string; format: string; runtime_minutes: number | null; summary: string; rating: string | null; rating_count: number | null; categories: string[]; sample_url: string; chapters: { title: string; start_sec: number; length_sec: number }[]; cover_url: string }
export interface HardcoverReview { user: string; rating: number | null; text: string; spoilers: boolean; likes: number; date: string }
export interface HardcoverInfo { not_found?: boolean; id?: string; url?: string; description?: string; pages?: number | null; rating?: number | null; ratings_count?: number | null; reviews_count?: number | null; readers?: number | null; finished?: number | null; genres?: string[]; moods?: string[]; tags?: string[]; content_warnings?: string[]; series?: { name: string; position: number | null }[]; audiobook_editions?: { asin: string; hours: number | null; publisher: string; released: string; format: string }[]; reviews?: HardcoverReview[] }
export type Extras = Record<string, { fetched: string; data: HardcoverInfo }>
export interface BookDetails { extras: Extras; book: Book; path_exists: boolean; share_down?: boolean; files: { count: number; total_bytes: number; truncated: boolean; list: { name: string; bytes: number }[] } | null; probe: ProbeInfo | null; audible: AudibleInfo | null }
export const coverUrl = (id: number) => withBase(`/api/books/${id}/cover`)
