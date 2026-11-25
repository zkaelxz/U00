import { describe, expect, it } from 'vitest'

import type { SearchResult, SourceSummary } from '../../types/sources'
import { canAddToLibrary, findRows } from './findResultsModel'

const src = (name: string, types: string[], languages: string[]) =>
  ({ name, display_name: name.toUpperCase(), content_types: types, languages }) as unknown as SourceSummary
const SOURCES = [src('a', ['manga'], ['ja']), src('b', ['novel'], ['zh']), src('c', ['video'], ['ko'])]
const RESULTS: SearchResult['results'] = [
  { title: 'Heaven', key: 'h', sources: ['a', 'b'], entries: [
    { source: 'a', series_id: '1', title: 'Heaven A', url: 'https://a.example/1', cover_url: '' },
    { source: 'b', series_id: '2', title: '', url: 'https://b.example/2', cover_url: '' },
  ] },
  { title: 'Clip', key: 'c', sources: ['c'], entries: [{ source: 'c', series_id: '3', title: 'Clip', url: 'https://c.example/3', cover_url: '' }] },
]

describe('findRows', () => {
  it('makes one row per source match, naming the kind, language and source', () => {
    const rows = findRows(RESULTS, SOURCES, 'all')
    expect(rows.map((r) => [r.key, r.title, r.kindWord, r.language, r.sourceName])).toEqual([
      ['a:1', 'Heaven A', 'Comic', 'ja', 'A'],
      ['b:2', 'Heaven', 'Novel', 'zh', 'B'],
      ['c:3', 'Clip', 'Video or audio', 'ko', 'C'],
    ])
  })

  it('narrows to one kind of source', () => {
    expect(findRows(RESULTS, SOURCES, 'novels').map((r) => r.key)).toEqual(['b:2'])
    expect(findRows(RESULTS, SOURCES, 'media').map((r) => r.key)).toEqual(['c:3'])
  })

  it('offers Add to Library for comics and novels only', () => {
    expect(findRows(RESULTS, SOURCES, 'all').map(canAddToLibrary)).toEqual([true, true, false])
  })
})
