import { describe, expect, it } from 'vitest'

import { getPcMode, resetPcModeForTests } from './pcOnly'
import * as rx from './reviewExtras'

type Call = { url: string; init?: RequestInit }

function fakeFetch(body: unknown, calls: Call[], status = 200) {
  return (async (input: RequestInfo | URL, init?: RequestInit) => {
    calls.push({ url: String(input), init })
    return new Response(JSON.stringify(body), { status })
  }) as typeof fetch
}

const sent = (c: Call) => (c.init?.body === undefined ? undefined : JSON.parse(String(c.init.body)))

describe('review extras api', () => {
  it('previews a merge with only the options given', async () => {
    const calls: Call[] = []
    await rx.previewMergeShort(4, { max_gap: 0.3, max_chars: Number.NaN }, fakeFetch({}, calls))
    expect(calls[0].url).toBe('/api/review-extras/dramas/4/merge-short/preview?max_gap=0.3')
    expect(calls[0].init?.method ?? 'GET').toBe('GET')
    expect(rx.mergeShortQuery()).toBe('')
  })

  it('applies a merge with the preview ids and groups', async () => {
    const calls: Call[] = []
    const body = { expected_line_ids: [1, 2, 3], expected_groups: [[1, 2]], min_duration: 1.2 }
    await rx.applyMergeShort(4, body, fakeFetch({ line_ids: [1, 3], lines: [], merged_groups: 1 }, calls))
    expect(calls[0].url).toBe('/api/review-extras/dramas/4/merge-short/apply')
    expect(calls[0].init?.method).toBe('POST')
    expect(sent(calls[0])).toEqual(body)
  })

  it('style: read, learn (blank engine left out), toggle, reset', async () => {
    const calls: Call[] = []
    const f = fakeFetch({}, calls)
    await rx.getStyle(2, f)
    await rx.learnStyle(2, { engine: '', model: 'm' }, f)
    await rx.setStyleApplied(2, false, f)
    await rx.resetStyle(2, f)
    expect(calls.map((c) => c.url)).toEqual([
      '/api/review-extras/dramas/2/style',
      '/api/review-extras/dramas/2/style/learn',
      '/api/review-extras/dramas/2/style/apply',
      '/api/review-extras/dramas/2/style/reset',
    ])
    expect(sent(calls[1])).toEqual({ model: 'm' })
    expect(sent(calls[2])).toEqual({ apply: false })
    expect(sent(calls[3])).toEqual({ confirm: true })
    expect(new Headers(calls[3].init?.headers).get('X-Baihe-Local')).toBe('1')
  })

  it('style restore is PC-only and sends the history index', async () => {
    const calls: Call[] = []
    await rx.restoreStyle(2, 0, fakeFetch({}, calls))
    expect(calls[0].url).toBe('/api/review-extras/dramas/2/style/restore')
    expect(sent(calls[0])).toEqual({ index: 0 })
    expect(new Headers(calls[0].init?.headers).get('X-Baihe-Local')).toBe('1')
    const refused = fakeFetch({ error: { code: 'forbidden', message: 'no' } }, [], 403)
    resetPcModeForTests('local')
    await expect(rx.restoreStyle(2, 0, refused)).rejects.toBeTruthy()
    expect(getPcMode()).toBe('remote')
    resetPcModeForTests()
  })

  it('style reset is PC-only: its 403 marks the tab remote, a pause 403 does not', async () => {
    const refused = fakeFetch({ error: { code: 'forbidden', message: 'no' } }, [], 403)
    resetPcModeForTests('local')
    await expect(rx.setStyleApplied(2, false, refused)).rejects.toBeTruthy()
    expect(getPcMode()).toBe('local')
    await expect(rx.resetStyle(2, refused)).rejects.toBeTruthy()
    expect(getPcMode()).toBe('remote')
    resetPcModeForTests()
  })

  it('sensevoice: start without a body, then read the rows', async () => {
    const calls: Call[] = []
    const f = fakeFetch({}, calls)
    await rx.startSenseVoice(5, f)
    await rx.getSenseVoice(5, f)
    expect(calls[0].url).toBe('/api/review-extras/dramas/5/sensevoice')
    expect(calls[0].init?.method).toBe('POST')
    expect(calls[0].init?.body).toBeUndefined()
    expect(calls[1].url).toBe('/api/review-extras/dramas/5/sensevoice')
  })

  it('burn preview: start, info, clip url', async () => {
    const calls: Call[] = []
    const f = fakeFetch({}, calls)
    await rx.startBurnPreview(6, { line_id: 9, pad_seconds: null, preset: '' }, f)
    await rx.startBurnPreview(6, { line_id: 9, pad_seconds: 0, preset: 'Bold' }, f)
    await rx.startBurnPreview(
      6,
      { line_id: 9, preset: 'Clean', style: { size: 40 }, speaker_colors: { A: '#112233' }, per_speaker_colors: true, wrap_chars_en: 30 },
      f,
    )
    await rx.getBurnPreviewInfo(6, f)
    expect(sent(calls[0])).toEqual({ line_id: 9 })
    expect(sent(calls[1])).toEqual({ line_id: 9, pad_seconds: 0, preset: 'Bold' })
    expect(sent(calls[2])).toEqual({
      line_id: 9,
      preset: 'Clean',
      style: { size: 40 },
      speaker_colors: { A: '#112233' },
      per_speaker_colors: true,
      wrap_chars_en: 30,
    })
    expect(calls[3].url).toBe('/api/review-extras/dramas/6/burn-preview/info')
    expect(rx.burnPreviewClipUrl(6, '2026-09-29T10:00')).toBe(
      '/api/review-extras/dramas/6/burn-preview/clip?v=2026-09-29T10%3A00',
    )
  })
})
