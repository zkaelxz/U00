import { afterEach, describe, expect, it, vi } from 'vitest'

import {
  getExtensionEngine, getExtensionStatus, revealExtensionToken, setExtensionEnabled, setExtensionEngine,
} from './extension'
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
    const { mock, f } = reply(200, { enabled: false, running: false, restart_needed: false })
    const out = await setExtensionEnabled(false, f)
    expect(out).toEqual({ enabled: false, running: false, restart_needed: false })
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

  it('engine read is a plain GET', async () => {
    const body = { engine: 'claude', model: null, ready: true, engines: [] }
    const { mock, f } = reply(200, body)
    expect(await getExtensionEngine(f)).toEqual(body)
    const [url, init] = mock.mock.calls[0]
    expect(url).toBe('/api/extension/engine')
    expect(init.method).toBeUndefined()
  })

  it('engine save sends only engine and model, PC only', async () => {
    const { mock, f } = reply(200, { engine: null, model: null, ready: false, engines: [] })
    await setExtensionEngine(null, null, f)
    const [url, init] = mock.mock.calls[0]
    expect(url).toBe('/api/extension/engine')
    expect(init.method).toBe('POST')
    expect(JSON.parse(init.body)).toEqual({ engine: null, model: null })
    expect(localHeader(init)).toBe('1')
  })
})
