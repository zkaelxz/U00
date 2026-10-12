import { describe, expect, it } from 'vitest'

import type { SeriesDefaultsField, SeriesDeletePreview, SeriesListItem } from '../../api/series'
import {
  copyPreviewText, copyResultText, copySources, defaultsForm, defaultsPreviewText, deleteBlocked, deleteLines, isSeriesTab,
  renameProblem, settingsFromForm, shownValue,
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

  it('turns the defaults form into settings, leaving blanks unset', () => {
    const fields: SeriesDefaultsField[] = [
      { key: 'beam_size', label: 'Beam size', group: 'transcribe', type: 'int', min: 1, max: 10 },
      { key: 'vad_threshold', label: 'VAD', group: 'transcribe', type: 'number', min: 0.1, max: 0.9 },
      { key: 'whisper_fast_mode', label: 'Fast', group: 'transcribe', type: 'bool' },
      { key: 'separation_backend', label: 'Sep', group: 'transcribe', type: 'choice', choices: ['auto', 'demucs'] },
    ]
    const form = defaultsForm(fields, { beam_size: 8, whisper_fast_mode: false })
    expect(form).toEqual({ beam_size: '8', vad_threshold: '', whisper_fast_mode: 'false', separation_backend: '' })
    expect(settingsFromForm(fields, { ...form, separation_backend: 'demucs', vad_threshold: '0.4' }))
      .toEqual({ settings: { beam_size: 8, vad_threshold: 0.4, whisper_fast_mode: false, separation_backend: 'demucs' } })
    expect(settingsFromForm(fields, { ...form, beam_size: '8.5' })).toEqual({ problem: 'Beam size: enter a whole number.' })
    expect(settingsFromForm(fields, { ...form, beam_size: '99' })).toEqual({ problem: 'Beam size: between 1 and 10.' })
  })

  it('words the defaults preview', () => {
    const p = { series_id: 1, mode: 'fill' as const, job_running: false, titles: [] }
    expect(defaultsPreviewText({ ...p, title_count: 3, changed_titles: 2, fields_changed: 5 }))
      .toBe('2 titles of 3 would change (5 settings in all).')
    expect(defaultsPreviewText({ ...p, title_count: 3, changed_titles: 0, fields_changed: 0 }))
      .toBe('Nothing to change: every title already matches.')
    expect(defaultsPreviewText({ ...p, title_count: 0, changed_titles: 0, fields_changed: 0 })).toBe('This series has no titles yet.')
    expect([shownValue(null), shownValue(true), shownValue(false), shownValue(5)]).toEqual(['app default', 'on', 'off', '5'])
  })
})
