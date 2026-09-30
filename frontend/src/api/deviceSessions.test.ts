import { describe, expect, it, vi } from 'vitest'

import { listDeviceSessions, signOutDevice, signOutOtherDevices } from './deviceSessions'

function reply(status: number, body: unknown) {
  const mock = vi.fn().mockResolvedValue(new Response(JSON.stringify(body), { status }))
  return { mock, f: mock as unknown as typeof fetch }
}

describe('device sessions api', () => {
  it('lists with a GET and signs out with bodyless POSTs', async () => {
    const { mock, f } = reply(200, { sessions: [], idle_timeout_days: 14, absolute_timeout_days: 30, revoked: 1 })
    await listDeviceSessions(f)
    await signOutDevice(12, f)
    await signOutOtherDevices(f)
    expect(mock.mock.calls.map(([u, init]) => `${init.method ?? 'GET'} ${u}`)).toEqual([
      'GET /api/auth/sessions',
      'POST /api/auth/sessions/12/revoke',
      'POST /api/auth/sessions/revoke-others',
    ])
    for (const [, init] of mock.mock.calls.slice(1)) expect(init.body).toBeUndefined()
  })
})
