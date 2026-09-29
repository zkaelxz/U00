import { afterEach, describe, expect, it, vi } from 'vitest'

import { getPcMode, resetPcModeForTests } from './pcOnly'
import {
  clearSourcesCache,
  dismissNotification,
  getSourcesJobResult,
  listAttempts,
  resetSourceHealth,
  rollbackProfile,
  setAdultEnabled,
  setSourceEnabled,
  seriesJobId,
  startSearch,
  startSeries,
  untrackSeries,
  updateSourcesSettings,
} from './sources'

function reply(status: number, body: unknown) {
  const mock = vi.fn().mockResolvedValue(new Response(JSON.stringify(body), { status }))
  return { mock, f: mock as unknown as typeof fetch }
}

const localHeader = (init: RequestInit) => new Headers(init.headers).get('X-Baihe-Local')

afterEach(() => resetPcModeForTests())

describe('sources api', () => {
  it('search leaves sources out unless limited', async () => {
    const { mock, f } = reply(200, { job_id: 'sources_search' })
    await startSearch('abc', undefined, f)
    await startSearch('abc', ['a'], f)
    expect(mock.mock.calls[0][0]).toBe('/api/sources/search')
    expect(JSON.parse(mock.mock.calls[0][1].body)).toEqual({ query: 'abc' })
    expect(JSON.parse(mock.mock.calls[1][1].body)).toEqual({ query: 'abc', sources: ['a'] })
  })

  it('series posts only the id; the job id is per source', async () => {
    const { mock, f } = reply(200, { job_id: 'sources_series_a b' })
    await startSeries('a b', '123', f)
    expect(mock.mock.calls[0][0]).toBe('/api/sources/a%20b/series')
    expect(JSON.parse(mock.mock.calls[0][1].body)).toEqual({ series_id: '123' })
    expect(seriesJobId('x')).toBe('sources_series_x')
  })

  it('reads a job result and attempts', async () => {
    const { mock, f } = reply(200, [])
    await getSourcesJobResult('sources_search', f)
    await listAttempts('foo', 20, f)
    expect(mock.mock.calls.map((c) => c[0])).toEqual([
      '/api/sources/jobs/sources_search/result',
      '/api/sources/foo/attempts?limit=20',
    ])
  })

  it('dismiss and untrack are plain posts', async () => {
    const { mock, f } = reply(200, [])
    await dismissNotification(7, f)
    await untrackSeries('foo', 's1', f)
    expect(mock.mock.calls[0][0]).toBe('/api/sources/notifications/7/dismiss')
    expect(mock.mock.calls[1][0]).toBe('/api/sources/tracked')
    expect(JSON.parse(mock.mock.calls[1][1].body)).toEqual({ source: 'foo', series_id: 's1', tracked: false })
  })

  it('settings calls are PC-only with the local header', async () => {
    const { mock, f } = reply(200, {})
    await setSourceEnabled('foo', false, f)
    await setAdultEnabled('foo', true, f)
    await resetSourceHealth('foo', f)
    await updateSourcesSettings({ pace_max_delay: 10 }, f)
    await clearSourcesCache(f)
    await rollbackProfile('site.example', 'novel', 2, f)
    expect(mock.mock.calls.map((c) => c[0])).toEqual([
      '/api/sources/foo/enabled',
      '/api/sources/foo/adult',
      '/api/sources/foo/health/reset',
      '/api/sources/settings',
      '/api/sources/cache/clear',
      '/api/sources/profiles/site.example/novel/rollback',
    ])
    const bodies = mock.mock.calls.map(([, i]) => (i.body ? JSON.parse(i.body) : undefined))
    expect(bodies).toEqual([
      { enabled: false }, { enabled: true }, undefined, { pace_max_delay: 10 }, { confirm: true }, { version: 2 },
    ])
    for (const [, init] of mock.mock.calls) expect(localHeader(init)).toBe('1')
  })

  it('a 403 on a settings call marks the tab remote', async () => {
    const { f } = reply(403, { error: { code: 'forbidden', message: 'no' } })
    await expect(updateSourcesSettings({ max_retries: 2 }, f)).rejects.toMatchObject({ status: 403 })
    expect(getPcMode()).toBe('remote')
  })
})
