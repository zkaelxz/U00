import { describe, expect, it } from 'vitest'

import {
  cleanupDone,
  cleanupSummary,
  clipCaption,
  fmtSeconds,
  mergeFormDefaults,
  mergeSummary,
  parseMergeForm,
  resolveLineNumber,
  senseVoiceSummary,
  styleSummary,
} from './aiExtrasLogic'

describe('parseMergeForm', () => {
  it('parses the defaults', () => {
    expect(parseMergeForm(mergeFormDefaults())).toEqual({
      options: { min_duration: 1.2, max_gap: 0.5, max_chars: 80 },
      errors: {},
    })
  })

  it('flags out-of-range, blank and fractional values', () => {
    const r = parseMergeForm({ min_duration: '0', max_gap: '', max_chars: '80.5' })
    expect(r.options).toBeNull()
    expect(Object.keys(r.errors).sort()).toEqual(['max_chars', 'max_gap', 'min_duration'])
    expect(r.errors.max_chars).toMatch(/whole number/)
  })
})

describe('cleanup summaries', () => {
  it('says so when nothing needs fixing', () => {
    expect(cleanupSummary({ lines_scanned: 12, lines_changed: 0 })).toBe('No common errors found (12 lines checked).')
    expect(cleanupSummary({ lines_scanned: 12, lines_changed: 3 })).toBe('3 of 12 lines would change.')
  })

  it('reports lines kept after a mid-save edit and points at the undo', () => {
    expect(cleanupDone({ applied: 1, stale: 0, history_id: 9 })).toBe('Fixed 1 line. The previous text is in Records → Line history.')
    expect(cleanupDone({ applied: 2, stale: 1, history_id: 9 })).toContain('1 edited meanwhile and kept')
  })
})

describe('summaries', () => {
  it('merge', () => {
    expect(mergeSummary({ line_count_before: 5, line_count_after: 5, groups: [] })).toBe('No short lines to merge (5 lines).')
    expect(mergeSummary({ line_count_before: 5, line_count_after: 3, groups: [[1, 2], [3, 4]] })).toBe('5 → 3 lines: 2 merges.')
  })

  it('style', () => {
    const base = { drama_id: 1, scope: 'global', drama_edit_count: 0, min_samples: 8, history: [], message: null }
    expect(styleSummary({ ...base, edit_count: 3, profile: null })).toBe('3 of 8 edits needed to learn a style.')
    expect(styleSummary({ ...base, edit_count: 9, profile: null })).toBe('9 edits recorded. Nothing learned yet.')
    expect(
      styleSummary({
        ...base,
        edit_count: 9,
        profile: { summary: '', confidence: 'high', preferences: ['a', 'b'], sample_count: 9, updated_at: null, applied: false },
      }),
    ).toBe('2 preferences · from 9 edits · high confidence · paused')
  })

  it('sensevoice', () => {
    expect(senseVoiceSummary({ tagged: 0, disagree: 0 })).toBe('Not tagged yet.')
    expect(senseVoiceSummary({ tagged: 1, disagree: 1 })).toMatch(/^1 line tagged/)
  })
})

describe('resolveLineNumber', () => {
  const lines = [
    { id: 40, idx: 0 },
    { id: 41, idx: 1 },
  ]
  it('maps a displayed number to the permanent id', () => {
    expect(resolveLineNumber(lines, '2')).toEqual({ lineId: 41 })
    expect(resolveLineNumber(lines, ' #1 ')).toEqual({ lineId: 40 })
  })
  it('rejects junk and unknown numbers', () => {
    expect(resolveLineNumber(lines, 'abc')).toHaveProperty('error')
    expect(resolveLineNumber(lines, '3')).toEqual({ error: 'No line #3 in this drama.' })
    expect(resolveLineNumber(lines, '0')).toHaveProperty('error')
  })
})

describe('clip caption', () => {
  it('formats times and the line number', () => {
    expect(fmtSeconds(65.25)).toBe('1:05.3')
    expect(fmtSeconds(null)).toBe('–')
    expect(clipCaption({ line_id: 4, idx: 2, start: 1, end: 6.5, preset: 'Clean', created_at: null })).toBe(
      'Line #3 · 0:01.0–0:06.5 · Clean',
    )
    expect(clipCaption({ line_id: null, idx: null, start: null, end: null, preset: null, created_at: null })).toBe('Preview')
  })
})
