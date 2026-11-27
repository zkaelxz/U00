import { describe, expect, it } from 'vitest'

import type { SeriesDefaultsField, SeriesDeletePreview, SeriesListItem } from '../../api/series'
import {
  ceilingProblem, money, copyPreviewText, copyResultText, copySources, defaultsForm, defaultsPreviewText, deleteBlocked, deleteLines, isSeriesTab,
  filterSeries, filterStreamers, renameProblem, seriesOfStreamer, streamerFolders, settingsFromForm, shownValue,
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
    const p = { series_id: 1, source_series_id: 2, new_terms: [], skipped_terms: [], replacements_new: 0 }
    expect(copyPreviewText({ ...p, new_count: 3, skipped_count: 2 }))
      .toBe('3 new terms will be added, 2 are already there and are skipped.')
    expect(copyPreviewText({ ...p, new_count: 1, skipped_count: 0 })).toBe('1 new term will be added.')
    expect(copyPreviewText({ ...p, new_count: 0, skipped_count: 4 })).toBe('Nothing to add: all 4 terms are already there.')
    expect(copyResultText({ added: 2, skipped: 1, replacements_added: 0 })).toBe('Added 2 terms; skipped 1 already there.')
    expect(copyPreviewText({ ...p, new_count: 0, skipped_count: 4, replacements_new: 2 }))
      .toBe('0 new terms and 2 word replacements will be added, 4 are already there and are skipped.')
    expect(copyResultText({ added: 1, skipped: 0, replacements_added: 2 })).toBe('Added 1 term and 2 word replacements.')
  })

  it('turns the defaults form into settings, leaving blanks unset', () => {
    const fields: SeriesDefaultsField[] = [
      { key: 'beam_size', label: 'Beam size', group: 'transcribe', type: 'int', min: 1, max: 10 },
      { key: 'vad_threshold', label: 'VAD', group: 'transcribe', type: 'number', min: 0.1, max: 0.9 },
      { key: 'whisper_fast_mode', label: 'Fast', group: 'transcribe', type: 'bool' },
      { key: 'separation_backend', label: 'Sep', group: 'transcribe', type: 'choice', choices: ['auto', 'audio_separator'] },
    ]
    const form = defaultsForm(fields, { beam_size: 8, whisper_fast_mode: false })
    expect(form).toEqual({ beam_size: '8', vad_threshold: '', whisper_fast_mode: 'false', separation_backend: '' })
    expect(settingsFromForm(fields, { ...form, separation_backend: 'audio_separator', vad_threshold: '0.4' }))
      .toEqual({ settings: { beam_size: 8, vad_threshold: 0.4, whisper_fast_mode: false, separation_backend: 'audio_separator' } })
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

describe('auto-run ceiling', () => {
  it('accepts a positive amount up to the server limit and refuses the rest', () => {
    expect(ceilingProblem('1')).toBeNull()
    expect(ceilingProblem('0.50')).toBeNull()
    for (const bad of ['', ' ', '0', '-2', 'abc', '1001']) expect(ceilingProblem(bad)).not.toBeNull()
  })

  it('shows money with cents', () => {
    expect(money(0.5)).toBe('$0.50')
  })
})

describe('filterSeries', () => {
  const items = [{ name: 'HS' }, { name: 'anvil00' }, { name: 'Chime' }]
  it('sorts by name ignoring case', () => {
    expect(filterSeries(items, '').map((s) => s.name)).toEqual(['anvil00', 'Chime', 'HS'])
  })
  it('keeps names that contain the text, ignoring case and outer spaces', () => {
    expect(filterSeries(items, '  CHI ').map((s) => s.name)).toEqual(['Chime'])
    expect(filterSeries(items, 'zzz')).toEqual([])
  })
})

describe('streamer folders', () => {
  const items = [
    { name: 'Anvil00', streamer: 'Chime', title_count: 41 },
    { name: 'HS', streamer: 'Chime', title_count: 3 },
    { name: 'Solo', streamer: 'anvil', title_count: 6 },
    { name: 'Loose', streamer: null, title_count: 5 },
    { name: 'Old', title_count: 1 },
  ]

  it('groups by streamer by name, with No streamer last', () => {
    expect(streamerFolders(items)).toEqual([
      { name: 'anvil', seriesCount: 1, titleCount: 6 },
      { name: 'Chime', seriesCount: 2, titleCount: 44 },
      { name: null, seriesCount: 2, titleCount: 6 },
    ])
    expect(streamerFolders(items.slice(0, 2)).map((f) => f.name)).toEqual(['Chime'])
  })

  it('filters folders by name and picks one streamer\'s series', () => {
    expect(filterStreamers(streamerFolders(items), 'chi').map((f) => f.name)).toEqual(['Chime'])
    expect(filterStreamers(streamerFolders(items), 'no str').map((f) => f.name)).toEqual([null])
    expect(seriesOfStreamer(items, 'Chime').map((s) => s.name)).toEqual(['Anvil00', 'HS'])
    const respelt = [...items, { name: 'Late', streamer: 'CHIME', title_count: 2 }]
    expect(streamerFolders(respelt).find((f) => f.name === 'Chime')?.seriesCount).toBe(3)
    expect(seriesOfStreamer(respelt, 'chime').map((s) => s.name)).toEqual(['Anvil00', 'HS', 'Late'])
    expect(seriesOfStreamer(items, '').map((s) => s.name)).toEqual(['Loose', 'Old'])
  })
})
