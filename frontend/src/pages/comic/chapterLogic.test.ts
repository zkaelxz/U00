import { describe, expect, it } from 'vitest'

import type { ComicChapter, ComicPageInfo } from '../../types/comic'
import {
  chapterLabel,
  chapterRows,
  chapterStart,
  loadChapterPrefs,
  optionText,
  positionOf,
  saveChapterPrefs,
  scopeCounts,
  snapToVisible,
  visibleOrdinals,
  finishedChapter,
  nextUnread,
  withMarks,
} from './chapterLogic'

const page = (n: number, chapter: string, over: Partial<ComicPageInfo> = {}): ComicPageInfo => ({
  id: n, ordinal: n, width: 1, height: 1, has_rendered: false, has_regions: true, image_version: 1,
  chapter_id: chapter, ...over,
})
const ch = (id: string, title: string, first: number, count: number, hidden = 0, known = true): ComicChapter => ({
  id, title, known, first_page: first, page_count: count, hidden_count: hidden, url: '', read: false, bookmarked: false,
})

// Chapter a: pages 1-3 (page 1 hidden), chapter b: pages 4-5.
const pages = [page(1, 'a', { hidden: true }), page(2, 'a'), page(3, 'a'), page(4, 'b'), page(5, 'b', { has_regions: false })]
const chapters = [ch('a', 'Chapter 12', 1, 3, 1), ch('b', '', 4, 2)]
const prefs = { chapterOnly: false, showHidden: false }

describe('chapter labels', () => {
  it('uses the source title, then a number, and names the group with no data', () => {
    expect(chapterLabel(chapters[0], 0)).toBe('Chapter 12')
    expect(chapterLabel(chapters[1], 1)).toBe('Chapter 2')
    expect(chapterLabel(ch('unknown', '', 1, 9, 0, false), 0)).toBe('Chapter unknown')
  })
  it('counts the pages the reader will see', () => {
    expect(optionText(chapters[0], 0, false)).toBe('Chapter 12 (2 pages)')
    expect(optionText(chapters[0], 0, true)).toBe('Chapter 12 (3 pages)')
    expect(optionText(ch('x', 'One', 1, 1), 0, false)).toBe('One (1 page)')
  })
})

describe('the reading list', () => {
  it('drops hidden pages unless shown, and other chapters when reading one', () => {
    expect(visibleOrdinals(pages, prefs, null)).toEqual([2, 3, 4, 5])
    expect(visibleOrdinals(pages, { ...prefs, showHidden: true }, null)).toEqual([1, 2, 3, 4, 5])
    expect(visibleOrdinals(pages, { ...prefs, chapterOnly: true }, 'b')).toEqual([4, 5])
  })
  it('snaps to the nearest listed page, preferring the direction of travel', () => {
    expect(snapToVisible(1, [2, 3, 4], 1)).toBe(2)
    expect(snapToVisible(1, [2, 3, 4], -1)).toBe(2)
    expect(snapToVisible(3, [2, 4], 1)).toBe(4)
    expect(snapToVisible(3, [2, 4], -1)).toBe(2)
    expect(snapToVisible(7, [], 1)).toBe(7)
  })
  it('starts a chapter at its first listed page', () => {
    expect(chapterStart(chapters[0], [2, 3, 4, 5])).toBe(2)
    expect(chapterStart(chapters[1], [2, 3, 4, 5])).toBe(4)
  })
  it('reports the position in the chapter and overall', () => {
    expect(positionOf(pages, 3, [2, 3, 4, 5])).toEqual({ inChapter: 2, chapterTotal: 2, overall: 2, overallTotal: 4 })
    expect(positionOf(pages, 5, [2, 3, 4, 5])).toEqual({ inChapter: 2, chapterTotal: 2, overall: 4, overallTotal: 4 })
  })
})

describe('translate scopes', () => {
  it('never counts hidden pages and counts pages without text', () => {
    const s = scopeCounts(pages, chapters, 'a')
    expect(s.all).toEqual({ pages: 4, todo: 1, ids: [2, 3, 4, 5] })
    expect(s.chapter).toEqual({ id: 'a', label: 'Chapter 12', pages: 2, todo: 0, ids: [2, 3] })
  })
  it('has no chapter scope for a single chapter or none selected', () => {
    expect(scopeCounts(pages, [chapters[0]], 'a').chapter).toBeNull()
    expect(scopeCounts(pages, chapters, null).chapter).toBeNull()
  })
})

describe('chapter prefs', () => {
  it('default to continuous reading with hidden pages left out, and survive junk', () => {
    expect(loadChapterPrefs(null, 3)).toEqual(prefs)
    const data: Record<string, string> = { 'baihe.pref.comic.chapters.3': '{"chapterOnly":"yes","showHidden":true}' }
    const storage = { getItem: (k: string) => data[k] ?? null, setItem: (k: string, v: string) => { data[k] = v }, removeItem: () => {} }
    expect(loadChapterPrefs(storage, 3)).toEqual({ chapterOnly: false, showHidden: true })
    expect(saveChapterPrefs(storage, 4, { chapterOnly: true, showHidden: false })).toBe(true)
    expect(loadChapterPrefs(storage, 4)).toEqual({ chapterOnly: true, showHidden: false })
  })
})

describe('chapterRows', () => {
  const chap = (id: string, first: number, count: number, over: Partial<ComicChapter> = {}): ComicChapter => ({
    id, title: '', known: true, first_page: first, page_count: count, hidden_count: 0, url: '', read: false, bookmarked: false, ...over,
  })
  const page = (ordinal: number, chapter_id: string, over: Partial<ComicPageInfo> = {}): ComicPageInfo => ({
    id: ordinal, ordinal, width: 1, height: 1, has_rendered: false, has_regions: false, image_version: null, chapter_id, ...over,
  })

  it('lists each chapter with its page count and how many pages have text, opening on its first shown page', () => {
    const pages = [page(1, 'a', { hidden: true }), page(2, 'a', { has_regions: true }), page(3, 'a'), page(4, 'b', { has_regions: true })]
    const rows = chapterRows(pages, [chap('a', 1, 3, { title: 'Prologue', hidden_count: 1 }), chap('b', 4, 1)])
    expect(rows).toEqual([
      { id: 'a', label: 'Prologue', meta: '2 pages · 1 translated', page: 2 },
      { id: 'b', label: 'Chapter 2', meta: '1 page · 1 translated', page: 4 },
    ])
  })

  it('shows no list when the pages have no chapter data', () => {
    expect(chapterRows([page(1, 'unknown')], [chap('unknown', 1, 1, { known: false })])).toEqual([])
    expect(chapterRows([], [])).toEqual([])
  })
})

describe('read marks', () => {
  const chapters = [ch('a', 'One', 1, 2), { ...ch('b', 'Two', 3, 2), read: true }, ch('c', 'Three', 5, 1)]
  const pages = [page(1, 'a'), page(2, 'a'), page(3, 'b'), page(4, 'b'), page(5, 'c')]

  it('shows a tick for read and a star for bookmarked in the chapter list', () => {
    expect(optionText({ ...ch('a', 'One', 1, 2), read: true, bookmarked: true }, 0, false)).toBe('✓ One (2 pages) ★')
    expect(optionText(ch('a', 'One', 1, 2), 0, false)).toBe('One (2 pages)')
  })

  it('finds the next unread chapter after this one, then wraps to earlier ones', () => {
    expect(nextUnread(chapters, 0)).toBe(2)
    expect(nextUnread(chapters, 2)).toBe(0)
    expect(nextUnread(chapters.map((c) => ({ ...c, read: true })), 1)).toBe(-1)
    // The chapter being read is not offered as its own next.
    expect(nextUnread([ch('a', 'One', 1, 2)], 0)).toBe(-1)
  })

  it('counts a chapter finished only on its last listed page, and only once', () => {
    const all = [1, 2, 3, 4, 5]
    expect(finishedChapter(pages, chapters, [1], all)).toBeNull()
    expect(finishedChapter(pages, chapters, [2], all)).toBe('a')
    // A hidden last page is not in the list, so the page before it ends the chapter.
    expect(finishedChapter(pages, chapters, [1], [1, 3, 4, 5])).toBe('a')
    expect(finishedChapter(pages, chapters, [4], all)).toBeNull()
    // In a two-page spread the chapter can end on the second page.
    expect(finishedChapter(pages, chapters, [1, 2], all)).toBe('a')
    expect(finishedChapter(pages, chapters, [4, 5], all)).toBe('c')
  })

  it('lays the saved marks over the list and leaves other chapters alone', () => {
    const next = withMarks(chapters, [{ id: 'a', read: true, bookmarked: true }])
    expect(next.map((c) => [c.read, c.bookmarked])).toEqual([[true, true], [true, false], [false, false]])
  })
})
