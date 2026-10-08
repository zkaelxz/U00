import type { SourceSummary } from '../../types/sources'

// While false, other devices cannot search sources from the UI (search stays
// PC only). The server's permissions (docs/remote-access-decision.md) still
// decide what a request may do; this is the owner's call on the UI side.
export const SEARCH_REMOTE_ALLOWED = false

export const RESULTS_PAGE = 30

export const CHAPTERS_PAGE = 100

/** Identifies the "Open on …" button that opened a series (focus returns to it). */
export const openerKey = (source: string, seriesId: string) => `${source}:${seriesId}`

export function looksLikeUrl(text: string): boolean {
  const t = text.trim().toLowerCase()
  return t.includes('://') || t.startsWith('www.')
}

/** Enabled sources that can search, in list order. */
export function searchableSources(sources: SourceSummary[]): SourceSummary[] {
  return sources.filter((s) => s.enabled && s.supports.search)
}

/**
 * The checked sources: every searchable one except those the viewer unticked.
 * The remembered list holds unticked names, so a newly enabled source starts
 * ticked and a name that is no longer enabled simply drops out.
 */
export function selectedSources(searchable: string[], excluded: string[]): string[] {
  return searchable.filter((n) => !excluded.includes(n))
}

/** The `sources` field to send: left out when every searchable source is ticked. */
export function searchSourcesParam(searchable: string[], selected: string[]): string[] | undefined {
  return selected.length === searchable.length ? undefined : selected
}

export function searchInSummary(selected: number, total: number): string {
  const noun = `searchable source${total === 1 ? '' : 's'}`
  if (selected === total) return `All ${total} ${noun}`
  return `${selected} of ${total} ${noun}`
}

/** Why Search is disabled, or null when it can run. */
export function searchDisabledReason(
  query: string,
  searchable: number,
  selected: number,
  remote: boolean,
): string | null {
  if (!query.trim()) return 'Still needed: a title.'
  if (looksLikeUrl(query)) return 'Enter a title, not a link.'
  if (searchable === 0) {
    return `Still needed: a source that can search. Turn one on in Source settings${remote ? ' on the main PC' : ''}.`
  }
  if (selected === 0) return 'Still needed: at least one source.'
  return null
}

export function resultsHeader(results: number, problems: number): string {
  const r = `${results} result${results === 1 ? '' : 's'}`
  if (!problems) return r
  return `${r} · ${problems} source${problems === 1 ? '' : 's'} had problems`
}

export function percent(progress: number | null | undefined): string {
  return progress === null || progress === undefined ? '' : ` ${Math.round(progress * 100)}%`
}
