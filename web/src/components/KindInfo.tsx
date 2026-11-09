// The question: "Is this a book, or radio / a play / a programme?" The finding: what the classifier decided, how sure it is, and the signals behind it (src/app/store/kinds.py), with a way to overrule it.
// Why it is shaped this way: the call is a score from many weak signals, so a card must show its reasons; a person's choice is saved and never recomputed.
import { useState } from 'react'
import { useQueryClient } from '@tanstack/react-query'
import { Button, Status, useAsyncAction, type Tone } from 'strata-react'
import { listenApi, type KindInfoData } from '../api'

export const KIND_LABEL: Record<string, string> = { book: 'Book', radio_series: 'Radio series', radio_show: 'Radio show', play: 'Play', other: 'Other', 'not-book': 'Radio, plays and programmes', review: 'Kind is a close call' }
/** The kinds a person may choose, in the order the buttons show them. */
const CHOICES = ['book', 'radio_series', 'radio_show', 'play'] as const

/** How sure the classifier is, in words and a tone: a call at the edges is shown as a call to review. */
export function kindCertainty(info: KindInfoData | null | undefined, kind: string | null | undefined): { tone: Tone; word: string } | null {
  if (!info) return null
  if (info.decided_by === 'user') return { tone: 'success', word: 'Your choice' }
  const pct = Math.round((info.band === 'book' || info.kind === 'book' && info.band === 'review' ? 1 - info.p_broadcast : info.p_broadcast) * 100)
  if (info.band === 'review') return { tone: 'warning', word: `Check the kind · ${Math.round(info.p_broadcast * 100)}% broadcast` }
  return { tone: kind === 'book' ? 'neutral' : 'info', word: `${pct}% sure` }
}

const sign = (w: number) => w > 0 ? `+${w}` : String(w)
/** Where a weight came from: learned from your answers (with the hand-set prior it moved from) or still the prior. */
export const origin = (s: { prior?: number; learned?: boolean }) => s.learned ? `learned, was ${sign(s.prior ?? 0)}` : s.prior !== undefined ? 'prior' : ''

/** The kind badge, its reasons (open to read) and buttons to overrule it. `onChanged` runs after a choice is saved. */
export default function KindInfo({ listenId, kind, info, onChanged }: { listenId: number; kind: string; info: KindInfoData | null | undefined; onChanged?: (kind: string) => void }) {
  const [open, setOpen] = useState(false)
  const qc = useQueryClient()
  const choose = useAsyncAction(async (k: string) => { await listenApi.setKind(listenId, k); void qc.invalidateQueries(); onChanged?.(k) })
  const sure = kindCertainty(info, kind)
  const signals = (info?.signals ?? []).filter(s => s.weight !== 0 || s.name === 'your decision' || s.name === 'audible disagrees')
  return (
    <div className="flex flex-col gap-1" aria-label="Kind of listen">
      <div className="flex flex-wrap items-center gap-2">
        <Status tone="neutral">{KIND_LABEL[kind] ?? kind}</Status>
        {sure && <Status tone={sure.tone}>{sure.word}</Status>}
        {info && info.band === 'review' && info.guess !== kind && <span className="type-caption text-muted-foreground">Could be: {KIND_LABEL[info.guess] ?? info.guess}</span>}
        <Button size="sm" variant="ghost" aria-expanded={open} onClick={() => setOpen(o => !o)}>{open ? 'Hide reasons' : 'Why?'}</Button>      {/* always: the panel also holds the buttons that correct a kind, even when no signal fired */}
      </div>
      {open && (
        <div className="rounded-surface border border-border bg-muted p-2">
          {signals.length === 0 && <p className="type-body-sm text-muted-foreground">No signal pointed either way: nothing in the names, tags, lengths or catalogues.</p>}
          <ul aria-label="Reasons for the kind" className="type-body-sm">
            {signals.map(s => <li key={s.name + s.detail} className="flex flex-wrap gap-x-2"><span className="tabular-nums text-muted-foreground">{sign(s.weight)}</span><span>{s.name}</span><span className="text-muted-foreground">{s.detail}</span>{origin(s) && <span className="type-caption text-muted-foreground">({origin(s)})</span>}</li>)}
          </ul>
          <p className="type-caption mt-1 text-muted-foreground">Positive weights point to radio, a play or a programme; negative to a book. Weights start from hand-set values and move as you answer.</p>
          <div className="mt-2 flex flex-wrap gap-2" role="group" aria-label="Set the kind">
            {info?.decided_by !== 'user' && <Button size="sm" variant="default" disabled={choose.phase === 'pending'} onClick={() => void choose.run(kind)}>Kind is right</Button>}
            {CHOICES.filter(k => k !== kind).map(k => <Button key={k} size="sm" variant="outline" disabled={choose.phase === 'pending'} onClick={() => void choose.run(k)}>{k === 'book' ? 'It is a book' : `It is a ${(KIND_LABEL[k] ?? k).toLowerCase()}`}</Button>)}
          </div>
          {choose.error && <p role="alert" className="type-caption text-destructive">{choose.error}</p>}
        </div>
      )}
    </div>
  )
}
