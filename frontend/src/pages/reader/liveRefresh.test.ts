import { describe, expect, it } from 'vitest'

import type { NovelLineChapterRow } from '../../types/novelChapters'
import { pageSignature, translatingNote } from './liveRefresh'

const ch = (number: number, first_idx: number, line_count: number, translated: number) =>
  ({ number, first_idx, line_count, translated, title: '', title_en: '', part: '', reviewed: 0 }) as unknown as NovelLineChapterRow

// Chapters of 40 lines each; the Reader shows 40 lines a page.
const chapters = [ch(1, 0, 40, 40), ch(2, 40, 40, 12), ch(3, 80, 40, 0)]

describe('pageSignature', () => {
  it('changes when a chapter on the page gains English', () => {
    const before = pageSignature(chapters, 40, 79)
    const after = pageSignature([chapters[0], ch(2, 40, 40, 20), chapters[2]], 40, 79)
    expect(after).not.toBe(before)
  })

  it('ignores chapters that are not on the page', () => {
    const before = pageSignature(chapters, 0, 39)
    const after = pageSignature([chapters[0], chapters[1], ch(3, 80, 40, 30)], 0, 39)
    expect(after).toBe(before)
  })

  it('covers every chapter a page spans', () => {
    expect(pageSignature(chapters, 0, 79)).toBe('1:40,2:12')
  })

  it('is null when there are no chapters', () => {
    expect(pageSignature([], 0, 39)).toBeNull()
  })
})

describe('translatingNote', () => {
  it('names the chapter being filled in', () => {
    expect(translatingNote(chapters)).toBe('Translating, chapter 2')
  })

  it('says only Translating before any chapter has English', () => {
    expect(translatingNote([ch(1, 0, 40, 0)])).toBe('Translating')
  })
})
