import { useState } from 'react'
import { api, audioUrl } from '../api'
import { useLoad } from '../route'
import { Card, Err } from './ui'
import { formatDuration } from './LibraryPage'

export default function Player({ id }: { id: number }) {
  const { data, error } = useLoad(() => api.output(id), [id])
  const [chapter, setChapter] = useState(0)
  return <Card className="mb-4"><h2 className="font-medium mb-2">Listen</h2><Err msg={error} />
    {!data && !error && <p>Loading chapters…</p>}
    {data && (data.chapters.length ? <><label className="block text-sm mb-2">Chapter <select className="ml-2 max-w-full" value={chapter} onChange={e => setChapter(Number(e.target.value))}>{data.chapters.map((c, i) => <option key={i} value={i}>{i + 1}. {c.title} · {formatDuration(c.duration)}</option>)}</select></label>
      <audio className="w-full" controls src={audioUrl(id, chapter)} onEnded={() => setChapter(i => Math.min(i + 1, data.chapters.length - 1))} />
      <details className="mt-3 text-sm"><summary className="cursor-pointer">Finished metadata and provenance</summary><pre className="whitespace-pre-wrap break-words overflow-auto max-h-96">{JSON.stringify({ book: data.book, metadata: data.metadata }, null, 2)}</pre></details></> : <p>No chapters available.</p>)}
  </Card>
}
