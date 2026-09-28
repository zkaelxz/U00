import { describe, expect, it, vi } from 'vitest'

import { ApiError } from './client'
import { getAssText, getSubtitleText, startBurnedVideo, subtitleQuery } from './export'

const resp = (body: string, status = 200) => new Response(body, { status })
const assReq = {
  field: 'en' as const,
  preset: 'Clean',
  per_speaker_colors: true,
  include_notes: false,
  notes_as_separate_line: false,
}

describe('subtitleQuery', () => {
  it('omits unset options', () => {
    expect(subtitleQuery({ fmt: 'srt', field: 'en', includeNotes: false })).toBe('fmt=srt&field=en')
  })
  it('includes notes and wrapping', () => {
    expect(
      subtitleQuery({ fmt: 'vtt', field: 'bilingual', includeNotes: true, wrapEn: 40, wrapSource: 20 }),
    ).toBe('fmt=vtt&field=bilingual&include_notes=true&wrap_chars_en=40&wrap_chars_source=20')
  })
})

describe('text fetches', () => {
  it('returns the body as text', async () => {
    const f = vi.fn().mockResolvedValue(resp('1\n00:00:00,000 --> 00:00:01,000\nHi\n'))
    expect(await getSubtitleText(4, { fmt: 'srt', field: 'en', includeNotes: false }, f)).toContain('Hi')
    expect(f.mock.calls[0][0]).toBe('/api/export/dramas/4/subtitle?fmt=srt&field=en')
  })
  it('turns a 422 JSON error into ApiError', async () => {
    const f = vi
      .fn()
      .mockResolvedValue(resp(JSON.stringify({ error: { code: 'invalid_input', message: 'bad' } }), 422))
    await expect(getAssText(4, assReq, f)).rejects.toMatchObject({ status: 422, code: 'invalid_input' })
  })
  it('maps a network failure to ApiError', async () => {
    const f = vi.fn().mockRejectedValue(new TypeError('x'))
    await expect(
      getSubtitleText(1, { fmt: 'srt', field: 'en', includeNotes: false }, f),
    ).rejects.toBeInstanceOf(ApiError)
  })
})

describe('startBurnedVideo', () => {
  it('posts the request as JSON', async () => {
    const f = vi.fn().mockResolvedValue(resp(JSON.stringify({ job_id: 'j' })))
    expect(await startBurnedVideo(2, assReq, f)).toEqual({ job_id: 'j' })
    expect(f.mock.calls[0][0]).toBe('/api/export/dramas/2/burned-video')
    expect(JSON.parse(f.mock.calls[0][1].body)).toEqual(assReq)
  })
})
