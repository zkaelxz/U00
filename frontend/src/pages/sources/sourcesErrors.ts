import { safeDetail } from '../../components/errorMessages'
import type { SourceErrorView } from '../../types/sources'

export interface SourceErrorCopy {
  text: string
  // A browser check: the page to open yourself (scheme+host+path).
  openUrl?: string
  // Worth offering Try again (a browser check the user may have cleared).
  retry?: boolean
}

type ErrLike = Partial<SourceErrorView> & { details?: unknown }

function detailsOf(err: ErrLike): Record<string, unknown> {
  const d = err.details
  return d && typeof d === 'object' ? (d as Record<string, unknown>) : {}
}

/** Plain-English copy for a failed source call (an ApiError or a search's per-source error). */
export function describeSourceError(err: unknown, source: string, remote = false): SourceErrorCopy {
  const e = (err ?? {}) as ErrLike
  const d = detailsOf(e)
  const reason = d.reason
  if (reason === 'CONTENT_HIDDEN') {
    return { text: `${source} hides some works. Turn on Adult works in Source settings${remote ? ' on the main PC' : ''}.` }
  }
  if (reason === 'TOS_PROHIBITED') return { text: `${source}'s terms restrict automated access.` }
  if (reason === 'NOT_SUPPORTED') return { text: `${source} can't do that.` }
  if (reason === 'CANCELLED') return { text: 'Stopped.' }
  if (e.status === 409 && d.handoff) {
    const url = typeof d.open_url === 'string' && /^https?:\/\//i.test(d.open_url) ? d.open_url : undefined
    return { text: `${source} showed a browser check. Baihe never gets past these.`, openUrl: url, retry: true }
  }
  if (e.status === 503) {
    const after = typeof d.retry_after === 'number' ? d.retry_after : 0
    if (after > 0) {
      return {
        text: `${source} is paused after repeated failures. Try again in ${Math.ceil(after / 60)} min.`,
      }
    }
    return { text: `${source} isn't reachable right now.` }
  }
  const detail = typeof e.message === 'string' ? safeDetail(e.message) : null
  return { text: detail ?? `${source} failed. Details are in the app log.` }
}
