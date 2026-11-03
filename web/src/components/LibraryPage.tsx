import SourceFiles from './SourceFiles'
import FolderTree, { inFolder } from './FolderTree'
import { useEffect, useMemo, useState, type FormEvent } from 'react'
import { REVIEWS, STATUSES, api, ingestApi, coverUrl, type Book, type Review, type Status, type LibraryStage } from '../api'
import { href, useLoad } from '../route'
import { Card, Err, btn, btnQuiet, input } from './ui'

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

const workflowLabels: Record<LibraryStage, string> = { processing: 'Processing', metadata: 'Book identification', audiobook: 'Audiobook identification', quality: 'Quality', proposed: 'Proposed', finished: 'Finished' }

export default function LibraryPage() {
  const [q, setQ] = useState('')
  const [folder, setFolder] = useState<string | null>(null)
  const [status, setStatus] = useState<Status | ''>('')
  const [review, setReview] = useState<Review | ''>('')
  const [showRadio, setShowRadio] = useState(false)
  const [coverOnly, setCoverOnly] = useState(false), [observedCovers, setObservedCovers] = useState<number[]>([])
  const [note, setNote] = useState<string | null>(null)
  const { data: loaded, error, reload } = useLoad(async () => {
    const [books, workflow, covers] = await Promise.all([api.books(), api.libraryWorkflow(), api.libraryCovers()])
    return { books, workflow: workflow.books, covers: covers.ids }
  }, [])
  const data = loaded?.books, workflow = loaded?.workflow ?? {}
  const covers = useMemo(() => new Set([...(loaded?.covers ?? []), ...observedCovers]), [loaded, observedCovers])
  const [includedStages, setIncludedStages] = useState<LibraryStage[]>([])
  const included = (b: Book) => !workflow[b.id]?.length || workflow[b.id].some(stage => includedStages.includes(stage))
  const [selected, setSelected] = useState<number[]>([]), [selectedOnly, setSelectedOnly] = useState(false), [online, setOnline] = useState(true), [submitting, setSubmitting] = useState(false), [page, setPage] = useState(0)
  const eligible = (b: Book) => b.kind === 'book' && b.review !== 'ignored' && !!b.path.trim()
  const terms = q.toLocaleLowerCase().trim().split(/\s+/).filter(Boolean)
  const books = data?.filter(b => inFolder(b.path, folder) && (!coverOnly || covers.has(b.id)) && included(b) && (showRadio || b.kind === 'book') && (!status || b.status === status) && (!review || b.review === review) && (!selectedOnly || selected.includes(b.id)) && terms.every(t => `${b.title} ${b.author} ${b.narrator} ${b.series} ${b.path} ${b.id}`.toLocaleLowerCase().includes(t)))
  const currentPage = Math.min(page, Math.max(0, Math.ceil((books?.length ?? 0) / 25) - 1)), visible = books?.slice(currentPage * 25, (currentPage + 1) * 25)
  const matchIds = (books ?? []).filter(eligible).map(b => b.id), pageIds = (visible ?? []).filter(eligible).map(b => b.id)
  useEffect(() => { if (data) setSelected(ids => ids.filter(id => data.some(b => b.id === id && eligible(b) && included(b)))) }, [loaded, includedStages])
  const [title, setTitle] = useState('')
  const [author, setAuthor] = useState('')
  const [err, setErr] = useState<string | null>(null)

  async function act(fn: () => Promise<unknown>) {
    setErr(null)
    try { await fn(); reload() } catch (x) { setErr(String((x as Error).message || x)) }
  }
  async function onScan() { await act(async () => { const r = await api.scan(); setNote(`Scanned: ${r.found} found, ${r.added} new, ${r.existing} already known.`) }) }
  async function onMatch() { await act(async () => { const r = await api.match(); setNote(`Matched ${r.tried}: ${r.suggested} suggestions, ${r.none} without a match, ${r.remaining} still to try.`) }) }
  function onSubmit(e: FormEvent) {
    e.preventDefault()
    act(async () => { await api.addBook({ title, author }); setTitle(''); setAuthor('') })
  }
  async function start() {
    setSubmitting(true); setErr(null); setNote(null)
    try { await ingestApi.start(selected, online); setNote(`Ingest queued for ${selected.length} selected ${selected.length === 1 ? 'entry' : 'entries'}. Follow progress in Processing.`); setSelected([]); setSelectedOnly(false); reload() }
    catch (e) { setErr((e as Error).message) } finally { setSubmitting(false) }
  }

  return (
    <div>
      <h1 className="text-xl font-semibold mb-2">Library · choose books to ingest</h1>
      <p className="text-sm text-stone-500 mb-4">Select source audiobooks to start book identification. Books already in the workflow are hidden by default. Review book identity, audiobook identity and quality on their own pages, and follow all work in <a className="underline" href={href.process()}>Processing</a>.</p>
      <Card className="mb-5">
        <h2 className="text-base font-medium mb-2">Add a book</h2>
        <form onSubmit={onSubmit} className="flex flex-wrap gap-2 items-center">
          <input aria-label="Title" className={`${input} flex-1 min-w-44`} placeholder="Title" value={title} onChange={e => setTitle(e.target.value)} />
          <input aria-label="Author" className={`${input} w-44`} placeholder="Author" value={author} onChange={e => setAuthor(e.target.value)} />
          <button type="submit" className={btn}>Add book</button>
        </form>
        <div className="flex flex-wrap items-center gap-2 mt-3 text-sm">
          <button type="button" className={btnQuiet} onClick={onScan}>Scan audiobook folder</button>
          <button type="button" className={btnQuiet} onClick={onMatch}>Match new books</button>
          {note && <span role="status" className="text-stone-500">{note}</span>}
        </div>
        <Err msg={err || error} />
      </Card>

      <div className="grid gap-5 lg:grid-cols-[16rem_minmax(0,1fr)]">
        <aside className="rounded border border-stone-200 dark:border-stone-700 p-3 max-h-[70vh] overflow-auto lg:sticky lg:top-4 lg:self-start"><FolderTree books={data ?? []} selected={folder} onSelect={value => { setFolder(value); setPage(0) }} /></aside>
        <div className="min-w-0">
        {folder !== null && <p className="text-sm mb-3 break-all">Showing: {folder || 'No source folder'}{folder && ' (including subfolders)'}</p>}
      <div className="flex flex-wrap items-center gap-2 mb-2">
        <h2 className="text-base font-medium mr-auto">{books ? books.length === 0 && data?.length ? 'No books match the current filters.' : describeCount(books.length) : 'Loading…'}</h2>
        <input type="search" aria-label="Search" className={`${input} w-64`} placeholder="Title, author, narrator, path or ID" value={q} onChange={e => { setQ(e.target.value); setPage(0) }} />
        <select aria-label="Filter by review" className={input} value={review} onChange={e => setReview(e.target.value as Review | '')}>
          <option value="">Any review state</option>
          {REVIEWS.map(r => <option key={r} value={r}>{r}</option>)}
        </select>
        <label className="text-sm flex items-center gap-1"><input type="checkbox" checked={showRadio} onChange={e => setShowRadio(e.target.checked)} />Show radio</label>
        <label className="text-sm flex items-center gap-1"><input type="checkbox" checked={coverOnly} onChange={e => { setCoverOnly(e.target.checked); setPage(0) }} />Has cover image</label>
        <select aria-label="Filter by status" className={input} value={status} onChange={e => setStatus(e.target.value as Status | '')}>
          <option value="">All</option>
          {STATUSES.map(s => <option key={s} value={s}>{s}</option>)}
        </select>
      </div>

      <fieldset className="mb-3 rounded border border-stone-200 dark:border-stone-700 p-3 text-sm">
        <legend className="px-1 font-medium">Include books already in…</legend>
        <div className="flex flex-wrap gap-x-4 gap-y-2">{(Object.entries(workflowLabels) as [LibraryStage, string][]).map(([stage, label]) => <label key={stage} className="flex items-center gap-1"><input type="checkbox" checked={includedStages.includes(stage)} onChange={e => { setIncludedStages(stages => e.target.checked ? [...stages, stage] : stages.filter(s => s !== stage)); setPage(0) }} />{label} ({data?.filter(b => workflow[b.id]?.includes(stage)).length ?? 0})</label>)}</div>
        {data && <p className="mt-2 text-xs text-stone-500">{data.filter(b => !included(b)).length} entries hidden because they are already elsewhere in the workflow.</p>}
      </fieldset>

      <Card className="mb-3"><div className="flex flex-wrap items-center gap-2 text-sm">
        <strong aria-live="polite">{selected.length} selected</strong>
        <button className={btnQuiet} disabled={submitting || !pageIds.length} onClick={() => setSelected([...new Set([...selected, ...pageIds])])}>Select this page</button>
        <button className={btnQuiet} disabled={submitting || !matchIds.length} onClick={() => setSelected([...new Set([...selected, ...matchIds])])}>Select all matches ({matchIds.length})</button>
        <button className={btnQuiet} disabled={submitting || !selected.length} onClick={() => setSelected([])}>Clear selection</button>
        <label><input type="checkbox" checked={selectedOnly} onChange={e => { setSelectedOnly(e.target.checked); setPage(0) }} /> Show selected only</label>
        <label><input type="checkbox" checked={online} disabled={submitting} onChange={e => setOnline(e.target.checked)} /> Query metadata providers</label>
        <button className={btn} disabled={submitting || !!error || !selected.length} onClick={start}>{submitting ? 'Queuing ingest…' : `Start ingest (${selected.length})`}</button>
        <button className={btnQuiet} onClick={reload}>Reload library</button>
      </div><p className="text-xs text-stone-500 mt-2">Selection is kept across searches, filters and pages. Books need source files and must not be ignored to start ingest.</p></Card>

      <div className="space-y-2">
        {visible?.map((b: Book) => (
          <Card key={b.id} className="flex flex-wrap items-center gap-2">
            <input type="checkbox" aria-label={`Select ${b.title} (ID ${b.id})`} disabled={submitting || !eligible(b)} checked={selected.includes(b.id)} onChange={() => setSelected(ids => ids.includes(b.id) ? ids.filter(id => id !== b.id) : [...ids, b.id])} />
            <img src={coverUrl(b.id)} alt="" loading="lazy" onLoad={() => setObservedCovers(ids => ids.includes(b.id) ? ids : [...ids, b.id])} className="w-10 h-10 rounded object-cover bg-stone-200 dark:bg-stone-800" onError={e => { e.currentTarget.style.visibility = 'hidden' }} />
            <div className="mr-auto">
              <a className="font-medium hover:underline" href={href.book(b.id)}>{b.title}</a>
              <span className="text-sm text-stone-500"> {b.author || 'unknown author'}{b.series ? ` · ${b.series}${b.series_position != null ? ` #${b.series_position}` : ''}` : ''}{b.duration_seconds ? ` · ${formatDuration(b.duration_seconds)}` : ''}</span>
              <p className="text-xs text-stone-500">ID {b.id}{workflow[b.id]?.length ? ` · ${workflow[b.id].map(stage => workflowLabels[stage]).join(' · ')}` : ''}{b.file_count != null && ` · ${b.file_count} files`}{b.narrator && ` · Narrated by ${b.narrator}`}</p>
              <SourceFiles bookId={b.id} />
            </div>
            <select aria-label={`Status of ${b.title}`} className={input} value={b.status} onChange={e => act(() => api.setStatus(b.id, e.target.value as Status))}>
              {STATUSES.map(s => <option key={s} value={s}>{s}</option>)}
            </select>
            <button type="button" className={btnQuiet} onClick={() => act(() => api.removeBook(b.id))}>Remove {b.title}</button>
            {b.review !== 'confirmed' && (
              <div className="basis-full text-sm flex flex-wrap items-center gap-2">
                <span className="rounded bg-stone-200 dark:bg-stone-800 px-1.5 text-xs">{b.review}</span>
                {b.suggestion?.title && <span>Suggested: <b>{b.suggestion.title}</b> by {b.suggestion.author || 'unknown'}{b.suggestion.series ? ` (${b.suggestion.series})` : ''} <span className="text-stone-500">{matchLabel(b.match_score)}{b.suggestion.sources?.length ? ` · via ${b.suggestion.sources.join(' + ')}` : ''}</span></span>}
                <button type="button" className={btnQuiet} onClick={() => act(() => api.confirm(b.id))}>Confirm {b.title}</button>
                {b.review !== 'ignored' && <button type="button" className={btnQuiet} onClick={() => act(() => api.ignore(b.id))}>Ignore {b.title}</button>}
              </div>
            )}
          </Card>
        ))}
      </div>
      {books && books.length > 25 && <nav aria-label="Library pages" className="flex items-center justify-between mt-3 text-sm"><button className={btnQuiet} disabled={currentPage === 0} onClick={() => setPage(currentPage - 1)}>Previous page</button><span>{currentPage * 25 + 1}–{Math.min((currentPage + 1) * 25, books.length)} of {books.length}</span><button className={btnQuiet} disabled={(currentPage + 1) * 25 >= books.length} onClick={() => setPage(currentPage + 1)}>Next page</button></nav>}
      </div></div>
      <p className="mt-4 text-xs text-stone-500">Suggestions use data from Audible, Audnexus and <a className="underline" href="https://audiobookdb.org">audiobookdb.org</a>.</p>
    </div>
  )
}
