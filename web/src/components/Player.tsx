import { useState } from 'react'
import { api, audioUrl } from '../api'
import { Disclosure, MediaPlayer, NativeSelect, SPEECH_RATES, useLoad } from 'strata-react'
import { readOf } from '../route'
import { Card, Err } from './ui'
import { formatDuration } from './LibraryPage'

export default function Player({ id }: { id: number }) {
  const { data, error } = readOf(useLoad({ queryKey: ['output', id], queryFn: () => api.output(id) }))
  const [chapter, setChapter] = useState(0)
  return <Card className="mb-4"><h2 className="font-medium mb-2">Listen</h2><Err msg={error} />
    {!data && !error && <p>Loading chapters…</p>}
    {data && (data.chapters.length ? <><label className="block type-body-sm mb-2">Chapter <NativeSelect className="ml-2 max-w-full" value={chapter} onChange={e => setChapter(Number(e.target.value))}>{data.chapters.map((c, i) => <option key={i} value={i}>{i + 1}. {c.title} · {formatDuration(c.duration)}</option>)}</NativeSelect></label>
      <MediaPlayer label="Audiobook" sources={data.chapters.map((c, i) => ({ src: audioUrl(id, i), title: c.title, duration: c.duration }))} index={chapter} onIndexChange={setChapter} rates={SPEECH_RATES} session={{ title: String(data.book.title ?? 'Audiobook'), artist: typeof data.book.author === 'string' ? data.book.author : undefined }} />
      <Disclosure className="mt-3 type-body-sm" summary="Finished metadata and provenance"><pre className="whitespace-pre-wrap break-words overflow-auto max-h-96">{JSON.stringify({ book: data.book, metadata: data.metadata }, null, 2)}</pre></Disclosure></> : <p>No chapters available.</p>)}
  </Card>
}
