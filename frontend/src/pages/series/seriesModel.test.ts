import { describe, expect, it } from 'vitest'

import type { SeriesDeletePreview, SeriesListItem } from '../../api/series'
import {
  copyPreviewText, copyResultText, copySources, deleteBlocked, deleteLines, isSeriesTab, renameProblem,
} from './seriesModel'

const preview = (over: Partial<SeriesDeletePreview> = {}): SeriesDeletePreview => ({
  series_id: 1, name: 'Saga', title_count: 0, glossary_term_count: 0, character_count: 0,
  translation_memory_count: 0, dismissal_count: 0, style_profile_count: 0, has_instructions: false,
  needs_typed_confirm: false, job_running: false, ...over,
})

const item = (id: number, name: string, terms = 1): SeriesListItem => ({
  id, name, is_private: false, owned_by_me: true, title_count: 1, glossary_term_count: terms,
})

describe('series page model', () => {
  it('knows its tabs', () => {
    expect(isSeriesTab('glossary')).toBe(true)
    expect(isSeriesTab('nope')).toBe(false)
  })

  it('checks a rename like the API does', () => {
    const others = [item(2, 'Other')]
    expect(renameProblem('  ', 'Saga', others)).toBe('Enter a name.')
    expect(renameProblem('Saga', 'Saga', others)).toBe('That is already its name.')
    expect(renameProblem(' OTHER ', 'Saga', others)).toMatch(/already a series called “Other”/)
    expect(renameProblem('Ｏｔｈｅｒ', 'Saga', others)).toMatch(/already a series/)
    expect(renameProblem('x'.repeat(301), 'Saga', others)).toMatch(/at most 300/)
    expect(renameProblem('saga', 'Saga', others)).toBeNull()
    expect(renameProblem('Saga II', 'Saga', others)).toBeNull()
  })

  it('lists what a delete removes and keeps', () => {
    const lines = deleteLines(preview({
      title_count: 2, glossary_term_count: 1, character_count: 3, has_instructions: true,
      translation_memory_count: 1, dismissal_count: 2, style_profile_count: 1,
    }))
    expect(lines.deleted).toEqual([
      '1 glossary term', '3 series characters', 'the series instructions',
      '1 translation memory entry', '2 dismissed glossary suggestions', 'the learned style profile',
    ])
    expect(lines.kept).toBe('2 titles kept: they become “No series”.')
    expect(deleteLines(preview())).toEqual({ deleted: [], kept: null })
  })

  it('blocks delete away from the PC and while a job runs', () => {
    expect(deleteBlocked(preview(), 'remote')).toBe('Deleting is PC only.')
    expect(deleteBlocked(preview({ job_running: true }), 'local')).toMatch(/job is running/)
    expect(deleteBlocked(preview(), 'local')).toBeNull()
  })

  it('offers other series with terms as copy sources, by name', () => {
    const all = [item(3, 'b'), item(1, 'self'), item(2, 'a'), item(4, 'empty', 0)]
    expect(copySources(all, 1).map((s) => s.id)).toEqual([2, 3])
  })

  it('words the copy preview and result', () => {
    const p = { series_id: 1, source_series_id: 2, new_terms: [], skipped_terms: [] }
    expect(copyPreviewText({ ...p, new_count: 3, skipped_count: 2 }))
      .toBe('3 new terms will be added, 2 are already there and are skipped.')
    expect(copyPreviewText({ ...p, new_count: 1, skipped_count: 0 })).toBe('1 new term will be added.')
    expect(copyPreviewText({ ...p, new_count: 0, skipped_count: 4 })).toBe('Nothing to add: all 4 terms are already there.')
    expect(copyResultText({ added: 2, skipped: 1 })).toBe('Added 2 terms; skipped 1 already there.')
  })
})
