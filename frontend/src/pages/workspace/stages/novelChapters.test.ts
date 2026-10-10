import { describe, expect, it } from 'vitest'

import type { NovelChapterList, NovelChapterRow } from '../../../types/novelChapters'
import { chaptersHeadline, chaptersSummary, loadedLabel, neighbour, optionLabel, rowMeta, rowTitle, translationLine } from './novelChapters'

const list = (over: Partial<NovelChapterList> = {}): NovelChapterList => ({
  drama_id: 1, present: true, size_bytes: 10, split: true, total: 12, char_count: 48210,
  in_translation: 0, translation_chars: 0, offset: 0, limit: 100, chapters: [], ...over,
})
const row = (over: Partial<NovelChapterRow> = {}): NovelChapterRow => ({
  number: 3, title: '第3章', chars: 100, source: 'xbanxia', imported_at: '2026-10-08T01:02:03Z',
  unsplit: false, in_translation: false, ...over,
})

describe('chaptersHeadline', () => {
  it('counts chapters and characters', () => {
    expect(chaptersHeadline(list())).toBe('12 chapters, 48,210 characters')
    expect(chaptersHeadline(list({ total: 1, char_count: 1 }))).toBe('1 chapter, 1 character')
  })
  it('shows the empty state for a missing or empty file', () => {
    expect(chaptersHeadline(list({ present: false, total: 0, char_count: 0 }))).toBe(
      'No chapters saved yet. Import from Sources or paste text.',
    )
    expect(chaptersHeadline(list({ char_count: 0 }))).toContain('No chapters saved yet')
  })
  it('describes unsplit text without a chapter count', () => {
    expect(chaptersHeadline(list({ split: false, total: 1 }))).toBe('Unsplit text, 48,210 characters')
    expect(chaptersSummary(list({ split: false }))).toBe('unsplit')
  })
  it('summarises while loading and when empty', () => {
    expect(chaptersSummary(null)).toBe('checking')
    expect(chaptersSummary(list({ present: false, char_count: 0 }))).toBe('none saved')
    expect(chaptersSummary(list())).toBe('12 chapters')
  })
})

describe('translationLine', () => {
  it('says nothing when there are no saved chapters', () => {
    expect(translationLine(list({ present: false, char_count: 0 }))).toBe('')
  })
  it('reports no attached text', () => {
    expect(translationLine(list())).toBe('No text is attached for translation yet.')
  })
  it('counts chapters in the translation text', () => {
    expect(translationLine(list({ in_translation: 5, translation_chars: 20000, chapters: Array.from({ length: 12 }, () => row()) }))).toBe(
      'Translation text has 5 of 12 listed chapters (20,000 characters).',
    )
  })
  it('handles unsplit text', () => {
    expect(translationLine(list({ split: false, total: 1, translation_chars: 9, in_translation: 1 }))).toBe(
      'The saved text is in the translation text.',
    )
  })
})

describe('row helpers', () => {
  it('joins the known source and date', () => {
    expect(rowMeta(row())).toBe('xbanxia · 2026-10-08')
    expect(rowMeta(row({ source: '' }))).toBe('2026-10-08')
    expect(rowMeta(row({ source: '', imported_at: '' }))).toBe('')
  })
  it('falls back to Chapter N for an untitled chapter', () => {
    expect(rowTitle(row())).toBe('第3章')
    expect(rowTitle(row({ title: '' }))).toBe('Chapter 3')
  })
  it('labels a partly loaded slice', () => {
    expect(loadedLabel(20000, 150000)).toBe('20,000 of 150,000 characters')
    expect(loadedLabel(5, 5)).toBe('5 characters')
  })
})

describe('chapter selector helpers', () => {
  it('steps to the previous and next chapter and stops at both ends', () => {
    expect(neighbour(2, 5, -1)).toBe(1)
    expect(neighbour(2, 5, 1)).toBe(3)
    expect(neighbour(1, 5, -1)).toBeNull()
    expect(neighbour(5, 5, 1)).toBeNull()
  })
  it('labels an option with its number and the source title', () => {
    expect(optionLabel(row())).toBe('3. 第3章')
    expect(optionLabel(row({ title: '' }))).toBe('3. Chapter 3')
  })
})
