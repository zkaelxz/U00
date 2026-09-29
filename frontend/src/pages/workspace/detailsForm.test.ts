import { describe, expect, it } from 'vitest'

import type { DramaDetail } from '../../api/types'
import type { SourceConfig } from '../../types/workspace'
import {
  buildDetailsPayload,
  formFromDrama,
  isEmptyPayload,
  mediaTypeOptions,
  modeUpdate,
  serverFieldErrors,
  validateDetails,
} from './detailsForm'

const drama = {
  id: 3, title_en: 'Moon', title_zh: '月', author: 'A', studio: null, director: null,
  voice_actors: null, summary: 'S', custom_tags: ['bl', 'fav'], media_type: 'music',
  source_language: 'zh', series_id: 2,
} as unknown as DramaDetail

describe('formFromDrama', () => {
  it('fills blanks for nulls and joins tags', () => {
    const f = formFromDrama(drama)
    expect(f.studio).toBe('')
    expect(f.custom_tags).toBe('bl, fav')
    expect(f.series_id).toBe('2')
  })
})

describe('mediaTypeOptions', () => {
  it('drops music/other but keeps an existing value', () => {
    expect(mediaTypeOptions('audio_drama')).not.toContain('music')
    expect(mediaTypeOptions('audio_drama')).not.toContain('other')
    expect(mediaTypeOptions('music')[0]).toBe('music')
  })
})

describe('buildDetailsPayload', () => {
  const init = formFromDrama(drama)
  it('sends nothing when unchanged', () => {
    expect(isEmptyPayload(buildDetailsPayload(init, init))).toBe(true)
  })
  it('sends only changed fields, tags normalized, language separately', () => {
    const p = buildDetailsPayload(
      { ...init, studio: 'X', custom_tags: ' bl ,fav,, new ', source_language: 'ja', series_id: '5' },
      init,
    )
    expect(p.metadata).toEqual({ studio: 'X', custom_tags: 'bl, fav, new', series_id: 5 })
    expect(p.sourceLanguage).toBe('ja')
  })
  it('treats whitespace-only tag edits as unchanged and keeps the legacy media type', () => {
    const p = buildDetailsPayload({ ...init, custom_tags: 'bl,fav' }, init)
    expect(isEmptyPayload(p)).toBe(true)
    expect(p.metadata.media_type).toBeUndefined()
  })
})

describe('validateDetails', () => {
  const init = formFromDrama(drama)
  it('passes the loaded values, including a legacy media type', () => {
    expect(validateDetails(init, init)).toEqual({})
  })
  it('reports per-field problems', () => {
    const e = validateDetails(
      { ...init, title_en: '', title_zh: ' ', author: 'x'.repeat(301), summary: 'x'.repeat(5001), series_id: '', source_language: 'fr' },
      init,
    )
    expect(e.title_en).toMatch(/title/)
    expect(e.author).toMatch(/300/)
    expect(e.summary).toMatch(/5000/)
    expect(e.series_id).toMatch(/not supported/)
    expect(e.source_language).toMatch(/language/)
  })
  it('rejects switching to a dropped media type', () => {
    const i = { ...init, media_type: 'anime' }
    expect(validateDetails({ ...i, media_type: 'other' }, i).media_type).toBeDefined()
  })
})

describe('serverFieldErrors', () => {
  it('maps request-validation loc to a field', () => {
    expect(serverFieldErrors([{ loc: ['body', 'author'], msg: 'too long' }], 'The request is invalid.')).toEqual({
      author: 'too long',
    })
  })
  it('falls back to a field named in the message', () => {
    expect(serverFieldErrors(null, 'Unknown media_type.')).toEqual({ media_type: 'Unknown media_type.' })
    expect(serverFieldErrors(null, 'Something else.')).toEqual({})
  })
})

describe('modeUpdate', () => {
  const c = { content_mode: 'audio_drama', transcript_mode: 'whisper' } as SourceConfig
  it('sends only changed modes', () => {
    expect(modeUpdate(c, 'audio_drama', 'whisper')).toEqual({})
    expect(modeUpdate(c, 'streamer_vod', 'whisper')).toEqual({ content_mode: 'streamer_vod' })
    expect(modeUpdate(c, 'audio_drama', 'have_transcript')).toEqual({ transcript_mode: 'have_transcript' })
  })
})
