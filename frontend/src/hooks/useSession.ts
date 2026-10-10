/*
 * useSession(): who is signed in, and which screen App shows.
 * A module store (like api/pcOnly.ts), so no provider is needed; the first
 * caller triggers one GET /api/auth/me per page load.
 *
 *   loading      /me has not answered: App shows "Connecting…".
 *   ready        /me answered. auth_enabled && !signed_in -> Login page only.
 *   unavailable  /me failed (API down or restarting): the app renders, routes
 *                still enforce, and /me is retried with capped backoff until
 *                it answers, so one blip doesn't last the whole page load.
 *
 * Any 401 from any call (api/client.ts onUnauthorized) marks the session
 * signed out. App shows the Login page, or, if the app was already on screen,
 * a sign-in overlay above it so unsaved drafts stay mounted. Sign out always
 * shows the Login page, so nothing stays behind on a shared device.
 */
import { useEffect, useSyncExternalStore } from 'react'

import { logout as apiLogout, me as apiMe, type AuthMe } from '../api/auth'
import { ApiError, onUnauthorized } from '../api/client'

export type SessionState =
  | { status: 'loading' }
  // expired: signed out by a 401 while the app was on screen, not by Sign out.
  | { status: 'ready'; me: AuthMe; expired?: boolean }
  | { status: 'unavailable' }

type GateView = 'connecting' | 'login' | 'app'

let state: SessionState = { status: 'loading' }
const listeners = new Set<() => void>()

function set(next: SessionState) {
  state = next
  listeners.forEach((l) => l())
}

export function getSession(): SessionState {
  return state
}

export function subscribeSession(listener: () => void): () => void {
  listeners.add(listener)
  return () => listeners.delete(listener)
}

/** Which screen App shows for a session state. */
export function gateView(s: SessionState): GateView {
  if (s.status === 'loading') return 'connecting'
  if (s.status === 'ready' && s.me.auth_enabled && !s.me.signed_in) return 'login'
  return 'app'
}

/** The signed-in user to show in the header menu, or null (auth off, signed out, unknown). */
export function menuUser(s: SessionState): AuthMe['user'] {
  if (s.status !== 'ready' || !s.me.auth_enabled || !s.me.signed_in) return null
  return s.me.user
}

export const REMOTE_ADMIN_NOTE = 'Changing other people’s items is done on the main PC.'

/** An admin signed in on the household (internet) address. They see everyone's items, but the
 * server strips admin.library and the other admin writes and the override there. */
export function isRemoteAdmin(s: SessionState): boolean {
  const u = menuUser(s)
  return s.status === 'ready' && !!u && u.is_admin && !u.is_local_owner && !s.me.permissions.includes('admin.library')
}

/** Offer Sign out? With a signed-in user, and while /me is unavailable: the
 * session cookie may still be good, and the person must be able to end it. */
export function canSignOut(s: SessionState): boolean {
  return menuUser(s) !== null || s.status === 'unavailable'
}

/** True when a 401 signed this tab out mid-visit: App keeps the app mounted under a sign-in overlay. */
export function sessionExpired(s: SessionState): boolean {
  return s.status === 'ready' && !!s.expired && gateView(s) === 'login'
}

/** The server said 401 (or the user signed out): show the Login page. */
export function markSignedOut(expired = false): void {
  const prev = state.status === 'ready' ? state.me : null
  // A signed-out app keeps polling behind the overlay; each 401 must not re-render it.
  // Sign out still goes through, so the Login page replaces the overlay.
  if (expired && prev && prev.auth_enabled && !prev.signed_in) return
  set({
    status: 'ready',
    expired: expired && gateView(state) === 'app',
    me: {
      // A 401 only happens with auth on.
      auth_enabled: true,
      signed_in: false,
      sign_in_configured: prev?.sign_in_configured ?? true,
      zone: prev?.zone ?? null,
      user: null,
      permissions: [],
    },
  })
}

onUnauthorized(() => markSignedOut(true))

let load: Promise<void> | null = null
let retryTimer: ReturnType<typeof setTimeout> | undefined

/** Delays between /me retries while it is unavailable; the last one repeats. */
export const ME_RETRY_MS = [1000, 2000, 5000, 10000, 30000]

function fetchSession(fetchMe: () => Promise<AuthMe>, attempt: number): Promise<void> {
  return fetchMe().then(
    (me) => set({ status: 'ready', me }),
    (e: unknown) => {
      // A 401 already switched to signed out; nothing to retry.
      if (e instanceof ApiError && e.status === 401) return
      if (state.status !== 'loading' && state.status !== 'unavailable') return
      if (state.status === 'loading') set({ status: 'unavailable' })
      const delay = ME_RETRY_MS[Math.min(attempt, ME_RETRY_MS.length - 1)]
      retryTimer = setTimeout(() => void fetchSession(fetchMe, attempt + 1), delay)
    },
  )
}

/** Fetch /api/auth/me once per page load (retrying while it fails). */
export function loadSession(fetchMe: () => Promise<AuthMe> = () => apiMe()): Promise<void> {
  load ??= fetchSession(fetchMe, 0)
  return load
}

/** Ask /me again now, e.g. after signing in from another tab. */
export function recheckSession(fetchMe: () => Promise<AuthMe> = () => apiMe()): Promise<void> {
  return fetchMe().then(
    (me) => {
      // Still signed out: keep the overlay (and the drafts under it) rather than swap in Login.
      if (sessionExpired(state) && me.auth_enabled && !me.signed_in) return
      set({ status: 'ready', me })
    },
    () => undefined,
  )
}

/** POST /api/auth/logout, then show the Login page. Errors propagate for the caller to show. */
export async function signOut(doLogout: () => Promise<unknown> = () => apiLogout()): Promise<void> {
  try {
    await doLogout()
  } catch (e) {
    // Already signed out on the server: that is the result we wanted.
    if (!(e instanceof ApiError && e.status === 401)) throw e
  }
  markSignedOut()
}

export function useSession(): SessionState {
  const s = useSyncExternalStore(subscribeSession, getSession, getSession)
  useEffect(() => {
    void loadSession()
  }, [])
  return s
}

/** Test-only: forget the session and the cached /me load. */
export function resetSessionForTests(next: SessionState = { status: 'loading' }): void {
  load = null
  clearTimeout(retryTimer)
  state = next
  listeners.clear()
}
