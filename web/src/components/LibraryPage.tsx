// The one question: "What will each listen with a chosen cover be called and tagged?" The finding: how many listens have chosen covers and are ready in the library.
// Why it is shaped this way: each listen with a chosen cover shows its metadata table where rows are metadata fields and columns are Original, each Audible recording, Open Library, and the resulting Output (with a dropdown of all distinct choices and write-in auto-save).
import { useEffect, useRef, useState } from 'react'
import { useQueryClient } from '@tanstack/react-query'
import { Link, useSearchParams } from 'react-router'
import { Button, DataView, EmptyState, Field, Input, NativeSelect, PageHeader, Skeleton, Status, format, useLoad } from 'strata-react'
import { coverApi, listenApi, type ConfigColumn, type ConfigField, type LibrarySort, type ListenConfig } from '../api'
import { KIND_LABEL, hours } from './ListensPage'

export function describeCount(n: number) {
  return n === 0 ? 'No books in the catalogue yet.' : `${n} book${n === 1 ? '' : 's'}`
}

export function matchLabel(score: number | null) {
  return score == null ? '' : `${Math.round(score * 100)}% match${score >= 0.85 ? '' : ' (check)'}`
}

export function formatDuration(sec: number | null) {
  if (!sec) return ''
  return `${Math.floor(sec / 3600)}h ${String(Math.floor((sec % 3600) / 60)).padStart(2, '0')}m`
}

const SORTS: [LibrarySort, string][] = [
  ['title', 'Title (A–Z)'],
  ['title-desc', 'Title (Z–A)'],
  ['author', 'Author (A–Z)'],
  ['year-desc', 'Year (newest first)'],
  ['year-asc', 'Year (oldest first)'],
  ['length-desc', 'Length (longest first)'],
  ['length-asc', 'Length (shortest first)'],
  ['recent', 'Recently added'],
]

const MISSING_FILTERS: [string, string][] = [
  ['', 'All listens'],
  ['missing', 'Missing core fields'],
  ['narrator', 'Missing narrator'],
  ['year', 'Missing year'],
  ['edited', 'Edited by you'],
  ['unedited', 'Default choices only'],
]

function WriteIn({ listenLabel, field, onSave }: { listenLabel: string; field: ConfigField; onSave: (value: string) => void }) {
  const [draft, setDraft] = useState('')
  const timer = useRef<ReturnType<typeof setTimeout> | null>(null)
  const flush = (raw: string, clear = false) => {
    if (timer.current) { clearTimeout(timer.current); timer.current = null }
    if (clear) setDraft('')
    const v = raw.trim()
    if (!v) return
    if (String(field.value ?? '') !== v) onSave(v)
  }
  useEffect(() => {
    if (!draft.trim()) return
    timer.current = setTimeout(() => flush(draft), 250)
    return () => { if (timer.current) clearTimeout(timer.current) }
  }, [draft]) // eslint-disable-line react-hooks/exhaustive-deps
  return (
    <Input
      aria-label={`Write in ${field.label.toLowerCase()} for ${listenLabel}`}
      placeholder={`Write in ${field.label.toLowerCase()}…`}
      value={draft}
      onChange={e => setDraft(e.target.value)}
      onBlur={() => flush(draft, true)}
      onKeyDown={e => { if (e.key === 'Enter') { e.preventDefault(); flush(draft, true) } }}
      className="min-w-36 flex-1"
    />
  )
}

function LibraryCard({
  item,
  error,
  onPickField,
  onPickColumn,
}: {
  item: ListenConfig
  error?: string
  onPickField: (listenId: number, field: string, value: string | number | null, source: string) => void
  onPickColumn: (listenId: number, col: ConfigColumn) => void
}) {
  const columns = item.sources ?? []
  const mins = item.minutes ?? item.inputs[0]?.minutes ?? 0
  const files = item.files ?? item.inputs.reduce((n, i) => n + i.files, 0)
  const path = item.path ?? item.inputs[0]?.path
  const groupId = item.group_id ?? item.inputs[0]?.group_id
  return (
    <article aria-label={item.label} className="space-y-4 rounded-surface border border-border bg-card p-4">
      <header className="flex flex-wrap items-start gap-4">
        {item.cover && (
          <Link to={`/covers?state=all&q=${encodeURIComponent(item.label)}`} className="shrink-0" title="Change cover on the Covers page">
            <img src={coverApi.image(item.cover.image_id, 240)} alt={`Cover for ${item.label}`} className="w-24 rounded-control bg-muted object-contain" />
          </Link>
        )}
        <div className="min-w-0 flex-1 space-y-1">
          <div className="flex flex-wrap items-baseline gap-2">
            <h2 className="type-body font-medium"><Link className="underline" to={`/listens/${item.listen_id}`}>{item.label}</Link></h2>
            <Status tone="neutral">{KIND_LABEL[item.kind] ?? item.kind}</Status>
            {item.cover && <span className="type-caption text-muted-foreground">{item.cover.width}×{item.cover.height}</span>}
          </div>
          <p className="type-body-sm text-muted-foreground">
            {[
              item.work ? `Matched to “${item.work.title}”${item.work.authors.length ? ` by ${item.work.authors.join(', ')}` : ''}` : 'Not matched to a work yet',
              files ? format.count(files, 'file') : '',
              mins ? hours(mins) : '',
            ].filter(Boolean).join(' · ')}
          </p>
          {path && (
            <p className="type-caption break-all text-muted-foreground">
              {groupId != null ? <Link className="underline" to={`/files?group=${groupId}`}>{path}</Link> : path}
            </p>
          )}
        </div>
      </header>

      {error && <p role="alert" className="type-body-sm text-danger">{error}</p>}

      <div className="overflow-x-auto">
        <table aria-label={`Metadata for ${item.label}`} className="w-full border-collapse type-body-sm">
          <thead>
            <tr className="border-b border-border text-left">
              <th scope="col" className="py-2 pr-3 font-medium text-muted-foreground">Field</th>
              {columns.map(col => {
                const nonEmpty = Object.values(col.values).filter(v => v != null && v !== '').length
                return (
                  <th key={col.id} scope="col" className={`px-2 py-2 align-top font-normal transition-state ${col.used ? 'bg-muted/70' : ''}`}>
                    <button
                      type="button"
                      aria-pressed={col.used}
                      disabled={nonEmpty === 0}
                      onClick={() => onPickColumn(item.listen_id, col)}
                      className={`flex w-full flex-col items-start gap-0.5 rounded-control border px-2.5 py-1.5 text-left transition-state hover:border-ring disabled:opacity-50 ${col.used ? 'border-ring bg-muted font-medium' : 'border-border bg-card'}`}
                    >
                      <span className="flex flex-wrap items-center gap-1.5">
                        <span>{col.label}</span>
                        {col.used && <Status tone="success">{`${col.used_fields.length} used`}</Status>}
                      </span>
                      {col.kind === 'audible' && col.runtime_minutes ? (
                        <span className="type-caption text-muted-foreground">{hours(col.runtime_minutes)}{col.abridged ? ' · abridged' : ''}</span>
                      ) : col.kind === 'original' && mins ? (
                        <span className="type-caption text-muted-foreground">{hours(mins)}</span>
                      ) : null}
                    </button>
                  </th>
                )
              })}
              <th scope="col" className="py-2 pl-3 font-medium text-muted-foreground">Output</th>
            </tr>
          </thead>
          <tbody>
            {item.fields.map(f => (
              <tr key={f.field} className="border-b border-border/60 align-middle">
                <th scope="row" className="py-2 pr-3 text-left font-medium text-muted-foreground whitespace-nowrap">{f.label}</th>
                {columns.map(col => {
                  const val = col.values[f.field]
                  const cellUsed = col.used_fields.includes(f.field)
                  return (
                    <td key={col.id} className={`px-2 py-1.5 align-middle ${col.used ? 'bg-muted/30' : ''}`}>
                      {val != null && val !== '' ? (
                        <button
                          type="button"
                          aria-pressed={cellUsed}
                          aria-label={`Use ${col.label} for ${f.label}: ${val}`}
                          onClick={() => onPickField(item.listen_id, f.field, val, col.label)}
                          className={`w-full rounded-control border px-2 py-1 text-left transition-state hover:border-ring ${cellUsed ? 'border-ring bg-muted font-medium' : 'border-transparent hover:bg-muted/50'}`}
                        >
                          {String(val)}
                        </button>
                      ) : (
                        <span className="px-2 text-muted-foreground">–</span>
                      )}
                    </td>
                  )
                })}
                <td className="py-1.5 pl-3 min-w-64">
                  <div className="flex flex-wrap items-center gap-1.5">
                    <NativeSelect
                      aria-label={`${f.label} for ${item.label}`}
                      value={f.value == null ? '' : String(f.value)}
                      onChange={e => onPickField(item.listen_id, f.field, e.target.value === '' ? null : e.target.value, 'dropdown')}
                      className="min-w-40 flex-1"
                    >
                      <option value="">Not set</option>
                      {f.value != null && !f.options.some(o => String(o.value) === String(f.value)) && (
                        <option value={String(f.value)}>{String(f.value)}</option>
                      )}
                      {f.options.map(o => <option key={String(o.value)} value={String(o.value)}>{String(o.value)}</option>)}
                    </NativeSelect>
                    <WriteIn listenLabel={item.label} field={f} onSave={v => onPickField(item.listen_id, f.field, v, 'typed')} />
                  </div>
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
    </article>
  )
}

const PAGE_SIZE = 10

export default function LibraryPage() {
  const qc = useQueryClient()
  const [params, setParams] = useSearchParams()
  const q = params.get('q') ?? ''
  const kind = params.get('kind') ?? ''
  const missing = params.get('missing') ?? ''
  const sort = (params.get('sort') ?? 'title') as LibrarySort
  const page = Math.max(1, Number(params.get('page') ?? '1') || 1)
  const offset = (page - 1) * PAGE_SIZE

  const [typed, setTyped] = useState(q)
  useEffect(() => { setTyped(q) }, [q])
  const updateParams = (updates: Record<string, string>) => {
    setParams(p => {
      const n = new URLSearchParams(p)
      for (const [k, v] of Object.entries(updates)) {
        if (v) n.set(k, v); else n.delete(k)
      }
      return n
    }, { replace: true })
  }
  useEffect(() => {
    if (typed.trim() === q.trim()) return
    const t = setTimeout(() => updateParams({ q: typed.trim(), page: '' }), 250)
    return () => clearTimeout(t)
  }, [typed]) // eslint-disable-line react-hooks/exhaustive-deps

  const queryKey = ['library-listens', q, kind, missing, sort, page] as const
  const load = useLoad({ queryKey, queryFn: () => listenApi.library(q, kind, missing, sort, PAGE_SIZE, offset) })
  const [updated, setUpdated] = useState<Record<number, ListenConfig>>({})
  const [errors, setErrors] = useState<Record<number, string>>({})

  const applySaved = (listenId: number, next: ListenConfig) => {
    setUpdated(u => ({ ...u, [listenId]: next }))
    void qc.invalidateQueries({ queryKey: ['listen-config', listenId] })
    void qc.invalidateQueries({ queryKey: ['library-listens'] })
  }

  const onPickField = (listenId: number, field: string, value: string | number | null, source: string) => {
    setErrors(e => { const { [listenId]: _, ...rest } = e; return rest })
    listenApi.chooseField(listenId, field, value, source).then(
      next => applySaved(listenId, next),
      (err: Error) => setErrors(e => ({ ...e, [listenId]: err.message })),
    )
  }

  const onPickColumn = (listenId: number, col: ConfigColumn) => {
    const values = Object.fromEntries(Object.entries(col.values).filter(([, v]) => v != null && v !== ''))
    setErrors(e => { const { [listenId]: _, ...rest } = e; return rest })
    listenApi.chooseFields(listenId, values, col.label).then(
      next => applySaved(listenId, next),
      (err: Error) => setErrors(e => ({ ...e, [listenId]: err.message })),
    )
  }

  const d = load.status === 'ready' ? load.data : null
  const totalPages = d ? Math.max(1, Math.ceil(d.total / d.limit)) : 1
  useEffect(() => {
    if (d && page > totalPages) updateParams({ page: totalPages <= 1 ? '' : String(totalPages) })
  }, [d, page, totalPages]) // eslint-disable-line react-hooks/exhaustive-deps
  const finding = d
    ? [
        `${format.count(d.all_total, 'listen')} with a confirmed match and cover`,
        d.total !== d.all_total ? `${d.total} matching` : '',
        d.without_cover ? `${format.count(d.without_cover, 'confirmed listen')} still need a cover` : '',
      ].filter(Boolean).join(' · ')
    : undefined

  return (
    <div className="space-y-5">
      <PageHeader
        title="Library"
        scope="Listens with a confirmed match and chosen cover, and their resulting metadata"
        finding={finding ? { text: finding } : undefined}
      />

      <div className="flex flex-wrap items-end gap-3" role="search" aria-label="Filter the library">
        <Field label="Search" className="min-w-64 flex-1">
          <Input type="search" placeholder="Title, author, narrator or path" value={typed} onChange={e => setTyped(e.target.value)} />
        </Field>
        <Field label="Kind">
          <NativeSelect value={kind} onChange={e => updateParams({ kind: e.target.value, page: '' })}>
            <option value="">All kinds</option>
            {Object.entries(KIND_LABEL).filter(([k]) => !['not-book', 'review'].includes(k)).map(([k, label]) => (
              <option key={k} value={k}>{label}{d?.kinds[k] != null ? ` (${d.kinds[k]})` : ''}</option>
            ))}
          </NativeSelect>
        </Field>
        <Field label="Metadata">
          <NativeSelect value={missing} onChange={e => updateParams({ missing: e.target.value, page: '' })}>
            {MISSING_FILTERS.map(([v, label]) => <option key={v} value={v}>{label}</option>)}
          </NativeSelect>
        </Field>
        <Field label="Sort by">
          <NativeSelect value={sort} onChange={e => updateParams({ sort: e.target.value === 'title' ? '' : e.target.value, page: '' })}>
            {SORTS.map(([v, label]) => <option key={v} value={v}>{label}</option>)}
          </NativeSelect>
        </Field>
      </div>

      <DataView
        state={load}
        isEmpty={x => x.all_total === 0}
        skeleton={<div className="space-y-4">{[0, 1].map(i => <Skeleton key={i} className="h-64 w-full" />)}</div>}
        empty={
          <EmptyState title="No listens with a confirmed match and cover yet">
            Confirm matches on the <Link className="underline" to="/">Matches page</Link> and choose covers on the <Link className="underline" to="/covers">Covers page</Link>, and they will appear here.
          </EmptyState>
        }
      >
        {x =>
          x.rows.length === 0 ? (
            <EmptyState title="No listens match these filters">
              Clear the search or choose another filter to see the listens in your library.
            </EmptyState>
          ) : (
            <div className="space-y-4">
              {x.rows.map(r => {
                const item = updated[r.listen_id] ?? r
                return (
                  <LibraryCard
                    key={item.listen_id}
                    item={item}
                    error={errors[item.listen_id]}
                    onPickField={onPickField}
                    onPickColumn={onPickColumn}
                  />
                )
              })}
              <nav aria-label="Pagination" className="flex flex-wrap items-center justify-between gap-3 border-t border-border pt-3">
                <span className="type-caption text-muted-foreground">
                  Showing {x.offset + 1}–{Math.min(x.offset + x.rows.length, x.total)} of {x.total} · Page {page} of {totalPages}
                </span>
                <div className="flex items-center gap-2">
                  <Button variant="outline" size="sm" disabled={page <= 1} onClick={() => updateParams({ page: page - 1 <= 1 ? '' : String(page - 1) })}>
                    Previous
                  </Button>
                  <Button variant="outline" size="sm" disabled={page >= totalPages} onClick={() => updateParams({ page: String(page + 1) })}>
                    Next
                  </Button>
                </div>
              </nav>
            </div>
          )
        }
      </DataView>
    </div>
  )
}
