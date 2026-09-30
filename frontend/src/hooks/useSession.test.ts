import { afterEach, describe, expect, it, vi } from 'vitest'

import type { AuthMe } from '../api/auth'
import { ApiError, getJson } from '../api/client'
import {
  gateView, getSession, isRemoteAdmin, loadSession, markSignedOut, menuUser, resetSessionForTests, signOut, subscribeSession,
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
  it('/me unavailable (older API or down): the app as before, no menu', () => {
    expect(gateView({ status: 'unavailable' })).toBe('app')
    expect(menuUser({ status: 'unavailable' })).toBeNull()
  })
})

describe('loadSession', () => {
  it('fetches /me once and becomes ready', async () => {
    const m = vi.fn().mockResolvedValue(meOf())
    await Promise.all([loadSession(m), loadSession(m)])
    expect(m).toHaveBeenCalledTimes(1)
    expect(getSession()).toEqual(ready())
  })
  it('a failed /me (e.g. 404 before the backend has it) is unavailable', async () => {
    await loadSession(() => Promise.reject(new ApiError(404, { code: 'not_found', message: 'x' })))
    expect(getSession()).toEqual({ status: 'unavailable' })
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
