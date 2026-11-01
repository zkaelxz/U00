import { describe, expect, it } from 'vitest'

import { isChunkLoadError, shouldReloadForChunk } from './chunkReload'

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

describe('isChunkLoadError', () => {
  it('recognises the failed page download in Chrome, Firefox and Safari wording', () => {
    expect(isChunkLoadError(new TypeError('Failed to fetch dynamically imported module: http://x/assets/Jobs-1.js'))).toBe(true)
    expect(isChunkLoadError(new TypeError('error loading dynamically imported module'))).toBe(true)
    expect(isChunkLoadError(new TypeError('Importing a module script failed.'))).toBe(true)
  })

  it('leaves a real render error alone', () => {
    expect(isChunkLoadError(new TypeError("Cannot read properties of undefined (reading 'x')"))).toBe(false)
    expect(isChunkLoadError('Failed to fetch dynamically imported module')).toBe(false)
  })
})
