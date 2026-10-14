import { describe, expect, it } from 'vitest'

import {
  DEFAULT_PROFILE, MEDIA_TYPES, NEW_SERIES, RENAME_MAX, buildCreateRequest, canConfirmDelete, chosenProfile, deleteNotice, groupHistory,
  validateCreate, validateRename,
} from './libraryForm'

describe('validateRename', () => {
  it('needs a new, non-blank name within the limit', () => {
    expect(validateRename('  ', 'Old')).toMatch(/name/)
    expect(validateRename(' Old ', 'Old')).toMatch(/already/)
    expect(validateRename('x'.repeat(RENAME_MAX + 1), 'Old')).toMatch(/at most/)
    expect(validateRename(' New ', 'Old')).toBeNull()
  })
})

describe('buildCreateRequest', () => {
  const form = { source_language: 'zh', media_type: 'anime', title_en: ' A ', title_zh: '', author: '', director: 'D' }
  it('drops blank text and sends no series by default', () => {
    expect(buildCreateRequest(form, { series: '', newSeriesName: '', preset: '' })).toEqual({
      source_language: 'zh', media_type: 'anime', title_en: 'A', director: 'D',
    })
  })
  it('sends a trimmed summary (parity P06), and none when blank', () => {
    const extras = { series: '', newSeriesName: '', preset: '' }
    expect(buildCreateRequest({ ...form, summary: '  A story.  ' }, extras).summary).toBe('A story.')
    expect(buildCreateRequest({ ...form, summary: '   ' }, extras).summary).toBeUndefined()
  })
  it('sends series_id or new_series_name, never both, plus preset', () => {
    const a = buildCreateRequest(form, { series: '4', newSeriesName: 'x', preset: '2' })
    expect(a.series_id).toBe(4)
    expect(a.new_series_name).toBeUndefined()
    expect(a.preset_id).toBe(2)
    const b = buildCreateRequest(form, { series: NEW_SERIES, newSeriesName: 'Saga', preset: '' })
    expect(b.new_series_name).toBe('Saga')
    expect(b.series_id).toBeUndefined()
  })
  it('rejects a blank new series name', () => {
    const b = buildCreateRequest(form, { series: NEW_SERIES, newSeriesName: ' ', preset: '' })
    expect(validateCreate(b)).toMatch(/series/)
  })
})

describe('groupHistory', () => {
  it('collapses consecutive rows of one title and keeps the newest', () => {
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
  it('accepts an English title with a language', () => {
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
  it('allows a blank name when a link is given, and checks the link', () => {
    expect(validateCreate({ source_language: 'ja', source_url: 'https://www.youtube.com/watch?v=x' })).toBeNull()
    expect(validateCreate({ source_language: 'ja', source_url: 'youtube.com/x' })).toMatch(/http/)
    expect(validateCreate({ source_language: 'ja', source_url: 'https://a.example/' + 'x'.repeat(2000) })).toMatch(/too long/)
  })
})

describe('buildCreateRequest with a link', () => {
  it('sends the link and the date, trimmed, and leaves them out when blank', () => {
    const extras = { series: '', newSeriesName: '', preset: '' }
    expect(buildCreateRequest({ source_language: 'ja', source_url: ' https://v.example/w ', release_date: '2026-03-01' }, extras))
      .toEqual({ source_language: 'ja', source_url: 'https://v.example/w', release_date: '2026-03-01' })
    expect(buildCreateRequest({ source_language: 'ja', title_en: 'A', source_url: '  ', release_date: '' }, extras))
      .toEqual({ source_language: 'ja', title_en: 'A' })
  })
})

describe('canConfirmDelete', () => {
  it('needs the exact word', () => {
    expect(canConfirmDelete('DELETE')).toBe(true)
    expect(canConfirmDelete('delete')).toBe(false)
    expect(canConfirmDelete('DELETE ')).toBe(false)
  })
})

describe('MEDIA_TYPES', () => {
  it('does not offer music or other for new titles', () => {
    expect(MEDIA_TYPES).not.toContain('music')
    expect(MEDIA_TYPES).not.toContain('other')
    expect(MEDIA_TYPES).toContain('audio_drama')
  })
})

describe('deleteNotice', () => {
  it('shows the server warning only when one was sent', () => {
    expect(deleteNotice({ deleted: true, drama_id: 1 })).toBeNull()
    expect(deleteNotice({ deleted: true, drama_id: 1, warning: null })).toBeNull()
    expect(deleteNotice({ deleted: true, drama_id: 1, warning: '  ' })).toBeNull()
    expect(deleteNotice({ deleted: true, drama_id: 1, warning: 'Files left behind.' })).toBe('Files left behind.')
  })
})

describe('the New title profile select', () => {
  const items = [{ id: 1 }, { id: 2, is_default: true }]
  it('uses the default profile until one is picked, and "No profile" stays none', () => {
    expect(chosenProfile(DEFAULT_PROFILE, items)).toBe('2')
    expect(chosenProfile(DEFAULT_PROFILE, [{ id: 1 }])).toBe('')
    expect(chosenProfile(DEFAULT_PROFILE, undefined)).toBe('')
    expect(chosenProfile('1', items)).toBe('1')
    expect(chosenProfile('', items)).toBe('')
  })
  it('never sends the unpicked marker as an id', () => {
    const form = { source_language: 'zh', title_en: 'A' }
    expect(buildCreateRequest(form, { series: '', newSeriesName: '', preset: DEFAULT_PROFILE }).preset_id).toBeUndefined()
  })
})
