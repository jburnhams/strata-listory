import { api } from '../api'
import { useLoad } from '../route'

export default function SourceFiles({ bookId }: { bookId: number }) {
  const { data, error } = useLoad(() => api.source(bookId), [bookId])
  return <div aria-label="Source folder and files" className="text-xs text-stone-500 my-2 break-all">
    {data?.grouping?.map(note => <p key={note}><strong>Grouping:</strong> {note}</p>)}
    {data?.folder ? <><p><strong>Source folder:</strong> {data.folder}</p><p><strong>Files:</strong> {data.files.join(' · ') || (data.available ? 'No audio files found' : 'Source unavailable')}{data.count != null && data.count > data.files.length && ` · +${data.count - data.files.length} more (${data.count} total)`}</p></>
      : <p>{error ? 'Source details unavailable' : data ? 'No source files' : 'Loading source files…'}</p>}
  </div>
}
