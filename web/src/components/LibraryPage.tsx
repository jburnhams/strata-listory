import { useState, type FormEvent } from 'react'
import { STATUSES, api, type Book, type Status } from '../api'
import { useLoad } from '../route'
import { Card, Err, btn, btnQuiet, input } from './ui'

export function describeCount(n: number) {
  return n === 0 ? 'No books in the catalogue yet.' : `${n} book${n === 1 ? '' : 's'}`
}

export function formatDuration(sec: number | null) {
  if (!sec) return ''
  return `${Math.floor(sec / 3600)}h ${String(Math.floor((sec % 3600) / 60)).padStart(2, '0')}m`
}

export default function LibraryPage() {
  const [q, setQ] = useState('')
  const [status, setStatus] = useState<Status | ''>('')
  const { data: books, error, reload } = useLoad(() => api.books(q, status || undefined), [q, status])
  const [title, setTitle] = useState('')
  const [author, setAuthor] = useState('')
  const [err, setErr] = useState<string | null>(null)

  async function act(fn: () => Promise<unknown>) {
    setErr(null)
    try { await fn(); reload() } catch (x) { setErr(String((x as Error).message || x)) }
  }
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
        <Err msg={err || error} />
      </Card>

      <div className="flex flex-wrap items-center gap-2 mb-2">
        <h2 className="text-base font-medium mr-auto">{books ? describeCount(books.length) : 'Loading…'}</h2>
        <input aria-label="Search" className={`${input} w-44`} placeholder="Search" value={q} onChange={e => setQ(e.target.value)} />
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
          </Card>
        ))}
      </div>
    </div>
  )
}
