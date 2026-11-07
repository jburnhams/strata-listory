// The one question: "Which books do I have, and which should go into the pipeline next?" The finding: how many are ready to ingest and how many are already somewhere in the workflow.
// Why it is shaped this way: the catalogue is large (hundreds of rows), so the list is a DataTable that virtualises itself, with search and filters in the URL so a filtered view can be linked or reloaded.
// Books already in the workflow are hidden unless asked for, because starting ingest on them again is the mistake this screen exists to prevent.
import { useMemo, useState, type FormEvent } from 'react'
import { useQueryClient } from '@tanstack/react-query'
import { Link, useSearchParams } from 'react-router'
import { AsyncButton, BulkBar, Button, DataTable, DataView, EmptyState, Field, Input, NativeSelect, PageHeader, RowAction, Status, TableSkeleton, UndoBar, format, useAsyncAction, useLoad, useSelection, useUndoable, type Column } from 'strata-react'
import { REVIEWS, STATUSES, api, ingestApi, type Book, type LibraryStage, type Review, type Status as ListenStatus } from '../api'
import SourceFiles from './SourceFiles'

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

const workflowLabels: Record<LibraryStage, string> = { processing: 'Processing', metadata: 'Book identification', audiobook: 'Audiobook identification', quality: 'Quality', proposed: 'Plan proposed', finished: 'Finished' }
const reviewTone = { new: 'neutral', suggested: 'info', confirmed: 'success', ignored: 'neutral' } as const
const eligible = (b: Book) => b.kind === 'book' && b.review !== 'ignored' && !!b.path.trim()

export default function LibraryPage() {
  const qc = useQueryClient()
  const [params, setParams] = useSearchParams()
  const q = params.get('q') ?? '', review = (params.get('review') ?? '') as Review | '', status = (params.get('status') ?? '') as ListenStatus | '', stage = params.get('stage') ?? ''
  const showRadio = params.get('radio') === '1'
  const setParam = (k: string, v: string) => setParams(p => { const n = new URLSearchParams(p); if (v) n.set(k, v); else n.delete(k); return n }, { replace: true })

  const load = useLoad({ queryKey: ['library'], queryFn: async () => {
    const [books, workflow] = await Promise.all([api.books(), api.libraryWorkflow()])
    return { books, workflow: workflow.books }
  } })
  const [online, setOnline] = useState(true)
  const [error, setError] = useState<string | null>(null)
  const [note, setNote] = useState<string | null>(null)
  const undoable = useUndoable()
  const refresh = () => qc.invalidateQueries({ queryKey: ['library'] })
  const act = async (fn: () => Promise<unknown>) => { setError(null); try { await fn(); await refresh() } catch (x) { setError((x as Error).message) } }

  const start = useAsyncAction(async () => {
    setError(null); setNote(null)
    await ingestApi.start(sel.selectedRows.map(b => b.id), online)
    setNote(`Ingest queued for ${format.count(sel.count, 'book')}. Follow it under Processing.`); sel.clear(); await refresh()
  }, { onError: e => setError((e as Error).message) })
  const scan = useAsyncAction(async () => { setError(null); const r = await api.scan(); setNote(`Scanned: ${r.found} found, ${r.added} new, ${r.existing} already known.`); await refresh() }, { onError: e => setError((e as Error).message) })
  const match = useAsyncAction(async () => { setError(null); const r = await api.match(); setNote(`Matched ${r.tried}: ${r.suggested} suggestions, ${r.none} without a match, ${r.remaining} still to try.`); await refresh() }, { onError: e => setError((e as Error).message) })
  const [title, setTitle] = useState(''), [author, setAuthor] = useState('')
  const onAdd = (e: FormEvent) => { e.preventDefault(); if (!title.trim()) return; act(async () => { await api.addBook({ title, author }); setTitle(''); setAuthor('') }) }

  const data = load.status === 'ready' ? load.data : null
  const inWorkflow = (b: Book) => (data?.workflow[b.id]?.length ?? 0) > 0
  const terms = q.toLocaleLowerCase().trim().split(/\s+/).filter(Boolean)
  const books = useMemo(() => (data?.books ?? []).filter(b => !undoable.isRemoved(String(b.id))
    && (stage ? data!.workflow[b.id]?.includes(stage as LibraryStage) : !inWorkflow(b))
    && (showRadio || b.kind === 'book') && (!status || b.status === status) && (!review || b.review === review)
    && terms.every(t => `${b.title} ${b.author} ${b.narrator} ${b.series} ${b.path} ${b.id}`.toLocaleLowerCase().includes(t))), [data, undoable.isRemoved, stage, showRadio, status, review, q]) // eslint-disable-line react-hooks/exhaustive-deps
  const hidden = data ? data.books.filter(inWorkflow).length : 0
  const sel = useSelection(books, b => String(b.id))

  const columns: Column<Book>[] = [
    { key: 'title', header: 'Book', sort: (a, b) => a.title.localeCompare(b.title), cell: b => (
      <div className="min-w-0 space-y-1">
        <p><Link className="underline" to={`/book/${b.id}`}>{b.title}</Link> <span className="text-muted-foreground">{b.author || 'unknown author'}{b.series ? ` · ${b.series}${b.series_position != null ? ` #${b.series_position}` : ''}` : ''}</span></p>
        <p className="type-caption text-muted-foreground">ID {b.id}{data?.workflow[b.id]?.length ? ` · ${data.workflow[b.id].map(s => workflowLabels[s]).join(' · ')}` : ''}{b.file_count != null && ` · ${format.count(b.file_count, 'file')}`}{b.narrator && ` · Narrated by ${b.narrator}`}</p>
        <SourceFiles bookId={b.id} />
        {b.review !== 'confirmed' && b.suggestion?.title && <p className="type-body-sm">Suggested: <b>{b.suggestion.title}</b> by {b.suggestion.author || 'unknown'}{b.suggestion.series ? ` (${b.suggestion.series})` : ''} <span className="text-muted-foreground">{matchLabel(b.match_score)}{b.suggestion.sources?.length ? ` · via ${b.suggestion.sources.join(' + ')}` : ''}</span></p>}
      </div>) },
    { key: 'length', header: 'Length', numeric: true, sort: (a, b) => (a.duration_seconds ?? 0) - (b.duration_seconds ?? 0), cell: b => formatDuration(b.duration_seconds) },
    { key: 'review', header: 'Match', cell: b => <Status tone={reviewTone[b.review]}>{b.review}</Status> },
    { key: 'status', header: 'Listening', cell: b => <NativeSelect className="min-w-36" aria-label={`Status of ${b.title}`} value={b.status} onChange={e => act(() => api.setStatus(b.id, e.target.value as ListenStatus))}>{STATUSES.map(s => <option key={s} value={s}>{s}</option>)}</NativeSelect> },
    { key: 'actions', header: 'Actions', cell: b => (
      <div className="flex flex-wrap gap-1">
        {b.review !== 'confirmed' && <RowAction label="Confirm" subject={b.title} onClick={() => act(() => api.confirm(b.id))} />}
        {b.review !== 'confirmed' && b.review !== 'ignored' && <RowAction label="Ignore" subject={b.title} onClick={() => act(() => api.ignore(b.id))} />}
        <RowAction label="Remove" subject={b.title} onClick={() => undoable.schedule({ id: String(b.id), message: `Removed ${b.title} from the catalogue.`, failure: `${b.title} is back in the catalogue: the removal did not go through.`, commit: async () => { await api.removeBook(b.id); await refresh() } })} />
      </div>) },
  ]

  return (
    <div className="space-y-5">
      <PageHeader title="Library" scope="Choose books to ingest"
        finding={data ? { text: `${format.count(books.length, 'book')} shown. ${hidden ? `${format.count(hidden, 'entry', 'entries')} already in the workflow ${stage ? '' : 'are hidden'}.` : ''}` } : undefined}
        actions={<AsyncButton variant="default" phase={start.phase} error={start.error} disabled={!sel.count} onClick={() => void start.run()} labels={{ idle: `Start ingest (${sel.count})`, pending: 'Queuing ingest…', done: 'Ingest queued' }} />} />
      {error && <p role="alert" className="type-body text-danger">{error}</p>}
      {note && <p role="status" className="type-body-sm text-muted-foreground">{note}</p>}

      <form onSubmit={onAdd} className="flex flex-wrap items-end gap-3" aria-label="Add a book">
        <Field label="Title" className="min-w-52 flex-1"><Input value={title} onChange={e => setTitle(e.target.value)} /></Field>
        <Field label="Author" className="min-w-40"><Input value={author} onChange={e => setAuthor(e.target.value)} /></Field>
        <Button type="submit">Add book</Button>
        <AsyncButton phase={scan.phase} error={scan.error} onClick={() => void scan.run()} labels={{ idle: 'Scan audiobook folder', pending: 'Scanning…', done: 'Scanned' }} />
        <AsyncButton phase={match.phase} error={match.error} onClick={() => void match.run()} labels={{ idle: 'Match new books', pending: 'Matching…', done: 'Matched' }} />
      </form>

      <div className="flex flex-wrap items-end gap-3" role="search" aria-label="Filter the library">
        <Field label="Search" className="min-w-64 flex-1"><Input type="search" placeholder="Title, author, narrator, path or ID" value={q} onChange={e => setParam('q', e.target.value)} /></Field>
        <Field label="Match state"><NativeSelect value={review} onChange={e => setParam('review', e.target.value)}><option value="">Any match state</option>{REVIEWS.map(r => <option key={r} value={r}>{r}</option>)}</NativeSelect></Field>
        <Field label="Listening"><NativeSelect value={status} onChange={e => setParam('status', e.target.value)}><option value="">Any</option>{STATUSES.map(s => <option key={s} value={s}>{s}</option>)}</NativeSelect></Field>
        <Field label="Workflow"><NativeSelect value={stage} onChange={e => setParam('stage', e.target.value)}><option value="">Not started</option>{(Object.entries(workflowLabels) as [LibraryStage, string][]).map(([s, l]) => <option key={s} value={s}>{l} ({data?.books.filter(b => data.workflow[b.id]?.includes(s)).length ?? 0})</option>)}</NativeSelect></Field>
        <label className="type-body-sm flex h-(--control-h) items-center gap-2"><input type="checkbox" checked={showRadio} onChange={e => setParam('radio', e.target.checked ? '1' : '')} />Show radio</label>
      </div>

      <BulkBar count={sel.count} noun="book" onClear={sel.clear}>
        <label className="flex items-center gap-2 type-body-sm"><input type="checkbox" checked={online} onChange={e => setOnline(e.target.checked)} /> Query metadata providers</label>
      </BulkBar>

      <DataView state={load} isEmpty={d => d.books.length === 0} skeleton={<TableSkeleton />}
        empty={<EmptyState title="No books yet" action={<Button variant="default" onClick={() => void scan.run()}>Scan audiobook folder</Button>}>Scan the audiobook folder from Settings, or add a book by title above, and it will appear here.</EmptyState>}>
        {() => books.length === 0
          ? <EmptyState title="No books match these filters">Clear the search, or choose another workflow stage to see books that are already in the pipeline.</EmptyState>
          : <DataTable caption="Library" columns={columns} rows={books} rowKey={b => String(b.id)} maxHeight={720} selection={{ selected: sel.selected, onChange: sel.onChange, rowLabel: b => `${b.title} (ID ${b.id})`, isSelectable: eligible }} />}
      </DataView>
      <UndoBar undoable={undoable} />
      <p className="type-caption text-muted-foreground">Suggestions use data from Audible, Audnexus and <a className="underline" href="https://audiobookdb.org">audiobookdb.org</a>.</p>
    </div>
  )
}
