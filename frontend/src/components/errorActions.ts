import { routeHref } from '../router'

export interface ErrorAction {
  label: string
  href: string
}

// A package key worth putting in a link: letters, digits and . _ - only (never text from a message).
const PACKAGE_KEY = /^[A-Za-z0-9][A-Za-z0-9._-]{0,59}$/

/**
 * The fix that matches the CAUSE of a dependency error: a missing key or token is added in
 * Settings, a missing package is installed in Diagnostics (with that package picked when the
 * server named it). Anything else gets no action here.
 */
export function errorAction(err: unknown): ErrorAction | null {
  const e = err as { code?: string; details?: { reason?: unknown; package?: unknown } } | null
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
