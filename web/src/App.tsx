import { useEffect, useState } from 'react'
import { api } from './api'
import { useRoute, href } from './route'
import LibraryPage from './components/LibraryPage'
import BookPage from './components/BookPage'
import ProcessPage from './components/ProcessPage'
import IngestPage from './components/IngestPage'
import SettingsPage from './components/SettingsPage'
import FinishedPage from './components/FinishedPage'
import MatchReviewPage from './components/MatchReviewPage'

export default function App() {
  const r = useRoute()
  const [version, setVersion] = useState<string | null>(null)
  useEffect(() => { api.health().then(h => setVersion(h.version)).catch(() => {}) }, [])
  return (
    <div className="max-w-4xl mx-auto px-4 py-6">
      <header className="mb-5 flex flex-wrap items-baseline gap-3">
        <a href={href.library()} className="text-xl font-semibold">strata-listory</a>
        {version && <span className="text-xs text-stone-500 font-mono">v{version}</span>}
        <nav aria-label="Main navigation" className="w-full flex flex-wrap gap-x-4 gap-y-2 text-sm">
          <a href={href.library()} className="text-blue-600 dark:text-blue-400 hover:underline">Library</a>
          <a href={href.process()} className="text-blue-600 dark:text-blue-400 hover:underline">Processing</a>
          <a href={href.metadata()} className="text-blue-600 dark:text-blue-400 hover:underline">Book identification</a>
          <a href={href.automatic()} className="text-blue-600 dark:text-blue-400 hover:underline">Automatic approvals</a>
          <a href={href.shortlist()} className="text-blue-600 dark:text-blue-400 hover:underline">Match shortlist</a>
          <a href={href.audiobook()} className="text-blue-600 dark:text-blue-400 hover:underline">Audiobook identification</a>
          <a href={href.quality()} className="text-blue-600 dark:text-blue-400 hover:underline">Quality</a>
          <a href={href.proposed()} className="text-blue-600 dark:text-blue-400 hover:underline">Proposed</a>
          <a href={href.finished()} className="text-blue-600 dark:text-blue-400 hover:underline">Finished</a>
          <a href={href.settings()} className="text-blue-600 dark:text-blue-400 hover:underline">Settings</a>
        </nav>
      </header>
      {r.page === 'library' && <LibraryPage />}
      {r.page === 'book' && <BookPage id={r.id} />}
      {(r.page === 'metadata' || r.page === 'audiobook' || r.page === 'quality') && <IngestPage key={r.page} phase={r.page} />}
      {(r.page === 'automatic' || r.page === 'shortlist') && <MatchReviewPage key={r.page} mode={r.page} />}
      {r.page === 'process' && <ProcessPage />}
      {r.page === 'proposed' && <ProcessPage mode="proposed" />}
      {r.page === 'finished' && <FinishedPage />}
      {r.page === 'settings' && <SettingsPage />}
    </div>
  )
}
