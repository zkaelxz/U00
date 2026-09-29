// Sign-in (Google, household allowlist). The server does the whole OAuth
// dance; the browser only reads /api/auth/me, navigates to /api/auth/login
// and posts /api/auth/logout. No token, client id or secret reaches here.

import { apiUrl, getJson, postJson } from './client'

type Fetch = typeof fetch

export interface AuthUser {
  id: number | null
  email: string | null
  display_name: string | null
  is_admin: boolean
  is_local_owner: boolean
}

export interface AuthMe {
  auth_enabled: boolean
  signed_in: boolean
  // False when auth is on but the PC has no Google client configured yet.
  sign_in_configured: boolean
  zone: string | null
  user: AuthUser | null
  permissions: string[]
}

export const me = (f?: Fetch) => getJson<AuthMe>('/api/auth/me', f)

export const logout = (f?: Fetch) => postJson<unknown>('/api/auth/logout', undefined, f)

/**
 * Where to come back to after Google: a same-site relative path. Anything
 * that isn't "/..." (or is "//host", "/\\host") falls back to "/"; the
 * server checks this again.
 */
export function safeReturnTo(path: string | null | undefined): string {
  if (!path || !path.startsWith('/') || path.startsWith('//') || path.includes('\\')) return '/'
  return path
}

/** The top-level navigation that starts sign-in (the server redirects to Google). */
export function loginHref(returnTo: string): string {
  return apiUrl(`/api/auth/login?return_to=${encodeURIComponent(safeReturnTo(returnTo))}`)
}

export type LoginErrorCode = 'denied' | 'not_allowed' | 'expired' | 'provider_error'

const LOGIN_ERRORS: Record<LoginErrorCode, string> = {
  denied: 'Sign-in was cancelled. You can try again.',
  not_allowed:
    "That Google account isn't on this household's list. Ask the person who runs Baihe on the PC to add it.",
  expired: 'The sign-in took too long and expired. Please try again.',
  provider_error: "Google sign-in didn't work just now. Please try again in a moment.",
}

const GENERIC_LOGIN_ERROR = "Sign-in didn't work. Please try again."

/** The `login_error` code from a query string ("?login_error=denied"), or null. */
export function readLoginError(search: string): string | null {
  const code = new URLSearchParams(search).get('login_error')
  return code ? code : null
}

/** A plain message for a `login_error` code; unknown codes get a generic one. */
export function loginErrorMessage(code: string): string {
  return Object.prototype.hasOwnProperty.call(LOGIN_ERRORS, code)
    ? LOGIN_ERRORS[code as LoginErrorCode]
    : GENERIC_LOGIN_ERROR
}
