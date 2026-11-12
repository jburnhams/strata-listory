// The one question: "What will this audiobook be called and tagged, and where does each value come from?" The finding: the listen's final configuration, field by field.
// Why it is shaped this way: every source offers its own value (the tags, the folder name, each Audible recording, Open Library); the person picks the source of each field here, and the plan for processing is made from exactly these picks.
import { useState } from 'react'
import { Link, useParams } from 'react-router'
import { useQueryClient } from '@tanstack/react-query'
import { AsyncButton, DataView, EmptyState, Input, PageHeader, Skeleton, Status, format, useAsyncAction, useLoad } from 'strata-react'
import { coverApi, listenApi, type ConfigField, type ConfigOption } from '../api'
import { hours } from './ListensPage'

const SOURCE: Record<string, string> = { tags: 'Tags', folder: 'Folder', audible: 'Audible', openlibrary: 'Open Library', typed: 'Typed', rule: 'Earlier', model: 'Earlier', import: 'Earlier' }
const BY: Record<string, string> = { user: 'chosen by you', rule: 'from the tags or folder name', model: 'chosen by the model', import: 'chosen earlier' }

function Option({ o, onPick }: { o: ConfigOption; onPick: () => void }) {
  return (
    <button type="button" aria-pressed={o.chosen} onClick={onPick}
      className={`flex w-full flex-wrap items-baseline gap-x-3 rounded-control border px-3 py-1.5 text-left transition-state hover:border-ring ${o.chosen ? 'border-ring bg-muted' : 'border-border'}`}>
      <span className="type-body">{String(o.value)}</span>
      <span className="type-caption text-muted-foreground">{o.sources.map(s => `${SOURCE[s.source] ?? s.source}${s.ref && s.source === 'audible' ? ` ${s.ref}` : ''}${s.note ? ` (${s.note})` : ''}`).join(' · ')}</span>
    </button>
  )
}

function Field({ f, listenId, onSaved }: { f: ConfigField; listenId: number; onSaved: () => void }) {
  const [typed, setTyped] = useState('')
  const pick = useAsyncAction(async (a: { value: string | number | null; source?: string }) => { await listenApi.chooseField(listenId, f.field, a.value, a.source ?? ''); setTyped(''); onSaved() }, { settleMs: 600 })
  return (
    <section aria-label={f.label} className="space-y-2 border-t border-border py-4">
      <div className="flex flex-wrap items-baseline gap-3">
        <h2 className="type-body font-medium">{f.label}</h2>
        <span className={f.value == null ? 'type-body text-muted-foreground' : 'type-body'}>{f.value == null ? 'not set' : String(f.value)}</span>
        {f.chosen_by && <Status tone={f.chosen_by === 'user' ? 'success' : 'neutral'}>{BY[f.chosen_by] ?? f.chosen_by}</Status>}
        {f.value != null && <button type="button" className="type-caption underline" onClick={() => void pick.run({ value: null })}>Clear</button>}
      </div>
      {f.options.length > 0 && <div className="space-y-1" role="group" aria-label={`Values for ${f.label}`}>{f.options.map(o => <Option key={String(o.value)} o={o} onPick={() => void pick.run({ value: o.value, source: o.sources.map(s => SOURCE[s.source] ?? s.source).join(', ') })} />)}</div>}
      <form className="flex flex-wrap items-center gap-2" onSubmit={e => { e.preventDefault(); if (typed.trim()) void pick.run({ value: typed.trim() }) }}>
        <Input aria-label={`Type a ${f.label.toLowerCase()}`} value={typed} onChange={e => setTyped(e.target.value)} placeholder={`Type a ${f.label.toLowerCase()}`} className="max-w-sm" />
        <AsyncButton type="submit" variant="outline" phase={pick.phase} error={pick.error} labels={{ idle: 'Use this', pending: 'Saving…', done: 'Saved' }} />
      </form>
      {pick.error && <p role="alert" className="type-body-sm text-danger">{pick.error}</p>}
    </section>
  )
}

export default function ListenPage() {
  const { id } = useParams()
  const listenId = Number(id)
  const qc = useQueryClient()
  const load = useLoad({ queryKey: ['listen-config', listenId], queryFn: () => listenApi.config(listenId) })
  const saved = () => { void qc.invalidateQueries({ queryKey: ['listen-config', listenId] }) }
  return (
    <div className="space-y-5">
      <nav aria-label="Breadcrumb" className="type-body-sm text-muted-foreground"><Link className="underline" to="/listens">Listens</Link> / {load.status === 'ready' ? load.data.label : '…'}</nav>
      <DataView state={load} isEmpty={() => false} empty={<EmptyState title="No such listen">It may have been merged into another.</EmptyState>} skeleton={<Skeleton className="h-64 w-full" />}>
        {d => (
          <>
            <PageHeader title={d.label} scope="Final configuration: where each value of the finished audiobook comes from"
              finding={{ text: [d.work ? `Matched to “${d.work.title}”${d.work.authors.length ? ` by ${d.work.authors.join(', ')}` : ''}` : 'Not matched to a work yet', `${format.count(d.recordings.length, 'Audible recording')}`, d.inputs.length ? `${format.count(d.inputs.reduce((n, i) => n + i.files, 0), 'file')}, ${hours(d.inputs[0]?.minutes ?? 0)}` : ''].filter(Boolean).join(' · ') }} />
            <section aria-label="Cover" className="flex items-center gap-4 rounded-surface border border-border bg-card p-3">
              {d.cover ? <img src={coverApi.image(d.cover.image_id, 160)} alt="Chosen cover" className="h-24 rounded-control" /> : <div className="grid h-24 w-16 place-items-center rounded-control bg-muted type-caption text-muted-foreground">none</div>}
              <div className="space-y-1">
                <p className="type-body">{d.cover ? `Cover chosen, ${d.cover.width}×${d.cover.height}` : 'No cover chosen yet'}</p>
                <Link className="type-body-sm underline" to={`/covers?state=all&q=${encodeURIComponent(d.label)}`}>{d.cover ? 'Change it on the Covers page' : 'Find and choose one on the Covers page'}</Link>
              </div>
            </section>
            <div>{d.fields.map(f => <Field key={f.field} f={f} listenId={listenId} onSaved={saved} />)}</div>
            {d.recordings.length > 0 && (
              <section aria-label="Audible recordings" className="space-y-2 border-t border-border pt-4">
                <h2 className="type-body font-medium">Audible recordings of this work</h2>
                <ul className="space-y-1 type-body-sm">{d.recordings.map(r => <li key={r.asin}>{r.asin} · {r.region.toUpperCase()} · {r.narrators.join(', ') || 'no narrator listed'}{r.runtime_minutes ? ` · ${hours(r.runtime_minutes)}` : ''}{r.release ? ` · ${r.release.slice(0, 4)}` : ''}{r.abridged ? ' · abridged' : ''}</li>)}</ul>
              </section>
            )}
            {d.inputs.length > 0 && (
              <section aria-label="Source files" className="space-y-2 border-t border-border pt-4">
                <h2 className="type-body font-medium">Source files</h2>
                <ul className="space-y-1 type-body-sm">{d.inputs.map(i => <li key={i.part_id}>{i.group_id ? <Link className="underline" to={`/files?group=${i.group_id}`}>{i.path ?? 'files'}</Link> : i.path} · {format.count(i.files, 'file')} · {hours(i.minutes)}{d.inputs.length > 1 ? ` · ${i.relation === 'part' ? 'part' : 'copy'}` : ''}</li>)}</ul>
              </section>
            )}
          </>
        )}
      </DataView>
    </div>
  )
}
