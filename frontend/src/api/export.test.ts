import { afterEach, describe, expect, it, vi } from 'vitest'

import { ApiError, onUnauthorized } from './client'
import { dispositionFilename, getAssText, getEpub, getSubtitleText, startBurnedVideo, subtitleQuery } from './export'

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
    expect((await getSubtitleText(4, { fmt: 'srt', field: 'en', includeNotes: false }, f)).text).toContain('Hi')
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

describe('CSRF and 401 on text/binary fetches', () => {
  afterEach(() => vi.unstubAllGlobals())
  const headers = (f: ReturnType<typeof vi.fn>) => f.mock.calls[0][1].headers as Record<string, string>

  it('getAssText (a POST) sends X-CSRF-Token and X-Baihe-Local when signed in', async () => {
    vi.stubGlobal('document', { cookie: '__Host-baihe_csrf=tok%3D' })
    const f = vi.fn().mockResolvedValue(resp('[Script Info]'))
    expect((await getAssText(4, assReq, f)).text).toBe('[Script Info]')
    expect(f.mock.calls[0][0]).toBe('/api/export/dramas/4/ass')
    expect(f.mock.calls[0][1].method).toBe('POST')
    expect(headers(f)).toEqual({
      Accept: 'text/plain',
      'Content-Type': 'application/json',
      'X-Baihe-Local': '1',
      'X-CSRF-Token': 'tok=',
    })
    expect(JSON.parse(f.mock.calls[0][1].body)).toEqual(assReq)
  })

  it('getAssText with auth off (no cookie) sends no CSRF header', async () => {
    const f = vi.fn().mockResolvedValue(resp('x'))
    await getAssText(4, assReq, f)
    expect(headers(f)['X-CSRF-Token']).toBeUndefined()
    expect(headers(f)['X-Baihe-Local']).toBe('1')
  })

  it('GETs (subtitle text, EPUB) carry neither header', async () => {
    vi.stubGlobal('document', { cookie: 'baihe_csrf=tok' })
    const f = vi.fn().mockImplementation(async () => resp('x'))
    await getSubtitleText(1, { fmt: 'srt', field: 'en', includeNotes: false }, f)
    await getEpub(1, 'en', f)
    for (const call of f.mock.calls) {
      const h = (call[1]?.headers ?? {}) as Record<string, string>
      expect(h['X-CSRF-Token']).toBeUndefined()
      expect(h['X-Baihe-Local']).toBeUndefined()
    }
  })

  it('a 401 notifies the session store and rejects with ApiError', async () => {
    const seen = vi.fn()
    const off = onUnauthorized(seen)
    const f = vi
      .fn()
      .mockResolvedValue(resp(JSON.stringify({ error: { code: 'unauthorized', message: 'Sign in' } }), 401))
    await expect(getAssText(4, assReq, f)).rejects.toMatchObject({ status: 401, code: 'unauthorized' })
    expect(seen).toHaveBeenCalledTimes(1)
    off()
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

describe('dispositionFilename', () => {
  it('prefers filename*= and decodes its UTF-8 percent escapes', () => {
    const h = `attachment; filename="__ - Ep 1.srt"; filename*=UTF-8''%E6%B2%BB%E6%84%88%20-%20Ep%201.srt`
    expect(dispositionFilename(h)).toBe('治愈 - Ep 1.srt')
  })
  it('reads a quoted filename= and unescapes quotes', () => {
    expect(dispositionFilename('attachment; filename="a b.srt"')).toBe('a b.srt')
    expect(dispositionFilename('attachment; filename="a\\"b.srt"')).toBe('a"b.srt')
  })
  it('reads an unquoted filename=', () => {
    expect(dispositionFilename('attachment; filename=a.srt')).toBe('a.srt')
  })
  it('falls back to filename= when the encoded name is malformed', () => {
    expect(dispositionFilename(`attachment; filename="ok.srt"; filename*=UTF-8''%E6%B2`)).toBe('ok.srt')
  })
  it('is null when the header is missing or has no name', () => {
    expect(dispositionFilename(null)).toBeNull()
    expect(dispositionFilename('attachment')).toBeNull()
  })
})
