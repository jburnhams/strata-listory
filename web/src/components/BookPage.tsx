import { useState } from 'react'
import { api, coverUrl, type AudibleInfo, type BookDetails, type Extras, type HardcoverInfo } from '../api'
import { href, useLoad } from '../route'
import { Card, Crumbs, Err } from './ui'
import { formatDuration, matchLabel } from './LibraryPage'

export function formatBytes(n: number) {
  if (n < 1024) return `${n} B`
  const u = ['KB', 'MB', 'GB', 'TB']; let i = -1
  do { n /= 1024; i++ } while (n >= 1024 && i < u.length - 1)
  return `${n.toFixed(n >= 100 ? 0 : 1)} ${u[i]}`
}

function Rows({ rows }: { rows: [string, React.ReactNode][] }) {
  return (
    <dl className="grid grid-cols-[9rem_1fr] gap-x-3 gap-y-1 text-sm">
      {rows.filter(([, v]) => v !== '' && v != null && v !== false).map(([k, v]) => <div key={k} className="contents"><dt className="text-stone-500">{k}</dt><dd className="break-words">{v}</dd></div>)}
    </dl>
  )
}

function Section({ title, children }: { title: string; children: React.ReactNode }) {
  return <Card className="mb-4"><h2 className="text-base font-medium mb-2">{title}</h2>{children}</Card>
}

function AudibleSection({ a }: { a: AudibleInfo }) {
  return (
    <Section title="Audible metadata file">
      <Rows rows={[
        ['Title', a.title + (a.subtitle ? `: ${a.subtitle}` : '')], ['ASIN', a.asin], ['Authors', a.authors.join(', ')], ['Narrators', a.narrators.join(', ')],
        ['Series', a.series.map(s => `${s.title}${s.sequence ? ` #${s.sequence}` : ''}`).join(', ')], ['Publisher', a.publisher], ['Released', a.release_date], ['Language', a.language], ['Format', a.format],
        ['Runtime', a.runtime_minutes ? formatDuration(a.runtime_minutes * 60) : ''], ['Rating', a.rating ? `${a.rating} (${a.rating_count?.toLocaleString()} ratings)` : ''], ['Categories', a.categories.join(' · ')],
      ]} />
      {a.summary && <p className="text-sm mt-3 text-stone-600 dark:text-stone-300">{a.summary}</p>}
      {a.chapters.length > 0 && (
        <details className="mt-3 text-sm"><summary className="cursor-pointer">{a.chapters.length} chapters</summary>
          <ol className="mt-1 space-y-0.5">{a.chapters.map((c, i) => <li key={i} className="flex justify-between gap-2"><span>{c.title}</span><span className="text-stone-500 font-mono">{formatDuration(c.length_sec) || `${c.length_sec}s`}</span></li>)}</ol>
        </details>
      )}
    </Section>
  )
}

function Community({ id, extras, onChange }: { id: number; extras: Extras; onChange: (e: Extras) => void }) {
  const [busy, setBusy] = useState(false)
  const [err, setErr] = useState<string | null>(null)
  const hc = extras.hardcover, h: HardcoverInfo | undefined = hc?.data
  async function refresh() {
    setBusy(true); setErr(null)
    try { onChange((await api.enrichBook(id)).extras) } catch (x) { setErr(String((x as Error).message || x)) } finally { setBusy(false) }
  }
  return (
    <Section title="Community ratings and reviews">
      {!h && <p className="text-sm text-stone-500 mb-2">Not fetched yet.</p>}
      {h?.not_found && <p className="text-sm text-stone-500 mb-2">This book was not found on Hardcover.</p>}
      {h && !h.not_found && (
        <>
          <Rows rows={[['Rating', h.rating ? `${h.rating.toFixed(2)} / 5 from ${h.ratings_count?.toLocaleString()} ratings` : ''], ['Reviews', h.reviews_count != null ? String(h.reviews_count) : ''], ['Readers', h.readers != null ? `${h.readers.toLocaleString()} (${h.finished?.toLocaleString() ?? 0} finished)` : ''],
            ['Genres', h.genres?.join(' · ')], ['Moods', h.moods?.join(' · ')], ['Content warnings', h.content_warnings?.join(' · ')], ['Tags', h.tags?.slice(0, 12).join(' · ')], ['Pages', h.pages ? String(h.pages) : ''],
            ['Series', h.series?.map(s => `${s.name}${s.position != null ? ` #${s.position}` : ''}`).join(', ')], ['Audiobook editions', h.audiobook_editions?.map(e => [e.publisher, e.hours ? `${e.hours} h` : ''].filter(Boolean).join(' ')).filter(Boolean).join(' · ')]]} />
          {h.description && <p className="text-sm mt-3 text-stone-600 dark:text-stone-300">{h.description}</p>}
          {h.reviews && h.reviews.length > 0 && (
            <details className="mt-3 text-sm"><summary className="cursor-pointer">{h.reviews.length} top reviews</summary>
              <ul className="mt-2 space-y-3">{h.reviews.map((r, i) => (
                <li key={i}>
                  <div className="text-stone-500">{r.user}{r.rating != null ? ` · ${r.rating}★` : ''}{r.date ? ` · ${r.date}` : ''} · {r.likes} likes</div>
                  {r.spoilers ? <details><summary className="cursor-pointer text-stone-500">Contains spoilers</summary><p className="whitespace-pre-line">{r.text}</p></details> : <p className="whitespace-pre-line">{r.text.length > 700 ? r.text.slice(0, 700) + '…' : r.text}</p>}
                </li>))}
              </ul>
            </details>
          )}
        </>
      )}
      <div className="flex flex-wrap items-center gap-3 mt-3">
        <button type="button" className="rounded-md border border-stone-300 dark:border-stone-700 px-2 py-0.5 text-xs hover:border-blue-500 disabled:opacity-50" disabled={busy} onClick={refresh}>{hc ? 'Refresh from Hardcover' : 'Fetch from Hardcover'}</button>
        {hc && <span className="text-xs text-stone-500">fetched {hc.fetched.slice(0, 10)}</span>}
        <span className="text-xs text-stone-500">Data from <a className="underline" href={h?.url || 'https://hardcover.app'}>hardcover.app</a></span>
      </div>
      <Err msg={err} />
    </Section>
  )
}

export function Details({ d, onExtras }: { d: BookDetails; onExtras?: (e: Extras) => void }) {
  const b = d.book, sug = b.suggestion, p = d.probe
  return (
    <>
      <div className="flex gap-4 mb-4">
        <img src={coverUrl(b.id)} alt={`Cover of ${b.title}`} className="w-40 h-40 rounded object-cover bg-stone-200 dark:bg-stone-800 shrink-0" onError={e => { e.currentTarget.style.visibility = 'hidden' }} />
        <div>
          <h1 className="text-xl font-semibold">{b.title}</h1>
          <p className="text-stone-500">{b.author || 'unknown author'}{b.series ? ` · ${b.series}${b.series_position != null ? ` #${b.series_position}` : ''}` : ''}</p>
          <p className="text-sm mt-1">{[b.status, b.review !== 'confirmed' ? b.review : '', b.kind === 'radio' ? 'radio' : '', b.duration_seconds ? formatDuration(b.duration_seconds) : ''].filter(Boolean).join(' · ')}</p>
        </div>
      </div>

      <Section title="Catalogue">
        <Rows rows={[['ID', String(b.id)], ['Title', b.title], ['Author', b.author], ['Narrator', b.narrator], ['Series', b.series], ['Position', b.series_position != null ? String(b.series_position) : ''], ['Duration', b.duration_seconds ? formatDuration(b.duration_seconds) : ''],
          ['Status', b.status], ['Review', b.review], ['Kind', b.kind], ['ASIN', b.asin], ['Path', b.path && <span className="font-mono text-xs">{b.path}</span>], ['Added', b.added]]} />
      </Section>

      {d.audible && <AudibleSection a={d.audible} />}
      {b.review === 'confirmed' && b.kind === 'book' && <Community id={b.id} extras={d.extras} onChange={e => onExtras?.(e)} />}

      {sug?.title && (
        <Section title="Match suggestion">
          <Rows rows={[['Title', sug.title], ['Author', sug.author], ['Narrators', sug.narrators?.join(', ')], ['Series', sug.series ? `${sug.series}${sug.series_position != null ? ` #${sug.series_position}` : ''}` : ''], ['Runtime', sug.runtime_minutes ? formatDuration(sug.runtime_minutes * 60) : ''], ['ASIN', sug.asin],
            ['Matched by', sug.matched_by], ['Found by', sug.sources?.join(' + ')], ['Score', matchLabel(b.match_score)]]} />
          {sug.alternatives && sug.alternatives.length > 0 && (
            <details className="mt-2 text-sm"><summary className="cursor-pointer">{sug.alternatives.length} other candidate{sug.alternatives.length === 1 ? '' : 's'}</summary>
              <ul className="mt-1 space-y-0.5">{sug.alternatives.map((a, i) => <li key={i}>{a.title} <span className="text-stone-500">by {a.author || 'unknown'} · {a.source} · {Math.round(a.score * 100)}%</span></li>)}</ul>
            </details>
          )}
          <p className="text-xs text-stone-500 mt-2">Data from Audible, Audnexus and <a className="underline" href="https://audiobookdb.org">audiobookdb.org</a>.</p>
        </Section>
      )}

      {!d.path_exists && <Section title="Files"><p className="text-sm text-stone-500">{b.path ? 'The path does not exist on this machine.' : 'This book has no path (entered by hand).'}</p></Section>}
      {d.files && (
        <Section title="Files">
          <p className="text-sm mb-2">{d.files.count}{d.files.truncated ? '+' : ''} audio file{d.files.count === 1 ? '' : 's'} · {formatBytes(d.files.total_bytes)}</p>
          {p?.available && p.files_probed ? (
            <Rows rows={[['Audio length', p.duration_seconds ? `${formatDuration(p.duration_seconds)}${p.complete ? '' : ` (first ${p.files_probed} files)`}` : ''], ['Codec', p.codec ? `${p.codec}${p.bitrate_kbps ? `, ${p.bitrate_kbps} kbps` : ''}` : ''], ['Embedded chapters', p.chapters ? String(p.chapters) : ''], ['Embedded cover art', p.has_cover_art ? 'yes' : '']]} />
          ) : p && !p.available ? <p className="text-sm text-stone-500">Install ffmpeg to read tags and durations.</p> : null}
          <details className="mt-2 text-sm"><summary className="cursor-pointer">File list</summary>
            <ul className="mt-1 space-y-0.5">{d.files.list.map(f => <li key={f.name} className="flex justify-between gap-2"><span className="font-mono text-xs break-all">{f.name}</span><span className="text-stone-500">{formatBytes(f.bytes)}</span></li>)}</ul>
          </details>
        </Section>
      )}
      {p?.tags && Object.keys(p.tags).length > 0 && (
        <Section title="Embedded tags (first file)">
          <Rows rows={Object.entries(p.tags).map(([k, v]) => [k, v] as [string, React.ReactNode])} />
        </Section>
      )}
    </>
  )
}

export default function BookPage({ id }: { id: number }) {
  const { data, error } = useLoad(() => api.details(id), [id])
  const [extras, setExtras] = useState<Extras | null>(null)
  return (
    <div>
      <Crumbs items={[{ label: 'Library', to: href.library() }, { label: data?.book.title || 'Book' }]} />
      <Err msg={error} />
      {!data && !error && <p className="text-sm text-stone-500">Loading…</p>}
      {data && <Details d={extras ? { ...data, extras } : data} onExtras={setExtras} />}
    </div>
  )
}
