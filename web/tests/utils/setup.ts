import { afterAll, afterEach, beforeAll } from 'vitest'
import { cleanup } from '@testing-library/react'
import '@testing-library/jest-dom/vitest'
import { installLayoutShims } from 'strata-react/testing'
import { resetMock, server } from './server'

// strata-react's virtualised lists and tables measure their box (jsdom has no layout): give them one (and an in-memory localStorage where the runtime's is unusable, Node 25+).
installLayoutShims()

// jsdom: relative `/api/...` URLs resolve against jsdom's origin, so msw sees them as they are. An endpoint without a handler fails the test.
beforeAll(() => server.listen({ onUnhandledFrame: 'error' }))
afterEach(() => { server.resetHandlers(); cleanup(); resetMock(); window.location.hash = '' })
afterAll(() => server.close())
