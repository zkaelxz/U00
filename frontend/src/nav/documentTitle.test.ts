import { describe, expect, it } from 'vitest'

import { formatDocumentTitle } from './documentTitle'

describe('formatDocumentTitle', () => {
  it('puts the page first and the app name last', () => {
    expect(formatDocumentTitle('Settings')).toBe('Settings · Baihe Studio')
    expect(formatDocumentTitle('  Signal \n ')).toBe('Signal · Baihe Studio')
  })

  it('is just the app name when there is no page heading', () => {
    expect(formatDocumentTitle(null)).toBe('Baihe Studio')
    expect(formatDocumentTitle('')).toBe('Baihe Studio')
    expect(formatDocumentTitle('Baihe Studio')).toBe('Baihe Studio')
  })
})
