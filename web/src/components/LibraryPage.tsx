import { useState, type FormEvent } from 'react'
import { REVIEWS, STATUSES, api, type Book, type Review, type Status } from '../api'
import { useLoad } from '../route'
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

export default function LibraryPage() {
  const [q, setQ] = useState('')
  const [status, setStatus] = useState<Status | ''>('')
  const [review, setReview] = useState<Review | ''>('')
  const [showRadio, setShowRadio] = useState(false)
  const [note, setNote] = useState<string | null>(null)
  const { data: books, error, reload } = useLoad(() => api.books({ q, status: status || undefined, review: review || undefined, kind: showRadio ? undefined : 'book' }), [q, status, review, showRadio])
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

  return (
    <div>
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
          {note && <span className="text-stone-500">{note}</span>}
        </div>
        <Err msg={err || error} />
      </Card>

      <div className="flex flex-wrap items-center gap-2 mb-2">
        <h2 className="text-base font-medium mr-auto">{books ? describeCount(books.length) : 'Loading…'}</h2>
        <input aria-label="Search" className={`${input} w-44`} placeholder="Search" value={q} onChange={e => setQ(e.target.value)} />
        <select aria-label="Filter by review" className={input} value={review} onChange={e => setReview(e.target.value as Review | '')}>
          <option value="">Any review state</option>
          {REVIEWS.map(r => <option key={r} value={r}>{r}</option>)}
        </select>
        <label className="text-sm flex items-center gap-1"><input type="checkbox" checked={showRadio} onChange={e => setShowRadio(e.target.checked)} />Show radio</label>
        <select aria-label="Filter by status" className={input} value={status} onChange={e => setStatus(e.target.value as Status | '')}>
          <option value="">All</option>
          {STATUSES.map(s => <option key={s} value={s}>{s}</option>)}
        </select>
      </div>

      <div className="space-y-2">
        {books?.map((b: Book) => (
          <Card key={b.id} className="flex flex-wrap items-baseline gap-2">
            <div className="mr-auto">
              <span className="font-medium">{b.title}</span>
              <span className="text-sm text-stone-500"> {b.author || 'unknown author'}{b.series ? ` · ${b.series}${b.series_position != null ? ` #${b.series_position}` : ''}` : ''}{b.duration_seconds ? ` · ${formatDuration(b.duration_seconds)}` : ''}</span>
            </div>
            <select aria-label={`Status of ${b.title}`} className={input} value={b.status} onChange={e => act(() => api.setStatus(b.id, e.target.value as Status))}>
              {STATUSES.map(s => <option key={s} value={s}>{s}</option>)}
            </select>
            <button type="button" className={btnQuiet} onClick={() => act(() => api.removeBook(b.id))}>Remove {b.title}</button>
            {b.review !== 'confirmed' && (
              <div className="basis-full text-sm flex flex-wrap items-center gap-2">
                <span className="rounded bg-stone-200 dark:bg-stone-800 px-1.5 text-xs">{b.review}</span>
                {b.suggestion?.title && <span>Suggested: <b>{b.suggestion.title}</b> by {b.suggestion.author || 'unknown'}{b.suggestion.series ? ` (${b.suggestion.series})` : ''} <span className="text-stone-500">{matchLabel(b.match_score)}</span></span>}
                <button type="button" className={btnQuiet} onClick={() => act(() => api.confirm(b.id))}>Confirm {b.title}</button>
                {b.review !== 'ignored' && <button type="button" className={btnQuiet} onClick={() => act(() => api.ignore(b.id))}>Ignore {b.title}</button>}
              </div>
            )}
          </Card>
        ))}
      </div>
      <p className="mt-4 text-xs text-stone-500">Suggestions use data from <a className="underline" href="https://audiobookdb.org">audiobookdb.org</a>.</p>
    </div>
  )
}
