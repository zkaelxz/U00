import { describe, expect, it } from 'vitest'

import type { CharacterEntry } from '../../../types/translateStage'
import {
  CUSTOM,
  buildCharacterUpdate,
  canRemember,
  formPronouns,
  isDirty,
  sampleCaption,
  toCharacterForm,
  unsetPronounsLabel,
} from './characterForm'

const entry: CharacterEntry = {
  speaker_label: 'SPEAKER_00', character_name: 'Lin', pronouns: 'she/her', tts_voice: 'v1',
  offline_voice: '', clone_engine: 'xtts', voice_design: '', has_ref_audio: true,
  ref_text_present: true, series_character_id: null, series_character_name: '', line_count: 4,
}

describe('character form', () => {
  it('sends nothing but the label when unchanged, and ignores blank ref text', () => {
    const u = buildCharacterUpdate(entry, toCharacterForm(entry))
    expect(u).toEqual({ speaker_label: 'SPEAKER_00' })
    expect(isDirty(u)).toBe(false)
  })

  it('sends only changed voice fields, "" to clear, trimmed name and ref text', () => {
    const f = { ...toCharacterForm(entry), character_name: ' Mei ', clone_engine: '', voice_design: 'warm', ref_text: ' hi ' }
    expect(buildCharacterUpdate(entry, f)).toEqual({
      speaker_label: 'SPEAKER_00', character_name: 'Mei', clone_engine: '', voice_design: 'warm', ref_text: 'hi',
    })
  })
})

describe('pronouns (C07)', () => {
  it('a stored custom value opens as Custom with its text', () => {
    const f = toCharacterForm({ ...entry, pronouns: 'xe/xem' })
    expect(f.pronoun_choice).toBe(CUSTOM)
    expect(f.custom_pronouns).toBe('xe/xem')
    expect(isDirty(buildCharacterUpdate({ ...entry, pronouns: 'xe/xem' }, f))).toBe(false)
  })

  it('legacy female/male show as presets and are not re-sent', () => {
    const e = { ...entry, pronouns: 'female' }
    const f = toCharacterForm(e)
    expect(f.pronoun_choice).toBe('she/her')
    expect(isDirty(buildCharacterUpdate(e, f))).toBe(false)
  })

  it('typed custom text is sent trimmed; Custom with nothing typed keeps the saved value', () => {
    const f = { ...toCharacterForm(entry), pronoun_choice: CUSTOM, custom_pronouns: ' ze/zir ' }
    expect(buildCharacterUpdate(entry, f).pronouns).toBe('ze/zir')
    const empty = { ...toCharacterForm(entry), pronoun_choice: CUSTOM, custom_pronouns: '  ' }
    expect(formPronouns(empty, 'she/her')).toBe('she/her')
    expect(isDirty(buildCharacterUpdate(entry, empty))).toBe(false)
  })

  it('picking the unset option clears', () => {
    const f = { ...toCharacterForm(entry), pronoun_choice: '' }
    expect(buildCharacterUpdate(entry, f).pronouns).toBe('')
  })

  it('names the series default on the unset option', () => {
    expect(unsetPronounsLabel(entry)).toBe('Unspecified')
    expect(unsetPronounsLabel({ ...entry, series_pronouns: 'male' })).toBe('Series default (he/him)')
  })
})

describe('sample lines and remember (C04, C08)', () => {
  it('captions samples, says so when there are none, null when not sent', () => {
    expect(sampleCaption(entry)).toBeNull()
    expect(sampleCaption({ ...entry, sample_lines: [] })).toBe('No lines attributed to this speaker yet.')
    expect(sampleCaption({ ...entry, sample_lines: ['你好', '再见'] })).toBe('“你好” / “再见”')
  })

  it('offers remember only in a series, with a saved name, when not linked', () => {
    expect(canRemember(entry, true)).toBe(true)
    expect(canRemember(entry, false)).toBe(false)
    expect(canRemember({ ...entry, character_name: ' ' }, true)).toBe(false)
    expect(canRemember({ ...entry, series_character_id: 3 }, true)).toBe(false)
  })
})
