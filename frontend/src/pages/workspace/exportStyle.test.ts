import { describe, expect, it } from 'vitest'

import type { AssStyleOptions } from '../../types/export'
import { buildAssRequest, emptyAssForm, exportFilename, resolveAssStyle } from './exportForm'

const opts: AssStyleOptions = {
  presets: {
    Clean: { font: 'Arial', size: 24, bold: false, italic: false, primary: '#FFFFFF', outline: '#000000', outline_width: 2, shadow: 1, alignment: 'bottom-center' },
    Loud: { font: 'Arial Black', size: 30, bold: true, outline_width: 4 },
  },
  default_preset: 'Clean',
  fonts: ['Arial'],
  custom_font_allowed: true,
  alignments: { 'bottom-center': 2, 'top-left': 7 },
  size_range: [10, 100],
  outline_width_range: [0, 8],
  shadow_range: [0, 8],
}

describe('buildAssRequest full style mapping', () => {
  it('maps every style field the API accepts to its snake_case key', () => {
    const f = {
      ...emptyAssForm('Loud'), font: ' Noto Sans ', size: '40', outlineWidth: '0', shadow: '3',
      primary: '#abcdef', outline: '#123456', bold: 'no' as const, italic: 'yes' as const,
      alignment: 'top-left', sfxAlignment: 'bottom-center', notesAlignment: 'top-left',
      speakerColors: 'A = #FF0000\nB=#00FF00', perSpeakerColors: false, field: 'bilingual' as const,
      includeNotes: true, notesAsSeparateLine: true, wrapEn: '42', wrapSource: '20', baseName: 'ep1',
    }
    expect(buildAssRequest(f, opts).request).toEqual({
      field: 'bilingual', preset: 'Loud', per_speaker_colors: false, include_notes: true,
      notes_as_separate_line: true, wrap_chars_en: 42, wrap_chars_source: 20,
      speaker_colors: { A: '#FF0000', B: '#00FF00' },
      style: {
        font: 'Noto Sans', size: 40, outline_width: 0, shadow: 3, primary: '#abcdef', outline: '#123456',
        bold: false, italic: true, alignment: 'top-left', sfx_alignment: 'bottom-center', notes_alignment: 'top-left',
      },
    })
  })
  it('never sends the client-only file name or notes-on-own-line without notes', () => {
    const r = buildAssRequest({ ...emptyAssForm('Clean'), baseName: 'x', notesAsSeparateLine: true }, opts).request
    expect(r).not.toHaveProperty('baseName')
    expect(r?.notes_as_separate_line).toBe(false)
  })
  it('rejects out-of-range numbers, non-integers and bad fonts', () => {
    expect(buildAssRequest({ ...emptyAssForm('Clean'), outlineWidth: '9' }, opts).error).toMatch(/Outline width.*0 to 8/)
    expect(buildAssRequest({ ...emptyAssForm('Clean'), shadow: '1.5' }, opts).error).toMatch(/Shadow/)
    expect(buildAssRequest({ ...emptyAssForm('Clean'), primary: '#FFF' }, opts).error).toMatch(/Text colour/)
    expect(buildAssRequest({ ...emptyAssForm('Clean'), font: 'a\nb' }, opts).error).toMatch(/font/)
    expect(buildAssRequest({ ...emptyAssForm('Clean'), wrapSource: '0' }, opts).error).toMatch(/wrapping/)
  })
})

describe('resolveAssStyle', () => {
  it('uses the preset when nothing is overridden', () => {
    expect(resolveAssStyle(emptyAssForm('Clean'), opts)).toEqual({
      font: 'Arial', size: 24, bold: false, italic: false, primary: '#FFFFFF', outline: '#000000',
      outlineWidth: 2, shadow: 1, alignment: 'bottom-center',
    })
  })
  it('applies valid overrides and ignores invalid ones', () => {
    const s = resolveAssStyle({ ...emptyAssForm('Loud'), size: '999', primary: '#00ff00', bold: 'no', alignment: 'top-left' }, opts)
    expect(s).toMatchObject({ font: 'Arial Black', size: 30, primary: '#00ff00', bold: false, outlineWidth: 4, alignment: 'top-left' })
  })
})

describe('exportFilename', () => {
  it('defaults to the API name shape', () => {
    expect(exportFilename('  ', 7, 'zh', 'srt')).toBe('drama_7_zh.srt')
  })
  it('keeps a custom base and replaces unsafe characters', () => {
    expect(exportFilename('My Show: ep 1/2', 7, 'en', 'ass')).toBe('My Show_ ep 1_2.ass')
    expect(exportFilename('name...', 7, 'en', 'vtt')).toBe('name.vtt')
    expect(exportFilename('x'.repeat(150), 1, 'en', 'srt')).toBe(`${'x'.repeat(100)}.srt`)
  })
})
