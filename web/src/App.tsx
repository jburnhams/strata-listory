import { useEffect, useState } from 'react'
import { api } from './api'
import { useRoute, href } from './route'
import LibraryPage from './components/LibraryPage'
import BookPage from './components/BookPage'
import SettingsPage from './components/SettingsPage'

export default function App() {
  const r = useRoute()
  const [version, setVersion] = useState<string | null>(null)
  useEffect(() => { api.health().then(h => setVersion(h.version)).catch(() => {}) }, [])
  return (
    <div className="max-w-4xl mx-auto px-4 py-6">
      <header className="mb-5 flex items-baseline gap-3">
        <a href={href.library()} className="text-xl font-semibold">strata-listory</a>
        {version && <span className="text-xs text-stone-500 font-mono">v{version}</span>}
        <nav className="ml-auto flex gap-4 text-sm">
          <a href={href.library()} className="text-blue-600 dark:text-blue-400 hover:underline">Library</a>
          <a href={href.settings()} className="text-blue-600 dark:text-blue-400 hover:underline">Settings</a>
        </nav>
      </header>
      {r.page === 'library' && <LibraryPage />}
      {r.page === 'book' && <BookPage id={r.id} />}
      {r.page === 'settings' && <SettingsPage />}
    </div>
  )
}
