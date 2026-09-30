// Pure text helpers for the web-search fallback (roadmap item 114): the
// Sources "Search the web" row and the Settings > Web search card. Unit-tested.
import { ApiError } from '../../api/client'
import type { WebSearchConfig } from '../../types/webSearch'

export const ADDRESS_WRITES_REFUSED =
  'The SearXNG address can only be changed on the Baihe PC itself, with key writes turned on (start the API with BAIHE_API_ALLOW_KEY_WRITES=1).'

export function webSearchSummary(c: WebSearchConfig): string {
  if (!c.enabled) return 'Off'
  return c.base_url ? 'On' : 'On, no address'
}

export function webResultsHeader(n: number): string {
  if (n === 0) return 'No web results.'
  return n === 1 ? '1 web result' : `${n} web results`
}

// Server messages for these routes are fixed text (never the address), so a
// 409/422/503 message is safe to show.
export function webSearchErrorMessage(err: unknown, addressWrite = false): string {
  if (err instanceof ApiError) {
    if (err.status === 403) return addressWrite ? ADDRESS_WRITES_REFUSED : 'This is PC only. Run it on the main PC.'
    if (err.status === 0) return 'Could not reach the Baihe API. Is it running?'
    if ([409, 422, 503].includes(err.status) && err.message) return err.message
  }
  return 'That did not work. Try again.'
}
