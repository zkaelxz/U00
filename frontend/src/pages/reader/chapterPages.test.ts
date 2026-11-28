import { describe, expect, it } from 'vitest'

import { chapterAt, chapterPage, firstLineOf, fixedStarts, lastLineOf, pageOfLine } from './chapterPages'

const rows = [
  { number: 1, title: 'Start', title_en: '', part: '', first_idx: 0, line_count: 50, translated: 50, reviewed: 10 },
  { number: 2, title: 'Fox', title_en: '', part: '', first_idx: 50, line_count: 30, translated: 12, reviewed: 0 },
  { number: 3, title: '', title_en: '', part: '', first_idx: 80, line_count: 20, translated: 0, reviewed: 0 },
]
// The server's pages for those chapters at 40 lines a page: each chapter opens a page.
const byChapter = [0, 40, 50, 80]

describe('chapter jump', () => {
  it('maps a chapter start to the page that holds it', () => {
    const fixed = fixedStarts(100, 40)
    expect(fixed).toEqual([0, 40, 80])
    expect(chapterPage(0, fixed)).toBe(1)
    expect(chapterPage(50, fixed)).toBe(2)
    expect(chapterPage(80, fixed)).toBe(3)
    expect(chapterPage(50, byChapter)).toBe(3)
    expect(firstLineOf(byChapter, chapterPage(50, byChapter))).toBe(50)
  })

  it('names the chapter a page starts in', () => {
    const fixed = fixedStarts(100, 40)
    expect(chapterAt(rows, 1, fixed)?.number).toBe(1)
    expect(chapterAt(rows, 2, fixed)?.number).toBe(1)
    expect(chapterAt(rows, 3, fixed)?.number).toBe(3)
    expect(chapterAt(rows, 3, byChapter)?.number).toBe(2)
  })

  it('finds a line\'s page and a page\'s first and last line', () => {
    expect(pageOfLine(byChapter, 0)).toBe(1)
    expect(pageOfLine(byChapter, 49)).toBe(2)
    expect(pageOfLine(byChapter, 99)).toBe(4)
    expect(lastLineOf(byChapter, 2, 100)).toBe(49)
    expect(lastLineOf(byChapter, 4, 100)).toBe(99)
    expect(fixedStarts(0, 40)).toEqual([0])
    expect(fixedStarts(90, 40)).toHaveLength(3)
    expect(fixedStarts(80, 40)).toHaveLength(2)
  })
})
