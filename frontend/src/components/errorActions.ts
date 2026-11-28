import { canReadSettings, getSession } from '../hooks/useSession'
import { routeHref } from '../router'
import { canOpenDiagnostics } from './errorMessages'

export interface ErrorAction {
  label: string
  href: string
}

// A package key worth putting in a link: letters, digits and . _ - only (never text from a message).
const PACKAGE_KEY = /^[A-Za-z0-9][A-Za-z0-9._-]{0,59}$/

/**
 * The fix that matches the CAUSE of a dependency error: a missing key or token is added in
 * Settings, a missing package is installed in Diagnostics (with that package picked when the
 * server named it). An internal error links to the log (admins only, and not on Diagnostics itself).
 */
export function errorAction(err: unknown): ErrorAction | null {
  const e = err as { code?: string; details?: { reason?: unknown; package?: unknown } } | null
  // Inside Diagnostics the link would only jump to the card that is already failing.
  const onDiagnostics = typeof window !== 'undefined' && window.location.hash.startsWith('#/diagnostics')
  if (e?.code === 'internal_error' && canOpenDiagnostics() && !onDiagnostics) return { label: 'Open the log', href: routeHref({ name: 'diagnostics', card: 'log' }) }
  if (e?.code !== 'dependency_unavailable') return null
  const reason = e.details?.reason
  // A link to a page the viewer can't open is a dead end; the message itself says to ask the PC owner.
  if (reason === 'no_key' || reason === 'no_token') return canReadSettings(getSession()) ? { label: 'Open Settings', href: routeHref({ name: 'settings' }) } : null
  if (reason === 'missing_package' && canOpenDiagnostics()) {
    const pkg = e.details?.package
    return {
      label: 'Open Diagnostics',
      href: routeHref({ name: 'diagnostics', card: 'packages', ...(typeof pkg === 'string' && PACKAGE_KEY.test(pkg) ? { pkg } : {}) }),
    }
  }
  return null
}
