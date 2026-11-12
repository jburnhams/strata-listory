// The one question: "What is there to listen to?" The finding: how many listens the library holds, by kind.
// Why it is shaped this way: a listen is one audiobook or radio series, made from the groups the Files page shows, so this page is the list of them with search and a kind filter in the URL; each row jumps to its files.
import { useEffect, useState } from 'react'
import { Link, useSearchParams } from 'react-router'
import { DataTable, DataView, EmptyState, Field, Input, PageHeader, Skeleton, Status, Tabs, format, useLoad, type Column } from 'strata-react'
import { listenApi, type ListenRow } from '../api'
import { size } from './FilesPage'

import KindInfo, { KIND_LABEL } from './KindInfo'
export { KIND_LABEL }
const kindCount = (k: string, n: number) => `${n} ${n === 1 ? (KIND_LABEL[k] ?? k).toLowerCase() : k === 'radio_series' ? 'radio series' : `${(KIND_LABEL[k] ?? k).toLowerCase()}s`}`
export const hours = (minutes: number) => minutes >= 60 ? `${Math.floor(minutes / 60)}h ${String(minutes % 60).padStart(2, '0')}m` : minutes ? `${minutes}m` : ''

export default function ListensPage() {
  const [params, setParams] = useSearchParams()
  const q = params.get('q') ?? '', kind = params.get('kind') ?? '', matchTab = params.get('match') ?? '', match = matchTab === '' ? 'matched' : matchTab === 'all' ? '' : matchTab
  const [typed, setTyped] = useState(q)
  useEffect(() => { setTyped(q) }, [q])
  const setParam = (k: string, v: string) => setParams(p => { const n = new URLSearchParams(p); if (v) n.set(k, v); else n.delete(k); return n }, { replace: true })
  useEffect(() => {                                           // the URL follows the box after a short pause
    if (typed.trim() === q.trim()) return
    const t = setTimeout(() => setParam('q', typed.trim()), 250)
    return () => clearTimeout(t)
  }, [typed]) // eslint-disable-line react-hooks/exhaustive-deps
  const load = useLoad({ queryKey: ['listens', q, kind, match], queryFn: () => listenApi.list(q, kind, '', match) })
  const columns: Column<ListenRow>[] = [
    { key: 'label', header: 'Listen', sort: (a, b) => a.label.localeCompare(b.label), cell: l => <div className="min-w-0"><p>{l.group_id ? <Link className="underline" to={`/files?group=${l.group_id}`}>{l.label}</Link> : l.label}</p><p className="type-body-sm text-muted-foreground">{[l.author, l.narrator && `read by ${l.narrator}`, l.year].filter(Boolean).join(' · ')}</p><p className="type-caption text-muted-foreground break-all">{l.path}</p></div> },
    { key: 'kind', header: 'Kind', cell: l => <KindInfo listenId={l.id} kind={l.kind} info={l.kind_info} /> },
    { key: 'status', header: 'Status', cell: l => <div className="flex flex-wrap gap-1"><Status tone="info">{l.status}</Status>{l.parts > 0 && <Status tone="neutral">{`${l.parts} parts`}</Status>}{l.copies > 1 && <Status tone="neutral">{`${l.copies} copies`}</Status>}</div> },
    { key: 'files', header: 'Files', numeric: true, sort: (a, b) => a.files - b.files, cell: l => l.files },
    { key: 'length', header: 'Length', numeric: true, sort: (a, b) => a.minutes - b.minutes, cell: l => hours(l.minutes) },
    { key: 'size', header: 'Size', numeric: true, sort: (a, b) => a.bytes - b.bytes, cell: l => size(l.bytes) },
  ]
  const tabs = load.status === 'ready' ? load.data.kinds : {}
  const all = Object.values(tabs).reduce((a, b) => a + b, 0)
  return (
    <div className="space-y-5">
      <PageHeader title="Listens" scope="What there is to listen to" finding={load.status === 'ready' && all ? { text: `${format.count(all, 'listen')}: ${Object.entries(tabs).map(([k, n]) => kindCount(k, n)).join(', ')}` } : undefined} />
      <Field label="Search listens" hint="Every word must appear in the title, author, narrator or folder path">
        <Input type="search" value={typed} onChange={e => setTyped(e.target.value)} placeholder="e.g. lee child" />
      </Field>
      <Tabs param="match" label="Match state" items={[{ value: '', label: 'Confirmed and accepted' }, { value: 'confirmed', label: 'Confirmed by you' }, { value: 'accepted', label: 'Accepted by the model' }, { value: 'unmatched', label: 'Not matched yet' }, { value: 'all', label: 'Any match state' }]}>{() => null}</Tabs>
      <Tabs param="kind" label="Kind of listen" items={[{ value: '', label: 'All' }, ...Object.keys(tabs).map(k => ({ value: k, label: `${KIND_LABEL[k] ?? k} (${tabs[k]})` })), ...(load.status === 'ready' && load.data.not_book > 0 ? [{ value: 'not-book', label: `${KIND_LABEL['not-book']} (${load.data.not_book})` }] : []), ...(load.status === 'ready' && load.data.review > 0 ? [{ value: 'review', label: `${KIND_LABEL.review} (${load.data.review})` }] : [])]}>
        {() => (
          <DataView state={load} isEmpty={d => d.rows.length === 0} skeleton={<div className="space-y-3">{[0, 1, 2].map(i => <Skeleton key={i} className="h-10 w-full" />)}</div>}
            empty={<EmptyState title={q || kind ? 'No listens match' : 'No listens yet'}>{q || kind ? 'Try fewer words or another kind.' : <>Run <code>./app listens-build</code> after <code>./app filegroups</code> and <code>./app filegroups-structure</code>.</>}</EmptyState>}>
            {d => <><DataTable caption="Listens" rows={d.rows} columns={columns} rowKey={l => String(l.id)} />{d.total > d.rows.length && <p className="type-caption text-muted-foreground">Showing {d.rows.length} of {d.total}; search to narrow it.</p>}</>}
          </DataView>
        )}
      </Tabs>
    </div>
  )
}
