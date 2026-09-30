import { describe, expect, it } from 'vitest'

import type { DramaDetail } from '../../api/types'
import type { SourceConfig } from '../../types/workspace'
import {
  buildDetailsPayload,
  formFromDrama,
  isEmptyPayload,
  mediaTypeOptions,
  modeUpdate,
  NEW_SERIES,
  reseedForm,
  newSeriesProblem,
  serverFieldErrors,
  seriesUpdate,
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
  it('sends series_id 0 to take the drama out of its series', () => {
    expect(buildDetailsPayload({ ...init, series_id: '' }, init).metadata).toEqual({ series_id: 0 })
    const none = { ...init, series_id: '' }
    expect(isEmptyPayload(buildDetailsPayload(none, none))).toBe(true)
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
    expect(e.series_id).toBeUndefined() // removing a series is allowed
    expect(e.source_language).toMatch(/language/)
  })
  it('rejects switching to a dropped media type', () => {
    const i = { ...init, media_type: 'anime' }
    expect(validateDetails({ ...i, media_type: 'other' }, i).media_type).toBeDefined()
  })
})

describe('P10 fields (genre, status, counts, source URL, episode summary)', () => {
  const full = {
    ...drama, genre: 'xianxia', publication_status: 'ongoing', chapter_count: 120,
    source_url: 'https://example.com/d/1', episode_number: 3, episode_summary: 'Before.',
  } as unknown as DramaDetail
  const init = formFromDrama(full)
  it('seeds from the drama, counts as text and nulls as empty', () => {
    expect(init).toMatchObject({
      genre: 'xianxia', publication_status: 'ongoing', chapter_count: '120', source_url: 'https://example.com/d/1',
      episode_number: '3', episode_summary: 'Before.',
    })
    expect(formFromDrama(drama)).toMatchObject({ chapter_count: '', episode_number: '', publication_status: '', source_url: '' })
  })
  it('sends only changed values; an emptied count is sent as 0 (clear)', () => {
    const p = buildDetailsPayload(
      { ...init, genre: 'romance', publication_status: 'completed', chapter_count: '', episode_number: '4', source_url: ' https://x.org/a ', episode_summary: '' },
      init,
    )
    expect(p.metadata).toEqual({
      genre: 'romance', publication_status: 'completed', chapter_count: 0, episode_number: 4,
      source_url: 'https://x.org/a', episode_summary: '',
    })
    expect(isEmptyPayload(buildDetailsPayload({ ...init, chapter_count: '0120' }, init))).toBe(true)
  })
  it('never sends a blank publication status (the API cannot clear it)', () => {
    const blank = formFromDrama(drama)
    expect(buildDetailsPayload({ ...blank }, blank).metadata.publication_status).toBeUndefined()
  })
  it('validates the URL scheme, whole-number counts and caps', () => {
    const e = validateDetails(
      { ...init, source_url: 'ftp://x', chapter_count: '1.5', episode_number: '-2', episode_summary: 'x'.repeat(5001), genre: 'x'.repeat(301) },
      init,
    )
    expect(e.source_url).toMatch(/http/)
    expect(e.chapter_count).toMatch(/whole number/)
    expect(e.episode_number).toMatch(/whole number/)
    expect(e.episode_summary).toMatch(/5000/)
    expect(e.genre).toMatch(/300/)
    expect(validateDetails({ ...init, source_url: '', chapter_count: '', episode_number: '' }, init)).toEqual({})
    expect(validateDetails({ ...init, publication_status: 'dropped' }, init).publication_status).toBeDefined()
  })
  it('checks the URL only when it changed, so an old free-text value never blocks a save', () => {
    const old = { ...init, source_url: 'example.com/novel' }
    expect(validateDetails({ ...old, genre: 'x' }, old)).toEqual({})
    expect(validateDetails({ ...old, source_url: 'example.com/other' }, old).source_url).toMatch(/http/)
  })
  it('maps an episode_summary server error to that field, not Summary', () => {
    expect(serverFieldErrors(null, 'episode_summary is too long.')).toEqual({ episode_summary: 'episode_summary is too long.' })
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

describe('P11/X09 series choice', () => {
  const init = formFromDrama(drama)
  it('"+ New series…" sends new_series_name (trimmed), never series_id', () => {
    const p = buildDetailsPayload({ ...init, series_id: NEW_SERIES, new_series_name: '  Saga ' }, init)
    expect(p.metadata).toEqual({ new_series_name: 'Saga' })
  })
  it('seriesUpdate maps no series to 0 and an id to a number', () => {
    expect(seriesUpdate('', '')).toEqual({ series_id: 0 })
    expect(seriesUpdate('9', 'ignored')).toEqual({ series_id: 9 })
    expect(seriesUpdate(NEW_SERIES, ' X ')).toEqual({ new_series_name: 'X' })
  })
  it('needs a name, within the cap, for a new series', () => {
    expect(validateDetails({ ...init, series_id: NEW_SERIES, new_series_name: ' ' }, init).new_series_name).toMatch(/name/)
    expect(newSeriesProblem(NEW_SERIES, 'x'.repeat(301))).toMatch(/300/)
    expect(newSeriesProblem('5', '')).toBeNull()
    expect(newSeriesProblem(NEW_SERIES, 'Saga')).toBeNull()
  })
  it('maps a new_series_name server error to its field', () => {
    expect(serverFieldErrors(null, 'new_series_name must not be blank.')).toEqual({
      new_series_name: 'new_series_name must not be blank.',
    })
  })
})

describe('reseedForm (the drama changed under unsaved edits)', () => {
  const init = formFromDrama(drama)
  it('keeps only the edited fields and takes the new value for the rest', () => {
    const edited = { ...init, genre: 'romance' }
    const next = { ...init, media_type: 'video_drama', source_url: 'https://x.org/a' }
    const out = reseedForm(edited, init, next)
    expect(out).toEqual({ ...next, genre: 'romance' })
    // So the save sends only the genre, never the old media type or URL.
    expect(buildDetailsPayload(out, next).metadata).toEqual({ genre: 'romance' })
  })
  it('a pending "+ New series…" stays with its name; otherwise the name clears', () => {
    const pending = { ...init, series_id: NEW_SERIES, new_series_name: 'Saga' }
    expect(reseedForm(pending, init, init)).toMatchObject({ series_id: NEW_SERIES, new_series_name: 'Saga' })
    const saved = reseedForm(pending, pending, { ...init, series_id: '41' })
    expect(saved).toMatchObject({ series_id: '41', new_series_name: '' })
  })
})
