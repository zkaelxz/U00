import { afterEach, describe, expect, it, vi } from 'vitest'

import { getPcMode, resetPcModeForTests } from './pcOnly'
import {
  addReevalCandidate, estimateReeval, getReevalDecisions, getReevalOverview, promoteReevalCandidate,
  rejectReevalCandidate, reopenReevalCandidate, saveReevalSettings, startReevalRun,
} from './reeval'

function reply(status: number, body: unknown) {
  const mock = vi.fn().mockResolvedValue(new Response(JSON.stringify(body), { status }))
  return { mock, f: mock as unknown as typeof fetch }
}

const localHeader = (init: RequestInit) => new Headers(init.headers).get('X-Baihe-Local')
const bodyOf = (init: RequestInit) => (init.body === undefined ? undefined : JSON.parse(String(init.body)))

afterEach(() => resetPcModeForTests())

describe('model re-evaluation api', () => {
  it('reads the overview and the decision history with GETs', async () => {
    const { mock, f } = reply(200, {})
    await getReevalOverview(f)
    await getReevalDecisions(f)
    const calls = mock.mock.calls as [string, RequestInit][]
    expect(calls.map((c) => c[0])).toEqual(['/api/models/reeval', '/api/models/reeval/decisions'])
    for (const [, init] of calls) expect(init.method).toBeUndefined()
  })

  it('the estimate is a bodyless POST that spends nothing (no confirm)', async () => {
    const { mock, f } = reply(200, { configs: [] })
    await estimateReeval(f)
    const [url, init] = mock.mock.calls[0] as [string, RequestInit]
    expect(url).toBe('/api/models/reeval/estimate')
    expect(init.method).toBe('POST')
    expect(init.body).toBeUndefined()
  })

  it('every write is a PC-only POST; run and promote send confirm: true', async () => {
    const { mock, f } = reply(200, {})
    await saveReevalSettings({ schedule_enabled: true, interval_days: 30, tier: null, set_name: 'flores' }, f)
    await addReevalCandidate({ engine: 'deepseek', model: 'deepseek-v4-flash' }, f)
    await rejectReevalCandidate(4, 'worse on names', f)
    await reopenReevalCandidate(4, f)
    await promoteReevalCandidate(5, 'cheaper, same score', f)
    await startReevalRun(f)
    const calls = mock.mock.calls as [string, RequestInit][]
    expect(calls.map((c) => c[0])).toEqual([
      '/api/models/reeval/settings',
      '/api/models/reeval/candidates',
      '/api/models/reeval/candidates/4/reject',
      '/api/models/reeval/candidates/4/reopen',
      '/api/models/reeval/candidates/5/promote',
      '/api/models/reeval/run',
    ])
    for (const [, init] of calls) {
      expect(init.method).toBe('POST')
      expect(localHeader(init)).toBe('1')
    }
    expect(calls.map((c) => bodyOf(c[1]))).toEqual([
      { schedule_enabled: true, interval_days: 30, tier: null, set_name: 'flores' },
      { engine: 'deepseek', model: 'deepseek-v4-flash' },
      { reason: 'worse on names' },
      undefined,
      { confirm: true, reason: 'cheaper, same score' },
      { confirm: true },
    ])
  })

  it('a 403 on a write marks the tab remote and surfaces as an error', async () => {
    const { f } = reply(403, { error: { code: 'forbidden', message: 'PC only.' } })
    await expect(startReevalRun(f)).rejects.toMatchObject({ status: 403 })
    expect(getPcMode()).toBe('remote')
  })

  it('a refused run keeps the server code and plain message', async () => {
    const { f } = reply(409, { error: { code: 'unsupported_operation', message: 'Add a candidate model first.' } })
    await expect(startReevalRun(f)).rejects.toMatchObject({ code: 'unsupported_operation', message: 'Add a candidate model first.' })
  })
})
