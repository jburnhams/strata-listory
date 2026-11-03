import { useEffect, useMemo, useState } from 'react'
import type { Book } from '../api'
import { btnQuiet } from './ui'

export function sourceFolder(path: string): string {
  const value = path.replaceAll('\\', '/').replace(/\/+$/, '')
  return /\.(mp3|m4[ab]|flac|aac|ogg|opus|wav|wma|aiff?|ape|mka|mp4)$/i.test(value) ? value.slice(0, value.lastIndexOf('/')) || '/' : value
}

export function inFolder(path: string, folder: string | null): boolean {
  if (folder === null) return true
  const raw = sourceFolder(path)
  const windows = /^[a-z]:/i.test(raw) || raw.startsWith('//')
  const source = windows ? raw.toLowerCase() : raw, target = windows ? folder.toLowerCase() : folder
  return source === target || !!target && source.startsWith(target.replace(/\/$/, '') + '/')
}

interface Node { path: string; label: string; children: Map<string, Node>; direct: boolean }
function tree(books: Book[]): Node[] {
  const roots = new Map<string, Node>()
  for (const book of books) {
    const folder = sourceFolder(book.path)
    if (!folder) continue
    let siblings = roots, current = folder.startsWith('//') ? '/' : '', node: Node | undefined
    for (const part of folder.split('/').filter(Boolean)) {
      current += (current || folder.startsWith('/') ? '/' : '') + part
      node = siblings.get(current)
      if (!node) { node = { path: current, label: part, children: new Map(), direct: false }; siblings.set(current, node) }
      siblings = node.children
    }
    if (node) node.direct = true
  }
  // Compress shared ancestors to avoid making the user expand /home/name/audio first.
  return [...roots.values()].map(root => {
    let node = root
    while (!node.direct && node.children.size === 1) node = [...node.children.values()][0]
    return { ...node, label: node.path }
  }).sort((a, b) => a.path.localeCompare(b.path))
}

export default function FolderTree({ books, selected, onSelect }: { books: Book[]; selected: string | null; onSelect: (folder: string | null) => void }) {
  const roots = useMemo(() => tree(books), [books])
  return <nav aria-label="Source folders" className="text-sm">
    <h2 className="font-semibold mb-2">Source folders</h2>
    <button type="button" className={`${btnQuiet} mb-2`} aria-pressed={selected === null} onClick={() => onSelect(null)}>All folders</button>
    <ul className="space-y-2">{roots.map(root => <FolderBranch key={root.path} node={root} selected={selected} onSelect={onSelect} root />)}</ul>
    {books.some(book => !book.path.trim()) && <button type="button" className={`${btnQuiet} mt-2`} aria-pressed={selected === ''} onClick={() => onSelect('')}>No source folder</button>}
  </nav>
}

function FolderBranch({ node, selected, onSelect, root = false }: { node: Node; selected: string | null; onSelect: (folder: string) => void; root?: boolean }) {
  const [expanded, setExpanded] = useState(root && node.children.size <= 50)
  useEffect(() => { if (selected && (selected === node.path || selected.startsWith(node.path + '/'))) setExpanded(true) }, [selected, node.path])
  return <li>
    <div className="flex items-center gap-1">
      {node.children.size > 0 && <button type="button" className="shrink-0 px-1" aria-label={`${expanded ? 'Collapse' : 'Expand'} ${node.path}`} aria-expanded={expanded} onClick={() => setExpanded(value => !value)}>{expanded ? '▾' : '▸'}</button>}
      <button type="button" className={`${btnQuiet} min-w-0 break-all text-left ${selected === node.path ? 'font-semibold text-blue-600' : ''}`} aria-pressed={selected === node.path} title={node.path} onClick={() => onSelect(node.path)}>{node.label}</button>
    </div>
    {expanded && node.children.size > 0 && <ul className="mt-1 pl-3 border-l border-stone-200 dark:border-stone-700 space-y-1">{[...node.children.values()].sort((a, b) => a.label.localeCompare(b.label)).map(child => <FolderBranch key={child.path} node={child} selected={selected} onSelect={onSelect} />)}</ul>}
  </li>
}
