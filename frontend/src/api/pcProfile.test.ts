import { afterEach, describe, expect, it, vi } from 'vitest'

import { setPcProfilePin, unlockPcProfile, validPin } from './pcProfile'

afterEach(() => vi.unstubAllGlobals())

function stubFetch(body: unknown) {
  const fetchMock = vi.fn(async () => new Response(JSON.stringify(body), { status: 200, headers: { 'Content-Type': 'application/json' } }))
  vi.stubGlobal('fetch', fetchMock)
  return fetchMock
}

describe('pc profile api', () => {
  it('accepts only 6 to 12 ASCII digits as a PIN', () => {
    for (const ok of ['123456', '123456789012']) expect(validPin(ok)).toBe(true)
    for (const bad of ['', '12345', 'abcdef', '12 3456', '1234567890123']) expect(validPin(bad)).toBe(false)
  })

  it('sends the PIN in the body of a PC-only POST, never in the URL', async () => {
    const f = stubFetch({ pin_set: true, guest: false })
    await unlockPcProfile('432100')
    const [url, init] = f.mock.calls[0] as unknown as [string, RequestInit]
    expect(url).toBe('/api/pc-profile/unlock')
    expect(JSON.parse(String(init.body))).toEqual({ pin: '432100' })
    expect(new Headers(init.headers).get('X-Baihe-Local')).toBe('1')
  })

  it('sends the current PIN only when there is one', async () => {
    const f = stubFetch({ pin_set: true, guest: false })
    await setPcProfilePin('111111')
    const [, init] = f.mock.calls[0] as unknown as [string, RequestInit]
    expect(JSON.parse(String(init.body))).toEqual({ new_pin: '111111', current_pin: null, hide_existing: false })
  })
})
