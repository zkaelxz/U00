import { afterEach, describe, expect, it, vi } from 'vitest'

import { getExtensionStatus, revealExtensionToken, setExtensionEnabled } from './extension'
import { getPcMode, resetPcModeForTests } from './pcOnly'

function reply(status: number, body: unknown) {
  const mock = vi.fn().mockResolvedValue(new Response(JSON.stringify(body), { status }))
  return { mock, f: mock as unknown as typeof fetch }
}

const localHeader = (init: RequestInit) => new Headers(init.headers).get('X-Baihe-Local')

afterEach(() => resetPcModeForTests())

describe('extension api', () => {
  it('status is a PC-only GET', async () => {
    const { mock, f } = reply(200, { enabled: true, running: false })
    expect(await getExtensionStatus(f)).toEqual({ enabled: true, running: false })
    const [url, init] = mock.mock.calls[0]
    expect(url).toBe('/api/extension/status')
    expect(init.method).toBeUndefined()
    expect(localHeader(init)).toBe('1')
  })

  it('enabled sends only the flag', async () => {
    const { mock, f } = reply(200, { enabled: false, running: true, restart_needed: true })
    const out = await setExtensionEnabled(false, f)
    expect(out.restart_needed).toBe(true)
    const [url, init] = mock.mock.calls[0]
    expect(url).toBe('/api/extension/enabled')
    expect(JSON.parse(init.body)).toEqual({ enabled: false })
    expect(localHeader(init)).toBe('1')
  })

  it('token posts confirm and returns the token to the caller only', async () => {
    const { mock, f } = reply(200, { token: 'abc' })
    expect(await revealExtensionToken(f)).toEqual({ token: 'abc' })
    const [url, init] = mock.mock.calls[0]
    expect(url).toBe('/api/extension/token')
    expect(JSON.parse(init.body)).toEqual({ confirm: true })
  })

  it('a 403 marks the tab remote', async () => {
    const { f } = reply(403, { error: { code: 'forbidden', message: 'PC only.' } })
    await expect(getExtensionStatus(f)).rejects.toMatchObject({ status: 403 })
    expect(getPcMode()).toBe('remote')
  })
})
