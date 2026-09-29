import { describe, expect, it } from 'vitest'

import { canConfirmDelete, groupHistory, validateCreate } from './libraryForm'

describe('groupHistory', () => {
  it('collapses consecutive rows of one drama and keeps the newest', () => {
    const rows = [
      { drama_id: 1, at: 'c' },
      { drama_id: 1, at: 'b' },
      { drama_id: 2, at: 'a' },
      { drama_id: 1, at: '0' },
    ]
    const g = groupHistory(rows)
    expect(g.map((x) => [x.entry.drama_id, x.count, x.entry.at])).toEqual([
      [1, 2, 'c'],
      [2, 1, 'a'],
      [1, 1, '0'],
    ])
  })
  it('handles an empty list', () => expect(groupHistory([])).toEqual([]))
})

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
