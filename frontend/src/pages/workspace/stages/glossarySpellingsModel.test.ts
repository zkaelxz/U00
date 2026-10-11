import { describe, expect, it } from 'vitest'

import type { NameConsistency, SpellingSuggestStatus } from '../../../types/translateStage'
import { consistencySummary, spellingsEmptyText, statusForTerm } from './glossarySpellingsModel'

const done = (over: Partial<SpellingSuggestStatus['result'] & object> = {}): SpellingSuggestStatus => ({
  job_id: 'j', status: 'done', progress: 1, message: '', run_id: 'r',
  result: { term: 'ヤロ', english: 'Yalo', proposals: [], unusable: false, lines_scanned: 10, truncated: false, ...over },
})

describe('spellingsEmptyText', () => {
  it('says the answer could not be used', () => {
    expect(spellingsEmptyText(done({ unusable: true }))).toContain('could not be used')
  })
  it('says none of the guesses occur', () => {
    expect(spellingsEmptyText(done())).toContain('None of the model')
  })
  it('is null while running, idle or when proposals exist', () => {
    expect(spellingsEmptyText(null)).toBeNull()
    expect(spellingsEmptyText({ ...done(), status: 'running', result: null })).toBeNull()
    const p = { text: 'ヤロチ', count: 2, is_alias: false, is_term: false, examples: [] }
    expect(spellingsEmptyText(done({ proposals: [p] }))).toBeNull()
  })
})

describe('statusForTerm', () => {
  it('hides another term’s finished result', () => {
    expect(statusForTerm(done(), 'ヤロ')).not.toBeNull()
    expect(statusForTerm(done({ term: 'Other' }), 'ヤロ')).toBeNull()
  })
})

describe('consistencySummary', () => {
  const base: NameConsistency = {
    term: 'ヤロ', english: 'Yalo', available: true, reason: '', lines_with_name: 4, untranslated: 1,
    consistent: 3, differing: [], other_words: [], truncated: false,
  }
  it('covers each state', () => {
    expect(consistencySummary({ ...base, available: false, reason: 'Type the English name first.' })).toBe('Type the English name first.')
    expect(consistencySummary({ ...base, lines_with_name: 0 })).toContain('No line')
    expect(consistencySummary(base)).toContain('Every translated line')
    expect(consistencySummary({ ...base, differing: [{ line_idx: 1, source: 'x', english: 'y' }] })).toContain('1 of 4')
  })
})
