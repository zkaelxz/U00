import { describe, expect, it } from 'vitest'

import { importSummary, MAX_IMPORT_CHARS, validateImportText } from './glossaryImportForm'

const empty = { added: [], overwritten: [], skipped_existing: [], invalid: [], warnings: [] }

describe('validateImportText', () => {
  it('needs some text and caps the length', () => {
    expect(validateImportText('  ')).toMatch(/Paste/)
    expect(validateImportText('a,b')).toBeNull()
    expect(validateImportText('x'.repeat(MAX_IMPORT_CHARS + 1))).toMatch(/too long/)
  })
})

describe('importSummary', () => {
  it('names only the non-empty groups', () => {
    expect(importSummary({ ...empty, added: ['a'] })).toBe('Added 1 term.')
    expect(importSummary({ ...empty, added: ['a', 'b'], skipped_existing: ['c'] })).toBe('Added 2 terms, kept 1 existing.')
    expect(importSummary({ ...empty, overwritten: ['a'], invalid: ['x'] })).toBe('Added 0 terms, replaced 1, skipped 1 too long.')
  })
})
