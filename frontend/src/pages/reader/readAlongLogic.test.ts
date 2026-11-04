import { describe, expect, it } from 'vitest'

import type { ReviewLine } from '../../types/review'
import { activeIndex, alongLines } from './readAlongLogic'

const line = (id: number, start: number, zh: string, en: string): ReviewLine => ({
  id, idx: id, start, end: start + 1, zh, en, speaker: null, speaker_manual: false, sfx: false,
  flag: null, flag_note: null, dub_filename: null, lang: null, reviewed: false, confidence: null,
})

describe('alongLines', () => {
  const rows = [line(2, 5, '二', 'Two'), line(1, 0, '一', 'One'), line(3, 9, '三', '')]

  it('shows the source text when the voice speaks the original language, in time order', () => {
    expect(alongLines(rows, 'original').map((l) => l.text)).toEqual(['一', '二', '三'])
  })

  it('shows the translation otherwise and skips a line with no translation', () => {
    expect(alongLines(rows, 'translation').map((l) => l.text)).toEqual(['One', 'Two'])
  })
})

describe('activeIndex', () => {
  const lines = [{ id: 1, start: 0, text: 'a' }, { id: 2, start: 5, text: 'b' }, { id: 3, start: 9, text: 'c' }]

  it('is the last line that has started', () => {
    expect(activeIndex(lines, 0)).toBe(0)
    expect(activeIndex(lines, 4.9)).toBe(0)
    expect(activeIndex(lines, 5)).toBe(1)
    expect(activeIndex(lines, 100)).toBe(2)
  })

  it('keeps a line lit through the pause after it', () => {
    expect(activeIndex(lines, 7)).toBe(1)
  })

  it('is null before the first line starts', () => {
    expect(activeIndex([{ id: 1, start: 2, text: 'a' }], 0.5)).toBeNull()
    expect(activeIndex([], 3)).toBeNull()
  })
})
