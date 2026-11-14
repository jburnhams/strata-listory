// The one question: "How were my audio files grouped?" The finding: how many files and folders the newest grouping covers.
// Why it is shaped this way: grouping files into listens is the first step to get right, so this page only browses the groups (folder, disc, book...) and the files in them, with the group in the URL so any level can be linked.
import { useEffect, useState } from 'react'
import { useQueryClient } from '@tanstack/react-query'
import { Link, useNavigate, useSearchParams } from 'react-router'
import { AsyncButton, Button, DataTable, DataView, EmptyState, Field, Input, PageHeader, Skeleton, Status, format, toast, useAsyncAction, useLoad, type Column } from 'strata-react'
import { api, fileGroupApi, type FileGroup, type FileGroupDetail, type FileSearch } from '../api'

export const length = (sec: number | null) => sec == null ? '' : `${Math.floor(sec / 3600) ? `${Math.floor(sec / 3600)}:` : ''}${String(Math.floor((sec % 3600) / 60)).padStart(Math.floor(sec / 3600) ? 2 : 1, '0')}:${String(Math.floor(sec % 60)).padStart(2, '0')}`
export const size = (bytes: number) => bytes >= 1e9 ? `${(bytes / 1e9).toFixed(1)} GB` : bytes >= 1e6 ? `${Math.round(bytes / 1e6)} MB` : `${Math.max(1, Math.round(bytes / 1e3))} KB`
const here = (id: number | null) => (id ? `?group=${id}` : '?')

export default function FilesPage() {
  const [params, setParams] = useSearchParams()
  const q = params.get('q') ?? ''
  const [typed, setTyped] = useState(q)
  useEffect(() => { setTyped(q) }, [q])
  useEffect(() => {                                           // the URL follows the box after a short pause, so a search can be linked and the back button works
    if (typed.trim() === q.trim()) return
    const t = setTimeout(() => setParams(p => { const n = new URLSearchParams(p); if (typed.trim()) n.set('q', typed.trim()); else n.delete('q'); return n }, { replace: true }), 250)
    return () => clearTimeout(t)
  }, [typed]) // eslint-disable-line react-hooks/exhaustive-deps
  const wanted = Number(params.get('group')) || null
  const load = useLoad({
    queryKey: ['filegroup', wanted],
    queryFn: async () => {
      const top = wanted ? null : await fileGroupApi.root()
      const id = wanted ?? top?.root?.id ?? null
      return { detail: id ? await fileGroupApi.group(id) : null, groupings: top?.groupings ?? 0 }
    },
  })
  return (
    <div className="space-y-5">
      <PageHeader title="Files" scope="How the audio files are grouped" finding={load.status === 'ready' && load.data.detail ? { text: `${format.count(load.data.detail.group.file_count, 'file')} in ${format.count(load.data.detail.group.group_count, 'group')}, ${size(load.data.detail.group.total_bytes)}` } : undefined} />
      {!wanted && !q && <Roots />}
      <Field label="Search files and folders" hint="Every word must appear in the name or folder path">
        <Input type="search" value={typed} onChange={e => setTyped(e.target.value)} placeholder="e.g. lee child persuader" />
      </Field>
      {q.trim().length >= 2 ? <Results q={q.trim()} /> : (
      <DataView state={load} isEmpty={d => !d.detail} skeleton={<div className="space-y-3">{[0, 1, 2].map(i => <Skeleton key={i} className="h-10 w-full" />)}</div>}
        empty={<EmptyState title="No files grouped yet">Run <code>./app filegroups</code> to scan the audiobook folder into the catalogue database and group it by folder.</EmptyState>}>
        {d => <Group detail={d.detail!} />}
      </DataView>)}
    </div>
  )
}

/** The folders the app reads audio from. The first one is the library folder in Settings; more can be added here. A rescan (`./app filegroups`) groups every folder into one listing, so a root is a top-level row of this page. */
function Roots() {
  const qc = useQueryClient()
  const load = useLoad({ queryKey: ['settings'], queryFn: api.settings })
  const [adding, setAdding] = useState(''), [open, setOpen] = useState(false)
  const save = useAsyncAction(async (dirs: string[]) => {
    await api.saveSettings({ library_dirs: dirs })
    setAdding(''); await qc.invalidateQueries({ queryKey: ['settings'] })
  }, { settleMs: 1500 })
  // One scan per folder: a job on the server's queue (the same as `./app filegroups`, but only this folder is walked again). The button greys while it runs; a toast says it is queued (with a way to the Processing page) and another when it finishes.
  const navigate = useNavigate()
  const scan = useAsyncAction(async (folder: string) => {
    toast.show({ title: 'Scan queued', description: `${folder} is being read into the listing. It runs in the background.`, action: { label: 'Open Processing', onPress: () => void navigate('/processing') } })
    const r = await api.scanFolder(folder)
    await qc.invalidateQueries({ predicate: q => String(q.queryKey[0]).startsWith('filegroup') })
    toast.show({ title: 'Scan finished', tone: 'success', description: `${r.files.toLocaleString()} files in the listing.` })
  }, { settleMs: 1500 })
  const roots = load.status === 'ready' ? load.data.library_dirs : []
  const primary = load.status === 'ready' ? load.data.library_dir : ''
  const extra = roots.filter(r => r !== primary)
  return (
    <section aria-label="Folders scanned" className="rounded-surface border border-border bg-card p-3">
      <Button variant="ghost" aria-expanded={open} onClick={() => setOpen(!open)}>Folders scanned ({roots.length || 'none yet'})</Button>
      {open && <div className="mt-3 space-y-3">
        <ul className="space-y-1">{roots.map(r => <li key={r} className="flex items-center gap-3"><span className="font-mono type-body-sm break-all">{r}</span><Button variant="ghost" disabled={scan.phase === 'pending'} onClick={() => void scan.run(r)}>{scan.phase === 'pending' ? 'Scanning…' : 'Scan'}<span className="sr-only"> {r}</span></Button>{r === primary ? <Status tone="neutral">library folder</Status> : <Button variant="ghost" onClick={() => void save.run(extra.filter(x => x !== r))}>Remove<span className="sr-only"> {r}</span></Button>}</li>)}</ul>
        <form className="flex items-end gap-3" onSubmit={e => { e.preventDefault(); if (adding.trim()) void save.run([...extra, adding.trim()]) }}>
          <Field label="Add a folder" hint="An absolute path; press Scan to read it into the listing"><Input value={adding} onChange={e => setAdding(e.target.value)} placeholder="/path/to/more/audio" /></Field>
          <AsyncButton type="submit" variant="default" phase={save.phase} error={save.error} labels={{ idle: 'Add folder', pending: 'Saving…', done: 'Saved' }} />
        </form>
        {scan.error && <p role="alert" className="type-body-sm text-danger">{scan.error}</p>}
        {save.error && <p role="alert" className="type-body-sm text-danger">{save.error}</p>}
      </div>}
    </section>
  )
}

/** Matches for a search: groups by name or folder path, files by path. Every row jumps to the folder (the `group` param) and drops the search. */
function Results({ q }: { q: string }) {
  const load = useLoad({ queryKey: ['filesearch', q], queryFn: () => fileGroupApi.search(q) })
  const groupCols: Column<FileGroup>[] = [
    { key: 'label', header: 'Group', cell: g => <Link className="underline" to={here(g.id)}>{g.label}</Link> },
    { key: 'kind', header: 'Kind', cell: g => <Status tone="neutral">{g.kind}</Status> },
    { key: 'path', header: 'Folder', cell: g => <span className="type-body-sm text-muted-foreground break-all">{g.path}</span> },
    { key: 'files', header: 'Files', numeric: true, cell: g => g.file_count },
  ]
  const fileCols: Column<FileSearch['files'][number]>[] = [
    { key: 'name', header: 'File', cell: f => <Link className="underline" to={here(f.group_id)}>{f.name}</Link> },
    { key: 'group', header: 'In', cell: f => f.group_label },
    { key: 'path', header: 'Path', cell: f => <span className="type-body-sm text-muted-foreground break-all">{f.path}</span> },
  ]
  return (
    <DataView state={load} isEmpty={r => r.groups.length + r.files.length === 0} skeleton={<Skeleton className="h-24 w-full" />}
      empty={<EmptyState title="Nothing matches">No group or file has all of: {q}</EmptyState>}>
      {r => (
        <div className="space-y-5">
          {r.groups.length > 0 && <section aria-label="Groups matching the search"><DataTable caption="Matching groups" rows={r.groups} columns={groupCols} rowKey={g => String(g.id)} />{r.more.groups > 0 && <p className="type-caption text-muted-foreground">{r.more.groups} more groups match; add a word to narrow it.</p>}</section>}
          {r.files.length > 0 && <section aria-label="Files matching the search"><DataTable caption="Matching files" rows={r.files} columns={fileCols} rowKey={f => `${f.id}-${f.group_id}`} />{r.more.files > 0 && <p className="type-caption text-muted-foreground">{r.more.files} more files match; add a word to narrow it.</p>}</section>}
        </div>
      )}
    </DataView>
  )
}

function Group({ detail }: { detail: FileGroupDetail }) {
  const [open, setOpen] = useState<number | null>(null)
  const { group, ancestors, children, files } = detail
  const groupCols: Column<FileGroup & { ordinal: number }>[] = [
    { key: 'label', header: 'Group', sort: (a, b) => a.label.localeCompare(b.label), cell: g => <Link className="underline" to={here(g.id)}>{g.label}</Link> },
    { key: 'kind', header: 'Kind', cell: g => <Status tone="neutral">{g.kind}</Status> },
    { key: 'files', header: 'Files', numeric: true, sort: (a, b) => a.file_count - b.file_count, cell: g => g.file_count },
    { key: 'groups', header: 'Groups below', numeric: true, sort: (a, b) => a.group_count - b.group_count, cell: g => g.group_count },
    { key: 'size', header: 'Size', numeric: true, sort: (a, b) => a.total_bytes - b.total_bytes, cell: g => size(g.total_bytes) },
  ]
  const fileCols: Column<FileGroupDetail['files'][number]>[] = [
    { key: 'ordinal', header: '#', numeric: true, cell: f => f.ordinal },
    { key: 'name', header: 'File', cell: f => <span title={f.path ?? ''}>{f.name}</span> },
    { key: 'length', header: 'Length', numeric: true, cell: f => length(f.duration_s) },
    { key: 'codec', header: 'Codec', cell: f => f.codec ?? (f.probe_error ? 'unreadable' : 'not probed') },
    { key: 'tags', header: 'Tags', cell: f => <span className="type-body-sm text-muted-foreground">{[f.tags.title, f.tags.artist ?? f.tags.album_artist, f.tags.album].filter(Boolean).join(' · ')}</span> },
    { key: 'size', header: 'Size', numeric: true, cell: f => size(f.size) },
    { key: 'meta', header: 'Metadata', cell: f => <Button variant="ghost" onClick={() => setOpen(open === f.id ? null : f.id)}>{open === f.id ? 'Hide' : 'Show'}<span className="sr-only"> metadata of {f.name}</span></Button> },
  ]
  return (
    <div className="space-y-5">
      <nav aria-label="Group path" className="type-body-sm text-muted-foreground">
        {ancestors.map(a => <span key={a.id}><Link className="underline" to={here(a.id)}>{a.label}</Link> / </span>)}
        <b className="text-foreground">{group.label}</b> <Status tone="neutral">{group.kind}</Status>
      </nav>
      {children.length > 0 && <section aria-label="Groups in this group"><DataTable caption="Groups" rows={children} columns={groupCols} rowKey={g => String(g.id)} /></section>}
      {files.length > 0 && <section aria-label="Files in this group"><DataTable caption="Files" rows={files} columns={fileCols} rowKey={f => String(f.ordinal)} />{open != null && <FileMetadata id={open} />}</section>}
    </div>
  )
}

/** Everything stored for one file: its hash, where it has been seen and the complete ffprobe output. */
function FileMetadata({ id }: { id: number }) {
  const load = useLoad({ queryKey: ['filegroup-file', id], queryFn: () => fileGroupApi.file(id) })
  return (
    <DataView state={load} isEmpty={() => false} empty={null} skeleton={<Skeleton className="h-24 w-full" />}>
      {f => (
        <div className="mt-3 space-y-2 rounded-surface border border-border bg-card p-3" role="group" aria-label="File metadata">
          <p className="type-body-sm font-mono break-all">sha256 {f.sha256}</p>
          <ul className="type-body-sm text-muted-foreground">{f.locations.map(l => <li key={l.path}>{l.path}{l.gone ? ' (no longer there)' : ''}</li>)}</ul>
          {f.probe_error && <p role="alert">ffprobe could not read this file: {f.probe_error}</p>}
          {f.probe && <pre className="type-caption max-h-96 overflow-auto font-mono">{JSON.stringify(f.probe, null, 2)}</pre>}
        </div>
      )}
    </DataView>
  )
}
