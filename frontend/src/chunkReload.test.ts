import { describe, expect, it } from 'vitest'

import { shouldReloadForChunk } from './chunkReload'

function memory() {
  const data = new Map<string, string>()
  return { getItem: (k: string) => data.get(k) ?? null, setItem: (k: string, v: string) => void data.set(k, v) }
}

describe('shouldReloadForChunk', () => {
  it('reloads once, then leaves a repeat failure to the error boundary', () => {
    const s = memory()
    expect(shouldReloadForChunk(s, 1_000_000)).toBe(true)
    expect(shouldReloadForChunk(s, 1_010_000)).toBe(false)
  })

  it('reloads again once the guard window has passed', () => {
    const s = memory()
    expect(shouldReloadForChunk(s, 1_000_000)).toBe(true)
    expect(shouldReloadForChunk(s, 1_031_000)).toBe(true)
  })

  it('never reloads without storage, since nothing could stop a loop', () => {
    expect(shouldReloadForChunk(null, 1_000_000)).toBe(false)
    const throwing = { getItem: () => { throw new Error('blocked') }, setItem: () => {} }
    expect(shouldReloadForChunk(throwing, 1_000_000)).toBe(false)
  })
})
