import { describe, expect, it } from 'vitest'

import type { CharacterEntry } from '../../../types/translateStage'
import { mergeChoices, mergeSummary } from './mergeSpeakers'

const e = (label: string, over: Partial<CharacterEntry> = {}) =>
  ({ speaker_label: label, character_name: '', line_count: 2, series_character_id: null, ...over }) as CharacterEntry

describe('mergeChoices', () => {
  it('lists the other speakers with name and line count', () => {
    const rows = [e('A'), e('B', { character_name: 'Mei', line_count: 1 }), e('C')]
    expect(mergeChoices(rows, rows[0]).map((c) => c.text)).toEqual(['B (Mei), 1 line', 'C, 2 lines'])
  })

  it('blocks a speaker linked to a different series character only', () => {
    const rows = [e('A', { series_character_id: 1 }), e('B', { series_character_id: 2 }), e('C', { series_character_id: 1 }), e('D')]
    expect(mergeChoices(rows, rows[0]).map((c) => c.blocked !== null)).toEqual([true, false, false])
  })

  it('is empty with one speaker', () => {
    expect(mergeChoices([e('A')], e('A'))).toEqual([])
  })
})

describe('mergeSummary', () => {
  it('says how many lines move', () => {
    expect(mergeSummary(e('B', { line_count: 12 }), 'A')).toMatch(/^12 lines from B will move to A/)
    expect(mergeSummary(e('B', { line_count: 1 }), 'A')).toMatch(/^1 line from B/)
  })
})
