import { describe, expect, it } from 'vitest'

import type { CharacterEntry } from '../../../types/translateStage'
import { mergeChoices, mergeSummary, readMergeUndo, saveMergeUndo } from './mergeSpeakers'

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

describe('saved merge undo', () => {
  const handle = { id: 'opaque', source: 'B', target: 'A', expiresAt: 5000 }
  const store = () => {
    const m = new Map<string, string>()
    return { getItem: (k: string) => m.get(k) ?? null, setItem: (k: string, v: string) => void m.set(k, v), removeItem: (k: string) => void m.delete(k) }
  }

  it('round-trips the handle and forgets it once expired or cleared', () => {
    const s = store()
    saveMergeUndo(1, handle, s)
    expect(readMergeUndo(1, 1000, s)).toEqual(handle)
    expect(readMergeUndo(2, 1000, s)).toBeNull()
    expect(readMergeUndo(1, 5000, s)).toBeNull()
    saveMergeUndo(1, null, s)
    expect(readMergeUndo(1, 1000, s)).toBeNull()
  })

  it('ignores anything that is not a handle', () => {
    const s = store()
    s.setItem('baihe.characters.mergeUndo.1', JSON.stringify({ source_row: {}, previous: [] }))
    expect(readMergeUndo(1, 0, s)).toBeNull()
  })
})
