// The one question: "Where does the app read and write?" The finding: the three folders in use right now.
// Fields start empty with the current value as the placeholder, so saving with a blank field keeps what is set rather than clearing it.
import { useState, type FormEvent } from 'react'
import { useQueryClient } from '@tanstack/react-query'
import { AsyncButton, DataView, Field, Input, PageHeader, Skeleton, useAsyncAction, useLoad } from 'strata-react'
import { api } from '../api'

export default function SettingsPage() {
  const qc = useQueryClient()
  const load = useLoad({ queryKey: ['settings'], queryFn: api.settings })
  const [libraryDir, setLibraryDir] = useState(''), [dataDir, setDataDir] = useState(''), [backupDir, setBackupDir] = useState('')
  const current = load.status === 'ready' ? load.data : null
  const save = useAsyncAction(async () => {
    await api.saveSettings({ library_dir: libraryDir.trim() || current?.library_dir, data_dir: dataDir.trim() || current?.data_dir, backup_dir: backupDir.trim() || current?.backup_dir })
    setLibraryDir(''); setDataDir(''); setBackupDir(''); await qc.invalidateQueries({ queryKey: ['settings'] })
  }, { settleMs: 1500 })
  const onSubmit = (e: FormEvent) => { e.preventDefault(); void save.run() }
  return (
    <div className="space-y-5">
      <PageHeader title="Settings" scope="Folders"
        finding={current ? { text: <>Audiobooks are read from <span className="font-mono">{current.library_dir || '(not set)'}</span>. Data lives in <span className="font-mono">{current.data_dir}</span>.</> } : undefined} />
      <DataView state={load} isEmpty={() => false} skeleton={<Skeleton className="h-64 w-full max-w-xl" />} empty={null}>
        {d => (
          <form onSubmit={onSubmit} className="max-w-xl space-y-4">
            <Field label="Audiobook folder" hint={`Current: ${d.library_dir || 'not set'}`}><Input placeholder={d.library_dir || '/path/to/audiobooks'} value={libraryDir} onChange={e => setLibraryDir(e.target.value)} /></Field>
            <Field label="Data folder" hint={`Current: ${d.data_dir}`}><Input placeholder={d.data_dir || '/path/to/data'} value={dataDir} onChange={e => setDataDir(e.target.value)} /></Field>
            <Field label="Backup folder" hint={`Current: ${d.backup_dir}`}><Input placeholder={d.backup_dir || '/path/to/backups'} value={backupDir} onChange={e => setBackupDir(e.target.value)} /></Field>
            {save.error && <p role="alert" className="type-body-sm text-danger">{save.error}</p>}
            <AsyncButton type="submit" variant="default" phase={save.phase} error={save.error} labels={{ idle: 'Save settings', pending: 'Saving…', done: 'Saved' }} />
          </form>
        )}
      </DataView>
    </div>
  )
}
