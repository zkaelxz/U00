import { describe, expect, it } from 'vitest'

import { clearMessage, MODE_HELP, MODE_OPTIONS } from './readingSpeed'

describe('reading speed check', () => {
  it('offers Normal, Relaxed and Off, named for a person', () => {
    expect(MODE_OPTIONS.map((o) => o.value)).toEqual(['normal', 'relaxed', 'off'])
    expect(MODE_OPTIONS[1].label).toContain('fast talkers')
    expect(MODE_HELP).toContain('Off never flags')
  })

  it('reports the cleared count and where to undo', () => {
    expect(clearMessage({ cleared_count: 8609, flagged_count: 0, history_id: 4 }, false)).toBe(
      'Cleared 8609 reading-speed flags. Undo from Records, Line history.')
    expect(clearMessage({ cleared_count: 1, flagged_count: 0, history_id: 4 }, false)).toContain('1 reading-speed flag.')
  })

  it('reports the re-flag count for a re-check, and nothing to do', () => {
    expect(clearMessage({ cleared_count: 10, flagged_count: 1, history_id: 4 }, true)).toContain(
      'flagged 1 line again')
    expect(clearMessage({ cleared_count: 0, flagged_count: 0, history_id: null }, true)).toBe(
      'No reading-speed flags to clear.')
  })
})
