import { afterEach, describe, expect, it, vi } from 'vitest'

import type { AuthMe } from '../api/auth'
import { ApiError, getJson } from '../api/client'
import {
  canSignOut, gateView, getSession, isRemoteAdmin, loadSession, markSignedOut, ME_RETRY_MS, menuUser, recheckSession,
  resetSessionForTests, sessionExpired, signOut, subscribeSession,
  type SessionState,
} from './useSession'

const user = { id: 1, email: 'kae@example.com', display_name: 'Kae', is_admin: true, is_local_owner: false }
const meOf = (over: Partial<AuthMe> = {}): AuthMe => ({
  auth_enabled: true, signed_in: true, sign_in_configured: true, zone: 'internet', user, permissions: ['lines.edit'], ...over,
})
const ready = (over: Partial<AuthMe> = {}): SessionState => ({ status: 'ready', me: meOf(over) })

afterEach(() => resetSessionForTests())

describe('gate', () => {
  it('loading shows Connecting', () => {
    expect(gateView({ status: 'loading' })).toBe('connecting')
  })
  it('auth on + signed out shows only Login', () => {
    expect(gateView(ready({ signed_in: false, user: null }))).toBe('login')
    expect(gateView(ready({ signed_in: false, user: null, sign_in_configured: false }))).toBe('login')
  })
  it('auth on + signed in shows the app and the user menu', () => {
    expect(gateView(ready())).toBe('app')
    expect(menuUser(ready())).toEqual(user)
  })
  it('auth off: the app, no menu', () => {
    const off = ready({ auth_enabled: false, signed_in: true, user: { ...user, is_local_owner: true } })
    expect(gateView(off)).toBe('app')
    expect(menuUser(off)).toBeNull()
  })
  it('/me unavailable (API down or restarting): the app, no user, but Sign out stays', () => {
    expect(gateView({ status: 'unavailable' })).toBe('app')
    expect(menuUser({ status: 'unavailable' })).toBeNull()
    expect(canSignOut({ status: 'unavailable' })).toBe(true)
  })
  it('Sign out is offered to a signed-in user, not with auth off or while loading', () => {
    expect(canSignOut(ready())).toBe(true)
    expect(canSignOut(ready({ auth_enabled: false }))).toBe(false)
    expect(canSignOut({ status: 'loading' })).toBe(false)
  })
})

describe('loadSession', () => {
  it('fetches /me once and becomes ready', async () => {
    const m = vi.fn().mockResolvedValue(meOf())
    await Promise.all([loadSession(m), loadSession(m)])
    expect(m).toHaveBeenCalledTimes(1)
    expect(getSession()).toEqual(ready())
  })
  it('a failed /me is unavailable, then retried with capped backoff until it answers', async () => {
    vi.useFakeTimers()
    try {
      const m = vi.fn()
        .mockRejectedValueOnce(new ApiError(502, { code: 'bad_gateway', message: 'x' }))
        .mockRejectedValueOnce(new ApiError(0, { code: 'network_error', message: 'x' }))
        .mockResolvedValue(meOf())
      await loadSession(m)
      expect(getSession()).toEqual({ status: 'unavailable' })
      await vi.advanceTimersByTimeAsync(ME_RETRY_MS[0] - 1)
      expect(m).toHaveBeenCalledTimes(1)
      await vi.advanceTimersByTimeAsync(1)
      expect(m).toHaveBeenCalledTimes(2)
      expect(getSession()).toEqual({ status: 'unavailable' })
      await vi.advanceTimersByTimeAsync(ME_RETRY_MS[1])
      expect(m).toHaveBeenCalledTimes(3)
      expect(getSession()).toEqual(ready())
      await vi.advanceTimersByTimeAsync(60_000)
      expect(m).toHaveBeenCalledTimes(3)
    } finally {
      vi.useRealTimers()
    }
  })
  it('the retry delay stops growing at the cap', async () => {
    vi.useFakeTimers()
    try {
      const m = vi.fn().mockRejectedValue(new ApiError(502, { code: 'bad_gateway', message: 'x' }))
      await loadSession(m)
      const total = ME_RETRY_MS.reduce((a, b) => a + b, 0)
      await vi.advanceTimersByTimeAsync(total)
      const n = m.mock.calls.length
      expect(n).toBe(ME_RETRY_MS.length + 1)
      await vi.advanceTimersByTimeAsync(ME_RETRY_MS[ME_RETRY_MS.length - 1])
      expect(m).toHaveBeenCalledTimes(n + 1)
    } finally {
      vi.useRealTimers()
    }
  })
  it('a 401 from /me is not retried', async () => {
    vi.useFakeTimers()
    try {
      markSignedOut()
      const m = vi.fn().mockRejectedValue(new ApiError(401, { code: 'unauthorized', message: 'x' }))
      await loadSession(m)
      await vi.advanceTimersByTimeAsync(60_000)
      expect(m).toHaveBeenCalledTimes(1)
    } finally {
      vi.useRealTimers()
    }
  })
  it('recheckSession asks /me again and applies the answer', async () => {
    resetSessionForTests(ready({ signed_in: false, user: null }))
    await recheckSession(() => Promise.resolve(meOf()))
    expect(getSession()).toEqual(ready())
  })
})

describe('signed out', () => {
  it('any 401 from the client marks the session signed out', async () => {
    resetSessionForTests(ready())
    const l = vi.fn()
    subscribeSession(l)
    const f = (async () =>
      new Response(JSON.stringify({ error: { code: 'unauthorized', message: 'x' } }), { status: 401 })) as typeof fetch
    await getJson('/api/library/dramas', f).catch(() => null)
    const s = getSession()
    expect(gateView(s)).toBe('login')
    expect(s.status === 'ready' && s.me.user).toBeNull()
    expect(s.status === 'ready' && s.me.permissions).toEqual([])
    expect(l).toHaveBeenCalled()
  })
  it('a 401 before /me answers still shows Login', async () => {
    markSignedOut()
    expect(gateView(getSession())).toBe('login')
    // The late /me failure does not undo it.
    await loadSession(() => Promise.reject(new ApiError(401, { code: 'unauthorized', message: 'x' })))
    expect(gateView(getSession())).toBe('login')
  })
  it('keeps sign_in_configured from /me', () => {
    resetSessionForTests(ready({ sign_in_configured: false }))
    markSignedOut()
    const s = getSession()
    expect(s.status === 'ready' && s.me.sign_in_configured).toBe(false)
  })
  it('signOut posts logout then shows Login; a 401 counts as signed out', async () => {
    resetSessionForTests(ready())
    const doLogout = vi.fn().mockResolvedValue({})
    await signOut(doLogout)
    expect(doLogout).toHaveBeenCalledTimes(1)
    expect(gateView(getSession())).toBe('login')

    resetSessionForTests(ready())
    await signOut(() => Promise.reject(new ApiError(401, { code: 'unauthorized', message: 'x' })))
    expect(gateView(getSession())).toBe('login')
  })
  it('signOut failure keeps the session and rethrows', async () => {
    resetSessionForTests(ready())
    await expect(
      signOut(() => Promise.reject(new ApiError(0, { code: 'network_error', message: 'down' }))),
    ).rejects.toThrow('down')
    expect(gateView(getSession())).toBe('app')
  })
})

describe('expired mid-visit (sign-in overlay)', () => {
  const signedOutMe = meOf({ signed_in: false, user: null, permissions: [] })
  const unauthorized = (async () =>
    new Response(JSON.stringify({ error: { code: 'unauthorized', message: 'x' } }), { status: 401 })) as typeof fetch

  it('a 401 while the app is on screen is expired, so App keeps the app mounted', async () => {
    resetSessionForTests(ready())
    await getJson('/api/library/dramas', unauthorized).catch(() => null)
    expect(gateView(getSession())).toBe('login')
    expect(sessionExpired(getSession())).toBe(true)
  })
  it('a 401 while /me is unavailable is expired too; one before /me answers is not', async () => {
    resetSessionForTests({ status: 'unavailable' })
    await getJson('/api/x', unauthorized).catch(() => null)
    expect(sessionExpired(getSession())).toBe(true)
    resetSessionForTests()
    await getJson('/api/x', unauthorized).catch(() => null)
    expect(sessionExpired(getSession())).toBe(false)
  })
  it('later 401s do not notify again', async () => {
    resetSessionForTests(ready())
    await getJson('/api/x', unauthorized).catch(() => null)
    const l = vi.fn()
    subscribeSession(l)
    await getJson('/api/x', unauthorized).catch(() => null)
    expect(l).not.toHaveBeenCalled()
  })
  it('Sign out is never expired: the Login page replaces everything', async () => {
    resetSessionForTests(ready())
    await signOut(() => Promise.reject(new ApiError(401, { code: 'unauthorized', message: 'x' })))
    expect(sessionExpired(getSession())).toBe(false)
    resetSessionForTests(ready())
    // The logout POST itself answering 401 goes through the client's 401 hook first.
    await signOut(() => getJson('/api/auth/logout', unauthorized))
    expect(gateView(getSession())).toBe('login')
    expect(sessionExpired(getSession())).toBe(false)
  })
  it('a recheck that is still signed out keeps the overlay; signed in clears it', async () => {
    resetSessionForTests(ready())
    markSignedOut(true)
    await recheckSession(() => Promise.resolve(signedOutMe))
    expect(sessionExpired(getSession())).toBe(true)
    await recheckSession(() => Promise.resolve(meOf()))
    expect(getSession()).toEqual(ready())
  })
})

describe('isRemoteAdmin', () => {
  it('is an admin without admin.library on a signed-in session', () => {
    expect(isRemoteAdmin(ready({ permissions: ['library.read', 'admin.users.read'] }))).toBe(true)
  })
  it('is not the PC admin, a member, the local owner, auth off, or unknown', () => {
    expect(isRemoteAdmin(ready({ permissions: ['library.read', 'admin.library'] }))).toBe(false)
    expect(isRemoteAdmin(ready({ user: { ...user, is_admin: false } }))).toBe(false)
    expect(isRemoteAdmin(ready({ user: { ...user, is_local_owner: true } }))).toBe(false)
    expect(isRemoteAdmin(ready({ auth_enabled: false }))).toBe(false)
    expect(isRemoteAdmin({ status: 'unavailable' })).toBe(false)
    expect(isRemoteAdmin({ status: 'loading' })).toBe(false)
  })
})
