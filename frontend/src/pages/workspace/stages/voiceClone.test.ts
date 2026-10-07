import { describe, expect, it } from 'vitest'

import type { DubConfig } from '../../../types/dub'
import type { CharacterEntry } from '../../../types/translateStage'
import {
  candidatesFor,
  clipStatus,
  cloneWarnings,
  formatSeconds,
  replaceEntry,
  skipText,
  speakerTitle,
  warningSummary,
} from './voiceClone'

const entry = (over: Partial<CharacterEntry> = {}): CharacterEntry => ({
  speaker_label: 'A', character_name: '', voice_actor: '', pronouns: '',
  clone_engine: '', voice_design: '', has_ref_audio: false, ref_text_present: false,
  series_character_id: null, series_character_name: '', line_count: 2, ...over,
})

const sp = (over = {}) => ({ speaker_label: 'A', candidates: [], skip_reason: null, closest_duration: null, ...over })

describe('voice-clone helpers', () => {
  it('maps clone warnings by label and ignores missing speakers', () => {
    const cfg = {
      speakers: [
        { speaker_label: 'B', clone_warning: 'b warn' },
        { speaker_label: 'A', clone_warning: null },
      ],
    } as unknown as DubConfig
    const w = cloneWarnings(cfg)
    expect(w.get('B')).toBe('b warn')
    expect(w.has('A')).toBe(false)
    expect(cloneWarnings({} as DubConfig).size).toBe(0)
    expect(cloneWarnings(null).size).toBe(0)
  })

  it('summarises the warning count', () => {
    expect(warningSummary(0)).toBeNull()
    expect(warningSummary(1)).toMatch(/^1 speaker is/)
    expect(warningSummary(3)).toMatch(/^3 speakers are/)
  })

  it('finds candidates by label', () => {
    const list = { drama_id: 1, speakers: [sp({ speaker_label: 'X' }), sp()] }
    expect(candidatesFor(list, 'A')?.speaker_label).toBe('A')
    expect(candidatesFor(list, 'Z')).toBeNull()
    expect(candidatesFor(null, 'A')).toBeNull()
  })

  it('explains an empty extraction', () => {
    expect(skipText(null)).toBeNull()
    expect(skipText(sp())).toBeNull()
    expect(skipText(sp({ skip_reason: 'too_short', closest_duration: 2 }))).toContain('2.0 s, under the 3 s')
    expect(skipText(sp({ skip_reason: 'too_long', closest_duration: 15.25 }))).toContain('15.3 s, over the 12 s')
    expect(skipText(sp({ skip_reason: 'no_segments' }))).toContain('No lines')
    const found = sp({ skip_reason: 'too_short', candidates: [{ id: 'x', start: 0, end: 5, duration: 5, ref_text: '' }] })
    expect(skipText(found)).toBeNull()
  })

  it('formats clip status, titles and seconds', () => {
    expect(clipStatus(entry())).toBe('No reference clip')
    expect(clipStatus(entry({ has_ref_audio: true }))).toContain('no transcript')
    expect(clipStatus(entry({ has_ref_audio: true, ref_text_present: true }))).toContain('with transcript')
    expect(speakerTitle(entry())).toBe('A')
    expect(speakerTitle(entry({ character_name: 'Mei' }))).toBe('Mei (A)')
    expect(formatSeconds(null)).toBe('')
    expect(formatSeconds(Number.NaN)).toBe('')
    expect(formatSeconds(6)).toBe('6.0 s')
  })

  it('replaces an entry by label, not position', () => {
    const list = [entry({ speaker_label: 'B' }), entry()]
    const out = replaceEntry(list, entry({ voice_actor: 'X' }))
    expect(out?.map((e) => [e.speaker_label, e.voice_actor])).toEqual([['B', ''], ['A', 'X']])
    expect(replaceEntry(null, entry())).toBeNull()
  })
})
