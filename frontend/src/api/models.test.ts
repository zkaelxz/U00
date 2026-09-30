import { afterEach, describe, expect, it, vi } from 'vitest'

import { checkModelProviders, getModelStatus, switchPresetModel } from './models'
import { getPcMode, resetPcModeForTests } from './pcOnly'

function reply(status: number, body: unknown) {
  const mock = vi.fn().mockResolvedValue(new Response(JSON.stringify(body), { status }))
  return { mock, f: mock as unknown as typeof fetch }
}

const localHeader = (init: RequestInit) => new Headers(init.headers).get('X-Baihe-Local')

afterEach(() => resetPcModeForTests())

describe('models api', () => {
  it('reads the cached status with a plain GET', async () => {
    const status = { items: [], warnings: 0, checked_at: null, engines_checked: {}, registry_updated: '2026-09-29' }
    const { mock, f } = reply(200, status)
    await expect(getModelStatus(f)).resolves.toEqual(status)
    const [url, init] = mock.mock.calls[0] as [string, RequestInit]
    expect(url).toBe('/api/models/status')
    expect(init.method).toBeUndefined()
  })

  it('the provider check is a bodyless PC-only POST', async () => {
    const { mock, f } = reply(200, { items: [] })
    await checkModelProviders(f)
    const [url, init] = mock.mock.calls[0] as [string, RequestInit]
    expect(url).toBe('/api/models/check')
    expect(init.method).toBe('POST')
    expect(init.body).toBeUndefined()
    expect(localHeader(init)).toBe('1')
  })

  it('the switch sends the model seen, the replacement and confirm, PC only', async () => {
    const { mock, f } = reply(200, { preset_id: 4, engine: 'claude', from_model: 'a', to_model: 'b' })
    await switchPresetModel(4, 'claude-sonnet-4-6', 'claude-sonnet-5', f)
    const [url, init] = mock.mock.calls[0] as [string, RequestInit]
    expect(url).toBe('/api/models/presets/4/switch')
    expect(init.method).toBe('POST')
    expect(localHeader(init)).toBe('1')
    expect(JSON.parse(String(init.body))).toEqual({ from_model: 'claude-sonnet-4-6', to_model: 'claude-sonnet-5', confirm: true })
  })

  it('a 429 and a 409 surface as ApiErrors with their codes', async () => {
    const tooSoon = reply(429, { error: { code: 'rate_limited', message: 'Models were checked less than a minute ago.' } })
    await expect(checkModelProviders(tooSoon.f)).rejects.toMatchObject({ status: 429, code: 'rate_limited' })
    const stale = reply(409, { error: { code: 'conflict', message: "The preset's model changed since you looked." } })
    await expect(switchPresetModel(1, 'a', 'b', stale.f)).rejects.toMatchObject({ status: 409, code: 'conflict' })
    expect(getPcMode()).toBe('unknown')
  })

  it('a 403 on a PC-only call marks the tab remote', async () => {
    const { f } = reply(403, { error: { code: 'forbidden', message: 'PC only.' } })
    await expect(checkModelProviders(f)).rejects.toMatchObject({ status: 403 })
    expect(getPcMode()).toBe('remote')
  })
})
