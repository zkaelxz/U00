import { describe, expect, it } from 'vitest'

import { chapterAt, chapterPage } from './ReaderChapters'

const rows = [
  { number: 1, title: 'Start', first_idx: 0, line_count: 50, translated: 50, reviewed: 10 },
  { number: 2, title: 'Fox', first_idx: 50, line_count: 30, translated: 12, reviewed: 0 },
  { number: 3, title: '', first_idx: 80, line_count: 20, translated: 0, reviewed: 0 },
]

describe('chapter jump', () => {
  it('maps a chapter start to the page that holds it', () => {
    expect(chapterPage(0, 40)).toBe(1)
    expect(chapterPage(50, 40)).toBe(2)
    expect(chapterPage(80, 40)).toBe(3)
  })

  it('names the chapter a page starts in', () => {
    expect(chapterAt(rows, 1, 40)?.number).toBe(1)
    expect(chapterAt(rows, 2, 40)?.number).toBe(1)
    expect(chapterAt(rows, 3, 40)?.number).toBe(3)
  })
})
