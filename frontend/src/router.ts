import { useEffect, useState } from 'react'

export type Route =
  | { name: 'library' }
  | { name: 'drama'; id: number; stage: string }
  | { name: 'settings' }
  | { name: 'diagnostics' }
  | { name: 'translate' }

export const DEFAULT_STAGE = 'source'

// Parses a location hash ("#/drama/3/review"). Unknown or malformed
// paths fall back to the library.
export function parseRoute(hash: string): Route {
  const parts = hash.replace(/^#\/?/, '').split('?')[0].split('/').filter(Boolean)
  const [head, a, b] = parts
  if (head === 'settings' && parts.length === 1) return { name: 'settings' }
  if (head === 'diagnostics' && parts.length === 1) return { name: 'diagnostics' }
  if (head === 'translate' && parts.length === 1) return { name: 'translate' }
  if (head === 'drama' && a && /^\d+$/.test(a) && Number(a) >= 1 && parts.length <= 3) {
    let stage = DEFAULT_STAGE
    if (b) {
      try {
        stage = decodeURIComponent(b)
      } catch {
        return { name: 'library' }
      }
    }
    return { name: 'drama', id: Number(a), stage }
  }
  return { name: 'library' }
}

export function routeHref(r: Route): string {
  if (r.name === 'drama') return `#/drama/${r.id}/${encodeURIComponent(r.stage)}`
  return `#/${r.name}`
}

export function navigate(r: Route) {
  window.location.hash = routeHref(r)
}

export function useRoute(): Route {
  const [route, setRoute] = useState(() => parseRoute(window.location.hash))
  useEffect(() => {
    const on = () => setRoute(parseRoute(window.location.hash))
    window.addEventListener('hashchange', on)
    return () => window.removeEventListener('hashchange', on)
  }, [])
  return route
}
