import { describe, expect, it } from 'vitest'

import type { LibraryPreset, ProfileField } from '../../types/library'
import { emptyProfile, fromPreset, toBody, validateProfile } from './profileForm'

const fields: ProfileField[] = [
  { key: 'whisper_size', label: 'Whisper model', group: 'transcribe', type: 'choice', choices: ['small', 'large-v3'] },
  { key: 'beam_size', label: 'Beam size', group: 'transcribe', type: 'int', min: 1, max: 10 },
  { key: 'vad_threshold', label: 'Voice detection threshold', group: 'transcribe', type: 'number', min: 0.1, max: 0.9 },
  { key: 'whisper_fast_mode', label: 'Fast mode', group: 'transcribe', type: 'bool' },
]

const preset = (o: Partial<LibraryPreset> = {}): LibraryPreset => ({
  id: 3, name: 'Wuxia', translation_engine: 'claude', engine_model: 'claude-b', style_preset: 'subtitle',
  locale: 'en-GB', default_female_pronouns: 1, include_genre_notes: 0,
  options: {
    batch_size: 12, reflect: true, style_note: 'Honorifics.', fallback_engines: ['deepseek'], cost_cap: 1.5,
    beam_size: 7, whisper_fast_mode: false, whisper_size: 'large-v3',
  },
  ...o,
})

describe('profile form', () => {
  it('loads every stored field, and never carries the English variant', () => {
    const f = fromPreset(preset(), 'audio_drama')
    expect(f).toMatchObject({
      name: 'Wuxia', engine: 'claude', model: 'claude-b', style_preset: 'subtitle', genre_notes: false,
      female_pronouns: true, reflect: true, bulk: false, batch_size: '12', cost_cap: '1.5',
      fallbacks: ['deepseek'], style_note: 'Honorifics.',
      title: { beam_size: '7', whisper_fast_mode: 'false', whisper_size: 'large-v3' },
    })
    expect(toBody(f, fields).locale).toBeNull()
  })

  it('sends only what is set, with switches as booleans and numbers as numbers', () => {
    const f = { ...emptyProfile('claude', 'audio_drama'), name: ' New ', title: { beam_size: '5', whisper_fast_mode: 'true', whisper_size: '' } }
    expect(toBody(f, fields)).toEqual({
      name: 'New', translation_engine: 'claude', engine_model: null, style_preset: 'audio_drama', locale: null,
      default_female_pronouns: false, include_genre_notes: true, options: { beam_size: 5, whisper_fast_mode: true },
    })
  })

  it('round-trips an edit without losing or adding options', () => {
    const p = preset()
    const body = toBody(fromPreset(p, 'x'), fields)
    expect(body.options).toEqual(p.options)
  })

  it('is a new profile when the old one had no options (an older preset)', () => {
    const f = fromPreset(preset({ options: undefined, include_genre_notes: null, style_preset: null }), 'audio_drama')
    expect(f).toMatchObject({ genre_notes: true, style_preset: 'audio_drama', batch_size: '', title: {} })
  })

  it('names the first problem', () => {
    const ok = { ...emptyProfile('claude', 'audio_drama'), name: 'P' }
    expect(validateProfile(ok, fields)).toBeNull()
    expect(validateProfile({ ...ok, name: ' ' }, fields)).toMatch(/name/)
    expect(validateProfile({ ...ok, engine: '' }, fields)).toMatch(/engine/)
    expect(validateProfile({ ...ok, batch_size: '0' }, fields)).toMatch(/Batch size/)
    expect(validateProfile({ ...ok, context_window: '101' }, fields)).toMatch(/Context window/)
    expect(validateProfile({ ...ok, cost_cap: '-1' }, fields)).toMatch(/cost cap/)
    expect(validateProfile({ ...ok, title: { beam_size: '11' } }, fields)).toMatch(/Beam size/)
    expect(validateProfile({ ...ok, title: { beam_size: '2.5' } }, fields)).toMatch(/Beam size/)
    expect(validateProfile({ ...ok, title: { vad_threshold: '0.5' } }, fields)).toBeNull()
  })
})
