// Typed client for the FastAPI server (src/app/server/app.py). Same-origin.
export interface Health { ok: boolean; version: string }
export interface Settings { data_dir: string; library_dir: string; backup_dir: string }
export interface SourceFilesInfo { grouping?: string[]; folder: string; files: string[]; count: number | null; available: boolean }
export type Status = 'unlistened' | 'listening' | 'finished'
export const STATUSES: Status[] = ['unlistened', 'listening', 'finished']
export type Review = 'new' | 'suggested' | 'confirmed' | 'ignored'
export const REVIEWS: Review[] = ['new', 'suggested', 'confirmed', 'ignored']
export type Kind = 'book' | 'radio'
export type LibraryStage = 'processing' | 'metadata' | 'audiobook' | 'quality' | 'proposed' | 'finished'
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
  if (r.status === 202 && typeof d.job_id === 'string') {
    for (;;) {
      const job = await req<{ state: string; result: T; error: string }>(`/api/jobs/${encodeURIComponent(d.job_id)}`)
      if (job.state === 'done') return job.result
      if (job.state === 'failed') throw new Error(job.error || 'Job failed')
      await new Promise(resolve => setTimeout(resolve, 750))
    }
  }
  return d as T
}
const send = (method: string, body: unknown): RequestInit => ({ method, headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(body) })

export const api = {
  health: () => req<Health>('/api/health'),
  settings: () => req<Settings>('/api/settings'),
  saveSettings: (s: Partial<Settings>) => req<Settings>('/api/settings', send('PUT', s)),
  books: (f: Filters = {}) => req<Book[]>(booksUrl(f).slice(BASE.length)),
  libraryCovers: () => req<{ ids: number[] }>('/api/library/covers'),
  libraryWorkflow: () => req<{ books: Record<string, LibraryStage[]> }>('/api/library/workflow'),
  addBook: (b: NewBook) => req<Book>('/api/books', send('POST', b)),
  setStatus: (id: number, status: Status) => req<Book>(`/api/books/${id}`, send('PATCH', { status })),
  removeBook: (id: number) => req<Book>(`/api/books/${id}`, { method: 'DELETE' }),
  confirm: (id: number) => req<Book>(`/api/books/${id}/confirm`, { method: 'POST' }),
  ignore: (id: number) => req<Book>(`/api/books/${id}/ignore`, { method: 'POST' }),
  details: (id: number) => req<BookDetails>(`/api/books/${id}/details`),
  source: (id: number) => req<SourceFilesInfo>(`/api/books/${id}/source`),
  enrichBook: (id: number) => req<{ found: boolean; extras: Extras }>(`/api/books/${id}/enrich`, { method: 'POST' }),
  enrich: (limit = 25) => req<{ tried: number; enriched: number; not_found: number; remaining: number }>(`/api/enrich?limit=${limit}`, { method: 'POST' }),
  queue: () => req<QueueView>('/api/process'),
  retryJob: (id: string) => req<unknown>(`/api/jobs/${id}/retry`, send('POST', {})),
  operations: (completed = false) => req<OperationsView>(`/api/operations?completed=${completed}`),
  finished: () => req<{ book_id: number; title: string; author: string; result: Processed }[]>('/api/finished'),
  output: (id: number) => req<Output>(`/api/books/${id}/output`),
  propose: (limit = 5) => req<Record<string, number>>(`/api/process/propose?limit=${limit}`, { method: 'POST' }),
  approve: (ids: number[]) => req<{ changed: number[] }>('/api/process/approve', send('POST', { ids })),
  reject: (ids: number[]) => req<{ changed: number[] }>('/api/process/reject', send('POST', { ids })),
  repropose: (ids: number[]) => req<{ changed: number[] }>('/api/process/repropose', send('POST', { ids })),
  runApproved: () => req<Record<string, number>>('/api/process/run', { method: 'POST' }),
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
export interface Output { book: Record<string, unknown>; metadata: unknown; chapters: { title: string; duration: number; file: string }[] }
export const audioUrl = (id: number, chapter: number) => withBase(`/api/books/${id}/audio/${chapter}`)
export interface BookDetails { processed: Processed | null; extras: Extras; book: Book; path_exists: boolean; share_down?: boolean; files: { count: number; total_bytes: number; truncated: boolean; list: { name: string; bytes: number }[] } | null; probe: ProbeInfo | null; audible: AudibleInfo | null }
export const coverUrl = (id: number) => withBase(`/api/books/${id}/cover`)

export type PlanState = 'planning' | 'proposed' | 'approved' | 'rejected' | 'running' | 'done' | 'failed'
export interface MetaRow { field: string; value: string; source: string; note: string }
export interface PlanSummary { book_metadata?: MetaRow[]; audiobook_metadata?: MetaRow[]; enhancement?: { method: string } | null; metadata: MetaRow[]; year: number | null; hours: number; files: number; chapters: number; chapter_source: string; tiers: Record<string, number>; in_bytes: number; est_bytes: number; out_dir: string; loudness_before: number; loudness_after_est: number; decision: string; reencoded: boolean; warnings: string[]; advisories?: string[]; downmix: string; chapter_list: { file: string; title: string; minutes: number; mode: string; tier: string }[] }
export interface QueueRow { book_id: number; title: string; author: string; state: PlanState; error: string; updated: string; summary: PlanSummary | null }
export interface QueueView { rows: QueueRow[]; worker: { busy: string | null; queued: number; last: Record<string, object>; counts: Record<string, number> }; result: Record<string, { out_bytes?: number; in_bytes?: number; loudness_after?: number; true_peak_after?: number; seconds?: number } | null> }
export interface Operation { job_id?: string; current_step?: string; step_elapsed?: number; logs?: { created: string; message: string; status: string }[]; id: string; book_id: number | null; run_id: string | null; title: string; stage: string; state: 'running' | 'queued' | 'failed' | 'waiting' | 'completed'; phase: 'metadata' | 'audiobook' | 'quality' | 'processing' | null; error: string }
export interface OperationsView { rows: Operation[]; busy: boolean }

export interface IngestCheck { metric: string; value: number | null; threshold: { min?: number; max?: number }; passed: boolean; reason: string }
export interface IngestPolicy { revision?: number; auto_approve_metadata: boolean; auto_approve_audiobook: boolean; auto_approve_quality?: boolean; auto_approve: boolean; auto_process: boolean; providers: string[]; allowed_kinds: string[]; gates: Record<string, { min?: number; max?: number }> }
export interface IngestEvaluation { eligible: boolean; kind_allowed: boolean; checks: IngestCheck[]; policy: IngestPolicy }
export interface IngestStage { stage: string; status: string; elapsed: number; error: string; metrics: Record<string, number | null> }
export interface StageReview { approved: boolean; eligible: boolean; complete: boolean; checks: IngestCheck[]; kind_allowed: boolean; groups?: { work: ScopedReview; performance: ScopedReview; source: ScopedReview } }
export type ScopedReview = Pick<StageReview, 'eligible' | 'complete' | 'checks' | 'kind_allowed'>
export interface MetadataEvidence { records: Record<string, Record<string, unknown>>; rows: MetaRow[] }
export interface WorkEvidence extends MetadataEvidence { score: number | null; families: number; lookups?: { source: string; status: string; reason: string }[] }
export interface RecordingChoice { key: string; candidate: { title: string; narrators: string[]; runtime_minutes: number; language: string; abridged: boolean | null; source: string }; sources: string[]; runtime_error: number }
export interface PerformanceEvidence extends MetadataEvidence { choices?: RecordingChoice[]; selected_choice?: string | null; matched: boolean; local: { title: string; narrators: string[]; language: string; abridged: boolean | null; duration_seconds: number; dates: string[] }; candidate: { source: string; title: string; narrators: string[]; publisher: string; release_date: string; runtime_minutes: number | null; language: string; abridged: boolean | null } | null }
export interface IngestMetadata { work?: WorkEvidence; performance?: PerformanceEvidence; rows: MetaRow[]; recording: { asin?: string; language?: string; runtime_minutes?: number; abridged?: boolean | null }; source_families: number | null; match_score: number | null }
export interface IngestRun { processing_proposed?: boolean; review_phase: 'metadata' | 'audiobook' | 'quality' | 'processing'; reviews: { metadata: StageReview; audiobook: StageReview; quality: StageReview }; metadata?: IngestMetadata; superseded?: boolean; quality_rating?: 'unrated' | 'good' | 'medium' | 'poor'; quality_suggestions?: string[]; selection?: { files: string[]; reason: string; provenance?: { file_count: number; codecs: string[]; extensions: string[]; bitrates_kbps: number[]; sample_rates_hz: number[]; medium_clues: { source: string; value: string }[]; medium_clues_truncated: boolean; recording_release_dates: { source: string; release_date: string }[]; embedded_dates: string[] }; loudness_range?: number | null; continuous_sound?: { flagged: boolean; valid_samples: number; sustained_samples: number; threshold_db: number; method: string }; acoustics?: { medians: { quiet_rms_dbfs: number | null; quiet_high_dbfs: number | null; quiet_to_active_db: number | null; active_low_to_presence_db: number | null }; samples: { start_seconds: number; duration_seconds: number; result: object | null }[] } | null; quiet_passages?: { median_dbfs: number | null; samples: { start_seconds: number; duration_seconds: number; rms_dbfs: number | null; status: string }[] } | null; ranking: { id: string; reasons: { coverage: number; true_peak: number | null; band4k_relative_db: number | null } }[] } | null; id: string; book_id: number; title: string; state: string; created: string; stage: string | null; metrics: Record<string, number | null>; stages: IngestStage[]; eligibility: IngestEvaluation | null; current_evaluation: IngestEvaluation; members: number[] }
export type ReviewSort = 'strongest' | 'metadata' | 'newest' | 'oldest' | 'title'
export interface IngestDashboard { total?: number; offset?: number; runs: IngestRun[]; policy: IngestPolicy; stages: string[]; worker: { busy: string | null; queued: number; last: Record<string, unknown> } }
export interface ListeningSample { index: number; start_seconds: number; duration_seconds: number; url: string; status: 'generating' | 'ready' | 'failed' }
export const ingestApi = {
  prepareSamples: (id: string) => req<{ samples: ListeningSample[] }>(`/api/ingest/runs/${encodeURIComponent(id)}/samples`, { method: 'POST' }),
  samples: (id: string) => req<{ samples: ListeningSample[] }>(`/api/ingest/runs/${encodeURIComponent(id)}/samples`),
  dashboard: (offset = 0, phase?: 'metadata' | 'audiobook' | 'quality', sort?: ReviewSort) => req<IngestDashboard>('/api/ingest' + (offset || phase || sort ? `?offset=${offset}${phase ? `&phase=${phase}` : ''}${sort ? `&sort=${sort}` : ''}` : '')),
  start: (ids: number[], online: boolean) => req('/api/ingest/run', send('POST', { ids, online, limit: ids.length })),
  resume: (id: string) => req(`/api/ingest/runs/${encodeURIComponent(id)}/resume`, { method: 'POST' }),
  chooseRecording: (id: string, key: string) => req(`/api/ingest/runs/${encodeURIComponent(id)}/recording`, { method: 'PUT', body: JSON.stringify({ key }) }),
  approveStage: (id: string, phase: 'metadata' | 'audiobook' | 'quality') => req(`/api/ingest/runs/${encodeURIComponent(id)}/approve/${phase}`, { method: 'POST' }),
  evaluate: (id: string) => req<IngestEvaluation>(`/api/ingest/runs/${encodeURIComponent(id)}/evaluate`, { method: 'POST' }),
  rateQuality: (id: string, rating: string) => req(`/api/ingest/runs/${encodeURIComponent(id)}/quality`, send('PUT', { rating })),
  enqueue: (id: string) => req(`/api/ingest/runs/${encodeURIComponent(id)}/enqueue`, { method: 'POST' }),
  savePolicy: (policy: IngestPolicy) => req<IngestPolicy>('/api/ingest/policy', send('PUT', policy)),
  details: (id: string) => req<object>(`/api/ingest/runs/${encodeURIComponent(id)}`),
}
