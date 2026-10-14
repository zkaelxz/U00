import { describe, expect, it } from 'vitest'

import type { TitleRestructurePreview, TitleRestructureResult } from '../../../types/titleRestructure'
import { chapterSize, confirmLabel, previewBlocker, previewSummary, resultText, safetyNote } from './titleRestructureModel'

const preview: TitleRestructurePreview = {
  source_id: 1, target_id: 2, kind: 'move', media: 'comic', target_title: 'B',
  chapters: [], moving_chapters: 2, moving_pages: 30, skipped_duplicates: 0,
  source_empty_after: false, stays: [], notes: [], blocked: [], fingerprint: 'f',
}

describe('title restructure wording', () => {
  it('names the action and the target on the confirm button', () => {
    expect(confirmLabel(preview)).toBe('Move 2 chapters to “B”')
    expect(confirmLabel({ ...preview, moving_chapters: 1 })).toBe('Move 1 chapter to “B”')
    expect(confirmLabel({ ...preview, kind: 'merge' })).toBe('Merge into “B”')
  })

  it('summarises what moves, with pages only for comics', () => {
    expect(previewSummary(preview)).toBe('2 chapters (30 pages) will move to the end of “B”.')
    expect(previewSummary({ ...preview, media: 'novel', kind: 'merge', source_empty_after: true }))
      .toBe('2 chapters will move to the end of “B”. This title will be left empty.')
  })

  it('tells the user the old title is empty and where the safety copy is', () => {
    const r: TitleRestructureResult = {
      source_id: 1, target_id: 2, kind: 'merge', moved_chapters: 3, moved_pages: 0,
      skipped_duplicates: 0, source_empty: true, snapshot: '20261014-x', warnings: [],
    }
    const text = resultText(r, 'B')
    expect(text).toContain('Moved 3 chapters to “B”.')
    expect(text).toContain("delete it from the Library when you're sure")
    expect(text).toContain('20261014-x')
  })

  it('never promises a comic page backup: the undo is moving the chapters back', () => {
    const comic = safetyNote('comic')
    expect(comic).toContain('page images are moved, not copied')
    expect(comic).toContain('to undo, move the chapters back')
    expect(comic).not.toContain('changed files')
    expect(safetyNote('novel')).toContain("both titles' original text")
    expect(resultText({
      source_id: 1, target_id: 2, kind: 'move', moved_chapters: 1, moved_pages: 4,
      skipped_duplicates: 0, source_empty: false, snapshot: 's', warnings: [],
    }, 'B')).not.toContain('safety copy')
  })

  it('says what is still needed before a preview', () => {
    expect(previewBlocker('merge', '', 0)).toBe('Still needed: the title to move to.')
    expect(previewBlocker('move', '2', 0)).toBe('Still needed: at least one chapter.')
    expect(previewBlocker('merge', '2', 0)).toBeNull()
    expect(previewBlocker('move', '2', 1)).toBeNull()
  })

  it('sizes a chapter by pages or characters', () => {
    expect(chapterSize({ pages: 1, chars: null })).toBe('1 page')
    expect(chapterSize({ pages: null, chars: 12000 })).toBe('12,000 characters')
    expect(chapterSize({ pages: null, chars: null })).toBe('')
  })
})
