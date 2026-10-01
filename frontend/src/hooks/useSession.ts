/*
 * useSession(): who is signed in, and which screen App shows.
 * A module store (like api/pcOnly.ts), so no provider is needed; the first
 * caller triggers one GET /api/auth/me per page load.
 *
 *   loading      /me has not answered: App shows "Connecting…".
 *   ready        /me answered. auth_enabled && !signed_in -> Login page only.
 *   unavailable  /me failed (API down, or an older API without it): the app
 *                renders exactly as before sign-in existed; routes still enforce.
 *
 * Any 401 from any call (api/client.ts onUnauthorized) marks the session
 * signed out, which swaps in the Login page.
 */
import { useEffect, useSyncExternalStore } from 'react'

import { logout as apiLogout, me as apiMe, type AuthMe } from '../api/auth'
import { ApiError, onUnauthorized } from '../api/client'

export type SessionState =
  | { status: 'loading' }
  | { status: 'ready'; me: AuthMe }
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

/** The server said 401 (or the user signed out): show the Login page. */
export function markSignedOut(): void {
  const prev = state.status === 'ready' ? state.me : null
  set({
    status: 'ready',
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

onUnauthorized(markSignedOut)

let load: Promise<void> | null = null

/** Fetch /api/auth/me once per page load. */
export function loadSession(fetchMe: () => Promise<AuthMe> = () => apiMe()): Promise<void> {
  load ??= fetchMe().then(
    (me) => set({ status: 'ready', me }),
    () => {
      // A 401 already switched to signed out; anything else: carry on as before sign-in.
      if (state.status === 'loading') set({ status: 'unavailable' })
    },
  )
  return load
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
  state = next
  listeners.clear()
}
