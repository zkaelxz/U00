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
 * server named it). A failed job links to Jobs and an internal error to the log (admins only).
 */
export function errorAction(err: unknown): ErrorAction | null {
  const e = err as { code?: string; details?: { reason?: unknown; package?: unknown } } | null
  // The banner's own text has no step for a bare job failure or an unexplained crash, so link to where the detail is.
  if (e?.code === 'job_failed') return { label: 'Open Jobs', href: routeHref({ name: 'jobs' }) }
  if (e?.code === 'internal_error' && canOpenDiagnostics()) return { label: 'Open the log', href: routeHref({ name: 'diagnostics', card: 'log' }) }
  if (e?.code !== 'dependency_unavailable') return null
  const reason = e.details?.reason
  if (reason === 'no_key' || reason === 'no_token') return { label: 'Open Settings', href: routeHref({ name: 'settings' }) }
  if (reason === 'missing_package') {
    const pkg = e.details?.package
    return {
      label: 'Open Diagnostics',
      href: routeHref({ name: 'diagnostics', card: 'packages', ...(typeof pkg === 'string' && PACKAGE_KEY.test(pkg) ? { pkg } : {}) }),
    }
  }
  return null
}
