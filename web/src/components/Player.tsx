import { useState } from 'react'
import { api, audioUrl } from '../api'
import { Disclosure, NativeSelect, useLoad } from 'strata-react'
import { readOf } from '../route'
import { Card, Err } from './ui'
import { formatDuration } from './LibraryPage'

export default function Player({ id }: { id: number }) {
  const { data, error } = readOf(useLoad({ queryKey: ['output', id], queryFn: () => api.output(id) }))
  const [chapter, setChapter] = useState(0)
  const [advance, setAdvance] = useState(false)
  return <Card className="mb-4"><h2 className="font-medium mb-2">Listen</h2><Err msg={error} />
    {!data && !error && <p>Loading chapters…</p>}
    {data && (data.chapters.length ? <><label className="block type-body-sm mb-2">Chapter <NativeSelect className="ml-2 max-w-full" value={chapter} onChange={e => { setAdvance(false); setChapter(Number(e.target.value)) }}>{data.chapters.map((c, i) => <option key={i} value={i}>{i + 1}. {c.title} · {formatDuration(c.duration)}</option>)}</NativeSelect></label>
      <audio className="w-full" controls autoPlay={advance} src={audioUrl(id, chapter)} onEnded={() => { if (chapter + 1 < data.chapters.length) { setAdvance(true); setChapter(chapter + 1) } }} />
      <Disclosure className="mt-3 type-body-sm" summary="Finished metadata and provenance"><pre className="whitespace-pre-wrap break-words overflow-auto max-h-96">{JSON.stringify({ book: data.book, metadata: data.metadata }, null, 2)}</pre></Disclosure></> : <p>No chapters available.</p>)}
  </Card>
}
