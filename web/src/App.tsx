// One question per area: Review (what do I need to confirm?), Library (what do I have, what should be ingested?), Pipeline (the older per-book approval stages, until identification decisions drive them),
// Processing (what is being turned into finished audiobooks?), Settings. Review is home because the identify model settles what it is sure of and leaves a ranked list of the rest.
import { useQuery } from '@tanstack/react-query'
import { Navigate, Route, Routes, useParams } from 'react-router'
import { AppShell, PageHeader, Tabs } from 'strata-react'
import { BookOpen, Library as LibraryIcon, ListChecks, Settings, SlidersHorizontal, Workflow } from 'lucide-react'
import { api } from './api'
import ReviewPage from './components/ReviewPage'
import LibraryPage from './components/LibraryPage'
import BookPage from './components/BookPage'
import ProcessPage from './components/ProcessPage'
import FinishedPage from './components/FinishedPage'
import IngestPage from './components/IngestPage'
import MatchReviewPage from './components/MatchReviewPage'
import SettingsPage from './components/SettingsPage'

const nav = [
  { to: '/', label: 'Review', icon: ListChecks, end: true },
  { to: '/library', label: 'Library', icon: LibraryIcon },
  { to: '/pipeline', label: 'Pipeline', icon: Workflow },
  { to: '/processing', label: 'Processing', icon: SlidersHorizontal },
  { to: '/settings', label: 'Settings', icon: Settings },
]

function Book() { const { id } = useParams(); return <BookPage id={Number(id)} /> }

function Pipeline() {
  return (
    <div className="space-y-5">
      <PageHeader title="Pipeline" scope="Per-book approval stages" finding={{ text: 'Identification decisions made in Review are saved as training labels. These stages still move each book through the ingest pipeline.' }} />
      <Tabs param="tab" label="Pipeline stage" items={[{ value: 'book', label: 'Book' }, { value: 'audiobook', label: 'Audiobook' }, { value: 'quality', label: 'Quality' }, { value: 'automatic', label: 'Automatic approvals' }, { value: 'shortlist', label: 'Match shortlist' }]}>
        {tab => tab === 'automatic' || tab === 'shortlist' ? <MatchReviewPage key={tab} mode={tab} />
          : <IngestPage key={tab} phase={tab === 'audiobook' || tab === 'quality' ? tab : 'metadata'} />}
      </Tabs>
    </div>
  )
}

function Processing() {
  return (
    <div className="space-y-5">
      <PageHeader title="Processing" scope="From approved plan to finished audiobook" />
      <Tabs param="tab" label="Processing view" items={[{ value: 'queue', label: 'Queue' }, { value: 'plans', label: 'Plans to approve' }, { value: 'finished', label: 'Finished' }]}>
        {tab => tab === 'finished' ? <FinishedPage /> : <ProcessPage key={tab} mode={tab === 'plans' ? 'proposed' : 'operations'} />}
      </Tabs>
    </div>
  )
}

// Paths from before the redesign keep working.
const legacy: [string, string][] = [['automatic-approvals', '/pipeline?tab=automatic'], ['match-shortlist', '/pipeline?tab=shortlist'], ['audiobook-identification', '/pipeline?tab=audiobook'], ['metadata', '/pipeline?tab=book'], ['ingest', '/pipeline?tab=book'], ['book-identification', '/pipeline?tab=book'], ['quality', '/pipeline?tab=quality'], ['process', '/processing'], ['proposed', '/processing?tab=plans'], ['finished', '/processing?tab=finished']]

export default function App() {
  const health = useQuery({ queryKey: ['health'], queryFn: api.health, staleTime: Infinity })
  return (
    <AppShell product={<span className="inline-flex items-center gap-2"><BookOpen aria-hidden className="size-5" />Strata Listory</span>} nav={nav} topBar={health.data ? <span className="type-caption text-muted-foreground font-mono">v{health.data.version}</span> : undefined}>
      <Routes>
        <Route path="/" element={<ReviewPage />} />
        <Route path="/library" element={<LibraryPage />} />
        <Route path="/book/:id" element={<Book />} />
        <Route path="/pipeline" element={<Pipeline />} />
        <Route path="/processing" element={<Processing />} />
        <Route path="/settings" element={<SettingsPage />} />
        {legacy.map(([from, to]) => <Route key={from} path={`/${from}`} element={<Navigate to={to} replace />} />)}
        <Route path="*" element={<Navigate to="/" replace />} />
      </Routes>
    </AppShell>
  )
}
