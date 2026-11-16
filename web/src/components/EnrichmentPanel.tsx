// Shared ratings, synopsis, and reviews display for Library cards and Listen detail pages.
import { useEffect, useState } from 'react'
import { Button, Status } from 'strata-react'
import { enrichmentApi, type EnrichmentSummary } from '../api'

export function RatingsLine({ enrichment }: { enrichment?: EnrichmentSummary }) {
  if (!enrichment) return null
  if (enrichment.status === 'queued' || enrichment.status === 'running') {
    return <p className="type-caption text-muted-foreground">Fetching ratings, synopsis &amp; reviews…</p>
  }
  if (!enrichment.ratings.length) return null
  return (
    <p className="flex flex-wrap items-baseline gap-x-3 gap-y-0.5 type-body-sm text-muted-foreground">
      {enrichment.ratings.map(r => (
        <span key={r.source}>
          <span className="font-medium text-foreground">{r.rating.toFixed(1)}★</span>
          {r.count != null ? ` (${r.count.toLocaleString()})` : ''}{' '}
          {r.url ? (
            <a href={r.url} target="_blank" rel="noreferrer" className="underline">{r.label}</a>
          ) : (
            r.label
          )}
        </span>
      ))}
    </p>
  )
}

export function EnrichmentPanel({
  listenId,
  enrichment,
  onRefreshed,
}: {
  listenId: number
  enrichment?: EnrichmentSummary
  onRefreshed: () => void
}) {
  const [busy, setBusy] = useState(false)
  const [err, setErr] = useState<string | null>(null)
  const [synIdx, setSynIdx] = useState(0)
  const [revealedSpoilers, setRevealedSpoilers] = useState<Record<number, boolean>>({})

  async function refresh() {
    setBusy(true)
    setErr(null)
    try {
      await enrichmentApi.fetch(listenId)
      onRefreshed()
    } catch (e) {
      setErr(String((e as Error).message || e))
    } finally {
      setBusy(false)
    }
  }

  const synopses = enrichment?.synopses ?? []
  const activeSyn = synopses[Math.min(synIdx, Math.max(0, synopses.length - 1))] ?? null
  const ratings = enrichment?.ratings ?? []
  const reviews = enrichment?.reviews ?? []
  const tags = enrichment?.tags
  const hasTags = Boolean(tags && (tags.genres.length || tags.moods.length || tags.content_warnings.length))
  const isFetching = busy || enrichment?.status === 'queued' || enrichment?.status === 'running'

  useEffect(() => {
    if (enrichment?.status !== 'queued' && enrichment?.status !== 'running') return
    const id = setInterval(() => onRefreshed(), 2000)
    return () => clearInterval(id)
  }, [enrichment?.status, onRefreshed])

  return (
    <div className="space-y-3">
      {ratings.length > 0 && (
        <div className="flex flex-wrap items-center gap-3 type-body-sm">
          {ratings.map(r => (
            <span key={r.source} className="rounded-control border border-border bg-muted/30 px-2.5 py-1">
              <span className="font-medium">{r.rating.toFixed(2)}★</span>
              {r.count != null ? ` · ${r.count.toLocaleString()} ratings` : ''}
              {r.reviews_count != null ? ` · ${r.reviews_count.toLocaleString()} reviews` : ''}
              {' — '}
              {r.url ? <a href={r.url} target="_blank" rel="noreferrer" className="underline">{r.label}</a> : r.label}
            </span>
          ))}
        </div>
      )}

      {activeSyn && (
        <div className="space-y-1.5">
          {synopses.length > 1 && (
            <div className="flex flex-wrap items-center gap-1.5 type-caption">
              <span className="text-muted-foreground">Synopsis from:</span>
              {synopses.map((s, i) => (
                <button
                  key={s.source}
                  type="button"
                  aria-pressed={i === synIdx}
                  onClick={() => setSynIdx(i)}
                  className={`rounded-control border px-2 py-0.5 transition-state ${i === synIdx ? 'border-ring bg-muted font-medium' : 'border-border hover:border-ring'}`}
                >
                  {s.label}
                </button>
              ))}
            </div>
          )}
          <p className="whitespace-pre-line type-body-sm text-foreground">{activeSyn.text}</p>
          <p className="type-caption text-muted-foreground">
            Source:{' '}
            {activeSyn.url ? (
              <a href={activeSyn.url} target="_blank" rel="noreferrer" className="underline">{activeSyn.label}</a>
            ) : (
              activeSyn.label
            )}
          </p>
        </div>
      )}

      {hasTags && tags && (
        <div className="space-y-1 type-caption text-muted-foreground">
          {tags.genres.length > 0 && <p><span className="font-medium text-foreground">Genres:</span> {tags.genres.join(' · ')}</p>}
          {tags.moods.length > 0 && <p><span className="font-medium text-foreground">Moods:</span> {tags.moods.join(' · ')}</p>}
          {tags.content_warnings.length > 0 && <p><span className="font-medium text-foreground">Content warnings:</span> {tags.content_warnings.join(' · ')}</p>}
        </div>
      )}

      {reviews.length > 0 && (
        <div className="space-y-2 border-t border-border/60 pt-2">
          <h3 className="type-body-sm font-medium">Reviews ({reviews.length})</h3>
          <ul className="space-y-2.5">
            {reviews.map((rev, i) => (
              <li key={`${rev.source}-${i}`} className="rounded-control border border-border/60 bg-muted/15 p-2.5 type-body-sm">
                <div className="mb-1 flex flex-wrap items-baseline gap-2 type-caption text-muted-foreground">
                  <span className="font-medium text-foreground">{rev.user}</span>
                  {rev.rating != null && <span>{rev.rating}★</span>}
                  {rev.date && <span>{rev.date}</span>}
                  {rev.likes > 0 && <span>{rev.likes} likes</span>}
                  <span>· {rev.url ? <a href={rev.url} target="_blank" rel="noreferrer" className="underline">{rev.label}</a> : rev.label}</span>
                </div>
                {rev.title && <p className="mb-1 font-medium">{rev.title}</p>}
                {rev.spoilers && !revealedSpoilers[i] ? (
                  <button
                    type="button"
                    onClick={() => setRevealedSpoilers(s => ({ ...s, [i]: true }))}
                    className="type-caption underline text-muted-foreground"
                  >
                    Contains spoilers — show review
                  </button>
                ) : (
                  <p className="whitespace-pre-line">{rev.text.length > 700 ? `${rev.text.slice(0, 700)}…` : rev.text}</p>
                )}
              </li>
            ))}
          </ul>
        </div>
      )}

      {!activeSyn && !ratings.length && !reviews.length && !isFetching && (
        <p className="type-body-sm text-muted-foreground">No ratings, synopsis, or reviews fetched yet.</p>
      )}

      <div className="flex flex-wrap items-center gap-2 pt-1">
        <Button variant="outline" size="sm" disabled={isFetching} onClick={() => void refresh()}>
          {isFetching ? 'Fetching…' : enrichment?.fetched_at ? 'Refresh details' : 'Fetch details'}
        </Button>
        {enrichment?.fetched_at && (
          <span className="type-caption text-muted-foreground">Fetched {enrichment.fetched_at.slice(0, 10)}</span>
        )}
        {enrichment && enrichment.sources.length > 0 && (
          <span className="type-caption text-muted-foreground">
            {enrichment.sources.filter(s => s.status === 'found').length} of {enrichment.sources.length} sources found data
          </span>
        )}
        {enrichment?.status === 'queued' && <Status tone="neutral">Queued</Status>}
        {enrichment?.status === 'running' && <Status tone="neutral">Running</Status>}
      </div>
      {err && <p role="alert" className="type-body-sm text-danger">{err}</p>}
    </div>
  )
}
