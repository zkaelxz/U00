import { afterEach, describe, expect, it, vi } from 'vitest'

import { resetPcModeForTests } from './pcOnly'
import {
  IDENTIFY_JOB_ID,
  PREFLIGHT_JOB_ID,
  getIdentifiedResource,
  listExtractions,
  previewPasted,
  startBulkPasted,
  startIdentifyMedia,
  startPastedImport,
  startPreflight,
  utf8Bytes,
} from './sourcesTools'

function reply(status: number, body: unknown) {
  const mock = vi.fn().mockResolvedValue(new Response(JSON.stringify(body), { status }))
  return { mock, f: mock as unknown as typeof fetch }
}

const bodyOf = (mock: ReturnType<typeof vi.fn>, i = 0) => JSON.parse(mock.mock.calls[i][1].body as string)
const header = (mock: ReturnType<typeof vi.fn>, name: string, i = 0) =>
  new Headers((mock.mock.calls[i][1] as RequestInit).headers).get(name)

afterEach(() => resetPcModeForTests())

describe('sources tools api', () => {
  it('job ids match the server', () => {
    expect(PREFLIGHT_JOB_ID).toBe('sources_url_preflight')
    expect(IDENTIFY_JOB_ID).toBe('sources_url_identify')
  })

  it('posts the site check, pasted preview and pasted import', async () => {
    const { mock, f } = reply(200, { job_id: 'x' })
    await startPreflight('https://a.example/b', f)
    await previewPasted('https://a.example/b', '<html>', f)
    await startPastedImport('https://a.example/b', '<html>', 3, f)
    expect(mock.mock.calls.map((c) => c[0])).toEqual([
      '/api/sources/url/preflight',
      '/api/sources/url/preview-pasted',
      '/api/sources/url/import-pasted',
    ])
    expect(bodyOf(mock, 0)).toEqual({ url: 'https://a.example/b' })
    expect(bodyOf(mock, 1)).toEqual({ url: 'https://a.example/b', html: '<html>' })
    expect(bodyOf(mock, 2)).toEqual({ url: 'https://a.example/b', html: '<html>', drama_id: 3 })
  })

  it('identify sends html only when pasted', async () => {
    const { mock, f } = reply(200, { job_id: 'x' })
    await startIdentifyMedia('https://v.example/1', null, f)
    await startIdentifyMedia('https://v.example/1', '<video>', f)
    expect(bodyOf(mock, 0)).toEqual({ url: 'https://v.example/1' })
    expect(bodyOf(mock, 1)).toEqual({ url: 'https://v.example/1', html: '<video>' })
  })

  it('the full resource address is a PC-only GET', async () => {
    const { mock, f } = reply(200, { run_id: 'r1', index: 2, resource_url: 'https://c.example/a.mp4?s=1' })
    const r = await getIdentifiedResource('r 1', 2, f)
    expect(mock.mock.calls[0][0]).toBe('/api/sources/url/identify-media/resource?run_id=r%201&index=2')
    expect(header(mock, 'X-Baihe-Local')).toBe('1')
    expect(r.resource_url).toBe('https://c.example/a.mp4?s=1')
  })

  it('lists extractions and posts the pasted listing', async () => {
    const { mock, f } = reply(200, [])
    await listExtractions(10, f)
    await startBulkPasted('一 二', 'jj', undefined, f)
    await startBulkPasted('一 二', 'jj', 'claude', f)
    expect(mock.mock.calls[0][0]).toBe('/api/sources/url/extractions?limit=10')
    expect(mock.mock.calls[1][0]).toBe('/api/discover/bulk-extract/pasted')
    expect(bodyOf(mock, 1)).toEqual({ text: '一 二', source_label: 'jj' })
    expect(bodyOf(mock, 2)).toEqual({ text: '一 二', source_label: 'jj', engine: 'claude' })
  })

  it('counts UTF-8 bytes', () => {
    expect(utf8Bytes('ab')).toBe(2)
    expect(utf8Bytes('白')).toBe(3)
  })
})
