import { describe, expect, it } from 'vitest'

import type { CharacterEntry } from '../../../types/translateStage'
import { buildCharacterUpdate, isDirty, toCharacterForm } from './characterForm'

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
