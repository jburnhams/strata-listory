import { describe, expect, it } from 'vitest'
import { renderHook, waitFor } from '@testing-library/react'
import { useLoad } from '../../src/route'

describe('useLoad (older pages)', () => {
  it('ignores a failure that arrives after the page has moved on', async () => {
    let fail: (e: Error) => void = () => {}
    const { result, rerender } = renderHook(({ id }) => useLoad(() => id === 1 ? new Promise<string>((_, reject) => { fail = reject }) : Promise.resolve('fresh'), [id]), { initialProps: { id: 1 } })
    rerender({ id: 2 })
    await waitFor(() => expect(result.current.data).toBe('fresh'))
    fail(new Error('too late'))
    await new Promise(r => setTimeout(r, 10))
    expect(result.current.error).toBeNull()
  })

  it('loads, reloads on demand, and reports a failure by its message', async () => {
    let n = 0
    const { result } = renderHook(() => useLoad(async () => { n += 1; if (n === 2) throw new Error('second load failed'); return n }, []))
    await waitFor(() => expect(result.current.data).toBe(1))
    result.current.reload()
    await waitFor(() => expect(result.current.error).toBe('second load failed'))
  })

  it('reports a failure that is not an Error', async () => {
    const { result } = renderHook(() => useLoad(() => Promise.reject({ message: '' }), []))
    await waitFor(() => expect(result.current.error).toBe('[object Object]'))
  })

  it('ignores an answer that arrives after the page has moved on', async () => {
    let release: (v: string) => void = () => {}
    const { result, rerender, unmount } = renderHook(({ id }) => useLoad(() => id === 1 ? new Promise<string>(r => { release = r }) : Promise.resolve('fast'), [id]), { initialProps: { id: 1 } })
    rerender({ id: 2 })
    await waitFor(() => expect(result.current.data).toBe('fast'))
    release('slow')
    await new Promise(r => setTimeout(r, 10))
    expect(result.current.data).toBe('fast')
    unmount()
  })
})
