import { describe, expect, it } from 'vitest'

import { globalShortcut } from './palette'

describe('globalShortcut', () => {
  it('"/" is search on the pages that have a search box', () => {
    for (const name of ['library', 'settings', 'sources', 'discover'] as const) {
      expect(globalShortcut('/', { name })).toBe('search')
    }
    expect(globalShortcut('/', { name: 'jobs' })).toBeNull()
  })

  it('"?" opens the list on every page but a title workspace, which has its own', () => {
    expect(globalShortcut('?', { name: 'library' })).toBe('sheet')
    expect(globalShortcut('?', { name: 'jobs' })).toBe('sheet')
    expect(globalShortcut('?', { name: 'drama', id: 1, stage: 'review' })).toBeNull()
    expect(globalShortcut('/', { name: 'drama', id: 1, stage: 'review' })).toBeNull()
  })

  it('ignores other keys', () => {
    expect(globalShortcut('j', { name: 'library' })).toBeNull()
  })
})
