import { afterEach, describe, expect, it, vi } from 'vitest'

import { auditQuery, listAdminUsers, listAudit, revokeUserAdmin, revokeUserSessions, setUserActive } from './adminUsers'
import { ApiError } from './client'
import { getPcMode, resetPcModeForTests } from './pcOnly'

function reply(status: number, body: unknown) {
  const mock = vi.fn().mockResolvedValue(new Response(JSON.stringify(body), { status }))
  return { mock, f: mock as unknown as typeof fetch }
}

afterEach(() => {
  resetPcModeForTests()
})

describe('admin users api', () => {
  it('reads are GETs on the admin URLs', async () => {
    const { mock, f } = reply(200, { users: [], events: [], next_before_id: null, actions: [] })
    await listAdminUsers(f)
    await listAudit({}, f)
    expect(mock.mock.calls.map(([u]) => u)).toEqual(['/api/admin/users', '/api/admin/audit?limit=50'])
    for (const [, init] of mock.mock.calls) expect(init.method ?? 'GET').toBe('GET')
  })

  it('writes are bodyless POSTs to the right action', async () => {
    const { mock, f } = reply(200, {})
    await setUserActive(7, false, f)
    await setUserActive(7, true, f)
    await revokeUserSessions(7, f)
    await revokeUserAdmin(7, f)
    expect(mock.mock.calls.map(([u, init]) => `${init.method} ${u}`)).toEqual([
      'POST /api/admin/users/7/deactivate',
      'POST /api/admin/users/7/activate',
      'POST /api/admin/users/7/revoke-sessions',
      'POST /api/admin/users/7/revoke-admin',
    ])
    for (const [, init] of mock.mock.calls) expect(init.body).toBeUndefined()
  })

  it('builds the audit query, leaving empty filters out', () => {
    expect(auditQuery()).toBe('?limit=50')
    expect(auditQuery({ before_id: 40, action: 'login.success', user_id: 3, limit: 10 }))
      .toBe('?limit=10&before_id=40&action=login.success&user_id=3')
    expect(auditQuery({ action: '', user_id: null, before_id: null })).toBe('?limit=50')
    expect(auditQuery({ action: 'a&b=c' })).toBe('?limit=50&action=a%26b%3Dc')
  })

  it('a 403 or 409 is an ApiError and does not switch the page to remote mode', async () => {
    const { f } = reply(409, { error: { code: 'conflict', message: "You can't deactivate your own account." } })
    const err = await setUserActive(1, false, f).catch((e: unknown) => e)
    expect(err).toBeInstanceOf(ApiError)
    expect((err as ApiError).code).toBe('conflict')
    const denied = reply(403, { error: { code: 'forbidden', message: 'Not allowed.' } })
    await expect(revokeUserSessions(1, denied.f)).rejects.toMatchObject({ status: 403 })
    expect(getPcMode()).not.toBe('remote')
  })

  it('a last-admin refusal keeps the server message for the banner', async () => {
    const msg = "This is the last active admin. Baihe needs at least one, so their admin rights can't be removed."
    const { f } = reply(409, { error: { code: 'conflict', message: msg } })
    const err = await revokeUserAdmin(2, f).catch((e: unknown) => e)
    expect(err).toBeInstanceOf(ApiError)
    expect((err as ApiError).message).toBe(msg)
    expect(getPcMode()).not.toBe('remote')
  })
})
