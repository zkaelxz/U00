import { describe, expect, it } from 'vitest'

import type { AssStyleOptions } from '../../types/export'
import { sectionStorageKey, writeSectionOpen } from '../../components/sectionStorage'
import {
  buildAssRequest,
  emptyAssForm,
  formatBytes,
  assFormFromDraft,
  mediaBlockOpen,
  mediaBlockStorageKey,
  parseSpeakerColors,
  parseWrap,
} from './exportForm'

const opts: AssStyleOptions = {
  presets: { Clean: {} },
  default_preset: 'Clean',
  fonts: ['Arial'],
  custom_font_allowed: true,
  alignments: { 'Bottom center': 2 },
  size_range: [10, 100],
  outline_width_range: [0, 8],
  shadow_range: [0, 8],
}

describe('parseSpeakerColors', () => {
  it('parses name = colour rows', () => {
    expect(parseSpeakerColors('A = #FF0000\n\nB=#00ff00').colors).toEqual({ A: '#FF0000', B: '#00ff00' })
  })
  it('rejects a bad row and too many entries', () => {
    expect(parseSpeakerColors('A red').error).toBeTruthy()
    expect(parseSpeakerColors('A = red').error).toBeTruthy()
    const many = Array.from({ length: 201 }, (_, i) => `s${i} = #000000`).join('\n')
    expect(parseSpeakerColors(many).error).toMatch(/200/)
    expect(parseSpeakerColors(`${'x'.repeat(101)} = #000000`).error).toMatch(/100/)
  })
})

describe('parseWrap', () => {
  it('accepts blank and 1..200 only', () => {
    expect(parseWrap('')).toEqual({})
    expect(parseWrap('40')).toEqual({ value: 40 })
    expect(parseWrap('0').error).toBeTruthy()
    expect(parseWrap('201').error).toBeTruthy()
    expect(parseWrap('4.5').error).toBeTruthy()
  })
})

describe('buildAssRequest', () => {
  it('sends only the fields the user set', () => {
    const r = buildAssRequest(emptyAssForm('Clean'), opts)
    expect(r.request).toEqual({
      field: 'en', preset: 'Clean', per_speaker_colors: true, include_notes: false, notes_as_separate_line: false,
    })
  })
  it('maps overrides', () => {
    const f = { ...emptyAssForm('Clean'), size: '48', bold: 'yes' as const, primary: '#FFFFFF', wrapEn: '30', includeNotes: true, notesAsSeparateLine: true }
    expect(buildAssRequest(f, opts).request).toMatchObject({
      style: { size: 48, bold: true, primary: '#FFFFFF' }, wrap_chars_en: 30, notes_as_separate_line: true,
    })
  })
  it('honours the API ranges and colour format', () => {
    expect(buildAssRequest({ ...emptyAssForm('Clean'), size: '5' }, opts).error).toMatch(/10 to 100/)
    expect(buildAssRequest({ ...emptyAssForm('Clean'), outline: 'red' }, opts).error).toMatch(/#RRGGBB/)
  })
})

describe('formatBytes', () => {
  it('scales units', () => {
    expect(formatBytes(512)).toBe('512 B')
    expect(formatBytes(2048)).toBe('2.0 KB')
    expect(formatBytes(3 * 1024 * 1024)).toBe('3.0 MB')
  })
})

describe('saved Export style', () => {
  it('round-trips per title and ignores bad data', () => {
    expect(assFormFromDraft(null)).toBeNull()
    expect(assFormFromDraft({ fmt: 'srt' })).toBeNull()
    expect(assFormFromDraft({ form: 'nope' })).toBeNull()
    const form = { ...emptyAssForm('Bold'), size: '40' }
    expect(assFormFromDraft({ fmt: 'ass', form: JSON.parse(JSON.stringify(form)) })).toEqual(form)
    expect(assFormFromDraft({ form: { size: 7, preset: 'X', perSpeakerColors: 'yes' } })).toMatchObject({ preset: 'X', size: '', perSpeakerColors: true })
  })
})

describe('mediaBlockOpen', () => {
  const store = (init: Record<string, string> = {}) => {
    const data = { ...init }
    return { getItem: (k: string) => data[k] ?? null, setItem: (k: string, v: string) => void (data[k] = v) }
  }

  it('opens only the subtitle-track video by default', () => {
    const s = store()
    expect(mediaBlockOpen(s, 'softsub_video')).toBe(true)
    expect(mediaBlockOpen(s, 'audio')).toBe(false)
    expect(mediaBlockOpen(s, 'video')).toBe(false)
    expect(mediaBlockOpen(s, 'dubbed_video')).toBe(false)
  })

  it('remembers a choice over the default, per block', () => {
    const s = store()
    writeSectionOpen(s, mediaBlockStorageKey('softsub_video'), false)
    writeSectionOpen(s, mediaBlockStorageKey('audio'), true)
    expect(mediaBlockOpen(s, 'softsub_video')).toBe(false)
    expect(mediaBlockOpen(s, 'audio')).toBe(true)
    expect(mediaBlockOpen(s, 'video')).toBe(false)
  })

  it('falls back to the default without storage or with junk in it', () => {
    expect(mediaBlockOpen(null, 'softsub_video')).toBe(true)
    expect(mediaBlockOpen(store({ [sectionStorageKey(mediaBlockStorageKey('audio'))]: 'maybe' }), 'audio')).toBe(false)
  })
})
