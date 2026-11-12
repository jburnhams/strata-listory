// Typed client for the FastAPI server (src/app/server/app.py). Same-origin.
export interface Health { ok: boolean; version: string }
export interface Settings { data_dir: string; library_dir: string; library_dirs: string[]; backup_dir: string }
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

async function req<T>(url: string, init?: RequestInit, waitForJob = true): Promise<T> {
  const r = await fetch(withBase(url), init)
  const d = await r.json().catch(() => ({}))
  if (!r.ok) throw new Error((d as { detail?: string }).detail || r.statusText)
  if (waitForJob && r.status === 202 && typeof d.job_id === 'string') {
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
  saveSettings: (s: Partial<Omit<Settings, 'library_dirs'>> & { library_dirs?: string[] }) => req<Settings>('/api/settings', send('PUT', s)),
  books: (f: Filters = {}) => req<Book[]>(booksUrl(f).slice(BASE.length)),
  libraryWorkflow: () => req<{ books: Record<string, LibraryStage[]> }>('/api/library/workflow'),
  addBook: (b: NewBook) => req<Book>('/api/books', send('POST', b)),
  setStatus: (id: number, status: Status) => req<Book>(`/api/books/${id}`, send('PATCH', { status })),
  removeBook: (id: number) => req<Book>(`/api/books/${id}`, { method: 'DELETE' }),
  confirm: (id: number) => req<Book>(`/api/books/${id}/confirm`, { method: 'POST' }),
  ignore: (id: number) => req<Book>(`/api/books/${id}/ignore`, { method: 'POST' }),
  details: (id: number) => req<BookDetails>(`/api/books/${id}/details`),
  source: (id: number) => req<SourceFilesInfo>(`/api/books/${id}/source`),
  enrichBook: (id: number) => req<{ found: boolean; extras: Extras }>(`/api/books/${id}/enrich`, { method: 'POST' }),
  queue: () => req<QueueView>('/api/process'),
  retryJob: (id: string) => req<unknown>(`/api/jobs/${id}/retry`, send('POST', {})),
  operations: (completed = false) => req<OperationsView>(`/api/operations?completed=${completed}`),
  finished: () => req<{ book_id: number; title: string; author: string; result: Processed }[]>('/api/finished'),
  output: (id: number) => req<Output>(`/api/books/${id}/output`),
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
  savePolicy: (policy: IngestPolicy) => req<IngestPolicy>('/api/ingest/policy', send('PUT', policy)),
  details: (id: string) => req<object>(`/api/ingest/runs/${encodeURIComponent(id)}`),
}

// Filegroups (src/app/store/api.py): the folder tree of the library as grouped into the listory Postgres schema.
export interface FileGroup { id: number; kind: 'root' | 'folder' | 'disc' | 'book' | 'series' | 'programme' | 'collection' | 'loose'; label: string; path: string | null; file_count: number; total_bytes: number; group_count: number }
export interface FileGroupDetail { group: FileGroup; ancestors: { id: number; label: string; kind: string }[]; children: (FileGroup & { ordinal: number })[]; files: { id: number; ordinal: number; name: string; path: string | null; size: number; duration_s: number | null; codec: string | null; tags: Record<string, string>; probe_error: string | null }[] }
export interface FileInfo { id: number; sha256: string; size: number; duration_s: number | null; codec: string | null; channels: number | null; sample_rate: number | null; bit_rate: number | null; probe: object | null; probe_error: string | null; locations: { path: string; gone: boolean }[] }
export interface FileSearch { groups: FileGroup[]; files: { id: number; name: string; path: string; group_id: number; group_label: string }[]; more: { groups: number; files: number } }
export const fileGroupApi = {
  search: (q: string) => req<FileSearch>(`/api/filegroups/search?${new URLSearchParams({ q })}`),
  root: () => req<{ root: FileGroup | null; groupings: number }>('/api/filegroups'),
  group: (id: number) => req<FileGroupDetail>(`/api/filegroups/${id}`),
  file: (id: number) => req<FileInfo>(`/api/filegroups/files/${id}`),
}

// Listens (src/app/store/api.py): the things you would listen to in one go, made from the book and programme groups.
/** How a listen was classified (src/app/store/kinds.py): the kind, what it would be if not a book, the band (broadcast, book or review), P(broadcast) and the signals behind it. */
export interface KindSignal { name: string; weight: number; detail: string; prior?: number; learned?: boolean }
export interface KindInfoData { kind: string; guess: string; band: 'broadcast' | 'book' | 'review'; confidence: number; p_broadcast: number; decided_by: 'rule' | 'user'; signals: KindSignal[] }
export interface ListenRow { id: number; label: string; kind: 'book' | 'radio_series' | 'radio_show' | 'play' | 'other'; kind_info: KindInfoData | null; status: string; files: number; bytes: number; minutes: number; group_id: number | null; path: string | null; copies: number; parts: number; author: string | null; narrator: string | null; year: number | null }
export interface ListenList { total: number; kinds: Record<string, number>; review: number; not_book: number; rows: ListenRow[] }
export const listenApi = {
  list: (q = '', kind = '', status = '', match = '', limit = 500) => req<ListenList>(`/api/listens?${new URLSearchParams({ q, kind, status, match, limit: String(limit) })}`),
  setKind: (id: number, kind: string) => req<{ ok: boolean; retraining: boolean; reidentified?: boolean; kind_info: KindInfoData | null }>(`/api/listens/${id}/kind`, send('POST', { kind })),
}

// Covers (src/app/covers/api.py): listens a cover search has run on, the distinct pictures found (with every source that offered each) or why none, and the preferred one.
export interface CoverSource { source: string; ref: string; label: string; url: string | null; image_id: number }
export interface CoverPicture { image_id: number; width: number; height: number; bytes: number; sources: CoverSource[]; members: number[] }
export interface CoverStatus { source: string; status: 'found' | 'none' | 'error' | 'limited' | 'skipped' | 'not_found'; detail: string; count: number }
export interface CoverRow { listen_id: number; label: string; run_id: number; searched: string | null; state: 'choose' | 'chosen' | 'none'; chosen: number | null; covers: CoverPicture[]; sources: CoverStatus[] }
export interface CoverJob { running: boolean; done: number; total: number; current: string; finished: number | null; error: string }
export interface CoverList { total: number; counts: Record<'choose' | 'chosen' | 'none', number>; waiting: number; rows: CoverRow[]; job: CoverJob }
export const coverApi = {
  list: (state = '', q = '', limit = 24) => req<CoverList>(`/api/covers?${new URLSearchParams({ state, q, limit: String(limit) })}`),
  choose: (listenId: number, imageId: number | null) => req<{ ok: boolean; chosen: number | null }>(`/api/covers/${listenId}/choose`, send('POST', { image_id: imageId })),
  run: (listenIds?: number[], force = false) => req<{ started: boolean }>('/api/covers/run', send('POST', { listen_ids: listenIds ?? null, force })),
  image: (id: number, w = 0) => withBase(`/api/covers/image/${id}${w ? `?w=${w}` : ''}`),
}

// The match queue (src/app/linkage/api.py): book listens with the model's candidate works, easiest first; answers become labels for the next fit.
export interface QueueRecording { asin: string; title: string; narrators: string[]; minutes: number | null; fmt: string; year: number | null }
export type QueueEvidence = Partial<Record<'title' | 'author' | 'narrator' | 'runtime' | 'asin' | 'year' | 'numbers' | 'sources' | 'popularity' | 'channel' | 'form', string | null>>
/** A radio programme proposed as the listen's match (BBC Programmes or Wikidata; the BBC's own record when it was fetched). */
export interface QueueProgramme { source: string; ref: string; title: string; service: string; channel: string; first_broadcast: string; type: string; episodes: number | null; genres: string[]; synopsis: string }
export interface QueueCandidate { part_id: number; ordinal: number; state: string; auto: boolean; p: number; work_id: number; key: string; title: string | null; authors: string[]; year: number | null; editions: number | null; ol: string[]; isbns: string[]; recordings: QueueRecording[]; programmes?: QueueProgramme[]; recording: string | null; alias: string | null; reading: string | null; evidence: QueueEvidence; p_none: number | null; second: number | null }
export interface QueueCopy { minutes: number | null; files: number | null; bytes: number | null; folder: string; path: string | null; group_id: number | null; relation?: 'copy' | 'part' }
export interface QueueItem { listen_id: number; p: number; label: string; cursor: string; kind: string | null; status: string | null; added: number | null; kind_info?: KindInfoData | null; candidates: QueueCandidate[]; chosen: Record<string, string | number>; tags: Record<string, string | number>; folder: Record<string, string | number>; copies: QueueCopy[] }
export interface QueueSummary { waiting: number; auto_unconfirmed: number; confirmed: number; not_found: number; statuses: Record<string, number>; decisions: number; since_train: number | null; model: string | null; auto_retrain: number; training: { running: boolean; finished: number | null; error: string; counts: Record<string, number> | null } }
/** The match queue's order and filters, as the page keeps them in its URL; '' means any. */
export interface QueueFilters { sort: 'certain' | 'uncertain' | 'recent' | 'oldest' | 'kind-certain' | 'kind-uncertain'; auto: '' | 'skip' | 'only'; kind: string; status: string; certainty: '' | 'sure' | 'likely' | 'close' | 'unsure'; audible: '' | 'yes' | 'no'; q: string }
export type QueueFacets = Record<'kind' | 'status' | 'certainty' | 'auto' | 'audible', Record<string, number>>
export interface QueuePage { summary: QueueSummary; total: number; facets: QueueFacets; items: QueueItem[] }
export type QueueAction = 'approve' | 'reject' | 'none'
export const matchApi = {
  queue: (after?: string, limit = 20, filters: Partial<QueueFilters> = {}) => req<QueuePage>(`/api/matches?${new URLSearchParams({ limit: String(limit), ...(after ? { after } : {}), ...Object.fromEntries(Object.entries(filters).filter(([, v]) => v)) })}`),
  decide: (listen_id: number, action: QueueAction, part_id?: number) => req<{ ok: boolean; status: string; retraining: boolean }>('/api/matches/decide', send('POST', { listen_id, action, part_id })),
  retrain: () => req<{ started: boolean }>('/api/matches/retrain', { method: 'POST' }),
}
