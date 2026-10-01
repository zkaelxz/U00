import { useEffect, useState } from 'react'

export type Route =
  | { name: 'library' }
  // stage null: no stage in the URL; the Workspace opens the drama's current stage.
  | { name: 'drama'; id: number; stage: string | null }
  | { name: 'settings' }
  | { name: 'admin' }
  | { name: 'diagnostics' }
  // compare: the raw "engine:model,engine:model" value of ?compare= (Model
  // health's "Compare in Benchmark Lab"; pages/benchmark/benchmarkForm.ts reads it).
  | { name: 'benchmark'; compare?: string }
  | { name: 'translate' }
  | { name: 'sources' }
  | { name: 'discover' }
  | { name: 'live' }
  | { name: 'assistant' }
  | { name: 'read'; id: number; page: number | null }
  | { name: 'comic'; id: number; page: number | null }

export const DEFAULT_STAGE = 'source'

// Parses a location hash ("#/drama/3/review"). Unknown or malformed
// paths fall back to the library.
export function parseRoute(hash: string): Route {
  const [path, qs = ''] = hash.replace(/^#\/?/, '').split('?')
  const parts = path.split('/').filter(Boolean)
  const [head, a, b] = parts
  if (head === 'settings' && parts.length === 1) return { name: 'settings' }
  if (head === 'admin' && parts.length === 1) return { name: 'admin' }
  if (head === 'diagnostics' && parts.length === 1) return { name: 'diagnostics' }
  if (head === 'benchmark' && parts.length === 1) {
    // Kept raw (still %-encoded) so a ":" or "," inside a model name can't be misread.
    const compare = qs.split('&').find((p) => p.startsWith('compare='))?.slice('compare='.length)
    return compare && compare.length <= 1000 ? { name: 'benchmark', compare } : { name: 'benchmark' }
  }
  if (head === 'translate' && parts.length === 1) return { name: 'translate' }
  if (head === 'sources' && parts.length === 1) return { name: 'sources' }
  if (head === 'discover' && parts.length === 1) return { name: 'discover' }
  if (head === 'live' && parts.length === 1) return { name: 'live' }
  if (head === 'assistant' && parts.length === 1) return { name: 'assistant' }
  if ((head === 'read' || head === 'comic') && a && /^\d+$/.test(a) && Number(a) >= 1 && parts.length === 2) {
    // "#/read/3?page=2", "#/comic/3?page=2"; a missing or bad page means "resume where I left off".
    const p = new URLSearchParams(qs).get('page')
    const page = p && /^\d+$/.test(p) && Number(p) >= 1 ? Number(p) : null
    return { name: head, id: Number(a), page }
  }
  if (head === 'drama' && a && /^\d+$/.test(a) && Number(a) >= 1 && parts.length <= 3) {
    let stage: string | null = null
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
  if (r.name === 'drama') return r.stage === null ? `#/drama/${r.id}` : `#/drama/${r.id}/${encodeURIComponent(r.stage)}`
  if (r.name === 'read' || r.name === 'comic') return `#/${r.name}/${r.id}${r.page ? `?page=${r.page}` : ''}`
  if (r.name === 'benchmark' && r.compare) return `#/benchmark?compare=${r.compare}`
  return `#/${r.name}`
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
