import { afterEach, describe, expect, it, vi } from 'vitest'

import { getBrowserInstallStatus, installBrowser } from './browserInstall'
import { resetPcModeForTests } from './pcOnly'

function reply(status: number, body: unknown) {
  const mock = vi.fn().mockResolvedValue(new Response(JSON.stringify(body), { status }))
  return { mock, f: mock as unknown as typeof fetch }
}

afterEach(() => resetPcModeForTests())

describe('browser install api', () => {
  it('reads the status', async () => {
    const { mock, f } = reply(200, {})
    await getBrowserInstallStatus(f)
    expect(mock.mock.calls[0][0]).toBe('/api/diagnostics/browser')
  })

  it('install sends only the confirm, PC only', async () => {
    const { mock, f } = reply(200, { job_id: 'browser_install', started: true })
    const out = await installBrowser(f)
    const [url, init] = mock.mock.calls[0]
    expect(url).toBe('/api/diagnostics/browser/install')
    expect(JSON.parse(init.body)).toEqual({ confirm: true })
    expect(init.method).toBe('POST')
    expect(new Headers(init.headers).get('X-Baihe-Local')).toBe('1')
    expect(out.job_id).toBe('browser_install')
  })
})
