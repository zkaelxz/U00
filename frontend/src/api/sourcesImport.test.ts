import { afterEach, describe, expect, it, vi } from 'vitest'

import { getPcMode, resetPcModeForTests } from './pcOnly'
import {
  URL_PREVIEW_JOB_ID,
  sourceImportJobId,
  startChapterImport,
  startUrlDownload,
  startUrlPreview,
  trackSeries,
  urlMediaJobId,
} from './sourcesImport'
import { getAiEngines, startNovelUrlImport } from './sourcesExtraction'

function reply(status: number, body: unknown) {
  const mock = vi.fn().mockResolvedValue(new Response(JSON.stringify(body), { status }))
  return { mock, f: mock as unknown as typeof fetch }
}

const localHeader = (init: RequestInit) => new Headers(init.headers).get('X-Baihe-Local')
const bodyOf = (mock: ReturnType<typeof vi.fn>, i = 0) => JSON.parse(mock.mock.calls[i][1].body as string)

afterEach(() => resetPcModeForTests())

describe('sources import api', () => {
  it('job ids match the contract', () => {
    expect(URL_PREVIEW_JOB_ID).toBe('sources_url_preview')
    expect(sourceImportJobId(4)).toBe('sourceimport_4')
    expect(urlMediaJobId(4)).toBe('urlmedia_4')
  })

  it('preview and url import post only the url (and drama id)', async () => {
    const { mock, f } = reply(200, { job_id: 'x' })
    await startUrlPreview('https://a.example/b', f)
    await startNovelUrlImport('https://a.example/b', 3, {}, f)
    expect(mock.mock.calls[0][0]).toBe('/api/sources/url/preview')
    expect(bodyOf(mock, 0)).toEqual({ url: 'https://a.example/b' })
    expect(mock.mock.calls[1][0]).toBe('/api/sources/url/import')
    expect(bodyOf(mock, 1)).toEqual({ url: 'https://a.example/b', drama_id: 3 })
  })

  it('url import adds the AI fallback fields only when asked; engines is a plain GET', async () => {
    const { mock, f } = reply(200, { job_id: 'x' })
    await startNovelUrlImport('https://a.example/b', 3, { use_ai: true, engine: 'ollama' }, f)
    expect(bodyOf(mock, 0)).toEqual({ url: 'https://a.example/b', drama_id: 3, use_ai: true, engine: 'ollama' })
    await getAiEngines(f)
    expect(mock.mock.calls[1][0]).toBe('/api/sources/url/ai-engines')
  })

  it('chapter import posts ids only, to the encoded source', async () => {
    const { mock, f } = reply(200, { job_id: 'sourceimport_2' })
    await startChapterImport('a b', { series_id: 's1', chapter_ids: ['c1', 'c2'], drama_id: 2 }, f)
    expect(mock.mock.calls[0][0]).toBe('/api/sources/a%20b/import')
    expect(bodyOf(mock)).toEqual({ series_id: 's1', chapter_ids: ['c1', 'c2'], drama_id: 2 })
  })

  it('track sends drama_id only when given', async () => {
    const { mock, f } = reply(200, [])
    await trackSeries('alpha', 's1', null, f)
    await trackSeries('alpha', 's1', 5, f)
    expect(mock.mock.calls[0][0]).toBe('/api/sources/tracked')
    expect(bodyOf(mock, 0)).toEqual({ source: 'alpha', series_id: 's1', tracked: true })
    expect(bodyOf(mock, 1)).toEqual({ source: 'alpha', series_id: 's1', tracked: true, drama_id: 5 })
  })

  it('url download is PC-only: local header, and a 403 marks the tab remote', async () => {
    const ok = reply(200, { job_id: 'urlmedia_1' })
    await startUrlDownload(1, { url: 'https://v.example/x', audio_only: true, confirm_replace_audio: false }, ok.f)
    expect(ok.mock.mock.calls[0][0]).toBe('/api/media/dramas/1/download-url')
    expect(bodyOf(ok.mock)).toEqual({ url: 'https://v.example/x', audio_only: true, confirm_replace_audio: false })
    expect(localHeader(ok.mock.mock.calls[0][1])).toBe('1')
    expect(getPcMode()).toBe('unknown')

    const refused = reply(403, { error: { code: 'forbidden', message: 'PC only.' } })
    await expect(
      startUrlDownload(1, { url: 'https://v.example/x', audio_only: false, confirm_replace_audio: false }, refused.f),
    ).rejects.toMatchObject({ status: 403 })
    expect(getPcMode()).toBe('remote')
  })
})
