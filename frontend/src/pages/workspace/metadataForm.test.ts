import { describe, expect, it } from 'vitest'

import type { DramaDetail } from '../../api/types'
import {
  acceptedFields,
  analysisSummary,
  autofillRequest,
  defaultSelection,
  formatDuration,
  suggestionRows,
} from './metadataForm'

const drama = { title_en: 'Mine', title_zh: null, studio: '', author: 'Same' } as unknown as DramaDetail

describe('metadataForm', () => {
  const rows = suggestionRows(
    { title_en: 'Theirs', title_zh: '名', studio: 'S', author: 'Same', bogus: 'x', director: ' ' },
    drama,
  )

  it('flags conflicts and unchanged values, ignores unknown or blank keys', () => {
    expect(rows.map((r) => r.key)).toEqual(['title_en', 'title_zh', 'author', 'studio'])
    expect(rows.find((r) => r.key === 'title_en')?.conflict).toBe(true)
    expect(rows.find((r) => r.key === 'author')?.same).toBe(true)
    expect(rows.find((r) => r.key === 'studio')?.conflict).toBe(false)
  })

  it('never pre-selects a field that would overwrite user data', () => {
    const sel = defaultSelection(rows)
    expect([...sel].sort()).toEqual(['studio', 'title_zh'])
    expect(acceptedFields(rows, sel)).toEqual({ title_zh: '名', studio: 'S' })
    expect(acceptedFields(rows, new Set(['title_en', 'author']))).toEqual({ title_en: 'Theirs' })
  })

  it('needs exactly one of a http(s) url or text', () => {
    expect(autofillRequest('', '')).toBeNull()
    expect(autofillRequest('http://a', 'b')).toBeNull()
    expect(autofillRequest('ftp://a', '')).toBeNull()
    expect(autofillRequest(' https://a.com ', '')).toEqual({ url: 'https://a.com' })
    expect(autofillRequest('', ' hi ')).toEqual({ page_text: 'hi' })
  })

  it('formats durations and the analysis summary', () => {
    expect(formatDuration(65.4)).toBe('1:05')
    expect(formatDuration(3725)).toBe('1:02:05')
    expect(
      analysisSummary({ drama_id: 1, duration_seconds: 65, has_video: false, has_audio: true, audio_track_count: 1, sample_rate: 44100 }),
    ).toBe('1:05 · audio only')
  })
})
