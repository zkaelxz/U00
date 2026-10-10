import { describe, expect, it, vi } from 'vitest'

import { applySubtitle, previewSubtitle, rankSidecars } from './subtitleImport'

const ok = (body: unknown) => vi.fn().mockResolvedValue(new Response(JSON.stringify(body), { status: 200 }))
const file = new File(['1\n00:00:01,000 --> 00:00:02,000\nhi\n'], 'a.srt')
const base = { mode: 'source', encoding: '', splitBilingual: false, translationFirst: false } as const

describe('subtitle import api', () => {
  it('preview posts the file and only the options that were chosen', async () => {
    const mock = ok({})
    await previewSubtitle(3, file, { ...base, encoding: 'cp932', splitBilingual: true, translationFirst: true }, mock as unknown as typeof fetch)
    const [url, init] = mock.mock.calls[0]
    expect(url).toBe('/api/subtitle-import/dramas/3/preview')
    expect(init.method).toBe('POST')
    const form = init.body as FormData
    expect(form.get('file')).toBeInstanceOf(File)
    expect([form.get('mode'), form.get('encoding'), form.get('split_bilingual'), form.get('translation_first')])
      .toEqual(['source', 'cp932', 'true', 'true'])
    expect(form.has('confirm_replace_lines')).toBe(false)
  })

  it('translation_first is only sent with the split', async () => {
    const mock = ok({})
    await previewSubtitle(3, file, { ...base, translationFirst: true }, mock as unknown as typeof fetch)
    const form = mock.mock.calls[0][1].body as FormData
    expect(form.has('split_bilingual')).toBe(false)
    expect(form.has('translation_first')).toBe(false)
  })

  it('apply carries the confirmations that were given', async () => {
    const mock = ok({})
    await applySubtitle(3, file, { ...base, mode: 'translation' }, { replaceLines: false, overwrite: true }, mock as unknown as typeof fetch)
    const [url, init] = mock.mock.calls[0]
    expect(url).toBe('/api/subtitle-import/dramas/3/apply')
    const form = init.body as FormData
    expect(form.get('mode')).toBe('translation')
    expect(form.get('confirm_overwrite')).toBe('true')
    expect(form.has('confirm_replace_lines')).toBe(false)
  })

  it('sidecars posts names as JSON', async () => {
    const mock = ok({ candidates: [], ambiguous: false })
    await rankSidecars(3, 'track.mp3', ['track.srt'], mock as unknown as typeof fetch)
    const [url, init] = mock.mock.calls[0]
    expect(url).toBe('/api/subtitle-import/dramas/3/sidecars')
    expect(JSON.parse(init.body as string)).toEqual({ media_name: 'track.mp3', names: ['track.srt'] })
  })
})
