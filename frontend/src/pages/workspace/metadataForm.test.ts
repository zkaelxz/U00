import { describe, expect, it } from 'vitest'

import type { DramaDetail } from '../../api/types'
import {
  acceptedFields,
  analysisDetails,
  analysisSummary,
  contentTypeSuggestion,
  pipelineSteps,
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

describe('P05 analysis details', () => {
  const video = {
    drama_id: 1, duration_seconds: 60, has_video: true, has_audio: true, audio_track_count: 1, sample_rate: 48000,
    width: 1920, height: 1080, fps: 29.97, content_type_guess: 'video_drama', content_type_reason: 'has a video track',
    subtitle_tracks: [{ index: 2, codec: 'ass', language: 'chi' }, { index: 3, codec: 'subrip', language: null }],
    suggested_pipeline: ['Import existing subtitle track (chi, unknown) instead of transcribing', 'Translate'],
  }
  it('shows resolution, frame rate and readable subtitle tracks', () => {
    const d = Object.fromEntries(analysisDetails(video))
    expect(d.Resolution).toBe('1920×1080')
    expect(d['Frame rate']).toBe('29.97 fps')
    expect(d['Subtitle tracks']).toBe('2 (Chinese ASS, unknown language SubRip)')
  })
  it('rebuilds the subtitle-import step without raw codes', () => {
    expect(pipelineSteps(video)).toEqual([
      'Import the existing subtitle tracks (Chinese ASS, unknown language SubRip) instead of transcribing',
      'Translate',
    ])
    expect(Object.fromEntries(analysisDetails({ ...video, subtitle_tracks: [{ index: 1, codec: 'webvtt', language: 'fre' }] }))['Subtitle tracks'])
      .toBe('1 (French WebVTT)')
  })
  it('handles audio only and an older server without the new fields', () => {
    const d = Object.fromEntries(analysisDetails({ drama_id: 1, duration_seconds: 5, has_video: false, has_audio: true, audio_track_count: 1, sample_rate: null }))
    expect(d.Resolution).toBe('Audio only')
    expect(d['Subtitle tracks']).toBe('None')
    expect(d['Frame rate']).toBe('—')
  })
  it('offers the guessed content type only when it is a known, different media type', () => {
    const allowed = ['audio_drama', 'video_drama', 'asmr']
    expect(contentTypeSuggestion(video, 'audio_drama', allowed)).toBe('video_drama')
    expect(contentTypeSuggestion(video, 'video_drama', allowed)).toBeNull()
    expect(contentTypeSuggestion({ ...video, content_type_guess: 'music' }, null, allowed)).toBeNull()
    expect(contentTypeSuggestion({ ...video, content_type_guess: null }, null, allowed)).toBeNull()
  })
})
