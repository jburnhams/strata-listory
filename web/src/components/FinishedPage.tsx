// The one question: "Which audiobooks are finished and ready to play?" Each row opens the book, where the player and the provenance are.
import { Link } from 'react-router'
import { DataView, EmptyState, Skeleton, useLoad } from 'strata-react'
import { api, coverUrl } from '../api'
import SourceFiles from './SourceFiles'

export default function FinishedPage() {
  const load = useLoad({ queryKey: ['finished'], queryFn: api.finished })
  return (
    <DataView state={load} isEmpty={rows => rows.length === 0} skeleton={<div className="space-y-3">{[0, 1, 2].map(i => <Skeleton key={i} className="h-24 w-full" />)}</div>}
      empty={<EmptyState title="No audiobooks finished yet">Approve a plan under Plans to approve, then process it from the Queue; finished books appear here.</EmptyState>}>
      {rows => (
        <ul aria-label="Finished audiobooks" className="space-y-3">
          {rows.map(b => (
            <li key={b.book_id} className="flex gap-4 rounded-surface border border-border bg-card p-3">
              <img src={coverUrl(b.book_id)} alt="" className="size-20 shrink-0 rounded-control bg-muted object-cover" onError={e => { e.currentTarget.style.visibility = 'hidden' }} />
              <div className="min-w-0">
                <Link className="type-heading underline" to={`/book/${b.book_id}`}>{b.title}</Link>
                <p className="type-body text-muted-foreground">{b.author}</p>
                <SourceFiles bookId={b.book_id} />
                <p className="type-body-sm">{b.result.chapters} chapters · Open to play and view metadata</p>
              </div>
            </li>
          ))}
        </ul>
      )}
    </DataView>
  )
}
