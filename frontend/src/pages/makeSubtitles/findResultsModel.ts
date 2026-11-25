// What a Find & Add search result row says and does, from the search result and the sources list. Pure of React.
import type { SearchResult, SourceSummary } from '../../types/sources'
import { kindOf } from '../sources/sourcesFormat'

export type FindFilter = 'all' | 'comics' | 'novels' | 'media'
export const FIND_FILTERS: { id: FindFilter; label: string }[] = [
  { id: 'all', label: 'All' },
  { id: 'comics', label: 'Comics' },
  { id: 'novels', label: 'Novels' },
  { id: 'media', label: 'Videos' },
]
const KIND_WORD = { comics: 'Comic', novels: 'Novel', media: 'Video or audio' } as const

export interface FindRow {
  key: string
  source: string
  seriesId: string
  url: string
  title: string
  kind: 'comics' | 'novels' | 'media'
  kindWord: string
  language: string | null
  sourceName: string
}

/** One row per source's match (the search groups the same work across sources), narrowed to one kind of source. */
export function findRows(results: SearchResult['results'], sources: SourceSummary[], filter: FindFilter): FindRow[] {
  const rows: FindRow[] = []
  for (const m of results) {
    for (const e of m.entries) {
      const s = sources.find((x) => x.name === e.source)
      const kind = s ? kindOf(s) : 'media'
      if (filter !== 'all' && kind !== filter) continue
      rows.push({
        key: `${e.source}:${e.series_id}`,
        source: e.source,
        seriesId: e.series_id,
        url: e.url,
        title: e.title || m.title,
        kind,
        kindWord: KIND_WORD[kind],
        language: s?.languages[0] ?? null,
        sourceName: s?.display_name ?? e.source,
      })
    }
  }
  return rows
}

/** Only a comic or a novel can be made into a title straight from its series; audio and video open in Sources. */
export const canAddToLibrary = (row: FindRow): boolean => row.kind !== 'media' && !!row.url
