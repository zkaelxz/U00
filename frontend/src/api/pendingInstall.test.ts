import { afterEach, describe, expect, it, vi } from 'vitest'

import { cancelPendingInstall, dismissInstallResult, getPendingInstall, planInstall, queueInstall } from './pendingInstall'
import { getPcMode, resetPcModeForTests } from './pcOnly'

function reply(status: number, body: unknown) {
  const mock = vi.fn().mockResolvedValue(new Response(JSON.stringify(body), { status }))
  return { mock, f: mock as unknown as typeof fetch }
}

const localHeader = (init: RequestInit) => new Headers(init.headers).get('X-Baihe-Local')

afterEach(() => resetPcModeForTests())

describe('pending install api', () => {
  it('reads the queued install', async () => {
    const { mock, f } = reply(200, {})
    await getPendingInstall(f)
    expect(mock.mock.calls[0][0]).toBe('/api/diagnostics/pending-install')
  })

  it('plan and queue send registry keys and flags only, PC only', async () => {
    const { mock, f } = reply(200, {})
    await planInstall(['paddleocr', 'paddlepaddle'], f)
    await queueInstall(['paddleocr'], true, f)
    const [planUrl, planInit] = mock.mock.calls[0]
    expect(planUrl).toBe('/api/diagnostics/pending-install/plan')
    expect(JSON.parse(planInit.body)).toEqual({ packages: ['paddleocr', 'paddlepaddle'] })
    const [queueUrl, queueInit] = mock.mock.calls[1]
    expect(queueUrl).toBe('/api/diagnostics/pending-install/queue')
    expect(JSON.parse(queueInit.body)).toEqual({ packages: ['paddleocr'], confirm: true, accept_risk: true })
    expect(localHeader(planInit)).toBe('1')
    expect(localHeader(queueInit)).toBe('1')
  })

  it('cancel and dismiss are POSTs with the PC header', async () => {
    const { mock, f } = reply(200, {})
    await cancelPendingInstall(f)
    await dismissInstallResult(f)
    expect(mock.mock.calls.map((c) => c[0])).toEqual([
      '/api/diagnostics/pending-install/cancel', '/api/diagnostics/pending-install/dismiss',
    ])
    expect(mock.mock.calls.every((c) => c[1].method === 'POST' && localHeader(c[1]) === '1')).toBe(true)
  })

  it('a 403 marks the tab remote', async () => {
    const { f } = reply(403, { error: { code: 'forbidden', message: 'PC only.' } })
    await expect(cancelPendingInstall(f)).rejects.toMatchObject({ status: 403 })
    expect(getPcMode()).toBe('remote')
  })
})
