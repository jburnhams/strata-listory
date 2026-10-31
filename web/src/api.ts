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
export interface Book { id: number; title: string; author: string; narrator: string; series: string; series_position: number | null; duration_seconds: number | null; path: string; status: Status; added: string; asin: string; kind: Kind; review: Review; file_count: number | null; suggestion: Suggestion | null; match_score: number | null; year: number | null; year_source: string }
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
  queue: () => req<QueueView>('/api/process'),
  propose: (limit = 5) => req<QueueView>(`/api/process/propose?limit=${limit}`, { method: 'POST' }),
  approve: (ids: number[]) => req<{ changed: number[] }>('/api/process/approve', send('POST', { ids })),
  reject: (ids: number[]) => req<{ changed: number[] }>('/api/process/reject', send('POST', { ids })),
  repropose: (ids: number[]) => req<{ changed: number[] }>('/api/process/repropose', send('POST', { ids })),
  runApproved: () => req<QueueView>('/api/process/run', { method: 'POST' }),
  scan: () => req<ScanResult>('/api/scan', send('POST', {})),
  match: (limit = 25) => req<MatchResult>(`/api/match?limit=${limit}`, { method: 'POST' }),
}

export interface ProbeInfo { available: boolean; files_probed?: number; complete?: boolean; duration_seconds?: number; tags?: Record<string, string>; codec?: string; bitrate_kbps?: number | null; chapters?: number; has_cover_art?: boolean }
export interface AudibleInfo { asin: string; title: string; subtitle: string; authors: string[]; narrators: string[]; series: { title: string; sequence: string }[]; publisher: string; release_date: string; language: string; format: string; runtime_minutes: number | null; summary: string; rating: string | null; rating_count: number | null; categories: string[]; sample_url: string; chapters: { title: string; start_sec: number; length_sec: number }[]; cover_url: string }
export interface HardcoverReview { user: string; rating: number | null; text: string; spoilers: boolean; likes: number; date: string }
export interface HardcoverInfo { not_found?: boolean; id?: string; url?: string; description?: string; pages?: number | null; rating?: number | null; ratings_count?: number | null; reviews_count?: number | null; readers?: number | null; finished?: number | null; genres?: string[]; moods?: string[]; tags?: string[]; content_warnings?: string[]; series?: { name: string; position: number | null }[]; audiobook_editions?: { asin: string; hours: number | null; publisher: string; released: string; format: string }[]; reviews?: HardcoverReview[] }
export interface OpenLibraryInfo { not_found?: boolean; id?: string; url?: string; title?: string; first_publish_year?: number | null; rating?: number | null; ratings_count?: number | null; subjects?: string[]; edition_count?: number | null }
export type Extras = { hardcover?: { fetched: string; data: HardcoverInfo }; openlibrary?: { fetched: string; data: OpenLibraryInfo } } & Record<string, { fetched: string; data: object } | undefined>
export interface Processed { status: string; error?: string; out_dir?: string; chapters?: number; chapter_source?: string; tiers?: Record<string, number>; in_bytes?: number; out_bytes?: number; loudness_before?: number; loudness_after?: number; true_peak_after?: number; retries?: number; warnings?: string[]; finished?: string }
export interface BookDetails { processed: Processed | null; extras: Extras; book: Book; path_exists: boolean; share_down?: boolean; files: { count: number; total_bytes: number; truncated: boolean; list: { name: string; bytes: number }[] } | null; probe: ProbeInfo | null; audible: AudibleInfo | null }
export const coverUrl = (id: number) => withBase(`/api/books/${id}/cover`)

export type PlanState = 'planning' | 'proposed' | 'approved' | 'rejected' | 'running' | 'done' | 'failed'
export interface MetaRow { field: string; value: string; source: string; note: string }
export interface PlanSummary { metadata: MetaRow[]; year: number | null; hours: number; files: number; chapters: number; chapter_source: string; tiers: Record<string, number>; in_bytes: number; est_bytes: number; out_dir: string; loudness_before: number; loudness_after_est: number; decision: string; reencoded: boolean; warnings: string[]; downmix: string; chapter_list: { file: string; title: string; minutes: number; mode: string; tier: string }[] }
export interface QueueRow { book_id: number; title: string; author: string; state: PlanState; error: string; updated: string; summary: PlanSummary | null }
export interface QueueView { rows: QueueRow[]; worker: { busy: string | null; queued: number; last: Record<string, object>; counts: Record<string, number> }; result: Record<string, { out_bytes?: number; in_bytes?: number; loudness_after?: number; true_peak_after?: number; seconds?: number } | null> }

export interface IngestCheck { metric: string; value: number | null; threshold: { min?: number; max?: number }; passed: boolean; reason: string }
export interface IngestPolicy { revision?: number; auto_approve: boolean; auto_process: boolean; providers: string[]; allowed_kinds: string[]; gates: Record<string, { min?: number; max?: number }> }
export interface IngestEvaluation { eligible: boolean; kind_allowed: boolean; checks: IngestCheck[]; policy: IngestPolicy }
export interface IngestStage { stage: string; status: string; elapsed: number; error: string; metrics: Record<string, number | null> }
export interface IngestRun { selection?: { files: string[]; reason: string; ranking: { id: string; reasons: { coverage: number; true_peak: number | null; band4k_relative_db: number | null } }[] } | null; id: string; book_id: number; title: string; state: string; created: string; stage: string | null; metrics: Record<string, number | null>; stages: IngestStage[]; eligibility: IngestEvaluation | null; current_evaluation: IngestEvaluation; members: number[] }
export interface IngestDashboard { runs: IngestRun[]; policy: IngestPolicy; stages: string[]; worker: { busy: string | null; queued: number; last: Record<string, unknown> } }
export const ingestApi = {
  dashboard: () => req<IngestDashboard>('/api/ingest'),
  start: (ids: number[] | null, online: boolean) => req('/api/ingest/run', send('POST', { ids, online, limit: 5 })),
  resume: (id: string) => req(`/api/ingest/runs/${encodeURIComponent(id)}/resume`, { method: 'POST' }),
  evaluate: (id: string) => req<IngestEvaluation>(`/api/ingest/runs/${encodeURIComponent(id)}/evaluate`, { method: 'POST' }),
  enqueue: (id: string) => req(`/api/ingest/runs/${encodeURIComponent(id)}/enqueue`, { method: 'POST' }),
  savePolicy: (policy: IngestPolicy) => req<IngestPolicy>('/api/ingest/policy', send('PUT', policy)),
  details: (id: string) => req<object>(`/api/ingest/runs/${encodeURIComponent(id)}`),
}
