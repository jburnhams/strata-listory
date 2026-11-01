import { api, coverUrl } from '../api'
import { href, useLoad } from '../route'
import { Card, Err } from './ui'

export default function FinishedPage() {
  const { data, error } = useLoad(() => api.finished(), [])
  return <div><div className="flex items-baseline justify-between mb-4"><h1 className="text-xl font-semibold">Finished audiobooks</h1><a className="text-blue-600 dark:text-blue-400 underline" href={href.process()}>Processing queue</a></div>
    <Err msg={error} />
    {!data && !error && <p>Loading…</p>}
    {data?.length === 0 && <p>No audiobooks have been processed yet.</p>}
    <div className="space-y-3">{data?.map(b => <Card key={b.book_id} className="flex gap-4"><img src={coverUrl(b.book_id)} alt="" className="w-20 h-20 object-cover rounded bg-stone-200" onError={e => { e.currentTarget.style.visibility = 'hidden' }} /><div><a className="font-medium text-blue-600 dark:text-blue-400 hover:underline" href={href.book(b.book_id)}>{b.title}</a><p className="text-stone-500">{b.author}</p><p className="text-sm">{b.result.chapters} chapters · Open to play and view metadata</p></div></Card>)}</div>
  </div>
}
