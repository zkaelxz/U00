import { afterEach, describe, expect, it } from 'vitest'

import { getPcMode, resetPcModeForTests } from './pcOnly'
import { scanlateApi, scanlateExportUrl } from './scanlate'

type Call = { url: string; init?: RequestInit }

function fakeFetch(calls: Call[], status = 200, body: unknown = {}) {
  return (async (input: RequestInfo | URL, init?: RequestInit) => {
    calls.push({ url: String(input), init })
    return new Response(JSON.stringify(body), { status, headers: { 'Content-Type': 'application/json' } })
  }) as typeof fetch
}

afterEach(() => resetPcModeForTests())

describe('scanlate api', () => {
  it('reads config and run notes', async () => {
    const calls: Call[] = []
    const f = fakeFetch(calls)
    await scanlateApi.config(4, f)
    await scanlateApi.runNotes(4, f)
    expect(calls.map((c) => c.url)).toEqual(['/api/scanlate/dramas/4/config', '/api/scanlate/dramas/4/run-notes'])
  })

  it('uploads every file as a files part with slice_strips, PC-only', async () => {
    const calls: Call[] = []
    const a = new File(['a'], 'a.png', { type: 'image/png' })
    const b = new File(['b'], 'b.pdf', { type: 'application/pdf' })
    await scanlateApi.upload(4, [a, b], false, fakeFetch(calls))
    const { url, init } = calls[0]
    expect(url).toBe('/api/scanlate/dramas/4/pages')
    expect(init?.method).toBe('POST')
    const form = init?.body as FormData
    expect(form.getAll('files').map((x) => (x as File).name)).toEqual(['a.png', 'b.pdf'])
    expect(form.get('slice_strips')).toBe('false')
    const headers = new Headers(init?.headers)
    expect(headers.get('X-Baihe-Local')).toBe('1')
    expect(headers.get('Content-Type')).toBeNull()
  })

  it('a refused upload marks the tab remote', async () => {
    await scanlateApi
      .upload(4, [new File(['a'], 'a.png')], true, fakeFetch([], 403, { error: { code: 'forbidden', message: 'no' } }))
      .catch(() => {})
    expect(getPcMode()).toBe('remote')
  })

  it('starts run, render and export jobs with JSON bodies', async () => {
    const calls: Call[] = []
    const f = fakeFetch(calls, 200, { job_id: 'scanlate_4' })
    await scanlateApi.run(4, { mode: 'page', page_id: 9, engine: 'ollama', detect_backend: 'auto' }, f)
    await scanlateApi.render(4, null, f)
    await scanlateApi.render(4, 9, f)
    await scanlateApi.export(4, ['zip', 'pdf'], f)
    expect(calls.map((c) => c.url)).toEqual([
      '/api/scanlate/dramas/4/run',
      '/api/scanlate/dramas/4/render',
      '/api/scanlate/dramas/4/render',
      '/api/scanlate/dramas/4/export',
    ])
    expect(calls.map((c) => JSON.parse(String(c.init?.body)))).toEqual([
      { mode: 'page', page_id: 9, engine: 'ollama', detect_backend: 'auto' },
      {},
      { page_id: 9 },
      { formats: ['zip', 'pdf'] },
    ])
  })

  it('points downloads at the artifact kinds', () => {
    expect(scanlateExportUrl(4, 'zip')).toBe('/api/artifacts/dramas/4/scanlate_zip')
    expect(scanlateExportUrl(4, 'pdf')).toBe('/api/artifacts/dramas/4/scanlate_pdf')
  })
})
