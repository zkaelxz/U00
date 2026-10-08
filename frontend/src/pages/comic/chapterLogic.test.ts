import { describe, expect, it } from 'vitest'

import type { ComicChapter, ComicPageInfo } from '../../types/comic'
import {
  chapterLabel,
  chapterStart,
  loadChapterPrefs,
  optionText,
  positionOf,
  saveChapterPrefs,
  scopeCounts,
  snapToVisible,
  visibleOrdinals,
} from './chapterLogic'

const page = (n: number, chapter: string, over: Partial<ComicPageInfo> = {}): ComicPageInfo => ({
  id: n, ordinal: n, width: 1, height: 1, has_rendered: false, has_regions: true, image_version: 1,
  chapter_id: chapter, ...over,
})
const ch = (id: string, title: string, first: number, count: number, hidden = 0, known = true): ComicChapter => ({
  id, title, known, host: '', first_page: first, page_count: count, hidden_count: hidden,
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
    expect(s.all).toEqual({ pages: 4, todo: 1 })
    expect(s.chapter).toEqual({ id: 'a', label: 'Chapter 12', pages: 2, todo: 0 })
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
