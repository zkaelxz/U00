import { afterEach, describe, expect, it, vi } from 'vitest'

import { getRealModelCheck, startRealModelCheck } from './realModelCheck'
import { getPcMode, resetPcModeForTests } from './pcOnly'

function reply(status: number, body: unknown) {
  const mock = vi.fn().mockResolvedValue(new Response(JSON.stringify(body), { status }))
  return { mock, f: mock as unknown as typeof fetch }
}

afterEach(() => resetPcModeForTests())

describe('real-model check api', () => {
  it('reads the latest state', async () => {
    const { mock, f } = reply(200, { checks: [] })
    await getRealModelCheck(f)
    expect(mock.mock.calls[0][0]).toBe('/api/diagnostics/real-model-check')
  })

  it('start sends only the confirm, PC only; a 403 marks the tab remote', async () => {
    const ok = reply(200, { job_id: 'real_model_check', started: true })
    await startRealModelCheck(ok.f)
    const [url, init] = ok.mock.mock.calls[0]
    expect(url).toBe('/api/diagnostics/real-model-check')
    expect(init.method).toBe('POST')
    expect(JSON.parse(init.body)).toEqual({ confirm: true })
    expect(new Headers(init.headers).get('X-Baihe-Local')).toBe('1')

    const denied = reply(403, { error: { code: 'forbidden', message: 'PC only.' } })
    await expect(startRealModelCheck(denied.f)).rejects.toMatchObject({ status: 403 })
    expect(getPcMode()).toBe('remote')
  })
})
