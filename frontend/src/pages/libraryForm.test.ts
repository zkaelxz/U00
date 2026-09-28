import { describe, expect, it } from 'vitest'

import { canConfirmDelete, validateCreate } from './libraryForm'

describe('validateCreate', () => {
  it('accepts a titled drama with a language', () => {
    expect(validateCreate({ source_language: 'zh', title_en: 'A' })).toBeNull()
  })
  it('requires language and a title', () => {
    expect(validateCreate({ source_language: '', title_en: 'A' })).toMatch(/language/)
    expect(validateCreate({ source_language: 'zh', title_en: '  ' })).toMatch(/title/)
  })
  it('enforces the API caps', () => {
    expect(validateCreate({ source_language: 'zh', title_en: 'x'.repeat(301) })).toMatch(/too long/)
    expect(
      validateCreate({ source_language: 'zh', title_en: 'A', summary: 'x'.repeat(5001) }),
    ).toMatch(/Summary/)
  })
})

describe('canConfirmDelete', () => {
  it('needs the exact word', () => {
    expect(canConfirmDelete('DELETE')).toBe(true)
    expect(canConfirmDelete('delete')).toBe(false)
    expect(canConfirmDelete('DELETE ')).toBe(false)
  })
})
