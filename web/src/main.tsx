import { StrictMode } from 'react'
import { createRoot } from 'react-dom/client'
import { HashRouter } from 'react-router'
import { StrataQueryProvider } from 'strata-react'
import './index.css'
import App from './App'

// Strata's dark variant is the `.dark` class; follow the system setting (the theme has both palettes).
const dark = window.matchMedia('(prefers-color-scheme: dark)')
const apply = () => document.documentElement.classList.toggle('dark', dark.matches)
apply(); dark.addEventListener('change', apply)

createRoot(document.getElementById('root')!).render(
  <StrictMode><StrataQueryProvider><HashRouter><App /></HashRouter></StrataQueryProvider></StrictMode>,
)
