// The one question: "How were my audio files grouped?" The finding: how many files and folders the newest grouping covers.
// Why it is shaped this way: grouping files into listens is the first step to get right, so this page only browses the groups (folder, disc, book...) and the files in them, with the group in the URL so any level can be linked.
import { useState } from 'react'
import { Link, useSearchParams } from 'react-router'
import { Button, DataTable, DataView, EmptyState, PageHeader, Skeleton, Status, format, useLoad, type Column } from 'strata-react'
import { fileGroupApi, type FileGroup, type FileGroupDetail } from '../api'

export const length = (sec: number | null) => sec == null ? '' : `${Math.floor(sec / 3600) ? `${Math.floor(sec / 3600)}:` : ''}${String(Math.floor((sec % 3600) / 60)).padStart(Math.floor(sec / 3600) ? 2 : 1, '0')}:${String(Math.floor(sec % 60)).padStart(2, '0')}`
export const size = (bytes: number) => bytes >= 1e9 ? `${(bytes / 1e9).toFixed(1)} GB` : bytes >= 1e6 ? `${Math.round(bytes / 1e6)} MB` : `${Math.max(1, Math.round(bytes / 1e3))} KB`
const here = (id: number | null) => (id ? `?group=${id}` : '?')

export default function FilesPage() {
  const [params] = useSearchParams()
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
      <DataView state={load} isEmpty={d => !d.detail} skeleton={<div className="space-y-3">{[0, 1, 2].map(i => <Skeleton key={i} className="h-10 w-full" />)}</div>}
        empty={<EmptyState title="No files grouped yet">Run <code>./app filegroups</code> to scan the audiobook folder into the catalogue database and group it by folder.</EmptyState>}>
        {d => <Group detail={d.detail!} />}
      </DataView>
    </div>
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
